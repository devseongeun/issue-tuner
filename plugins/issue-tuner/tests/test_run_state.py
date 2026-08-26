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

    def test_reports_every_stage_as_pending_before_any_status_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)

            items = run_state.checklist("demo-run", home)
            self.assertEqual(
                [item["stage"] for item in items],
                list(run_state.CHECKLIST_STAGES),
            )
            self.assertEqual(
                [item["label"] for item in items],
                ["입력 정리", "재현", "진단", "구현", "검증", "게시 승인"],
            )
            self.assertEqual({item["status"] for item in items}, {"pending"})
            self.assertEqual({item["status_label"] for item in items}, {"대기"})
            self.assertEqual(
                sorted(items[0]),
                ["label", "stage", "status", "status_label"],
            )
            self.assertEqual(
                json.loads((home / "runs" / "demo-run" / "state.json").read_text())["stages"],
                {},
            )

            rendered = run_state.render_checklist("demo-run", home)
            lines = rendered.split("\n")
            self.assertEqual(lines[0], "## 진행 체크리스트")
            self.assertEqual(len(lines), 7)
            self.assertEqual(lines[1], "- [ ] 입력 정리 — 대기")
            self.assertFalse(rendered.endswith("\n"))

    def test_distinguishes_all_six_statuses_with_korean_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            expected = {
                "issue-report": "done",
                "reproduction": "in_progress",
                "diagnosis": "pending",
                "implementation": "skipped",
                "verification": "failed",
                "publication-approval": "blocked",
            }
            for stage, status in expected.items():
                run_state.set_stage_status("demo-run", stage, status, home)

            items = {item["stage"]: item for item in run_state.checklist("demo-run", home)}
            self.assertEqual({stage: items[stage]["status"] for stage in expected}, expected)
            self.assertEqual(
                {items[stage]["status_label"] for stage in expected},
                {"완료", "진행 중", "대기", "생략", "실패", "차단됨"},
            )
            for stage, status in expected.items():
                self.assertEqual(
                    items[stage]["status_label"],
                    run_state.CHECKLIST_STATUSES[status],
                )

            rendered = run_state.render_checklist("demo-run", home)
            self.assertIn("- [x] 입력 정리 — 완료", rendered)
            self.assertIn("- [~] 재현 — 진행 중", rendered)
            self.assertIn("- [ ] 진단 — 대기", rendered)
            self.assertIn("- [!] 검증 — 실패", rendered)
            self.assertIn("- [-] 게시 승인 — 차단됨", rendered)

    def test_rejects_unknown_stage_status_without_writing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            state_path = home / "runs" / "demo-run" / "state.json"
            snapshot = state_path.read_text()

            for status in ("완료", "done ", "DONE", "", "unknown", None, 3):
                with self.assertRaises(ValueError):
                    run_state.set_stage_status("demo-run", "diagnosis", status, home)
                self.assertEqual(state_path.read_text(), snapshot)

            self.assertEqual(json.loads(snapshot)["stages"], {})

    def test_marks_skipped_implementation_stage_as_saengryak(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            run_state.set_stage_status("demo-run", "implementation", "skipped", home)

            items = {item["stage"]: item for item in run_state.checklist("demo-run", home)}
            self.assertEqual(items["implementation"]["status"], "skipped")
            self.assertEqual(items["implementation"]["status_label"], "생략")
            self.assertEqual(items["implementation"]["label"], "구현")

            rendered = run_state.render_checklist("demo-run", home)
            self.assertIn("- [/] 구현 — 생략", rendered.split("\n"))
            self.assertEqual(
                json.loads((home / "runs" / "demo-run" / "state.json").read_text())["stages"][
                    "implementation"
                ]["status"],
                "skipped",
            )

    def test_restores_recorded_statuses_from_disk_in_a_freshly_loaded_module(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            run_state.set_stage_status("demo-run", "issue-report", "done", home)
            run_state.set_stage_status("demo-run", "reproduction", "done", home)
            run_state.set_stage_status("demo-run", "diagnosis", "in_progress", home)

            spec = importlib.util.spec_from_file_location("run_state_reloaded", SCRIPT)
            reloaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reloaded)
            self.assertIsNot(reloaded, run_state)

            items = {item["stage"]: item for item in reloaded.checklist("demo-run", home)}
            self.assertEqual(items["issue-report"]["status"], "done")
            self.assertEqual(items["reproduction"]["status"], "done")
            self.assertEqual(items["diagnosis"]["status"], "in_progress")
            self.assertEqual(items["verification"]["status"], "pending")
            self.assertEqual(items["diagnosis"]["status_label"], "진행 중")
            self.assertEqual(
                reloaded.render_checklist("demo-run", home),
                run_state.render_checklist("demo-run", home),
            )

    def test_renders_checklist_without_writing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            run_state.set_stage_status("demo-run", "verification", "done", home)
            state_path = home / "runs" / "demo-run" / "state.json"
            snapshot = state_path.read_text()

            with patch.object(
                run_state, "_write_json", side_effect=AssertionError("must not write")
            ):
                items = run_state.checklist("demo-run", home)
                rendered = run_state.render_checklist("demo-run", home)

            self.assertEqual(len(items), len(run_state.CHECKLIST_STAGES))
            self.assertIn("- [x] 검증 — 완료", rendered.split("\n"))
            self.assertEqual(state_path.read_text(), snapshot)
            self.assertFalse((home / "runs" / "demo-run" / "metrics.json").exists())

    def test_appends_unknown_stages_after_known_ones_in_alphabetical_order(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            run_state.record_attempt("demo-run", "zeta-stage", home)
            run_state.record_attempt("demo-run", "alpha-stage", home)
            run_state.set_stage_status("demo-run", "zeta-stage", "done", home)

            items = run_state.checklist("demo-run", home)
            self.assertEqual(
                [item["stage"] for item in items],
                list(run_state.CHECKLIST_STAGES) + ["alpha-stage", "zeta-stage"],
            )
            unknown = {item["stage"]: item for item in items[len(run_state.CHECKLIST_STAGES):]}
            self.assertEqual(unknown["alpha-stage"]["label"], "alpha-stage")
            self.assertEqual(unknown["alpha-stage"]["status"], "pending")
            self.assertEqual(unknown["zeta-stage"]["status_label"], "완료")

            lines = run_state.render_checklist("demo-run", home).split("\n")
            self.assertEqual(lines[-1], "- [x] zeta-stage — 완료")
            self.assertEqual(lines[-2], "- [ ] alpha-stage — 대기")

    def test_rejects_stage_status_on_finished_run_without_changing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            run_state.set_stage_status("demo-run", "diagnosis", "done", home)
            run_state.finish("demo-run", 150, home)
            state_path = home / "runs" / "demo-run" / "state.json"
            snapshot = state_path.read_text()

            with self.assertRaises(ValueError):
                run_state.set_stage_status("demo-run", "verification", "done", home)
            self.assertEqual(state_path.read_text(), snapshot)

            items = {item["stage"]: item for item in run_state.checklist("demo-run", home)}
            self.assertEqual(items["diagnosis"]["status"], "done")
            self.assertEqual(items["verification"]["status"], "pending")

    def test_carries_stage_status_into_metrics_when_run_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run_state.create("demo-run", 100, home)
            run_state.record_attempt("demo-run", "verification", home)
            run_state.set_outcome("demo-run", "verification", "pass", home)
            run_state.set_stage_status("demo-run", "verification", "done", home)
            run_state.set_stage_status("demo-run", "implementation", "skipped", home)
            run_state.finish("demo-run", 150, home)

            metrics = json.loads((home / "runs" / "demo-run" / "metrics.json").read_text())
            self.assertEqual(
                metrics["stages"],
                {
                    "verification": {"attempts": 1, "outcome": "pass", "status": "done"},
                    "implementation": {"attempts": 0, "status": "skipped"},
                },
            )


if __name__ == "__main__":
    unittest.main()
