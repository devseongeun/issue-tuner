import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "publish.py"
SPEC = importlib.util.spec_from_file_location("publish", SCRIPT)
publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish)


class PublishTest(unittest.TestCase):
    def test_maps_supported_hosts_without_accepting_spoofs(self):
        cases = {
            "git@github.com:demo/repo.git": "github",
            "https://gitlab.com/demo/repo.git": "gitlab",
            "ssh://git.example.invalid/repo.git": "manual",
            "git@evilgithub.com:demo/repo.git": "manual",
            "https://gitlab.com.evil.invalid/demo/repo.git": "manual",
            "file://github.com/demo/repo.git": "manual",
            "ftp://gitlab.com/demo/repo.git": "manual",
            "custom://github.com/demo/repo.git": "manual",
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote):
                self.assertEqual(publish.host_kind(remote), expected)

    def test_builds_exact_github_draft_command(self):
        with tempfile.TemporaryDirectory() as directory:
            body = Path(directory) / "body.md"
            body.write_text("Draft body\n", encoding="utf-8")
            with patch.object(publish.shutil, "which", return_value="/usr/bin/gh"):
                self.assertEqual(
                    publish.draft_command("git@github.com:demo/repo.git", "main", "Fix bug", body.resolve()),
                    [
                        "gh",
                        "pr",
                        "create",
                        "--draft",
                        "--base",
                        "main",
                        "--title",
                        "Fix bug",
                        "--body-file",
                        str(body.resolve()),
                    ],
                )

    def test_builds_exact_gitlab_draft_command(self):
        with tempfile.TemporaryDirectory() as directory:
            body = Path(directory) / "body.md"
            body.write_text("Draft body\n", encoding="utf-8")
            with patch.object(publish.shutil, "which", return_value="/usr/bin/glab"):
                self.assertEqual(
                    publish.draft_command("https://gitlab.com/demo/repo.git", "main", "Fix bug", body.resolve()),
                    [
                        "glab",
                        "mr",
                        "create",
                        "--draft",
                        "--target-branch",
                        "main",
                        "--title",
                        "Fix bug",
                        "--description-file",
                        str(body.resolve()),
                    ],
                )

    def test_returns_none_for_manual_host_or_missing_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            body = Path(directory) / "body.md"
            body.write_text("Draft body\n", encoding="utf-8")
            with patch.object(publish.shutil, "which", return_value=None):
                self.assertIsNone(
                    publish.draft_command("git@github.com:demo/repo.git", "main", "Fix bug", body.resolve())
                )
            with patch.object(publish.shutil, "which", return_value="/usr/bin/tool"):
                self.assertIsNone(
                    publish.draft_command("ssh://git.example.invalid/repo.git", "main", "Fix bug", body.resolve())
                )

    def test_validates_command_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            body = directory / "body.md"
            body.write_text("Draft body\n", encoding="utf-8")
            link = directory / "body-link.md"
            link.symlink_to(body)
            invalid = (
                ("", "Fix bug", body.resolve()),
                ("main", " ", body.resolve()),
                ("main", "Fix bug", Path("body.md")),
                ("main", "Fix bug", (directory / "missing.md").resolve()),
                ("main", "Fix bug", link.absolute()),
            )
            for base, title, path in invalid:
                with self.subTest(base=base, title=title, path=path), self.assertRaises(ValueError):
                    publish.draft_command("git@github.com:demo/repo.git", base, title, path)


if __name__ == "__main__":
    unittest.main()
