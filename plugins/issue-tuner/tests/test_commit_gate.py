import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "commit_gate.py"
SPEC = importlib.util.spec_from_file_location("commit_gate", SCRIPT)
commit_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(commit_gate)


def command(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


class CommitGateTest(unittest.TestCase):
    def repo(self, directory):
        repo = Path(directory) / "repo"
        command(directory, "git", "init", "-b", "main", str(repo))
        command(repo, "git", "config", "user.name", "Test User")
        command(repo, "git", "config", "user.email", "test@example.com")
        (repo / "app.txt").write_text("initial\n", encoding="utf-8")
        command(repo, "git", "add", "app.txt")
        command(repo, "git", "commit", "-m", "initial")
        return repo

    def verification(self, directory, **updates):
        data = {
            "verdict": "pass",
            "source": "automated",
            "channels": ["test"],
            "automated_runs": ["test: pass"],
            "failed_automated_runs": [],
            "residual_risks": [],
            "blockers": [],
        }
        data.update(updates)
        path = Path(directory) / "verification.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_records_fingerprint_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = Path(directory) / "commit-gate.json"
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")
            command(repo, "git", "add", "app.txt")

            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())
            stored = json.loads(gate.read_text(encoding="utf-8"))
            self.assertEqual(stored["verification"]["verdict"], "pass")
            self.assertEqual(stored["verification"]["channels"], ["test"])
            self.assertEqual(stored["verification"].get("source"), "automated")
            self.assertEqual(stored["verification_file"]["type"], "file")
            self.assertEqual(len(stored["verification_file"]["sha256"]), 64)
            self.assertIsInstance(stored["verification_file"]["mtime_ns"], int)
            self.assertEqual(stored["files"]["app.txt"]["type"], "file")
            self.assertEqual(stored["files"]["app.txt"]["mode"], "100644")
            self.assertEqual(len(stored["files"]["app.txt"]["sha256"]), 64)
            self.assertIsInstance(stored["files"]["app.txt"]["mtime_ns"], int)
            self.assertEqual(commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()), [])

            (repo / "app.txt").write_text("tampered\n", encoding="utf-8")
            self.assertIn(
                "verified file changed after verification: app.txt",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

    def test_rejects_timestamp_changes_and_requires_exact_staged_set(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = Path(directory) / "commit-gate.json"
            app = repo / "app.txt"
            app.write_text("verified\n", encoding="utf-8")
            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())

            self.assertIn(
                "verified file is not staged: app.txt",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )
            command(repo, "git", "add", "app.txt")
            self.assertEqual(commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()), [])

            stat = app.stat()
            os.utime(app, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            self.assertIn(
                "verified file changed after verification: app.txt",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

    def test_rejects_newly_staged_unverified_file_and_missing_change(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = Path(directory) / "commit-gate.json"
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")
            command(repo, "git", "add", "app.txt")
            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())

            (repo / "extra.txt").write_text("extra\n", encoding="utf-8")
            command(repo, "git", "add", "extra.txt")
            self.assertIn(
                "unverified file present: extra.txt",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

            command(repo, "git", "reset", "--hard", "HEAD")
            self.assertIn(
                "verified file missing from current changes: app.txt",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

    def test_accepts_staged_deletion_and_hashes_symlink_target_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = directory / "commit-gate.json"
            (repo / "app.txt").unlink()
            command(repo, "git", "add", "app.txt")

            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())
            deleted = json.loads(gate.read_text())["files"]["app.txt"]
            self.assertEqual(deleted["type"], "deleted")
            self.assertEqual(deleted["mode"], "deleted")
            self.assertEqual(commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()), [])

            command(repo, "git", "reset", "--hard", "HEAD")
            outside = directory / "outside.txt"
            outside.write_text("must not be read\n", encoding="utf-8")
            (repo / "link").symlink_to(outside)
            command(repo, "git", "add", "link")
            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())
            fingerprint = json.loads(gate.read_text())["files"]["link"]
            self.assertEqual(fingerprint["type"], "symlink")
            self.assertEqual(fingerprint["mode"], "120000")
            self.assertNotIn("must not be read", gate.read_text())
            self.assertEqual(commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()), [])

    def test_rejects_failed_verification_and_changed_verification_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            gate = Path(directory) / "commit-gate.json"
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")
            for updates in (
                {"verdict": "fail"},
                {"blockers": ["blocked"]},
                {"channels": []},
                {"channels": [""]},
                {"channels": "test"},
                {"automated_runs": []},
                {"failed_automated_runs": ["synthetic voice: failed"]},
                {"source": "unknown"},
                {"automated_runs": [" "]},
                {"failed_automated_runs": [" "]},
                {"residual_risks": [" "]},
                {"source": "user_confirmed", "automated_runs": [], "residual_risks": []},
                {
                    "source": "user_confirmed",
                    "automated_runs": [],
                    "failed_automated_runs": [],
                    "residual_risks": ["not automated"],
                },
            ):
                with self.subTest(updates=updates):
                    verification = self.verification(directory, **updates)
                    with self.assertRaises(ValueError):
                        commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())

            verification = self.verification(directory)
            command(repo, "git", "add", "app.txt")
            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())
            verification.write_text(
                json.dumps(
                    {
                        "verdict": "pass",
                        "source": "automated",
                        "channels": ["browser"],
                        "automated_runs": ["browser: pass"],
                        "failed_automated_runs": [],
                        "residual_risks": [],
                        "blockers": [],
                    }
                ),
                encoding="utf-8",
            )
            self.assertIn(
                "verification file changed after verification",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

    def test_accepts_user_confirmed_pass_with_recorded_limitation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            gate = Path(directory) / "commit-gate.json"
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")
            verification = self.verification(
                directory,
                source="user_confirmed",
                automated_runs=[],
                failed_automated_runs=["Computer Use unavailable"],
                residual_risks=["not automated"],
            )

            stored = commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())

            self.assertEqual(stored["verification"].get("source"), "user_confirmed")

    def test_rejects_verification_timestamp_change(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = Path(directory) / "commit-gate.json"
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")
            command(repo, "git", "add", "app.txt")
            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())

            metadata = verification.stat()
            os.utime(
                verification,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000),
            )
            self.assertIn(
                "verification file changed after verification",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

    def test_preserves_rename_operation_in_current_and_staged_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = Path(directory) / "commit-gate.json"
            command(repo, "git", "config", "status.renames", "false")
            command(repo, "git", "mv", "app.txt", "renamed.txt")
            command(repo, "git", "add", "renamed.txt")

            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())
            stored = json.loads(gate.read_text(encoding="utf-8"))
            self.assertEqual(
                stored["operations"],
                [{"kind": "rename", "path": "renamed.txt", "source": "app.txt"}],
            )
            self.assertEqual(set(stored["files"]), {"app.txt", "renamed.txt"})
            self.assertEqual(commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()), [])

            command(repo, "git", "reset", "--hard", "HEAD")
            (repo / "renamed.txt").write_text("initial\n", encoding="utf-8")
            command(repo, "git", "add", "renamed.txt")
            self.assertIn(
                "verified change operation changed after verification",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

    def test_treats_magic_filename_as_a_literal_staged_path(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = Path(directory) / "commit-gate.json"
            name = ":(glob)a*"
            special = repo / name
            special.write_text("original\n", encoding="utf-8")
            command(repo, "git", "--literal-pathspecs", "add", "--", name)
            command(repo, "git", "commit", "-m", "literal filename")
            special.write_text("initial\n", encoding="utf-8")
            command(repo, "git", "--literal-pathspecs", "add", "--", name)
            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())
            verified_stat = special.stat()

            special.write_text("tampered\n", encoding="utf-8")
            command(repo, "git", "--literal-pathspecs", "add", "--", name)
            special.write_text("initial\n", encoding="utf-8")
            os.utime(
                special,
                ns=(verified_stat.st_atime_ns, verified_stat.st_mtime_ns),
            )
            self.assertIn(
                f"staged content differs from verified file: {name}",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

    def test_rejects_index_only_mode_change(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = Path(directory) / "commit-gate.json"
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")
            (repo / "app.txt").chmod(0o654)
            command(repo, "git", "add", "app.txt")
            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())
            self.assertEqual(
                json.loads(gate.read_text())["files"]["app.txt"]["mode"],
                "100644",
            )
            self.assertEqual(commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()), [])

            command(repo, "git", "update-index", "--chmod=+x", "--", "app.txt")
            self.assertIn(
                "staged content differs from verified file: app.txt",
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve()),
            )

    def test_requires_absolute_paths_and_keeps_gate_outside_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                commit_gate.record(Path("repo"), verification.resolve(), (Path(directory) / "gate.json").resolve())
            with self.assertRaises(ValueError):
                commit_gate.record(repo.resolve(), verification.resolve(), (repo / "gate.json").resolve())

    def test_rejects_committed_verification_file_inside_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = repo / "verification.json"
            verification.write_text(
                json.dumps({"verdict": "pass", "channels": ["test"], "blockers": []}),
                encoding="utf-8",
            )
            command(repo, "git", "add", "verification.json")
            command(repo, "git", "commit", "-m", "verification")
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                commit_gate.record(
                    repo.resolve(),
                    verification.resolve(),
                    (Path(directory) / "commit-gate.json").resolve(),
                )

    def test_rejects_unsafe_paths_in_a_tampered_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            verification = self.verification(directory)
            gate = Path(directory) / "commit-gate.json"
            (repo / "app.txt").write_text("verified\n", encoding="utf-8")
            commit_gate.record(repo.resolve(), verification.resolve(), gate.resolve())
            data = json.loads(gate.read_text(encoding="utf-8"))
            data["files"] = {"../outside.txt": data["files"]["app.txt"]}
            gate.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(ValueError):
                commit_gate.check(repo.resolve(), verification.resolve(), gate.resolve())


if __name__ == "__main__":
    unittest.main()
