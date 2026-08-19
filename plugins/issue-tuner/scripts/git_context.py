#!/usr/bin/env python3
import os
from pathlib import Path
import re
import subprocess


SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


class WorktreeCreationError(RuntimeError):
    def __init__(self, target, branch_exists, target_exists, registered):
        super().__init__(
            "worktree creation may be partial: "
            f"target={target}, branch_exists={branch_exists}, "
            f"target_exists={target_exists}, registered={registered}; "
            "manual cleanup/continuation required"
        )


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


def _validate_branch(branch: str, cwd=None) -> str:
    if not isinstance(branch, str):
        raise ValueError("branch must be a string")
    try:
        subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        raise ValueError("branch must be a valid local branch name") from None
    return branch


def suggest_branch(issue_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", issue_id).strip("-")
    if not safe:
        raise ValueError("issue_id must contain a branch-safe character")
    return _validate_branch(f"fix/{safe}")


def _branch_exists(root: Path, branch: str) -> bool:
    _validate_branch(branch, root)
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError("unable to determine whether local branch exists")


def _registered_worktree(root: Path, target: Path):
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=root, text=True, capture_output=True
    )
    if result.returncode:
        return None
    return f"worktree {target.resolve()}" in result.stdout.splitlines()


def detect(repo: Path, proposed_branch: str = None) -> dict:
    root = Path(git(Path(repo), "rev-parse", "--show-toplevel")).resolve()
    branch = git(root, "branch", "--show-current")
    remotes = git(root, "remote").splitlines()
    if "origin" in remotes:
        remote = git(root, "remote", "get-url", "origin")
    else:
        remote = None

    reasons = []
    if not branch:
        reasons.append("detached_head")
    if git(root, "--no-optional-locks", "status", "--porcelain"):
        reasons.append("dirty_worktree")
    if proposed_branch and _branch_exists(root, proposed_branch):
        reasons.append("branch_exists")
    return {
        "root": str(root),
        "branch": branch,
        "remote": remote,
        "dirty": "dirty_worktree" in reasons,
        "confirmation_required": bool(reasons),
        "reasons": reasons,
    }


def _safe_component(value: str, name: str) -> str:
    if not isinstance(value, str) or not SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"{name} must be a safe filename component")
    return value


def _home(home) -> Path:
    home = Path(home if home is not None else os.environ.get("ISSUE_TUNER_HOME") or Path.home() / ".issue-tuner")
    if not home.is_absolute():
        raise ValueError("home must be absolute")
    if home.is_symlink():
        raise ValueError("home must not be a symlink")
    return home


def _within(path: Path, root: Path, message: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(message) from None


def create_worktree(repo, run_id, repo_name, branch, base, home=None) -> dict:
    repo = Path(repo)
    if not repo.is_absolute():
        raise ValueError("repo must be absolute")
    _safe_component(run_id, "run_id")
    _safe_component(repo_name, "repo_name")
    root = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    if _branch_exists(root, branch):
        raise FileExistsError(f"branch already exists: {branch}")

    configured_home = _home(home)
    try:
        configured_home.resolve().relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("home must be outside the source repository")
    worktrees = configured_home / "worktrees"
    if worktrees.is_symlink():
        raise ValueError("worktrees root must not be a symlink")
    _within(worktrees, configured_home, "worktrees root escapes configured home")
    run_directory = worktrees / run_id
    if run_directory.is_symlink():
        raise ValueError("run directory must not be a symlink")
    target = run_directory / repo_name
    try:
        target.resolve().relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("worktree target must be outside the source repository")
    if target.is_symlink():
        raise ValueError("worktree target must not be a symlink")
    _within(target, worktrees, "worktree target escapes worktrees root")
    if target.exists():
        raise FileExistsError(f"worktree target already exists: {target}")

    run_directory.mkdir(parents=True, exist_ok=True)
    if run_directory.is_symlink():
        raise ValueError("run directory must not be a symlink")
    _within(run_directory, worktrees, "run directory escapes worktrees root")
    try:
        git(root, "worktree", "add", str(target), "-b", branch, base)
    except subprocess.CalledProcessError:
        raise WorktreeCreationError(
            target,
            _branch_exists(root, branch),
            target.exists(),
            _registered_worktree(root, target),
        ) from None
    return {"path": str(target), "branch": branch, "context": detect(target)}
