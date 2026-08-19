import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "runtime.py"
SPEC = importlib.util.spec_from_file_location("runtime", SCRIPT)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


CONFIG = {"runtime": {"start": "python3 -m http.server ${PORT}", "ready_path": "/"}}


class RuntimeTest(unittest.TestCase):
    def layout(self, directory):
        root = Path(directory)
        worktree = root / "worktree"
        home = root / "state"
        run_dir = home / "runs" / "demo-run"
        worktree.mkdir()
        run_dir.mkdir(parents=True)
        return worktree, home, run_dir

    def test_finds_first_loopback_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            occupied = listener.getsockname()[1]
            self.assertEqual(runtime.find_port(occupied), occupied + 1)

    def test_wrapper_child_listener_is_owned_and_stop_tombstones_record(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree, home, run_dir = self.layout(directory)
            (worktree / "wrapper.py").write_text(
                "import subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-m', 'http.server', sys.argv[1]])\n"
                "raise SystemExit(child.wait())\n",
                encoding="utf-8",
            )
            record = None
            try:
                record = runtime.start_runtime(
                    worktree,
                    run_dir,
                    {"runtime": {"start": "python3 wrapper.py ${PORT}", "ready_path": "/"}},
                    home=home,
                )
                listeners = runtime._listener_pids(record["port"])
                self.assertTrue(listeners)
                self.assertNotIn(record["pid"], listeners)
                self.assertTrue(all(os.getpgid(pid) == record["pgid"] for pid in listeners))
                self.assertTrue(runtime._owned_process(record))
                self.assertEqual(record["status"], "running")
                self.assertEqual(record["pgid"], record["pid"])
                self.assertEqual(set(record["identity"]), {"start_time", "command_hash"})
                path = run_dir / "runtime" / "record.json"
                self.assertEqual(record["record_path"], str(path.resolve()))

                self.assertTrue(runtime.stop_owned_process(record, home=home))
                saved = json.loads(path.read_text())
                self.assertEqual(saved["status"], "stopped")
                self.assertIsInstance(saved["stopped_at"], int)
                self.assertEqual(saved["identity"], record["identity"])
                self.assertFalse(runtime.stop_owned_process(saved, home=home))
                self.assertFalse(self._listening(record["port"]))
            finally:
                if record and runtime._pid_alive(record["pid"]):
                    runtime._terminate_group(record["pid"], record["pgid"])

    def test_identity_or_cwd_mismatch_refuses_stop_and_leaves_process_running(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree, home, run_dir = self.layout(directory)
            other = Path(directory) / "other"
            other.mkdir()
            record = runtime.start_runtime(worktree, run_dir, CONFIG, home=home)
            try:
                with patch.object(runtime, "_process_identity", return_value={"start_time": "other", "command_hash": "0" * 64}):
                    self.assertFalse(runtime.stop_owned_process(record, home=home))
                self.assertTrue(self._listening(record["port"]))
                self.assertFalse(runtime._owned_process(dict(record, cwd=str(other.resolve()))))
                self.assertTrue(runtime.stop_owned_process(record, home=home))
            finally:
                if runtime._pid_alive(record["pid"]):
                    runtime._terminate_group(record["pid"], record["pgid"])

    def test_uses_literal_argv_and_sanitized_explicit_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree, home, run_dir = self.layout(directory)
            process = MagicMock(pid=4242)
            identity = {"start_time": "start", "command_hash": "a" * 64}
            token_name = "SYNTHETIC_" + "TOKEN"
            password_name = "service_" + "password"
            key_names = {
                "AWS_ACCESS_" + "KEY_ID",
                "API" + "KEY",
                "SSH_PRIVATE_" + "KEY_FILE",
            }
            sensitive_names = {
                token_name,
                password_name,
                "API_KEY",
                "GITHUB_" + "TOKEN",
                "SESSION_" + "SECRET",
                "HTTP_" + "COOKIE",
                "CLIENT_" + "AUTH",
                "HTTP_" + "AUTHORIZATION",
                "SERVICE_" + "CREDENTIALS",
                *key_names,
            }
            benign = {
                "MONKEY": "banana",
                "KEYBOARD_LAYOUT": "us",
                "AUTHOR_NAME": "example",
                "TOKENIZER_CONFIG": "local",
                "NORMAL_SETTING": "kept",
            }
            environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp/home",
                "LANG": "C",
                "TMPDIR": "/tmp",
                **benign,
                **{name: "drop" for name in sensitive_names},
            }
            try:
                with patch.dict(os.environ, environment, clear=True), patch.object(
                    runtime, "find_port", return_value=3456
                ), patch.object(runtime.subprocess, "Popen", return_value=process) as popen, patch.object(
                    runtime, "_wait_ready", return_value=True
                ), patch.object(runtime, "_process_identity", return_value=identity), patch.object(
                    runtime.os, "getpgid", return_value=4242
                ), patch.object(
                    runtime, "_listener_pids", return_value={4242}
                ):
                    record = runtime.start_runtime(
                        worktree,
                        run_dir,
                        {
                            "runtime": {
                                "start": "python3 -c 'print(1)' ${PORT} ${HOME}",
                                "ready_path": "/health",
                            }
                        },
                        home=home,
                    )

                self.assertEqual(popen.call_args.args[0], ["python3", "-c", "print(1)", "3456", "${HOME}"])
                self.assertEqual(popen.call_args.kwargs["cwd"], worktree.resolve())
                self.assertFalse(popen.call_args.kwargs["shell"])
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                child_env = popen.call_args.kwargs["env"]
                self.assertEqual(child_env["PATH"], environment["PATH"])
                self.assertEqual({name: child_env[name] for name in benign}, benign)
                self.assertFalse(sensitive_names & set(child_env))
                saved = json.loads((run_dir / "runtime" / "record.json").read_text())
                self.assertEqual(saved, record)
                self.assertNotIn("argv", saved)
                self.assertNotIn("env", saved)
            finally:
                runtime._CHILDREN.pop(process.pid, None)

    def test_accepts_only_direct_safe_run_under_configured_home(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            worktree, home, run_dir = self.layout(directory)
            outside_run = Path(outside) / "demo-run"
            outside_run.mkdir()
            with patch.object(runtime.subprocess, "Popen") as popen:
                with self.assertRaises(ValueError):
                    runtime.start_runtime(worktree, outside_run, CONFIG, home=home)

                inside_home = worktree / "state"
                inside_run = inside_home / "runs" / "demo-run"
                inside_run.mkdir(parents=True)
                with self.assertRaises(ValueError):
                    runtime.start_runtime(worktree, inside_run, CONFIG, home=inside_home)

                linked_home = Path(directory) / "linked-home"
                linked_home.symlink_to(home, target_is_directory=True)
                with self.assertRaises(ValueError):
                    runtime.start_runtime(worktree, run_dir, CONFIG, home=linked_home)

                real_runs = home / "real-runs"
                real_runs.mkdir()
                linked_runs_home = Path(directory) / "linked-runs-home"
                linked_runs_home.mkdir()
                (linked_runs_home / "runs").symlink_to(real_runs, target_is_directory=True)
                linked_run = real_runs / "demo-run"
                linked_run.mkdir()
                with self.assertRaises(ValueError):
                    runtime.start_runtime(worktree, linked_run, CONFIG, home=linked_runs_home)

                linked_run_dir = home / "runs" / "linked-run"
                linked_run_dir.symlink_to(run_dir, target_is_directory=True)
                with self.assertRaises(ValueError):
                    runtime.start_runtime(worktree, linked_run_dir, CONFIG, home=home)

                outside_record = Path(outside) / "record.json"
                outside_record.write_text("unchanged", encoding="utf-8")
                runtime_dir = run_dir / "runtime"
                runtime_dir.mkdir()
                (runtime_dir / "record.json").symlink_to(outside_record)
                with self.assertRaises(ValueError):
                    runtime.start_runtime(worktree, run_dir, CONFIG, home=home)
                self.assertEqual(outside_record.read_text(), "unchanged")

            popen.assert_not_called()

    def test_rejects_ready_listener_outside_started_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree, home, run_dir = self.layout(directory)
            process = MagicMock(pid=4242)
            identity = {"start_time": "start", "command_hash": "a" * 64}

            def process_group(pid):
                return 4242 if pid == 4242 else 9999

            try:
                with patch.object(runtime, "find_port", return_value=3456), patch.object(
                    runtime.subprocess, "Popen", return_value=process
                ), patch.object(runtime, "_wait_ready", return_value=True), patch.object(
                    runtime, "_process_identity", return_value=identity
                ), patch.object(runtime, "_listener_pids", return_value={7777}), patch.object(
                    runtime.os, "getpgid", side_effect=process_group
                ), patch.object(runtime, "_terminate_group", return_value=True):
                    with self.assertRaises(RuntimeError):
                        runtime.start_runtime(worktree, run_dir, CONFIG, home=home)
                self.assertFalse((run_dir / "runtime" / "record.json").exists())
            finally:
                runtime._CHILDREN.pop(process.pid, None)

    def test_rejects_ambiguous_ready_paths_before_record_write(self):
        with tempfile.TemporaryDirectory() as directory:
            worktree, home, run_dir = self.layout(directory)
            paths = ["/health?mode=test", "/health#fragment", "/health\\admin", "/health%2fadmin", "/health\nadmin"]
            with patch.object(runtime.subprocess, "Popen") as popen:
                for ready_path in paths:
                    with self.subTest(ready_path=ready_path), self.assertRaises(ValueError):
                        runtime.start_runtime(
                            worktree,
                            run_dir,
                            {"runtime": {"start": "python3 -m http.server ${PORT}", "ready_path": ready_path}},
                            home=home,
                        )
            popen.assert_not_called()
            self.assertFalse((run_dir / "runtime" / "record.json").exists())

    def test_readiness_reads_at_most_one_byte(self):
        process = MagicMock()
        process.poll.return_value = None
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        with patch.object(runtime.http.client, "HTTPConnection", return_value=connection):
            self.assertTrue(runtime._wait_ready(3456, "/", process, timeout=0.1))
        response.read.assert_called_once_with(1)
        connection.close.assert_called_once()

    def test_refuses_invalid_or_stopped_records(self):
        records = [
            {"pid": True, "port": 3000, "cwd": str(Path.cwd().resolve())},
            {"pid": os.getpid(), "port": 3000, "cwd": str(Path.cwd().resolve())},
            {"pid": 1, "port": 3000, "cwd": "\x00"},
            {"pid": 1, "port": 3000, "cwd": str(Path.cwd().resolve()), "status": "stopped"},
        ]
        for record in records:
            with self.subTest(record=record):
                self.assertFalse(runtime._owned_process(record))

    @staticmethod
    def _listening(port):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return True
        except OSError:
            return False


if __name__ == "__main__":
    unittest.main()
