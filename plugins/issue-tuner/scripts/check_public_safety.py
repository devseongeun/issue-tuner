#!/usr/bin/env python3
import re
import sys
from pathlib import Path


FORBIDDEN_EXTENSIONS = {".log", ".png", ".jpg", ".jpeg", ".har", ".trace", ".zip"}
TEXT_EXTENSIONS = {".md", ".json", ".py", ".sh", ".yml", ".yaml", ".txt"}
SECRET_PATTERNS = (
    re.compile(r"(?im)^\s*Authorization\s*:"),
    re.compile(r"(?im)^\s*Cookie\s*:"),
    re.compile(
        r'''(?im)(?:^\s*|[,{]\s*)["']?(?:[a-z0-9]+_)*(?:token|password|secret)["']?\s*:\s*["']?[^\s"',}]+'''
    ),
    re.compile(
        r'''(?i)\b(?:[a-z0-9]+_)*(?:token|password|secret)\b\s*=\s*["']?[^\s"',}]+'''
    ),
    re.compile("/" + r"Users/[A-Za-z0-9._-]+(?=/|[^A-Za-z0-9._-]|$)"),
)
ALLOWED_SYNTHETIC_IDS = re.compile(r"\bDEMO" + r"-\d+\b")
TICKET_PATTERN = re.compile(r"\b[A-Z][A-Z0" + r"-9]+-\d+\b")


def scan(root: Path) -> list[str]:
    findings = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_symlink() or candidate.is_file()):
        relative = path.relative_to(root)
        if ".git" in relative.parts or any(
            relative.parts[index : index + 3] == ("tests", "fixtures", "unsafe")
            for index in range(len(relative.parts) - 2)
        ):
            continue

        display = relative.as_posix()
        extension = path.suffix.lower()
        if extension in FORBIDDEN_EXTENSIONS:
            findings.append(f"{display}: forbidden artifact extension")

        text = ["/" + display]
        if path.is_symlink():
            try:
                text.append(path.readlink().as_posix())
            except OSError:
                pass
        elif extension in TEXT_EXTENSIONS or path.name in {"LICENSE", ".gitignore"}:
            text.append(path.read_text(encoding="utf-8", errors="replace"))

        if any(TICKET_PATTERN.search(ALLOWED_SYNTHETIC_IDS.sub("", value)) for value in text):
            findings.append(f"{display}: non-synthetic ticket id")
        if any(pattern.search(value) for value in text for pattern in SECRET_PATTERNS):
            findings.append(f"{display}: secret-like content")
    return findings


def main() -> int:
    if len(sys.argv) > 2:
        print("usage: check_public_safety.py [directory]", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]) if len(sys.argv) == 2 else Path(".")
    if not root.is_dir():
        print(f"{root}: not a directory", file=sys.stderr)
        return 2

    findings = scan(root)
    for finding in findings:
        print(finding, file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
