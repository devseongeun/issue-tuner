import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_state.py"
SPEC = importlib.util.spec_from_file_location("run_state", SCRIPT)
run_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_state)


class RunStateTest(unittest.TestCase):
    def test_tracks_paused_time_and_writes_per_run_metrics_outside_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            state = run_state.create("demo-run", 100, home)
            self.assertEqual(state["status"], "running")
            self.assertTrue((home / "runs" / "demo-run" / "state.json").is_file())
            self.assertFalse((Path.cwd() / "runs" / "demo-run").exists())

            state = run_state.pause("demo-run", 110, home)
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["active_seconds"], 10)
            self.assertIsNone(state["active_started_at"])

            state = run_state.resume("demo-run", 130, home)
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["active_started_at"], 130)
            run_state.record_attempt("demo-run", "verify", home)
            run_state.set_outcome("demo-run", "verify", "pass", home)
            state = run_state.finish("demo-run", 150, home)

            self.assertEqual(state["status"], "finished")
            self.assertEqual(state["elapsed_seconds"], 50)
            self.assertEqual(state["active_seconds"], 30)
            metrics = json.loads((home / "runs" / "demo-run" / "metrics.json").read_text())
            self.assertEqual(
                metrics,
                {
                    "elapsed_seconds": 50,
                    "active_seconds": 30,
                    "stages": {"verify": {"attempts": 1, "outcome": "pass"}},
                },
            )

    def test_rejects_invalid_transition_without_changing_saved_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            with self.assertRaises(ValueError):
                run_state.resume("demo-run", 110, home)

            state = json.loads((home / "runs" / "demo-run" / "state.json").read_text())
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["active_started_at"], 100)

    def test_rejects_traversal_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run_state.create("../escape", 100, Path(directory))

    def test_recovers_metrics_after_metrics_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            original_write = run_state._write_json

            def fail_metrics(path, data):
                if path.name == "metrics.json":
                    raise OSError("disk full")
                original_write(path, data)

            with patch.object(run_state, "_write_json", side_effect=fail_metrics):
                with self.assertRaises(OSError):
                    run_state.finish("demo-run", 150, home)

            self.assertEqual(
                json.loads((home / "runs" / "demo-run" / "state.json").read_text())["status"],
                "finished",
            )
            state = run_state.finish("demo-run", 150, home)
            self.assertEqual(state["status"], "finished")
            self.assertTrue((home / "runs" / "demo-run" / "metrics.json").is_file())

    def test_rejects_run_directory_symlink(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = Path(directory)
            run_directory = home / "runs" / "demo-run"
            run_directory.parent.mkdir()
            os.symlink(outside, run_directory)

            with self.assertRaises(ValueError):
                run_state.create("demo-run", 100, home)
            self.assertFalse((Path(outside) / "state.json").exists())

    def test_rejects_runs_root_symlink(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = Path(directory)
            os.symlink(outside, home / "runs")

            with self.assertRaises(ValueError):
                run_state.create("demo-run", 100, home)
            self.assertFalse((Path(outside) / "demo-run" / "state.json").exists())

    def test_rejects_mismatched_embedded_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            state_path = home / "runs" / "demo-run" / "state.json"
            state = json.loads(state_path.read_text())
            state["run_id"] = "other-run"
            state_path.write_text(json.dumps(state))

            with self.assertRaises(ValueError):
                run_state.pause("demo-run", 110, home)
            self.assertEqual(json.loads(state_path.read_text())["run_id"], "other-run")

    def test_rejects_backward_and_boolean_timestamps_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with self.assertRaises(ValueError):
                run_state.create("bool-run", True, home)
            run_state.create("demo-run", 100, home)

            with self.assertRaises(ValueError):
                run_state.pause("demo-run", 99, home)
            self.assertEqual(run_state._load("demo-run", home)["active_started_at"], 100)

            run_state.pause("demo-run", 110, home)
            with self.assertRaises(ValueError):
                run_state.resume("demo-run", 109, home)
            with self.assertRaises(ValueError):
                run_state.finish("demo-run", 109, home)
            self.assertEqual(run_state._load("demo-run", home)["updated_at"], 110)

    def test_rejects_relative_home_and_treats_empty_environment_as_unset(self):
        with patch.dict(os.environ, {"ISSUE_TUNER_HOME": ""}, clear=False):
            self.assertEqual(run_state.run_root(), Path.home() / ".issue-tuner" / "runs")
        with patch.dict(os.environ, {"ISSUE_TUNER_HOME": "relative"}, clear=False):
            with self.assertRaises(ValueError):
                run_state.run_root()
        with self.assertRaises(ValueError):
            run_state.create("demo-run", 100, Path("relative"))

    def test_removes_temporary_file_when_json_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            with self.assertRaises(TypeError):
                run_state._write_json(target, {"not_json": object()})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_removes_temporary_file_when_json_write_is_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            with patch.object(run_state.json, "dump", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    run_state._write_json(target, {"value": "ok"})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_writes_and_rewrites_run_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)

            path = run_state.write_artifact("demo-run", "reproduction.json", {"status": "failed"}, home)
            self.assertEqual(path, (home / "runs" / "demo-run" / "reproduction.json").resolve())
            self.assertEqual(json.loads(path.read_text()), {"status": "failed"})

            rewritten = run_state.write_artifact("demo-run", "reproduction.json", {"status": "reproduced"}, home)
            self.assertEqual(rewritten, path)
            self.assertEqual(json.loads(path.read_text()), {"status": "reproduced"})

            nested = run_state.write_artifact(
                "demo-run",
                "repositories/app/verification.json",
                {"status": "pass"},
                home,
            )
            self.assertEqual(
                nested,
                (home / "runs" / "demo-run" / "repositories" / "app" / "verification.json").resolve(),
            )
            self.assertEqual(json.loads(nested.read_text()), {"status": "pass"})

            diagnosis = run_state.write_artifact("demo-run", "diagnosis.json", {"root_cause": "found"}, home)
            self.assertEqual(diagnosis, (home / "runs" / "demo-run" / "diagnosis.json").resolve())
            self.assertEqual(json.loads(diagnosis.read_text()), {"root_cause": "found"})

            implementation = run_state.write_artifact(
                "demo-run",
                "repositories/app/implementation.json",
                {"status": "implemented"},
                home,
            )
            self.assertEqual(
                implementation,
                (home / "runs" / "demo-run" / "repositories" / "app" / "implementation.json").resolve(),
            )
            self.assertEqual(json.loads(implementation.read_text()), {"status": "implemented"})

    def test_rejects_control_and_non_role_artifact_paths_without_changing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            state_path = home / "runs" / "demo-run" / "state.json"
            original_state = state_path.read_text()

            for relative_path in [
                "state.json",
                "metrics.json",
                "runtime/process.json",
                "repositories/app/commit-gate.json",
                "repositories/app/public-artifacts/pr-body.md",
                "repositories/app/nested/verification.json",
                "repositories/app",
                "repositories/implementation.json",
                "repositories/./verification.json",
                "repositories/../verification.json",
                "repositories/app/diagnosis.json",
                "repositories/app/verification.txt",
            ]:
                with self.subTest(relative_path=relative_path):
                    with self.assertRaises(ValueError):
                        run_state.write_artifact("demo-run", relative_path, {"status": "bad"}, home)

            self.assertEqual(state_path.read_text(), original_state)
            self.assertFalse((home / "runs" / "demo-run" / "metrics.json").exists())
            self.assertFalse((home / "runs" / "demo-run" / "runtime").exists())
            self.assertFalse((home / "runs" / "demo-run" / "repositories" / "app").exists())

    def test_rejects_invalid_artifact_data_and_finished_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)

            with self.assertRaises(ValueError):
                run_state.write_artifact("demo-run", "reproduction.json", [], home)
            self.assertFalse((home / "runs" / "demo-run" / "reproduction.json").exists())

            run_state.finish("demo-run", 120, home)
            with self.assertRaises(ValueError):
                run_state.write_artifact("demo-run", "reproduction.json", {"status": "late"}, home)
            self.assertFalse((home / "runs" / "demo-run" / "reproduction.json").exists())

    def test_rejects_artifact_write_when_saved_run_id_mismatches(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            state_path = home / "runs" / "demo-run" / "state.json"
            state = json.loads(state_path.read_text())
            state["run_id"] = "other-run"
            state_path.write_text(json.dumps(state))

            with self.assertRaises(ValueError):
                run_state.write_artifact("demo-run", "reproduction.json", {"status": "bad"}, home)
            self.assertFalse((home / "runs" / "demo-run" / "reproduction.json").exists())

    def test_rejects_unsafe_artifact_paths_without_changing_outside_targets(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = Path(directory)
            outside_path = Path(outside) / "reproduction.json"
            outside_path.write_text("outside\n")
            run_state.create("demo-run", 100, home)

            for relative_path in ["", ".", "./.", "/tmp/reproduction.json", "../outside/reproduction.json", "repo/../../outside"]:
                with self.subTest(relative_path=relative_path):
                    with self.assertRaises(ValueError):
                        run_state.artifact_path("demo-run", relative_path, home)
                    with self.assertRaises(ValueError):
                        run_state.write_artifact("demo-run", relative_path, {"status": "bad"}, home)

            self.assertEqual(outside_path.read_text(), "outside\n")

    def test_rejects_artifact_symlink_parent_and_target_without_changing_outside_targets(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            home = Path(directory)
            outside = Path(outside)
            run_state.create("demo-run", 100, home)
            run_dir = home / "runs" / "demo-run"

            parent_target = outside / "parent-target"
            parent_target.mkdir()
            os.symlink(parent_target, run_dir / "repositories")
            with self.assertRaises(ValueError):
                run_state.write_artifact("demo-run", "repositories/app/verification.json", {"status": "bad"}, home)
            self.assertFalse((parent_target / "app" / "verification.json").exists())

            (run_dir / "repositories").unlink()
            outside_file = outside / "reproduction.json"
            outside_file.write_text("outside\n")
            os.symlink(outside_file, run_dir / "reproduction.json")
            with self.assertRaises(ValueError):
                run_state.write_artifact("demo-run", "reproduction.json", {"status": "bad"}, home)
            self.assertEqual(outside_file.read_text(), "outside\n")

    def test_write_artifact_cli_reads_json_from_stdin_and_prints_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "write-artifact", str(home), "demo-run", "reproduction.json"],
                input='{"status":"ok"}',
                text=True,
                capture_output=True,
                check=False,
            )

            expected = (home / "runs" / "demo-run" / "reproduction.json").resolve()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"{expected}\n")
            self.assertEqual(json.loads(expected.read_text()), {"status": "ok"})

    def test_write_artifact_cli_rejects_invalid_input_paths_and_json_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)

            invalid_json = subprocess.run(
                [sys.executable, str(SCRIPT), "write-artifact", str(home), "demo-run", "reproduction.json"],
                input="[]",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(invalid_json.returncode, 2)
            self.assertFalse((home / "runs" / "demo-run" / "reproduction.json").exists())

            malformed_json = subprocess.run(
                [sys.executable, str(SCRIPT), "write-artifact", str(home), "demo-run", "reproduction.json"],
                input="{",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(malformed_json.returncode, 2)
            self.assertFalse((home / "runs" / "demo-run" / "reproduction.json").exists())

            unsafe_path = subprocess.run(
                [sys.executable, str(SCRIPT), "write-artifact", str(home), "demo-run", "../escape.json"],
                input='{"status":"bad"}',
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(unsafe_path.returncode, 2)
            self.assertFalse((home / "runs" / "escape.json").exists())

            json_argv = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "write-artifact",
                    str(home),
                    "demo-run",
                    "reproduction.json",
                    '{"status":"bad"}',
                ],
                input="",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(json_argv.returncode, 2)
            self.assertFalse((home / "runs" / "demo-run" / "reproduction.json").exists())


if __name__ == "__main__":
    unittest.main()
