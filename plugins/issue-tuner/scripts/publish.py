#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit


SCP_REMOTE = re.compile(r"^(?:[^@/:]+@)?(\[[^]]+\]|[^/:]+):.+$")


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


def draft_command(remote: str, base: str, title: str, body_file: Path):
    if not isinstance(base, str) or not base.strip():
        raise ValueError("base must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    body_file = Path(body_file)
    if not body_file.is_absolute() or body_file.is_symlink() or not body_file.is_file():
        raise ValueError("body_file must be an absolute, existing regular non-symlink file")

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
