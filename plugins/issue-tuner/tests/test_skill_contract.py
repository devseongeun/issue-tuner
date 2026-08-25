from pathlib import Path
import json
import re
import unittest


PLUGIN = Path(__file__).parents[1]
SKILL = PLUGIN / "skills" / "issue-tuner" / "SKILL.md"
REFERENCES = {
    "Reproducer": PLUGIN / "skills" / "issue-tuner" / "references" / "reproducer.md",
    "Diagnoser": PLUGIN / "skills" / "issue-tuner" / "references" / "diagnoser.md",
    "Implementer": PLUGIN / "skills" / "issue-tuner" / "references" / "implementer.md",
    "Verifier": PLUGIN / "skills" / "issue-tuner" / "references" / "verifier.md",
}
OUTPUT_FIELDS = {
    "Reproducer": ("status", "source", "scenario", "limitations", "blockers"),
    "Diagnoser": ("status", "root_cause", "evidence", "symbols", "blockers"),
    "Implementer": ("status", "repository", "changed_files", "red_runs", "blockers"),
    "Verifier": ("verdict", "source", "channels", "automated_runs", "failed_automated_runs", "residual_risks", "blockers"),
}


def frontmatter(text):
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    return dict(
        line.split(":", 1) for line in match.group(1).splitlines() if ":" in line
    )


class SkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = SKILL.read_text(encoding="utf-8")
        cls.references = {
            role: path.read_text(encoding="utf-8") for role, path in REFERENCES.items()
        }

    def test_frontmatter_is_discoverable_and_bounded(self):
        metadata = frontmatter(self.skill)

        self.assertEqual(metadata.get("name", "").strip(), "issue-tuner")
        description = metadata.get("description", "").strip()
        self.assertTrue(description.startswith("Use when"))
        self.assertLessEqual(len(description), 300)

    def test_skill_names_contracts_roles_and_safety_gates(self):
        required = (
            "Issue Report",
            "사용자 확인",
            "Reproducer",
            "Diagnoser",
            "Implementer",
            "Verifier",
            "production",
            "Draft PR",
            "Draft MR",
            "Computer Use",
            "user_confirmed",
            "fix/<issue-id>",
            "commit gate",
            "force push 금지",
            "pipeline 수동 실행 금지",
            "issue-report.json",
            ".issue-tuner.json",
            "validate_contract.py",
        )

        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_skill_keeps_exact_workflow_policies(self):
        required = (
            "read-only",
            "origin",
            "environment.target",
            "로그인",
            "같은 task/session",
            "자동화 채널 실패",
            "결정적",
            "1회",
            "비결정적",
            "3회",
            "환경/설정 실패",
            "RED가 아니다",
            "한 번의 최종 게시 승인",
            "현재 브랜치 push",
            "merge 금지",
            "deploy 금지",
            "reviewer 변경 금지",
            "run evidence",
        )

        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_external_evidence_stays_private_and_only_public_artifacts_are_scanned(self):
        self.assertRegex(
            self.skill,
            r"external raw evidence.*check_public_safety\.py.*실행하지 않는다",
        )
        self.assertRegex(
            self.skill,
            r"public Issue Tuner repo.*sanitized Draft PR/MR body.*check_public_safety\.py",
        )
        self.assertIn("raw artifact가 아닌 sanitized summary", self.skill)
        public_directory = "<run>/repositories/<repo-name>/public-artifacts"
        command = (
            "python3 <plugin-root>/scripts/check_public_safety.py "
            f"{public_directory}"
        )
        self.assertIn(public_directory, self.skill)
        self.assertIn(command, self.skill)
        safety_commands = re.findall(
            r"python3 <plugin-root>/scripts/check_public_safety\.py [^`]+",
            self.skill,
        )
        self.assertEqual(safety_commands, [command])

    def test_skill_uses_exact_module_apis_and_checks_results(self):
        calls = (
            "run_state.create",
            "run_state.pause",
            "run_state.resume",
            "run_state.resolve",
            "run_state.finish",
            "git_context.detect",
            "git_context.create_worktree",
            "runtime.start_runtime",
            "runtime.stop_owned_process",
            "commit_gate.record",
            "commit_gate.check",
            "publish.host_kind",
            "publish.draft_command",
        )
        for call in calls:
            with self.subTest(call=call):
                self.assertIn(call, self.skill)
        self.assertIn(
            "run_state.py, git_context.py, runtime.py, publish.py만 library-only",
            self.skill,
        )
        self.assertNotIn("commit_gate.py, publish.py`는 Python module/function이며 standalone CLI가 아니다", self.skill)
        self.assertIn("모든 return과 error를 확인", self.skill)
        self.assertIn(
            "python3 <plugin-root>/scripts/validate_contract.py issue-report <run>/issue-report.json",
            self.skill,
        )
        self.assertIn("`work_seconds`와 `wait_seconds`는 해결 시각까지", self.skill)
        self.assertIn("`cleanup_seconds`는 해결부터 종료까지", self.skill)

    def test_publish_executes_only_returned_command_and_verifies_draft_url(self):
        self.assertIn(
            "subprocess.run(command, cwd=<confirmed repo worktree>, shell=False, check=True, capture_output=True, text=True)",
            self.skill,
        )
        self.assertRegex(self.skill, r"반환된 command만.*expected remote.*branch.*repo.*Draft URL.*검증")
        self.assertRegex(
            self.skill,
            r"command가 없으면.*manual command/body.*생성 성공으로 보고하지 않는다",
        )

    def test_each_repository_owns_results_gate_and_publication(self):
        for name in ("implementation.json", "verification.json", "commit-gate.json"):
            with self.subTest(name=name):
                self.assertIn(f"<run>/repositories/<repo-name>/{name}", self.skill)
        self.assertNotIn("publication.json", self.skill)
        self.assertRegex(self.skill, r"verification/gate.*repo root.*재사용하지 않는다")
        self.assertRegex(self.skill, r"dependency order.*독립적으로 gate와 publish")

    def test_orchestrator_owns_state_and_gates_not_role_judgment(self):
        self.assertIn("Orchestrator", self.skill)
        self.assertRegex(self.skill, r"Orchestrator.*state.*gate.*승인")
        self.assertRegex(self.skill, r"role 판단.*대체하지 않는다")

    def test_role_references_have_required_sections_and_exact_outputs(self):
        for role, text in self.references.items():
            with self.subTest(role=role):
                for heading in ("# Input", "# Allowed", "# Forbidden", "# Output"):
                    self.assertIn(heading, text)
                output = text.split("# Output", 1)[1]
                fields = re.findall(r'^  "([a-z_]+)"\s*:', output, re.MULTILINE)
                self.assertEqual(fields, list(OUTPUT_FIELDS[role]))

    def test_role_outputs_use_concrete_valid_examples_and_list_enums_in_prose(self):
        allowed = {
            "Reproducer": ("status: `reproduced`, `failed`, `blocked`", "source: `automated`, `user_confirmed`"),
            "Diagnoser": ("status: `diagnosed`, `blocked`",),
            "Implementer": ("status: `implemented`, `blocked`",),
            "Verifier": (
                "verdict: `pass`, `fail`",
                "source: `automated`, `user_confirmed`",
            ),
        }
        expected = {
            "Reproducer": {"status": "reproduced", "source": "automated"},
            "Diagnoser": {"status": "diagnosed"},
            "Implementer": {"status": "implemented"},
            "Verifier": {"verdict": "pass", "source": "automated"},
        }

        for role, text in self.references.items():
            output = text.split("# Output", 1)[1]
            block = re.search(r"```json\n(.*?)\n```", output, re.DOTALL).group(1)
            with self.subTest(role=role):
                self.assertNotIn("|", block)
                example = json.loads(block)
                for field, value in expected[role].items():
                    self.assertEqual(example.get(field), value)
                for phrase in allowed[role]:
                    self.assertIn(phrase, text)

    def test_role_references_preserve_required_behavior(self):
        checks = {
            "Reproducer": (
                "user_confirmed",
                "비결정적",
                "최대 3회",
                "production",
                "Computer Use",
                "코드",
                "Git",
            ),
            "Diagnoser": (
                "callers",
                "policy",
                "test",
                "Serena",
                "Codex search",
                "rg",
                "evidence level",
                "코드 변경 금지",
            ),
            "Implementer": (
                "confirmed worktree",
                "test first",
                "결정적",
                "1회",
                "비결정적",
                "3회",
                "환경/설정 실패",
                "게시 금지",
            ),
            "Verifier": (
                "fresh context",
                "declared commands",
                "Computer Use",
                "미해소 channel failure",
                "자동화 수단 실패",
                "fail",
                "코드 변경 금지",
                "게시 금지",
                "automated",
                "user_confirmed",
                "failed_automated_runs",
                "residual_risks",
            ),
        }

        for role, phrases in checks.items():
            for phrase in phrases:
                with self.subTest(role=role, phrase=phrase):
                    self.assertIn(phrase, self.references[role])

    def test_verifier_distinguishes_unresolved_failure_from_user_fallback(self):
        verifier = self.references["Verifier"]

        self.assertRegex(verifier, r"미해소 channel failure.*verdict: fail")
        self.assertRegex(
            verifier,
            r"Computer Use.*자동화 수단 실패.*사용자 직접 검증으로 대체",
        )
        self.assertRegex(
            verifier,
            r"source: automated.*pass.*nonempty.*automated_runs.*빈.*failed_automated_runs",
        )
        self.assertRegex(
            self.skill,
            r"미해소 channel failure.*fail.*자동화 수단 실패.*사용자 직접 검증",
        )
        self.assertIn("failed_automated_runs", verifier)
        self.assertIn("residual_risks", verifier)

    def test_public_content_has_no_private_or_retired_terms(self):
        forbidden = (
            "TQ-",
            "/Users/",
            "김성은",
            "Playwright",
            "run_browser_agent",
            "browser_session",
            "codex exec",
        )
        contents = {"SKILL.md": self.skill, **self.references}

        for name, text in contents.items():
            for phrase in forbidden:
                with self.subTest(file=name, phrase=phrase):
                    self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
