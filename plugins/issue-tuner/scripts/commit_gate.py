#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile


def _run(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True
    )
    if result.returncode:
        raise RuntimeError("git command failed")
    return result.stdout


def _repo_root(repo: Path) -> Path:
    repo = Path(repo)
    if not repo.is_absolute() or repo.is_symlink() or not repo.is_dir():
        raise ValueError("repo must be an absolute, non-symlink directory")
    try:
        root = Path(os.fsdecode(_run(repo, "rev-parse", "--show-toplevel")).strip()).resolve()
    except (OSError, RuntimeError):
        raise ValueError("repo must be a Git worktree") from None
    if root.is_symlink() or not root.is_dir():
        raise ValueError("repository root must be a non-symlink directory")
    return root


def _file_path(path: Path, name: str, must_exist: bool = True) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError(f"{name} must be an absolute, non-symlink path")
    if must_exist and not path.is_file():
        raise ValueError(f"{name} must be an existing regular file")
    if not must_exist and path.exists() and not path.is_file():
        raise ValueError(f"{name} must be a regular file path")
    return path


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _outside_file_path(
    path: Path, name: str, root: Path, must_exist: bool = True
) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError(f"{name} must be an absolute, non-symlink path")
    if _inside(path, root):
        raise ValueError(f"{name} must be outside the repository")
    return _file_path(path, name, must_exist)


def _read_regular(path: Path) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError("unable to read required file safely") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("required path must be a regular file")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        ):
            raise ValueError("required file changed while being read")
        return b"".join(chunks), after
    finally:
        os.close(descriptor)


def _verification(path: Path) -> tuple[dict, dict]:
    content, metadata = _read_regular(path)
    try:
        data = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("verification file must contain valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("verification must be a JSON object")
    if data.get("verdict") != "pass":
        raise ValueError("verification verdict must be pass")
    if data.get("blockers") != []:
        raise ValueError("verification blockers must be an empty list")
    source = data.get("source")
    if source not in {"automated", "user_confirmed"}:
        raise ValueError("verification source must be automated or user_confirmed")
    channels = data.get("channels")
    if not isinstance(channels, list) or not channels or any(
        not isinstance(channel, str) or not channel.strip() for channel in channels
    ):
        raise ValueError("verification channels must be a non-empty string list")
    automated_runs = data.get("automated_runs")
    failed_automated_runs = data.get("failed_automated_runs")
    residual_risks = data.get("residual_risks")
    if not isinstance(automated_runs, list) or any(
        not isinstance(run, str) or not run.strip() for run in automated_runs
    ):
        raise ValueError("verification automated_runs must be a string list")
    if not isinstance(failed_automated_runs, list) or any(
        not isinstance(run, str) or not run.strip() for run in failed_automated_runs
    ):
        raise ValueError("verification failed_automated_runs must be a string list")
    if not isinstance(residual_risks, list) or any(
        not isinstance(risk, str) or not risk.strip() for risk in residual_risks
    ):
        raise ValueError("verification residual_risks must be a string list")
    if source == "automated" and (not automated_runs or failed_automated_runs):
        raise ValueError("automated verification requires successful runs without failed runs")
    if source == "user_confirmed" and (
        automated_runs or not failed_automated_runs or not residual_risks
    ):
        raise ValueError("user_confirmed verification requires failed runs and residual risks")
    facts = {
        "verdict": "pass",
        "source": source,
        "channels": channels,
        "automated_runs": automated_runs,
        "failed_automated_runs": failed_automated_runs,
        "residual_risks": residual_risks,
        "blockers": [],
    }
    fingerprint = {
        "type": "file",
        "mode": _regular_mode(metadata),
        "sha256": hashlib.sha256(content).hexdigest(),
        "mtime_ns": metadata.st_mtime_ns,
    }
    return facts, fingerprint


def _relative(raw: bytes) -> str:
    path = os.fsdecode(raw)
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts:
        raise ValueError("git reported an unsafe repository path")
    return path


def _operation(kind: str, path: str, source: str = None) -> dict:
    operation = {"kind": kind, "path": path}
    if source is not None:
        operation["source"] = source
    return operation


def _ordinary_kind(status_code: bytes) -> str:
    if status_code == b"??" or b"A" in status_code:
        return "add"
    if b"D" in status_code:
        return "delete"
    if b"T" in status_code:
        return "typechange"
    if b"U" in status_code:
        return "unmerged"
    if b"M" in status_code:
        return "modify"
    raise ValueError("git reported an unsupported change operation")


def _operation_paths(operations: list[dict]) -> set[str]:
    paths = {operation["path"] for operation in operations}
    paths.update(
        operation["source"]
        for operation in operations
        if operation["kind"] == "rename"
    )
    return paths


def _sorted_operations(operations: list[dict]) -> list[dict]:
    return sorted(
        operations,
        key=lambda operation: (
            operation["path"],
            operation["kind"],
            operation.get("source", ""),
        ),
    )


def _validate_operations(operations: list[dict]) -> list[dict]:
    validated = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("gate_file has an invalid structure")
        kind = operation.get("kind")
        expected_keys = (
            {"kind", "path", "source"}
            if kind in {"rename", "copy"}
            else {"kind", "path"}
        )
        if (
            kind
            not in {
                "add",
                "copy",
                "delete",
                "modify",
                "rename",
                "typechange",
                "unmerged",
            }
            or set(operation) != expected_keys
        ):
            raise ValueError("gate_file has an invalid structure")
        path = operation.get("path")
        if not isinstance(path, str) or _relative(os.fsencode(path)) != path:
            raise ValueError("gate_file has an invalid structure")
        if "source" in operation:
            source = operation["source"]
            if not isinstance(source, str) or _relative(os.fsencode(source)) != source:
                raise ValueError("gate_file has an invalid structure")
        validated.append(operation)
    return _sorted_operations(validated)


def _current_changes(repo: Path) -> tuple[set[str], list[dict]]:
    output = _run(
        repo,
        "--no-optional-locks",
        "status",
        "--porcelain=v1",
        "-z",
        "--find-renames",
        "--untracked-files=all",
    )
    records = output.split(b"\0")
    operations = []
    index = 0
    while index < len(records) and records[index]:
        record = records[index]
        if len(record) < 4 or record[2:3] != b" ":
            raise RuntimeError("git returned malformed status data")
        status_code = record[:2]
        path = _relative(record[3:])
        index += 1
        if b"R" in status_code or b"C" in status_code:
            if index >= len(records) or not records[index]:
                raise RuntimeError("git returned malformed rename data")
            source = _relative(records[index])
            kind = "rename" if b"R" in status_code else "copy"
            operations.append(_operation(kind, path, source))
            index += 1
        else:
            operations.append(_operation(_ordinary_kind(status_code), path))
    operations = _sorted_operations(operations)
    return _operation_paths(operations), operations


def _regular_mode(metadata: os.stat_result) -> str:
    return "100755" if metadata.st_mode & stat.S_IXUSR else "100644"


def _fingerprint(repo: Path, relative: str) -> dict:
    path = repo / relative
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {
            "type": "deleted",
            "mode": "deleted",
            "sha256": None,
            "mtime_ns": None,
        }
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(os.fsencode(path))
        if isinstance(target, str):
            target = os.fsencode(target)
        return {
            "type": "symlink",
            "mode": "120000",
            "sha256": hashlib.sha256(target).hexdigest(),
            "mtime_ns": metadata.st_mtime_ns,
        }
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"changed path has unsupported type: {relative}")
    content, checked = _read_regular(path)
    if (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns) != (
        checked.st_dev,
        checked.st_ino,
        checked.st_mtime_ns,
    ):
        raise ValueError("changed file changed while being fingerprinted")
    return {
        "type": "file",
        "mode": _regular_mode(checked),
        "sha256": hashlib.sha256(content).hexdigest(),
        "mtime_ns": checked.st_mtime_ns,
    }


def _fingerprints(repo: Path, paths: set[str]) -> dict:
    return {path: _fingerprint(repo, path) for path in sorted(paths)}


def _staged_changes(repo: Path) -> tuple[set[str], list[dict]]:
    output = _run(
        repo,
        "diff",
        "--cached",
        "--name-status",
        "-z",
        "-M",
        "--diff-filter=ACDMRTUXB",
    )
    records = output.split(b"\0")
    operations = []
    index = 0
    while index < len(records) and records[index]:
        status_code = records[index]
        index += 1
        if status_code.startswith((b"R", b"C")):
            if index + 1 >= len(records) or not records[index] or not records[index + 1]:
                raise RuntimeError("git returned malformed staged rename data")
            source = _relative(records[index])
            path = _relative(records[index + 1])
            kind = "rename" if status_code.startswith(b"R") else "copy"
            operations.append(_operation(kind, path, source))
            index += 2
        else:
            if index >= len(records) or not records[index]:
                raise RuntimeError("git returned malformed staged change data")
            operations.append(_operation(_ordinary_kind(status_code), _relative(records[index])))
            index += 1
    operations = _sorted_operations(operations)
    return _operation_paths(operations), operations


def _staged_fingerprint(repo: Path, relative: str) -> dict:
    output = _run(
        repo,
        "--literal-pathspecs",
        "ls-files",
        "--stage",
        "-z",
        "--",
        relative,
    )
    if not output:
        return {"type": "deleted", "mode": "deleted", "sha256": None}
    entries = [entry for entry in output.split(b"\0") if entry]
    requested = os.fsencode(relative)
    stage_zero = []
    for entry in entries:
        metadata, separator, returned_path = entry.partition(b"\t")
        if not separator or returned_path != requested:
            raise ValueError(f"staged index entry does not match path: {relative}")
        fields = metadata.split()
        if len(fields) == 3 and fields[2] == b"0":
            stage_zero.append(fields)
    if len(stage_zero) != 1:
        raise ValueError(f"staged path must have exactly one stage-zero entry: {relative}")
    mode, object_id, _ = stage_zero[0]
    if mode == b"120000":
        kind = "symlink"
    elif mode in {b"100644", b"100755"}:
        kind = "file"
    else:
        raise ValueError(f"staged path has unsupported type: {relative}")
    content = _run(repo, "cat-file", "blob", os.fsdecode(object_id))
    return {
        "type": kind,
        "mode": os.fsdecode(mode),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _atomic_json(path: Path, data: dict) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("gate parent must be an existing non-symlink directory")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def record(repo: Path, verification_file: Path, gate_file: Path) -> dict:
    root = _repo_root(repo)
    verification_file = _outside_file_path(verification_file, "verification_file", root)
    gate_file = _outside_file_path(gate_file, "gate_file", root, must_exist=False)
    facts, verification_fingerprint = _verification(verification_file)
    paths, operations = _current_changes(root)
    data = {
        "version": 1,
        "repository_root": str(root),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "verification_file_sha256": verification_fingerprint["sha256"],
        "verification_file": verification_fingerprint,
        "verification": facts,
        "files": _fingerprints(root, paths),
        "operations": operations,
    }
    _atomic_json(gate_file, data)
    return data


def _load_gate(path: Path) -> dict:
    content, _ = _read_regular(path)
    try:
        data = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("gate_file must contain valid JSON") from None
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("files"), dict)
        or not isinstance(data.get("operations"), list)
    ):
        raise ValueError("gate_file has an invalid structure")
    for relative, fingerprint in data["files"].items():
        if (
            not isinstance(relative, str)
            or _relative(os.fsencode(relative)) != relative
            or not isinstance(fingerprint, dict)
        ):
            raise ValueError("gate_file has an invalid structure")
        kind = fingerprint.get("type")
        mode = fingerprint.get("mode")
        digest = fingerprint.get("sha256")
        mtime = fingerprint.get("mtime_ns")
        if kind == "deleted":
            valid = mode == "deleted" and digest is None and mtime is None
        else:
            valid = (
                kind in {"file", "symlink"}
                and (
                    (kind == "file" and mode in {"100644", "100755"})
                    or (kind == "symlink" and mode == "120000")
                )
                and isinstance(digest, str)
                and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                and isinstance(mtime, int)
                and not isinstance(mtime, bool)
            )
        if not valid:
            raise ValueError("gate_file has an invalid structure")
    if _validate_operations(data["operations"]) != data["operations"]:
        raise ValueError("gate_file has an invalid structure")
    return data


def check(repo: Path, verification_file: Path, gate_file: Path) -> list[str]:
    root = _repo_root(repo)
    verification_file = _outside_file_path(verification_file, "verification_file", root)
    gate_file = _outside_file_path(gate_file, "gate_file", root)
    gate = _load_gate(gate_file)
    errors = []
    if gate.get("repository_root") != str(root):
        errors.append("gate was recorded for a different repository")
    facts, verification_fingerprint = _verification(verification_file)
    if (
        gate.get("verification_file") != verification_fingerprint
        or gate.get("verification") != facts
    ):
        errors.append("verification file changed after verification")

    recorded = gate["files"]
    current_paths, current_operations = _current_changes(root)
    recorded_paths = set(recorded)
    if current_operations != gate["operations"]:
        errors.append("verified change operation changed after verification")
    for path in sorted(current_paths - recorded_paths):
        errors.append(f"unverified file present: {path}")
    for path in sorted(recorded_paths - current_paths):
        errors.append(f"verified file missing from current changes: {path}")
    for path in sorted(recorded_paths & current_paths):
        if _fingerprint(root, path) != recorded[path]:
            errors.append(f"verified file changed after verification: {path}")

    staged, staged_operations = _staged_changes(root)
    if staged_operations != gate["operations"]:
        errors.append("staged change operations do not match verification")
    for path in sorted(recorded_paths - staged):
        errors.append(f"verified file is not staged: {path}")
    for path in sorted(staged - recorded_paths):
        errors.append(f"staged file was not verified: {path}")
    for path in sorted(recorded_paths & staged):
        expected = {
            key: recorded[path].get(key) for key in ("type", "mode", "sha256")
        }
        if _staged_fingerprint(root, path) != expected:
            errors.append(f"staged content differs from verified file: {path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("record", "check"))
    parser.add_argument("repo", type=Path)
    parser.add_argument("verification_file", type=Path)
    parser.add_argument("gate_file", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "record":
            record(args.repo, args.verification_file, args.gate_file)
            return 0
        errors = check(args.repo, args.verification_file, args.gate_file)
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
