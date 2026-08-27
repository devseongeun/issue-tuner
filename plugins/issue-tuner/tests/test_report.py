import datetime
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "report.py"
SPEC = importlib.util.spec_from_file_location("report", SCRIPT)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


HEADINGS = (
    "# 최종 해결 보고서",
    "## 증상",
    "## 재현",
    "## 근본 원인",
    "## 시간",
)

# 합성 fixture 전용 문자열. 어느 것도 실제 티켓·경로·인증정보가 아니다.
SYMPTOM = "결제 화면이 흰 화면으로 남는다"
EXPECTED = "결제 화면에 주문 요약이 보인다"
STEP = "합성 계정으로 결제 단계를 연다"
SCENARIO = "합성 데이터로 흰 화면을 그대로 재현했다"
LIMITATION = "야간 배치 경로는 재현 범위 밖이다"
ROOT_CAUSE = "주문 요약 응답의 빈 값을 화면이 그대로 그린다"
OTHER_ROOT_CAUSE = "주문 요약 캐시가 만료 후 갱신되지 않는다"
EVIDENCE_ONE = "응답 본문 조각에 남은 원본 로그 문장 하나"
EVIDENCE_TWO = "호출부 스택에 남은 원본 로그 문장 둘"
SYMBOL = "checkout.summary.render"

# 보고서에 그대로 새면 안 되는 값들. 안전 게이트에 걸리지 않도록 문자열을 쪼개 조합한다.
LEAKED_VALUE = "sy" + "nthetic-demo-value"
HEADER_LEAK = "Authorization" + ": Bearer " + LEAKED_VALUE
FIELD_LEAK = '"api' + '_token": "' + LEAKED_VALUE + '"'
HOME_LEAK = "/Users" + "/demo-person/workspace/notes.txt"
TARGET = "https://staging.demo.invalid/entry-point"
REPOSITORY_PATH = "/opt/synthetic/workspace-alpha"

STARTED_AT = 1_700_000_000
RESOLVED_AT = 1_700_004_211
FINISHED_AT = 1_700_007_321
WORK_SECONDS = 3001
WAIT_SECONDS = 1210
ELAPSED_SECONDS = FINISHED_AT - STARTED_AT
CLEANUP_SECONDS = FINISHED_AT - RESOLVED_AT


def utc_stamp(value):
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def section(text, heading):
    # 다음 절 heading 직전까지를 그 절의 본문으로 본다.
    lines = text.splitlines()
    start = lines.index(heading)
    body = []
    for line in lines[start + 1 :]:
        if re.match(r"#{1,2} ", line):
            break
        body.append(line)
    return "\n".join(body)


def finished_state(**overrides):
    state = {
        "run_id": "demo-run",
        "status": "finished",
        "started_at": STARTED_AT,
        "resolved_at": RESOLVED_AT,
        "finished_at": FINISHED_AT,
        "resolution_source": "automated",
        "work_seconds": WORK_SECONDS,
        "wait_seconds": WAIT_SECONDS,
        "elapsed_seconds": ELAPSED_SECONDS,
        "active_started_at": None,
        "active_seconds": WORK_SECONDS,
        "stages": {},
        "updated_at": FINISHED_AT,
    }
    state.update(overrides)
    return state


def finished_metrics(**overrides):
    metrics = {
        "started_at": STARTED_AT,
        "resolved_at": RESOLVED_AT,
        "finished_at": FINISHED_AT,
        "resolution_source": "automated",
        "work_seconds": WORK_SECONDS,
        "wait_seconds": WAIT_SECONDS,
        "cleanup_seconds": CLEANUP_SECONDS,
        "elapsed_seconds": ELAPSED_SECONDS,
        "active_seconds": WORK_SECONDS,
        "stages": {},
    }
    metrics.update(overrides)
    return metrics


def issue_report(**overrides):
    payload = {
        "issue": {"id": "DEMO-77", "expected": EXPECTED, "actual": SYMPTOM, "steps": [STEP]},
        "environment": {"name": "staging", "target": TARGET},
        "repositories": [{"name": "checkout-web", "path": REPOSITORY_PATH, "branch": "main"}],
        "verification": {"channels": ["browser"]},
    }
    payload.update(overrides)
    return payload


def reproduction(**overrides):
    payload = {
        "status": "reproduced",
        "source": "automated",
        "scenario": SCENARIO,
        "limitations": [LIMITATION],
        "blockers": [],
    }
    payload.update(overrides)
    return payload


def diagnosis(**overrides):
    payload = {
        "status": "diagnosed",
        "root_cause": ROOT_CAUSE,
        "evidence": [EVIDENCE_ONE, EVIDENCE_TWO],
        "symbols": [SYMBOL],
        "blockers": [],
    }
    payload.update(overrides)
    return payload


class ReportTest(unittest.TestCase):
    def dump(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    def build(self, home, documents=None, run_id="demo-run"):
        run = Path(home) / "runs" / run_id
        run.mkdir(parents=True)
        for name, payload in (documents or {}).items():
            self.dump(run / f"{name}.json", payload)
        return run

    def complete(self, home, documents=None):
        base = {
            "state": finished_state(),
            "metrics": finished_metrics(),
            "issue-report": issue_report(),
            "reproduction": reproduction(),
            "diagnosis": diagnosis(),
        }
        base.update(documents or {})
        return self.build(home, base)

    def test_quotes_every_stage_document_as_the_only_source_and_follows_later_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run = self.complete(home)

            text = report.final_report("demo-run", home)

            for quoted in (SYMPTOM, EXPECTED, STEP, SCENARIO, LIMITATION, ROOT_CAUSE, SYMBOL):
                with self.subTest(quoted=quoted):
                    self.assertIn(quoted, text)

            self.dump(run / "diagnosis.json", diagnosis(root_cause=OTHER_ROOT_CAUSE))
            rewritten = report.final_report("demo-run", home)

            self.assertNotEqual(rewritten, text)
            self.assertIn(OTHER_ROOT_CAUSE, rewritten)
            self.assertNotIn(ROOT_CAUSE, rewritten)

    def test_places_every_required_heading_once_in_the_specified_order(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.complete(home)

            text = report.final_report("demo-run", home)
            lines = text.splitlines()

            for heading in HEADINGS:
                with self.subTest(heading=heading):
                    self.assertIn(heading, text)
                    self.assertEqual(lines.count(heading), 1)
            positions = [text.index(heading) for heading in HEADINGS]
            self.assertEqual(positions, sorted(positions))
            self.assertEqual(lines[0], HEADINGS[0])
            self.assertFalse(text.endswith("\n"))
            # PR A 범위는 run 전체 수준 네 개 절뿐이다.
            self.assertEqual(len([line for line in lines if line.startswith("## ")]), 4)

    def test_reports_utc_timestamps_and_second_counts_and_marks_missing_times_unrecorded(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.complete(home)

            body = section(report.final_report("demo-run", home), "## 시간")

            for value in (STARTED_AT, RESOLVED_AT, FINISHED_AT):
                with self.subTest(value=value):
                    self.assertIn(utc_stamp(value), body)
            for line in body.splitlines():
                if utc_stamp(STARTED_AT) in line or utc_stamp(RESOLVED_AT) in line or utc_stamp(FINISHED_AT) in line:
                    with self.subTest(line=line):
                        # 시각은 UTC임이 표기되어야 지역 시간과 혼동되지 않는다.
                        self.assertTrue("Z" in line or "+00:00" in line or "UTC" in line)
            for seconds in (ELAPSED_SECONDS, WORK_SECONDS, WAIT_SECONDS):
                with self.subTest(seconds=seconds):
                    self.assertIn(str(seconds), body)

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.build(home, {"state": {"run_id": "demo-run", "status": "running", "stages": {}}})

            body = section(report.final_report("demo-run", home), "## 시간")

            self.assertIn("미기록", body)
            # 기록이 없으면 0초나 임의 시각을 지어내지 않는다.
            self.assertNotRegex(body, r"\d")

    def test_masks_credentials_home_paths_targets_and_raw_diagnosis_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.complete(
                home,
                documents={
                    "issue-report": issue_report(
                        issue={
                            "id": "DEMO-77",
                            "expected": EXPECTED,
                            "actual": f"{SYMPTOM} ({HOME_LEAK})",
                            "steps": [STEP],
                        }
                    ),
                    "reproduction": reproduction(scenario=f"{SCENARIO} — {HEADER_LEAK}"),
                    "diagnosis": diagnosis(symbols=[f"{SYMBOL} {FIELD_LEAK}"]),
                },
            )

            text = report.final_report("demo-run", home)

            for leak in (HEADER_LEAK, FIELD_LEAK, HOME_LEAK, LEAKED_VALUE, "/Users" + "/demo-person"):
                with self.subTest(leak=leak):
                    self.assertNotIn(leak, text)
            # 민감한 조각만 가리고 사람이 읽을 문장 자체는 남아야 한다.
            self.assertIn(SYMPTOM, text)
            self.assertIn(SCENARIO, section(text, "## 재현"))
            self.assertIn(SYMBOL, section(text, "## 근본 원인"))

            for hidden in (TARGET, REPOSITORY_PATH, EVIDENCE_ONE, EVIDENCE_TWO):
                with self.subTest(hidden=hidden):
                    self.assertNotIn(hidden, text)
            # 근거는 내용 대신 건수만 남긴다.
            self.assertRegex(section(text, "## 근본 원인"), r"2\s*건")

    def test_builds_a_report_for_a_run_without_any_stage_file_without_inventing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run = self.build(home)

            text = report.final_report("demo-run", home)

            for heading in HEADINGS:
                with self.subTest(heading=heading):
                    self.assertIn(heading, text)
            for heading in ("## 증상", "## 재현", "## 근본 원인", "## 시간"):
                with self.subTest(heading=heading):
                    self.assertIn("미기록", section(text, heading))
            self.assertNotRegex(section(text, "## 시간"), r"\d")
            self.assertEqual(list(run.iterdir()), [])

    def test_rejects_run_ids_that_escape_the_runs_root_and_relative_homes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.complete(home)

            for run_id in ("../escape", "..", "", "demo/run", "./demo-run", "demo-run/../../escape"):
                with self.subTest(run_id=run_id):
                    with self.assertRaises(ValueError):
                        report.final_report(run_id, home)
                    with self.assertRaises(ValueError):
                        report.write_final_report(run_id, home)

            for relative in (Path("relative-home"), Path("."), "relative-home"):
                with self.subTest(home=relative):
                    with self.assertRaises(ValueError):
                        report.final_report("demo-run", relative)
                    with self.assertRaises(ValueError):
                        report.write_final_report("demo-run", relative)

    def test_write_final_report_stores_the_same_text_and_stays_safe_when_called_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run = self.complete(home)
            before = sorted(entry.name for entry in run.iterdir())

            path = report.write_final_report("demo-run", home)

            self.assertEqual(Path(path).resolve(), (run / "final-report.md").resolve())
            self.assertTrue(Path(path).is_file())
            self.assertEqual(Path(path).read_text(encoding="utf-8"), report.final_report("demo-run", home))

            again = report.write_final_report("demo-run", home)

            self.assertEqual(Path(again).resolve(), Path(path).resolve())
            self.assertEqual(Path(again).read_text(encoding="utf-8"), report.final_report("demo-run", home))
            # 원자적 교체라면 임시 파일이 남지 않는다.
            self.assertEqual(sorted(entry.name for entry in run.iterdir()), sorted(before + ["final-report.md"]))

    def test_final_report_leaves_the_run_directory_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run = self.complete(home)
            before = sorted(entry.name for entry in run.iterdir())

            report.final_report("demo-run", home)

            self.assertFalse((run / "final-report.md").exists())
            self.assertEqual(sorted(entry.name for entry in run.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
