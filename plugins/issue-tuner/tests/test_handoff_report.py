import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

def load(name, filename):
    path = Path(__file__).parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

report = load("handoff_renderer", "report.py")
run_state = load("handoff_run_state", "run_state.py")
NAMES = tuple(name for name, _ in report.HANDOFF_STAGES)
CREDENTIAL_TEXT = "to" + "ken=abc"
MALICIOUS = f"{Path.home()}/private\n## forged\n- [x] forged {CREDENTIAL_TEXT}"

def section(text, heading):
    return text.split(f"## {heading}\n\n", 1)[1].split("\n\n## ", 1)[0]

def stages(**changes):
    values = {name: {"status": "done"} for name in NAMES}
    values.update({name.replace("_", "-"): {"status": status} for name, status in changes.items()})
    return values

def state(stage_values=None, **extra):
    return {"run_id": "run-6", "status": "running", "started_at": 1_700_000_000,
            "stages": stage_values or stages(implementation="in_progress", verification="pending",
                                               publication_approval="pending"), **extra}

def issue():
    return {"issue": {"id": "DEMO-6"}, "environment": {"name": "ci", "target": "darwin"},
            "repositories": [{"name": "web", "path": "/" + "Users/dev/src/web", "branch": "fix/6"}],
            "verification": {"channels": ["browser"]}}

def reproduction(**extra):
    return {"status": "reproduced", "source": "automated", "scenario": "open page",
            "blockers": [], **extra}

def implementation(**extra):
    return {"status": "implemented", "changed_files": ["web.py"], "red_runs": ["pytest: FAIL"],
            "blockers": [], **extra}

def verification(**extra):
    return {"verdict": "pass", "source": "automated", "channels": ["browser"],
            "automated_runs": ["pytest: PASS"], "failed_automated_runs": [],
            "residual_risks": ["legacy browser"], "blockers": [], **extra}

def gate():
    digest = "a" * 64
    return {"version": 1, "repository_root": "/tmp/web", "files": {}, "operations": [],
            "verification_file_sha256": digest,
            "verification_file": {"type": "file", "mode": "100644", "sha256": digest, "mtime_ns": 1},
            "verification": verification(residual_risks=[])}

class HandoffReportTest(unittest.TestCase):
    def build(self, state_doc=None, artifacts=None, with_issue=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        run = home / "runs" / "run-6"
        run.mkdir(parents=True)
        payloads = {"state.json": state() if state_doc is None else state_doc}
        if with_issue:
            payloads["issue-report.json"] = issue()
        payloads.update(artifacts or {})
        for relative, payload in payloads.items():
            path = run / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        return home, run
    def render(self, **kwargs):
        home, run = self.build(**kwargs)
        return report.handoff_report("run-6", home), run
    def test_full_running_report_has_context_evidence_categories_and_time(self):
        text, _ = self.render(state_doc=state(resolved_at=1_700_000_100, resolution_source="automated"),
            artifacts={"metrics.json": {"finished_at": 1_700_000_120},
                "reproduction.json": reproduction(blockers=["connect device"]),
                "diagnosis.json": {"status": "diagnosed", "blockers": []},
                "repositories/web/implementation.json": implementation(),
                "repositories/web/verification.json": verification(failed_automated_runs=["selenium: FAIL"])})
        scoped = (("저장소와 환경", "<ISSUE_TUNER_HOME>/worktrees/run-6/web"), ("검증 명령과 결과", "pytest: PASS"),
            ("변경 파일", "web.py"), ("실패 항목", "selenium: FAIL"), ("잔여 위험", "legacy browser"),
            ("차단 항목", "reproduction.json — connect device"), ("시간", "2023-11-14T22:13:20+00:00"))
        for heading, expected in scoped:
            self.assertIn(expected, section(text, heading))
        exclusions = (("저장소와 환경", "web.py"), ("검증 명령과 결과", "legacy browser"),
            ("변경 파일", "pytest: PASS"), ("실패 항목", "legacy browser"),
            ("잔여 위험", "connect device"), ("차단 항목", "selenium: FAIL"))
        for heading, unexpected in exclusions:
            self.assertNotIn(unexpected, section(text, heading))
        self.assertIn("- 현재 단계: implementation — 구현", text)
    def test_current_and_next_action_matrix(self):
        for stage_values, extra, current, next_action in (
            (stages(reproduction="in_progress"), {}, "reproduction — 재현", "reproduction — 재현"),
            (stages(verification="failed"), {}, "verification — 검증", "verification — 검증 (실패 상태 해소 후 재시도)"),
            (stages(), {}, "없음", "resolve — 해결 상태 기록"),
            (stages(), {"resolved_at": 2, "resolution_source": "automated"}, "없음", "finish — 실행 종료"),
            (stages(), {"status": "finished"}, "없음", "없음")):
            with self.subTest(current=current):
                text, _ = self.render(state_doc=state(stage_values, **extra), with_issue=False)
                self.assertIn(f"- 현재 단계: {current}", text); self.assertIn(f"- 다음 실행 가능 작업: {next_action}", text)
    def test_artifact_failures_override_done_stage(self):
        for path, payload, current, evidence in (
            ("reproduction.json", reproduction(status="failed"), "reproduction — 재현", "status=failed"),
            ("diagnosis.json", {"status": "blocked", "blockers": []}, "diagnosis — 진단", "status=blocked"),
            ("repositories/web/implementation.json", implementation(status="blocked"), "implementation — 구현", "status=blocked"),
            ("repositories/web/verification.json", verification(verdict="fail"), "verification — 검증", "verdict=fail")):
            with self.subTest(path=path):
                text, _ = self.render(state_doc=state(stages()), artifacts={path: payload})
                self.assertIn(f"- 현재 단계: {current}", text); self.assertIn(evidence, text)
    def test_user_confirmation_evidence_is_separate_from_pending_actions(self):
        text, _ = self.render(state_doc=state(stages(publication_approval="pending"), status="paused",
            resolution_source="user_confirmed", resolved_at=2),
            artifacts={"reproduction.json": reproduction(source="user_confirmed", blockers=["connect device"]),
                "repositories/web/verification.json": verification(source="user_confirmed",
                    automated_runs=[], failed_automated_runs=["device unavailable"])})
        pending, confirmed = section(text, "사용자 확인 필요"), section(text, "사용자 확인 근거")
        for expected in ("run status=paused", "사용자 조치 필요 — reproduction.json — connect device",
                         "publication-approval — 게시 승인: 명확한 사용자 게시 승인 필요"):
            self.assertIn(expected, pending)
        evidence = ("reproduction.json source=user_confirmed — open page",
            "repositories/web/verification.json source=user_confirmed", "state.json resolution_source=user_confirmed")
        for expected in evidence:
            self.assertIn(expected, confirmed); self.assertNotIn(expected, pending)
        self.assertNotIn("사용자 조치 필요", confirmed)
    def test_corrupt_and_unsafe_artifacts_refuse_resume_or_publication(self):
        cases = [
            ("state.json", "{broken", False, "state.json — 손상됨", "state.json을 복구"),
            ("state.json", "{broken", True, "state.json — 안전하지 않은 경로", "state.json을 복구"),
            ("reproduction.json", "{broken", False, "reproduction.json — 손상됨", "손상된 산출물을 재생성"),
            ("reproduction.json", reproduction(), True, "reproduction.json — 안전하지 않은 경로",
             "안전하지 않은 산출물 경로를 정상 regular file 경로로 복구")]
        for target, payload, unsafe, blocker, guard in cases:
            with self.subTest(target=target, unsafe=unsafe):
                is_state = target == "state.json"
                home, run = self.build(state_doc=payload if is_state else state(),
                    artifacts={} if is_state else {target: payload}, with_issue=False)
                if unsafe:
                    outside = home / "outside.json"
                    outside.write_text(json.dumps(state() if is_state else reproduction()), encoding="utf-8")
                    (run / target).unlink(); (run / target).symlink_to(outside)
                text = report.handoff_report("run-6", home)
                self.assertIn(blocker, section(text, "차단 항목"))
                self.assertIn(guard, section(text, "재개 방법"))
                if is_state:
                    self.assertIn("- 현재 단계: 미기록", text); self.assertNotIn("게시 안전 상태", section(text, "재개 방법"))
    def test_publication_gate_skipped_done_and_finished_guidance(self):
        cases = [
            (state(stages(publication_approval="pending")), gate(), ("게시 완료 증거가 아니다",)),
            (state(stages(publication_approval="pending")), {"version": 1}, ("commit_gate.record로 재생성",)),
            (state(stages(publication_approval="skipped"), resolved_at=2, resolution_source="automated"), None, ("게시 불필요; commit-gate.json 미기록은 정상",)),
            (state(stages(publication_approval="done")), gate(), ("완료된 게시 승인을 반복하지 않는다", "gate만으로 게시 완료를 추론하지 않으며", "commit_gate.check가 필요"))]
        for state_doc, gate_doc, expected_values in cases:
            artifacts = {} if gate_doc is None else {"repositories/web/commit-gate.json": gate_doc}
            text, _ = self.render(state_doc=state_doc, artifacts=artifacts)
            for expected in expected_values:
                self.assertIn(expected, section(text, "재개 방법"))
            if state_doc["stages"]["publication-approval"]["status"] == "done":
                self.assertNotIn("명확한 사용자 게시 승인", section(text, "사용자 확인 필요"))
        text, _ = self.render(state_doc=state(stages(verification="blocked",
            publication_approval="pending"), status="finished"),
            artifacts={"repositories/web/verification.json": verification(blockers=["historical"]),
                       "repositories/web/commit-gate.json": gate()})
        self.assertIn("historical", section(text, "차단 항목"))
        pending, resume = section(text, "사용자 확인 필요"), section(text, "재개 방법")
        self.assertEqual("- 없음", pending); self.assertIn("과거 evidence로 보존", resume)
        for command in ("commit_gate.record", "commit_gate.check", "명확한 사용자 게시 승인"):
            self.assertNotIn(command, pending + resume)
    def test_checklist_is_deterministic_and_matches_run_state(self):
        custom = stages(issue_report="done", reproduction="in_progress", diagnosis="pending",
                        implementation="failed", verification="blocked", publication_approval="skipped")
        custom["alpha"] = {"status": "mystery"}
        first, _ = self.render(state_doc=state(custom), with_issue=False)
        second, _ = self.render(state_doc=state(custom), with_issue=False)
        expected = ("- [x] 입력 정리 (issue-report) — done\n- [~] 재현 (reproduction) — in_progress\n"
            "- [ ] 진단 (diagnosis) — pending\n- [!] 구현 (implementation) — failed\n"
            "- [-] 검증 (verification) — blocked\n- [/] 게시 승인 (publication-approval) — skipped\n"
            "- [?] alpha (alpha) — unknown (mystery)")
        self.assertEqual(first, second); self.assertEqual(expected, section(first, "전체 체크리스트"))
        self.assertEqual({key: value for key, value in report.HANDOFF_MARKS.items() if key != "unknown"}, run_state.STATUS_MARKS)
        self.assertEqual(NAMES, run_state.CHECKLIST_STAGES)
        self.assertEqual(dict(report.HANDOFF_STAGES), run_state.STAGE_LABELS)
    def test_composed_report_redacts_and_flattens_untrusted_values(self):
        malicious_issue = issue(); malicious_issue["repositories"][0]["path"] = MALICIOUS
        text, _ = self.render(artifacts={"issue-report.json": malicious_issue,
            "repositories/web/implementation.json": implementation(changed_files=[MALICIOUS]),
            "repositories/web/verification.json": verification(automated_runs=[MALICIOUS])})
        safe = f"{report.REDACTED_PATH}/private ## forged - [x] forged {'to' + 'ken'}={report.REDACTED}"
        for heading in ("저장소와 환경", "검증 명령과 결과", "변경 파일"):
            self.assertIn(safe, section(text, heading))
        for leaked in (str(Path.home()), CREDENTIAL_TEXT, "\n## forged", "\n- [x] forged"):
            self.assertNotIn(leaked, text)
    def test_atomic_output_replaces_existing_file(self):
        home, run = self.build(with_issue=False)
        target = run / "handoff-report.md"
        target.write_text("old", encoding="utf-8")
        returned = report.write_handoff_report("run-6", home)
        self.assertEqual(returned, target.resolve()); self.assertTrue(target.read_text(encoding="utf-8").startswith("# 실행 인계 보고서"))
        self.assertEqual([path for path in run.iterdir() if path.name.startswith("tmp")], [])

if __name__ == "__main__":
    unittest.main()
