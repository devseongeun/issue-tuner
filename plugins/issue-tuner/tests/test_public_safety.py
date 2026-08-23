import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_public_safety.py"
SPEC = importlib.util.spec_from_file_location("check_public_safety", SCRIPT)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PublicSafetyTests(unittest.TestCase):
    def test_accepts_synthetic_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.json").write_text('{"issue": "DEMO-123"}')

            self.assertEqual(CHECKER.scan(root), [])

    def test_rejects_run_artifacts_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "screen.png").touch()
            (root / "debug.log").touch()
            (root / "secret.txt").write_text("Authorization" + ": Bearer synthetic")

            findings = CHECKER.scan(root)

            self.assertIn("screen.png: forbidden artifact extension", findings)
            self.assertIn("debug.log: forbidden artifact extension", findings)
            self.assertIn("secret.txt: secret-like content", findings)

    def test_allows_doc_assets_images_but_still_rejects_other_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "docs" / "assets"
            assets.mkdir(parents=True)
            (assets / "flow.png").touch()
            (assets / "shot.jpeg").touch()
            (assets / "trace.har").touch()
            (assets / "bundle.zip").touch()
            nested = root / "runs" / "docs" / "assets"
            nested.mkdir(parents=True)
            (nested / "evidence.png").touch()

            findings = CHECKER.scan(root)

            self.assertNotIn("docs/assets/flow.png: forbidden artifact extension", findings)
            self.assertNotIn("docs/assets/shot.jpeg: forbidden artifact extension", findings)
            self.assertIn("docs/assets/trace.har: forbidden artifact extension", findings)
            self.assertIn("docs/assets/bundle.zip: forbidden artifact extension", findings)
            self.assertIn("runs/docs/assets/evidence.png: forbidden artifact extension", findings)

    def test_rejects_json_and_compound_secret_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, key in (("token.json", "token"), ("password.json", "password"), ("client.json", "client_secret")):
                (root / name).write_text(json.dumps({key: "live"}))
            (root / "assignment.txt").write_text("const " + "token" + "=live")
            (root / "credentials.yaml").write_text("credentials:\n  client_" + "secret: live")

            findings = CHECKER.scan(root)

            for name in ("token.json", "password.json", "client.json", "assignment.txt", "credentials.yaml"):
                self.assertIn(f"{name}: secret-like content", findings)

    def test_rejects_ticket_ids_in_content_and_paths_but_allows_demo_and_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ticket = "PROJ" + "-42"
            (root / f"{ticket}.md").write_text("safe")
            (root / "notes.md").write_text(f"{ticket}\nDEMO-123\n2026-08-19")

            findings = CHECKER.scan(root)

            self.assertEqual(
                findings,
                [f"{ticket}.md: non-synthetic ticket id", "notes.md: non-synthetic ticket id"],
            )

    def test_rejects_macos_paths_in_content_paths_and_broken_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = "/" + "Users/alice"
            (root / "home.txt").write_text(home)
            (root / "content.txt").write_text(home + "/private")
            nested = root / "Users" / "alice"
            nested.mkdir(parents=True)
            (nested / "note.md").write_text("safe")
            (root / "home-link").symlink_to(home)
            (root / "shortcut").symlink_to(home + "/private")

            findings = CHECKER.scan(root)

            self.assertIn("content.txt: secret-like content", findings)
            self.assertIn("home.txt: secret-like content", findings)
            self.assertIn("Users/alice/note.md: secret-like content", findings)
            self.assertIn("home-link: secret-like content", findings)
            self.assertIn("shortcut: secret-like content", findings)

    def test_skips_unsafe_fixture_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "tests" / "fixtures" / "unsafe" / "internal.log"
            fixture.parent.mkdir(parents=True)
            fixture.write_text("Authorization" + ": Bearer synthetic")

            self.assertEqual(CHECKER.scan(Path(directory)), [])

    def test_cli_rejects_extra_missing_and_file_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_root = root / "file.txt"
            file_root.write_text("safe")
            cases = (["checker", "one", "two"], ["checker", str(root / "missing")], ["checker", str(file_root)])

            for argv in cases:
                with self.subTest(argv=argv), mock.patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(CHECKER.main(), 2)
                    self.assertTrue(stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
