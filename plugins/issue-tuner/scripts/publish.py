#!/usr/bin/env python3
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit


SCP_REMOTE = re.compile(r"^(?:[^@/:]+@)?(\[[^]]+\]|[^/:]+):.+$")
# 30분 내 리뷰 가능한 Micro PR 상한.
REVIEW_LINE_LIMIT = 600


def host_kind(remote: str) -> str:
    if not isinstance(remote, str):
        return "manual"
    if "://" in remote:
        try:
            parsed = urlsplit(remote)
            if parsed.scheme.lower() not in {"https", "http", "ssh", "git"}:
                return "manual"
            hostname = parsed.hostname
        except ValueError:
            return "manual"
    else:
        match = SCP_REMOTE.fullmatch(remote)
        hostname = match.group(1).strip("[]") if match else None
    hostname = hostname.lower().rstrip(".") if hostname else ""
    if hostname == "github.com":
        return "github"
    if hostname == "gitlab.com":
        return "gitlab"
    return "manual"


def _run(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True
    )
    if result.returncode:
        raise RuntimeError("git command failed")
    return result.stdout


def _repo_root(repo: Path) -> Path:
    repo = Path(repo)
    if not repo.is_absolute() or repo.is_symlink() or not repo.is_dir():
        raise ValueError("repo must be an absolute, non-symlink directory")
    try:
        root = Path(os.fsdecode(_run(repo, "rev-parse", "--show-toplevel")).strip()).resolve()
    except (OSError, RuntimeError):
        raise ValueError("repo must be a Git worktree") from None
    if root.is_symlink() or not root.is_dir():
        raise ValueError("repository root must be a non-symlink directory")
    return root


def _commit(root: Path, ref: str, name: str) -> None:
    # ref가 옵션으로 해석되지 않게 막은 뒤 실제 커밋으로 풀리는지 확인한다.
    if not isinstance(ref, str) or not ref.strip() or ref.startswith("-"):
        raise ValueError(f"{name} must be a non-empty ref that is not an option")
    try:
        _run(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except (OSError, RuntimeError):
        raise ValueError(f"{name} must resolve to a commit") from None


def _numstat_field(raw: bytes) -> int | None:
    # 바이너리 등 줄 수로 셀 수 없는 변경은 "-"로 보고된다.
    text = raw.decode("ascii", "replace")
    if text == "-":
        return None
    if not text.isdigit():
        raise RuntimeError("git returned malformed numstat data")
    return int(text)


def review_budget(repo: Path, base: str, head: str) -> dict:
    root = _repo_root(repo)
    _commit(root, base, "base")
    _commit(root, head, "head")
    # 3-dot diff는 merge-base 기준이라 GitHub PR이 보여주는 범위와 일치한다.
    records = _run(root, "diff", "--numstat", "-z", f"{base}...{head}", "--").split(b"\0")
    files = []
    unmeasurable = []
    added_total = 0
    deleted_total = 0
    index = 0
    while index < len(records) and records[index]:
        fields = records[index].split(b"\t")
        if len(fields) != 3:
            raise RuntimeError("git returned malformed numstat data")
        added = _numstat_field(fields[0])
        deleted = _numstat_field(fields[1])
        if fields[2]:
            path = os.fsdecode(fields[2])
            index += 1
        else:
            # rename/copy는 빈 경로 뒤에 source, dest 레코드가 따로 온다.
            if index + 2 >= len(records) or not records[index + 1] or not records[index + 2]:
                raise RuntimeError("git returned malformed numstat data")
            path = os.fsdecode(records[index + 2])
            index += 3
        if added is None or deleted is None:
            unmeasurable.append(path)
            continue
        files.append({"path": path, "added": added, "deleted": deleted})
        added_total += added
        deleted_total += deleted
    total = added_total + deleted_total
    return {
        "repository_root": str(root),
        "base": base,
        "head": head,
        "added": added_total,
        "deleted": deleted_total,
        "total": total,
        "limit": REVIEW_LINE_LIMIT,
        "within_limit": total <= REVIEW_LINE_LIMIT,
        "files": sorted(files, key=lambda entry: entry["path"]),
        "unmeasurable": sorted(unmeasurable),
    }


def split_plan(budget: dict, limit: int = None) -> list:
    if limit is None:
        limit = budget["limit"]
    groups = {}
    for entry in budget["files"]:
        # 최상위 경로 구성요소를 기능 단위로 본다.
        groups.setdefault(entry["path"].split("/", 1)[0], []).append(entry)
    plan = []
    for key in sorted(groups):
        paths = []
        running = 0
        for entry in sorted(groups[key], key=lambda item: item["path"]):
            lines = entry["added"] + entry["deleted"]
            if lines > limit:
                if paths:
                    plan.append({"paths": paths, "total": running, "oversized": False})
                    paths = []
                    running = 0
                plan.append({"paths": [entry["path"]], "total": lines, "oversized": True})
                continue
            if paths and running + lines > limit:
                plan.append({"paths": paths, "total": running, "oversized": False})
                paths = []
                running = 0
            paths.append(entry["path"])
            running += lines
        if paths:
            plan.append({"paths": paths, "total": running, "oversized": False})
    return plan


def render_budget(budget: dict, plan: list = None) -> str:
    scope = (
        "30분 내 검토 가능"
        if budget["within_limit"]
        else "제한 초과 — 게시 차단, 분할 필요"
    )
    lines = [
        "## 리뷰 분량",
        f"- 변경 줄 수: {budget['total']}줄 "
        f"(추가 {budget['added']}, 삭제 {budget['deleted']}) / 제한 {budget['limit']}줄",
        f"- 변경 파일: {len(budget['files'])}개",
        f"- 예상 리뷰 범위: {scope}",
    ]
    if budget["unmeasurable"]:
        lines.append(
            f"- 줄 수 측정 불가: {', '.join(budget['unmeasurable'])}"
            " — 30분 내 검토 가능한지 사용자 확인 필요"
        )
    if plan:
        lines.append("### 분할안")
        for number, group in enumerate(plan, start=1):
            suffix = " — 단일 파일이 제한 초과, 수동 분해 필요" if group["oversized"] else ""
            lines.append(
                f"- 그룹 {number} ({group['total']}줄): {', '.join(group['paths'])}{suffix}"
            )
    return "\n".join(lines)


def draft_command(remote: str, base: str, title: str, body_file: Path, budget: dict):
    if not isinstance(base, str) or not base.strip():
        raise ValueError("base must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    body_file = Path(body_file)
    if not body_file.is_absolute() or body_file.is_symlink() or not body_file.is_file():
        raise ValueError("body_file must be an absolute, existing regular non-symlink file")
    if not isinstance(budget, dict):
        raise ValueError("budget must be a review budget mapping")
    if budget.get("base") != base:
        raise ValueError("budget base does not match requested base")
    if budget.get("within_limit") is not True:
        raise ValueError("change exceeds the review line limit")

    kind = host_kind(remote)
    if kind == "github" and shutil.which("gh"):
        return [
            "gh",
            "pr",
            "create",
            "--draft",
            "--base",
            base,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ]
    if kind == "gitlab" and shutil.which("glab"):
        return [
            "glab",
            "mr",
            "create",
            "--draft",
            "--target-branch",
            base,
            "--title",
            title,
            "--description-file",
            str(body_file),
        ]
    return None
