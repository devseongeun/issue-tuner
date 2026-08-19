import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "git_context.py"
SPEC = importlib.util.spec_from_file_location("git_context", SCRIPT)
git_context = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(git_context)


def command(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


class GitContextTest(unittest.TestCase):
    def repo(self, directory):
        repo = Path(directory) / "repo"
        command(directory, "git", "init", "-b", "main", str(repo))
        command(repo, "git", "config", "user.name", "Test User")
        command(repo, "git", "config", "user.email", "test@example.com")
        (repo / "README.md").write_text("initial\n", encoding="utf-8")
        command(repo, "git", "add", "README.md")
        command(repo, "git", "commit", "-m", "initial")
        return repo

    def test_detects_clean_repo_without_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            self.assertEqual(
                git_context.detect(repo),
                {
                    "root": str(repo.resolve()),
                    "branch": "main",
                    "remote": None,
                    "dirty": False,
                    "confirmation_required": False,
                    "reasons": [],
                },
            )

    def test_detect_uses_no_optional_locks_for_status(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            with patch.object(git_context, "git", wraps=git_context.git) as mocked:
                git_context.detect(repo)
            self.assertTrue(
                any(args[1:] == ("--no-optional-locks", "status", "--porcelain") for args, _ in mocked.call_args_list)
            )

    def test_detect_propagates_configured_origin_failure_and_rejects_invalid_collision_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            original = git_context.git

            def broken_origin(cwd, *args):
                if args == ("remote", "get-url", "origin"):
                    raise subprocess.CalledProcessError(2, ["git", *args], stderr="private failure")
                return original(cwd, *args)

            command(repo, "git", "remote", "add", "origin", "https://example.invalid/repo.git")
            with patch.object(git_context, "git", side_effect=broken_origin):
                with self.assertRaises(subprocess.CalledProcessError):
                    git_context.detect(repo)
            with self.assertRaises(ValueError):
                git_context.detect(repo, "bad..branch")

    def test_detect_requires_confirmation_for_dirty_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            context = git_context.detect(repo)
            self.assertTrue(context["dirty"])
            self.assertTrue(context["confirmation_required"])
            self.assertIn("dirty_worktree", context["reasons"])

    def test_detect_requires_confirmation_for_detached_head_and_branch_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            command(repo, "git", "branch", "fix/DEMO-123")
            command(repo, "git", "checkout", "--detach")
            context = git_context.detect(repo, "fix/DEMO-123")
            self.assertEqual(context["branch"], "")
            self.assertTrue(context["confirmation_required"])
            self.assertIn("detached_head", context["reasons"])
            self.assertIn("branch_exists", context["reasons"])

    def test_suggest_branch_sanitizes_and_rejects_empty_id(self):
        self.assertEqual(git_context.suggest_branch("DEMO 123!"), "fix/DEMO-123")
        for issue_id in ("!!!", ".", "..", "name.lock"):
            with self.subTest(issue_id=issue_id):
                with self.assertRaises(ValueError):
                    git_context.suggest_branch(issue_id)

    def test_creates_worktree_only_under_configured_home(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            repo = self.repo(directory)
            home = directory / "state"
            result = git_context.create_worktree(repo, "run-1", "demo", "fix/DEMO-123", "main", home)
            target = home / "worktrees" / "run-1" / "demo"
            self.assertEqual(result["path"], str(target))
            self.assertEqual(result["branch"], "fix/DEMO-123")
            self.assertTrue((target / "README.md").is_file())
            self.assertEqual(
                subprocess.run(
                    ["git", "branch", "--show-current"], cwd=target, text=True, capture_output=True, check=True
                ).stdout.strip(),
                "fix/DEMO-123",
            )

    def test_refuses_homes_inside_the_source_repository_without_creating_a_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            for name, home in (("root", repo), ("nested", repo / "state")):
                branch = f"fix/{name}"
                target = home / "worktrees" / "run" / "demo"
                with self.subTest(home=home):
                    with self.assertRaises(ValueError):
                        git_context.create_worktree(repo, "run", "demo", branch, "main", home)
                    self.assertFalse(target.exists())
                    self.assertEqual(
                        subprocess.run(
                            ["git", "branch", "--list", branch],
                            cwd=repo,
                            text=True,
                            capture_output=True,
                            check=True,
                        ).stdout.strip(),
                        "",
                    )

    def test_reports_partial_worktree_creation_without_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            repo = self.repo(directory)
            home = directory / "state"
            branch = "fix/partial"
            target = home / "worktrees" / "run" / "demo"
            original = git_context.git

            def partial_failure(cwd, *args):
                if args[:2] == ("worktree", "add"):
                    command(repo, "git", "branch", branch)
                    target.mkdir()
                    raise subprocess.CalledProcessError(1, ["git", *args], stderr="secret checkout detail")
                return original(cwd, *args)

            with patch.object(git_context, "git", side_effect=partial_failure):
                with self.assertRaises(RuntimeError) as raised:
                    git_context.create_worktree(repo, "run", "demo", branch, "main", home)
            self.assertTrue(target.exists())
            self.assertEqual(
                subprocess.run(
                    ["git", "branch", "--list", branch], cwd=repo, text=True, capture_output=True, check=True
                ).stdout.strip(),
                branch,
            )
            self.assertIn(str(target), str(raised.exception))
            self.assertIn("manual cleanup/continuation required", str(raised.exception))
            self.assertNotIn("secret checkout detail", str(raised.exception))

    def test_refuses_traversal_existing_target_and_existing_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            repo = self.repo(directory)
            home = directory / "state"
            with self.assertRaises(ValueError):
                git_context.create_worktree(repo, "../run", "demo", "fix/a", "main", home)
            with self.assertRaises(ValueError):
                git_context.create_worktree(repo, "run", "../demo", "fix/a", "main", home)
            target = home / "worktrees" / "run" / "demo"
            target.mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                git_context.create_worktree(repo, "run", "demo", "fix/a", "main", home)
            with self.assertRaises(FileExistsError):
                git_context.create_worktree(repo, "other", "demo", "main", "main", home)


if __name__ == "__main__":
    unittest.main()
