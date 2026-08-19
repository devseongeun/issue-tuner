import unittest
from pathlib import Path


ROOT = Path(__file__).parents[3]


class PublicDocsTests(unittest.TestCase):
    def test_public_release_files_and_core_contract(self):
        required = (
            "README.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "LICENSE",
            "docs/design.md",
            "docs/implementation.md",
            ".github/workflows/ci.yml",
            "plugins/issue-tuner/adapters/claude/README.md",
        )
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for phrase in (
            "Codex 기준",
            "Claude 지원",
            "Issue Report Form",
            "Jira connector가 설치",
            "connector가 없을 때",
            "macOS",
            "Computer Use",
            "사용자 직접",
            "uv",
            "Serena",
            "저장소 밖",
            "최종 게시 승인",
            "승인받지 못하면",
            "docs/assets/issue-tuner-flow.svg",
            "$issue-tuner",
            ".issue-tuner.json",
            "Reproducer",
            "Diagnoser",
            "Implementer",
            "Verifier",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)
        self.assertNotIn("AIOSS", readme)

        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "README.md", ROOT / "docs/design.md", ROOT / "docs/implementation.md")
        ).lower()
        self.assertNotIn("play" + "wright", combined)
        self.assertNotIn("node" + ".js", combined)

    def test_ci_is_minimal_and_macos_only(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: macos-latest", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("actions/setup-python@v5", workflow)
        self.assertIn("python-version: \"3.13\"", workflow)
        self.assertIn("unittest discover", workflow)
        self.assertIn("check_public_safety.py", workflow)
        self.assertIn("json.load", workflow)
        self.assertNotIn("ubuntu-", workflow)

    def test_license_is_mit(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2026 Seongeun Kim", license_text)

    def test_security_and_contribution_boundaries(self):
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("GitHub Security Advisories", security)
        self.assertIn("Privately report a security vulnerability", security)
        self.assertIn("공개 이슈", security)

        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("합성 fixture", contributing)


if __name__ == "__main__":
    unittest.main()
