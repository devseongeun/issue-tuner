import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_state.py"
SPEC = importlib.util.spec_from_file_location("run_state", SCRIPT)
run_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_state)


class RunStateTest(unittest.TestCase):
    def test_records_first_resolution_and_calculates_work_and_wait_time(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            run_state.pause("demo-run", 110, home)
            run_state.resume("demo-run", 130, home)

            state = run_state.resolve("demo-run", 150, "automated", home)
            self.assertEqual(state["resolved_at"], 150)
            self.assertEqual(state["resolution_source"], "automated")
            self.assertEqual(state["work_seconds"], 30)
            self.assertEqual(state["wait_seconds"], 20)

            state = run_state.resolve("demo-run", 160, "user_confirmed", home)
            self.assertEqual(state["resolved_at"], 150)
            self.assertEqual(state["resolution_source"], "automated")

            state = run_state.finish("demo-run", 170, home)
            self.assertEqual(state["finished_at"], 170)
            self.assertEqual(state["elapsed_seconds"], 70)
            metrics = json.loads((home / "runs" / "demo-run" / "metrics.json").read_text())
            self.assertEqual(metrics["started_at"], 100)
            self.assertEqual(metrics["resolved_at"], 150)
            self.assertEqual(metrics["finished_at"], 170)
            self.assertEqual(metrics["resolution_source"], "automated")
            self.assertEqual(metrics["work_seconds"], 30)
            self.assertEqual(metrics["wait_seconds"], 20)
            self.assertEqual(metrics["cleanup_seconds"], 20)
            self.assertEqual(
                metrics["work_seconds"] + metrics["wait_seconds"] + metrics["cleanup_seconds"],
                metrics["elapsed_seconds"],
            )

    def test_accepts_user_confirmed_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)

            state = run_state.resolve("demo-run", 110, "user_confirmed", home)

            self.assertEqual(state["resolution_source"], "user_confirmed")

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


if __name__ == "__main__":
    unittest.main()
