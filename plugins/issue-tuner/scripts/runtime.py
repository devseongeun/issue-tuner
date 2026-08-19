#!/usr/bin/env python3
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
HEX_HASH = re.compile(r"[0-9a-f]{64}\Z")
SENSITIVE_ENV_COMPONENTS = {
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "COOKIE",
    "AUTH",
    "AUTHORIZATION",
    "CREDENTIAL",
    "CREDENTIALS",
    "KEY",
    "APIKEY",
}
_CHILDREN = {}


def _register_process(process):
    _CHILDREN[process.pid] = process


def validate_directory(value, name):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{name} must be an existing non-symlink directory")
    return path.resolve()


def _configured_home(home=None):
    value = home if home is not None else os.environ.get("ISSUE_TUNER_HOME") or Path.home() / ".issue-tuner"
    return validate_directory(value, "home")


def _runs(home=None):
    configured_home = _configured_home(home)
    runs = configured_home / "runs"
    if runs.is_symlink() or not runs.is_dir():
        raise ValueError("runs must be an existing non-symlink directory")
    resolved = runs.resolve()
    if resolved.parent != configured_home:
        raise ValueError("runs escapes configured home")
    return resolved


def _validated_run_dir(run_dir, home=None):
    path = Path(run_dir)
    if not path.is_absolute():
        raise ValueError("run_dir must be absolute")
    if path.is_symlink() or not path.is_dir():
        raise ValueError("run_dir must be an existing non-symlink directory")
    runs = _runs(home)
    resolved = path.resolve()
    if resolved.parent != runs or not SAFE_RUN_ID.fullmatch(resolved.name):
        raise ValueError("run_dir must be a direct safe child of configured runs")
    return resolved


def find_port(start=3000):
    if not isinstance(start, int) or isinstance(start, bool) or not 1 <= start <= 65535:
        raise ValueError("start must be a valid port")
    for port in range(start, 65536):
        with socket.socket() as candidate:
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no loopback port available")


def _write_json(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, sort_keys=True)
            file.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _runtime_directory(run_dir):
    directory = run_dir / "runtime"
    if directory.is_symlink():
        raise ValueError("runtime directory must not be a symlink")
    directory.mkdir(exist_ok=True)
    resolved = directory.resolve()
    if resolved.parent != run_dir:
        raise ValueError("runtime directory escapes run directory")
    return resolved


def _open_log(directory, name):
    path = directory / name
    if path.is_symlink():
        raise ValueError("runtime log must not be a symlink")
    return path.open("ab")


def _new_record_path(directory):
    path = directory / "record.json"
    if path.is_symlink():
        raise ValueError("runtime record must not be a symlink")
    if path.exists():
        raise FileExistsError("runtime record already exists")
    return path


def _configuration(worktree, config):
    if config is None:
        try:
            with (worktree / ".issue-tuner.json").open(encoding="utf-8") as file:
                config = json.load(file)
        except (OSError, json.JSONDecodeError):
            raise ValueError("invalid runtime configuration") from None
    if not isinstance(config, dict) or not isinstance(config.get("runtime"), dict):
        raise ValueError("invalid runtime configuration")
    command = config["runtime"].get("start")
    ready_path = config["runtime"].get("ready_path")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("runtime start command must be non-empty")
    if not isinstance(ready_path, str) or not ready_path.startswith("/"):
        raise ValueError("runtime ready_path must be an absolute URL path")
    if (
        not ready_path.isascii()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in ready_path)
        or any(character in ready_path for character in "\\%?#")
    ):
        raise ValueError("runtime ready_path must be an absolute URL path")
    parsed = urlsplit(ready_path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("runtime ready_path must be an absolute URL path")
    return command, ready_path


def _runtime_env():
    environment = {}
    for name, value in os.environ.items():
        if SENSITIVE_ENV_COMPONENTS.intersection(name.upper().split("_")):
            continue
        environment[name] = value
    environment.setdefault("PATH", os.defpath)
    return environment


def _wait_ready(port, path, process=None, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            response.read(1)
            if 200 <= response.status < 400:
                return True
        except OSError:
            pass
        finally:
            connection.close()
        time.sleep(0.05)
    return False


def _ps_value(pid, field):
    result = subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", f"{field}="],
        text=True,
        capture_output=True,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _process_identity(pid):
    start_time = _ps_value(pid, "lstart")
    command = _ps_value(pid, "command")
    if start_time is None or command is None:
        return None
    return {
        "start_time": start_time,
        "command_hash": hashlib.sha256(command.encode()).hexdigest(),
    }


def _pid_alive(pid):
    process = _CHILDREN.get(pid)
    if process is not None:
        return process.poll() is None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_group(pid, pgid=None, timeout=5):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or pid == os.getpid():
        return False
    if pgid is None:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            _CHILDREN.pop(pid, None)
            return True
    if not isinstance(pgid, int) or isinstance(pgid, bool) or pgid != pid or pgid == os.getpgrp():
        return False
    if not _group_alive(pgid):
        _CHILDREN.pop(pid, None)
        return True
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process = _CHILDREN.get(pid)
        if process is not None:
            process.poll()
        if not _group_alive(pgid):
            _CHILDREN.pop(pid, None)
            return True
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and _group_alive(pgid):
        process = _CHILDREN.get(pid)
        if process is not None:
            process.poll()
        time.sleep(0.05)
    stopped = not _group_alive(pgid)
    if stopped:
        _CHILDREN.pop(pid, None)
    return stopped


def _listener_pids(port):
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        return set()
    listeners = set()
    for line in result.stdout.splitlines():
        if line.startswith("f") and line[1:].isdigit():
            continue
        if not line.startswith("p") or not line[1:].isdigit():
            return set()
        pid = int(line[1:])
        if pid <= 0 or pid == os.getpid():
            return set()
        listeners.add(pid)
    return listeners


def _listeners_in_group(port, pgid):
    listeners = _listener_pids(port)
    if not listeners:
        return False
    try:
        return all(os.getpgid(listener) == pgid for listener in listeners)
    except (ProcessLookupError, PermissionError):
        return False


def _valid_record(record):
    required = {
        "status",
        "pid",
        "pgid",
        "port",
        "cwd",
        "identity",
        "origin",
        "ready_path",
        "record_path",
        "started_at",
    }
    if not isinstance(record, dict) or set(record) != required or record.get("status") != "running":
        return None
    pid = record["pid"]
    pgid = record["pgid"]
    port = record["port"]
    cwd = record["cwd"]
    identity = record["identity"]
    record_path = record["record_path"]
    try:
        resolved_cwd = str(Path(cwd).resolve()) if isinstance(cwd, str) else None
        resolved_record = str(Path(record_path).resolve()) if isinstance(record_path, str) else None
    except (OSError, ValueError):
        return None
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or pid == os.getpid()
        or not isinstance(pgid, int)
        or isinstance(pgid, bool)
        or pgid != pid
        or pgid == os.getpgrp()
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
        or not isinstance(cwd, str)
        or not Path(cwd).is_absolute()
        or cwd != resolved_cwd
        or not isinstance(record_path, str)
        or not Path(record_path).is_absolute()
        or record_path != resolved_record
        or not isinstance(record["started_at"], int)
        or isinstance(record["started_at"], bool)
        or record["origin"] != f"http://127.0.0.1:{port}"
        or not isinstance(record["ready_path"], str)
        or not isinstance(identity, dict)
        or set(identity) != {"start_time", "command_hash"}
        or not isinstance(identity["start_time"], str)
        or not identity["start_time"]
        or not isinstance(identity["command_hash"], str)
        or not HEX_HASH.fullmatch(identity["command_hash"])
    ):
        return None
    return pid, pgid, port, cwd


def _owned_process(record):
    valid = _valid_record(record)
    if valid is None:
        return False
    pid, pgid, port, cwd = valid
    if not _pid_alive(pid):
        return False
    try:
        if os.getpgid(pid) != pgid:
            return False
    except (ProcessLookupError, PermissionError):
        return False
    if _process_identity(pid) != record["identity"]:
        return False
    current = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        text=True,
        capture_output=True,
    )
    paths = [line[1:] for line in current.stdout.splitlines() if line.startswith("n")]
    if current.returncode or paths != [cwd]:
        return False
    return _listeners_in_group(port, pgid)


def _record_for_stop(record, home=None):
    if _valid_record(record) is None:
        return None
    path = Path(record["record_path"])
    if path.is_symlink() or path.name != "record.json" or path.parent.name != "runtime":
        return None
    try:
        run_dir = _validated_run_dir(path.parent.parent, home)
    except ValueError:
        return None
    expected = (run_dir / "runtime" / "record.json").resolve()
    if path != expected or path.parent.is_symlink() or not path.parent.is_dir():
        return None
    try:
        with path.open(encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return (saved, path) if saved == record else None


def start_runtime(worktree, run_dir, config=None, home=None):
    worktree = validate_directory(worktree, "worktree")
    run_dir = _validated_run_dir(run_dir, home)
    try:
        run_dir.relative_to(worktree)
    except ValueError:
        pass
    else:
        raise ValueError("run_dir must be outside worktree")
    command, ready_path = _configuration(worktree, config)
    port = find_port()
    try:
        argv = shlex.split(command.replace("${PORT}", str(port)))
    except ValueError:
        raise ValueError("runtime start command is invalid") from None
    if not argv:
        raise ValueError("runtime start command must be non-empty")

    runtime_dir = _runtime_directory(run_dir)
    record_path = _new_record_path(runtime_dir)
    stdout = _open_log(runtime_dir, "stdout.log")
    stderr = _open_log(runtime_dir, "stderr.log")
    try:
        process = subprocess.Popen(
            argv,
            cwd=worktree,
            env=_runtime_env(),
            stdout=stdout,
            stderr=stderr,
            shell=False,
            start_new_session=True,
        )
    except OSError:
        raise RuntimeError("runtime failed to start") from None
    finally:
        stdout.close()
        stderr.close()

    _register_process(process)
    pgid = process.pid
    try:
        if os.getpgid(process.pid) != pgid:
            raise RuntimeError("runtime process group was invalid")
        if not _wait_ready(port, ready_path, process):
            raise RuntimeError("runtime did not become ready")
        if not _listeners_in_group(port, pgid):
            raise RuntimeError("runtime listener ownership was invalid")
        identity = _process_identity(process.pid)
        if identity is None or os.getpgid(process.pid) != pgid:
            raise RuntimeError("runtime process identity was unavailable")
        record = {
            "status": "running",
            "pid": process.pid,
            "pgid": pgid,
            "port": port,
            "cwd": str(worktree),
            "identity": identity,
            "origin": f"http://127.0.0.1:{port}",
            "ready_path": ready_path,
            "record_path": str(record_path),
            "started_at": int(time.time()),
        }
        _write_json(record_path, record)
    except BaseException:
        _terminate_group(process.pid, pgid)
        raise
    return record


def stop_owned_process(record, home=None):
    saved = _record_for_stop(record, home)
    if saved is None or not _owned_process(saved[0]):
        return False
    current, path = saved
    if not _terminate_group(current["pid"], current["pgid"]):
        return False
    tombstone = dict(current, status="stopped", stopped_at=int(time.time()))
    _write_json(path, tombstone)
    return True
