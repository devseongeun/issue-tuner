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
    "## 해결 조치",
    "## 검증 결과",
    "## 실패와 잔여 위험",
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
CHANGED_FILE = "src/checkout/summary.py"
RED_RUN = "합성 회귀 테스트가 수정 전 한 번 실패했다"
AUTOMATED_RUN = "합성 회귀 스위트 12개가 통과했다"
FAILED_RUN = "무관한 알림 테스트가 시간 초과로 실패했다"
RESIDUAL_RISK = "야간 배치 경로는 이번 실행에서 확인하지 못했다"
REPRODUCTION_BLOCKER = "합성 관리자 화면은 권한이 모자라 열지 못했다"
DIAGNOSIS_BLOCKER = "합성 검색 도구가 응답하지 않아 호출부 일부를 못 봤다"
IMPLEMENTATION_BLOCKER = "합성 의존 저장소는 이번 범위에서 손대지 못했다"
VERIFICATION_BLOCKER = "합성 브라우저 채널이 열리지 않아 남은 화면을 못 봤다"

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
    # 다음 절 heading 직전까지를 그 절의 본문으로 본다. 저장소별 소제목(###)은 본문에 포함한다.
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


def implementation(repository="checkout-web", **overrides):
    payload = {
        "status": "implemented",
        "repository": repository,
        "changed_files": [CHANGED_FILE],
        "red_runs": [RED_RUN],
        "blockers": [],
    }
    payload.update(overrides)
    return payload


def verification(verdict="pass", source="automated", **overrides):
    # 스키마의 allOf 조건: pass+automated는 automated_runs가 있어야 하고 실패 목록이 비어야 하며,
    # pass+user_confirmed는 automated_runs가 비고 실패 목록과 잔여 위험이 각각 하나 이상이어야 한다.
    automated = source == "automated"
    payload = {
        "verdict": verdict,
        "source": source,
        "channels": ["browser"],
        "automated_runs": [AUTOMATED_RUN] if automated else [],
        "failed_automated_runs": [] if automated else [FAILED_RUN],
        "residual_risks": [] if automated else [RESIDUAL_RISK],
        "blockers": [],
    }
    payload.update(overrides)
    return payload


class ReportTest(unittest.TestCase):
    def dump(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    def build(self, home, documents=None, repositories=None, run_id="demo-run"):
        run = Path(home) / "runs" / run_id
        run.mkdir(parents=True)
        for name, payload in (documents or {}).items():
            self.dump(run / f"{name}.json", payload)
        for name, stages in (repositories or {}).items():
            for stage, payload in stages.items():
                self.dump(run / "repositories" / name / f"{stage}.json", payload)
        return run

    def complete(self, home, documents=None, repositories=None):
        base = {
            "state": finished_state(),
            "metrics": finished_metrics(),
            "issue-report": issue_report(),
            "reproduction": reproduction(),
            "diagnosis": diagnosis(),
        }
        base.update(documents or {})
        if repositories is None:
            repositories = {"checkout-web": {"implementation": implementation(), "verification": verification()}}
        return self.build(home, base, repositories)

    def test_quotes_every_stage_document_as_the_only_source_and_follows_later_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            run = self.complete(home)

            text = report.final_report("demo-run", home)

            for quoted in (SYMPTOM, EXPECTED, STEP, SCENARIO, ROOT_CAUSE, CHANGED_FILE, AUTOMATED_RUN):
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

    def test_states_no_code_change_for_a_missing_and_for_an_empty_implementation(self):
        cases = {
            "missing": {"verification": verification()},
            "empty": {"implementation": implementation(changed_files=[]), "verification": verification()},
        }
        for label, stages in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                self.complete(home, repositories={"checkout-web": stages})

                text = report.final_report("demo-run", home)

                self.assertIn("코드 변경 없음", section(text, "## 해결 조치"))
                self.assertNotIn(CHANGED_FILE, text)

    def test_keeps_failed_runs_residual_risks_and_every_stage_blocker_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.complete(
                home,
                documents={
                    "reproduction": reproduction(blockers=[REPRODUCTION_BLOCKER]),
                    "diagnosis": diagnosis(blockers=[DIAGNOSIS_BLOCKER]),
                },
                repositories={
                    "checkout-web": {
                        "implementation": implementation(blockers=[IMPLEMENTATION_BLOCKER]),
                        "verification": verification(
                            failed_automated_runs=[FAILED_RUN],
                            residual_risks=[RESIDUAL_RISK],
                            blockers=[VERIFICATION_BLOCKER],
                        ),
                    }
                },
            )

            body = section(report.final_report("demo-run", home), "## 실패와 잔여 위험")

            for entry in (
                FAILED_RUN,
                RESIDUAL_RISK,
                REPRODUCTION_BLOCKER,
                DIAGNOSIS_BLOCKER,
                IMPLEMENTATION_BLOCKER,
                VERIFICATION_BLOCKER,
            ):
                with self.subTest(entry=entry):
                    self.assertIn(entry, body)

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.complete(home)

            body = section(report.final_report("demo-run", home), "## 실패와 잔여 위험")

            self.assertIn("없음", body)

    def test_separates_user_confirmed_verification_from_automated_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.complete(home)
            automated = section(report.final_report("demo-run", home), "## 검증 결과")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.complete(
                home,
                repositories={
                    "checkout-web": {
                        "implementation": implementation(),
                        "verification": verification(source="user_confirmed"),
                    }
                },
            )
            confirmed = section(report.final_report("demo-run", home), "## 검증 결과")

        self.assertNotEqual(automated, confirmed)
        self.assertIn("자동", automated)
        self.assertNotIn("사용자 확인", automated)
        self.assertIn("사용자", confirmed)
        # 사용자 확인 근거는 자동 검증과 같은 무게로 읽히면 안 된다.
        self.assertRegex(confirmed, r"자동\s*검증(이)?\s*아[님니]")
        self.assertNotIn(AUTOMATED_RUN, confirmed)

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
                },
                repositories={
                    "checkout-web": {
                        "implementation": implementation(),
                        "verification": verification(
                            failed_automated_runs=[FAILED_RUN],
                            residual_risks=[f"{RESIDUAL_RISK} {FIELD_LEAK}"],
                        ),
                    }
                },
            )

            text = report.final_report("demo-run", home)

            for leak in (HEADER_LEAK, FIELD_LEAK, HOME_LEAK, LEAKED_VALUE, "/Users" + "/demo-person"):
                with self.subTest(leak=leak):
                    self.assertNotIn(leak, text)
            # 민감한 조각만 가리고 사람이 읽을 문장 자체는 남아야 한다.
            self.assertIn(SYMPTOM, text)
            self.assertIn(RESIDUAL_RISK, section(text, "## 실패와 잔여 위험"))

            for hidden in (TARGET, REPOSITORY_PATH, EVIDENCE_ONE, EVIDENCE_TWO):
                with self.subTest(hidden=hidden):
                    self.assertNotIn(hidden, text)
            self.assertIn("checkout-web", text)
            # 근거는 내용 대신 건수만 남긴다.
            self.assertRegex(section(text, "## 근본 원인"), r"2\s*건")

    def test_derives_the_final_status_from_verdicts_and_blockers(self):
        cases = {
            "해결됨": {
                "checkout-web": {"implementation": implementation(), "verification": verification()},
                "orders-api": {
                    "implementation": implementation(repository="orders-api"),
                    "verification": verification(),
                },
            },
            "미해결": {
                "checkout-web": {"implementation": implementation(), "verification": verification()},
                "orders-api": {
                    "implementation": implementation(repository="orders-api"),
                    "verification": verification(verdict="fail", automated_runs=[AUTOMATED_RUN]),
                },
            },
            "차단됨": {
                "checkout-web": {
                    "implementation": implementation(),
                    "verification": verification(blockers=[VERIFICATION_BLOCKER]),
                },
            },
        }
        for expected, repositories in cases.items():
            with self.subTest(status=expected), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                self.complete(home, repositories=repositories)

                text = report.final_report("demo-run", home)

                self.assertIn(expected, text)
                for other in set(cases) - {expected}:
                    with self.subTest(other=other):
                        self.assertNotIn(other, text)

    def test_renders_every_repository_in_sorted_order_without_mixing_their_results(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            # 사전순 뒤에 오는 저장소를 먼저 만들어 디렉터리 순서에 기대지 않는지 본다.
            self.complete(
                home,
                repositories={
                    "zebra-web": {
                        "implementation": implementation(repository="zebra-web", changed_files=["src/zebra/view.py"]),
                        "verification": verification(verdict="fail"),
                    },
                    "alpha-service": {
                        "implementation": implementation(
                            repository="alpha-service", changed_files=["src/alpha/handler.py"]
                        ),
                        "verification": verification(),
                    },
                },
            )

            text = report.final_report("demo-run", home)

            for heading in ("## 해결 조치", "## 검증 결과"):
                with self.subTest(heading=heading):
                    body = section(text, heading)
                    self.assertRegex(body, r"(?m)^#{3,} *alpha-service")
                    self.assertRegex(body, r"(?m)^#{3,} *zebra-web")
                    self.assertLess(body.index("alpha-service"), body.index("zebra-web"))
            fixes = section(text, "## 해결 조치")
            for quoted in ("src/alpha/handler.py", "src/zebra/view.py"):
                with self.subTest(quoted=quoted):
                    self.assertIn(quoted, fixes)
            self.assertLess(fixes.index("src/alpha/handler.py"), fixes.index("src/zebra/view.py"))

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
            self.assertIn("코드 변경 없음", section(text, "## 해결 조치"))
            self.assertIn("없음", section(text, "## 실패와 잔여 위험"))
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
