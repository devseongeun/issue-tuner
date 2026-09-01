#!/usr/bin/env python3
import datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile


SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
SAFE_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")

# 민감정보 마스킹 규칙. check_public_safety.py와 같은 개념이되 독립으로 정의한다.
REDACTED = "[redacted]"
REDACTED_PATH = "[redacted-path]"
HEADER_NAMES = r"(?:Author" + r"ization|Coo" + r"kie)"
HEADER_LINE = re.compile(r"(?im)^\s*" + HEADER_NAMES + r"\s*:.*$")
HEADER_INLINE = re.compile(r"(?i)\b" + HEADER_NAMES + r"\s*:[^\n]*")
KEYED_VALUE = re.compile(
    r"""(?i)(["']?(?:[a-z0-9]+_)*(?:token|password|secret)["']?\s*[:=]\s*)["']?[^\s"',}]+["']?"""
)
HOME_PATH = re.compile("/" + r"Users/[A-Za-z0-9._-]+")

REPRODUCTION_STATUSES = {"reproduced": "재현됨", "failed": "재현 실패", "blocked": "차단됨"}
SOURCES = {"automated": "자동 재현", "user_confirmed": "사용자 직접 확인"}
DIAGNOSIS_STATUSES = {"diagnosed": "진단됨", "blocked": "차단됨"}
IMPLEMENTATION_STATUSES = {"implemented": "구현됨", "blocked": "차단됨"}
VERDICTS = {"pass": "통과", "fail": "실패"}

UNRECORDED = "미기록"
NONE_TEXT = "없음"


def run_root() -> Path:
    home = os.environ.get("ISSUE_TUNER_HOME") or Path.home() / ".issue-tuner"
    home = Path(home)
    if not home.is_absolute():
        raise ValueError("ISSUE_TUNER_HOME must be absolute")
    return home / "runs"


def _runs(home):
    root = run_root() if home is None else Path(home) / "runs"
    if not root.is_absolute():
        raise ValueError("home must be absolute")
    if root.is_symlink():
        raise ValueError("runs root must not be a symlink")
    configured_home = root.parent.resolve()
    resolved_root = root.resolve()
    try:
        resolved_root.relative_to(configured_home)
    except ValueError:
        raise ValueError("runs root escapes configured home") from None
    return resolved_root


def _run_dir(run_id, home):
    if not isinstance(run_id, str) or not SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a safe filename component")
    root = _runs(home)
    directory = root / run_id
    if directory.is_symlink():
        raise ValueError("run directory must not be a symlink")
    try:
        directory.resolve().relative_to(root)
    except ValueError:
        raise ValueError("run directory escapes runs root") from None
    return directory


def _read_json(path):
    if path.is_symlink() or not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError:
        raise ValueError(f"invalid json document: {path.name}") from None
    if not isinstance(data, dict):
        raise ValueError(f"document must be an object: {path.name}")
    return data


def _redact(text) -> str:
    if not isinstance(text, str):
        return text
    text = HEADER_LINE.sub(REDACTED, text)
    text = HEADER_INLINE.sub(REDACTED, text)
    text = KEYED_VALUE.sub(r"\1" + REDACTED, text)
    return HOME_PATH.sub(REDACTED_PATH, text)


def _timestamp_text(value) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        return UNRECORDED
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat()


def _seconds_text(value) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        return UNRECORDED
    return f"{value}초"


def _bullets(items, empty=NONE_TEXT):
    # 라벨 줄 아래에 붙는 하위 항목이라 2칸 들여쓴다.
    values = list(items) if isinstance(items, (list, tuple)) else []
    if not values:
        return [f"  - {empty}"]
    return [f"  - {_redact(value)}" for value in values]


def _text(value):
    return _redact(value) if isinstance(value, str) and value else UNRECORDED


def _label(value, labels):
    return labels.get(value, UNRECORDED) if isinstance(value, str) else UNRECORDED


def _count(value):
    return len(value) if isinstance(value, (list, tuple)) else 0


def _missing(name):
    return [f"- {UNRECORDED} — {name} 파일이 없다"]


def _repositories(run_dir):
    directory = run_dir / "repositories"
    if directory.is_symlink() or not directory.is_dir():
        return []
    names = []
    for entry in directory.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        if SAFE_REPO_NAME.fullmatch(entry.name):
            names.append(entry.name)
    return sorted(names)


def _blocked(document):
    if not isinstance(document, dict):
        return False
    return document.get("status") == "blocked" or _count(document.get("blockers")) > 0


def _final_status(documents, verifications):
    if any(_blocked(document) for document in documents):
        return "차단됨"
    verdicts = [document.get("verdict") for document in verifications.values() if document is not None]
    if not verdicts or any(verdict != "pass" for verdict in verdicts):
        return "미해결"
    return "해결됨"


def _symptom_lines(issue_report):
    if issue_report is None:
        return _missing("issue-report.json")
    issue = issue_report.get("issue")
    issue = issue if isinstance(issue, dict) else {}
    lines = [
        f"- 기대 결과: {_text(issue.get('expected'))}",
        f"- 실제 결과: {_text(issue.get('actual'))}",
        "- 재현 단계:",
    ]
    steps = issue.get("steps")
    steps = steps if isinstance(steps, list) else []
    if not steps:
        lines.append(f"  - {NONE_TEXT}")
    for number, step in enumerate(steps, start=1):
        lines.append(f"  {number}. {_redact(step)}")
    return lines


def _reproduction_lines(reproduction):
    if reproduction is None:
        return _missing("reproduction.json")
    lines = [
        f"- 결과: {_label(reproduction.get('status'), REPRODUCTION_STATUSES)}",
        f"- 근거: {_label(reproduction.get('source'), SOURCES)}",
        f"- 시나리오: {_text(reproduction.get('scenario'))}",
        "- 한계:",
    ]
    lines.extend(_bullets(reproduction.get("limitations")))
    return lines


def _diagnosis_lines(diagnosis):
    if diagnosis is None:
        return _missing("diagnosis.json")
    lines = [
        f"- 진단 상태: {_label(diagnosis.get('status'), DIAGNOSIS_STATUSES)}",
        f"- 근본 원인: {_text(diagnosis.get('root_cause'))}",
        "- 관련 심볼:",
    ]
    lines.extend(_bullets(diagnosis.get("symbols")))
    # evidence 본문은 민감정보가 될 수 있어 건수만 남긴다.
    lines.append(f"- 진단 근거: {_count(diagnosis.get('evidence'))}건")
    return lines


def _blocks(blocks):
    lines = []
    for block in blocks:
        if lines:
            lines.append("")
        lines.extend(block)
    return lines


def _implementation_lines(names, implementations):
    blocks = []
    changed = any(
        _count((implementations.get(name) or {}).get("changed_files")) > 0 for name in names
    )
    if not changed:
        blocks.append(["- 코드 변경 없음 — 이 run은 저장소 파일을 수정하지 않았다"])
    for name in names:
        block = [f"### {name}", ""]
        implementation = implementations.get(name)
        if implementation is None:
            block.extend(_missing("implementation.json"))
        else:
            block.append(f"- 상태: {_label(implementation.get('status'), IMPLEMENTATION_STATUSES)}")
            block.append("- 변경 파일:")
            block.extend(_bullets(implementation.get("changed_files")))
            block.append(f"- RED 실행: {_count(implementation.get('red_runs'))}건")
        blocks.append(block)
    return _blocks(blocks)


def _boundary_line(verification):
    source = verification.get("source")
    if source == "automated":
        return f"- 근거 경계: 자동 검증 (자동 실행 {_count(verification.get('automated_runs'))}건)"
    if source == "user_confirmed":
        return (
            "- 근거 경계: 사용자 직접 확인 — 자동 검증 아님 "
            f"(실패한 자동 실행 {_count(verification.get('failed_automated_runs'))}건, "
            f"잔여 위험 {_count(verification.get('residual_risks'))}건)"
        )
    return f"- 근거 경계: {UNRECORDED}"


def _verification_lines(names, verifications):
    if not names:
        return _missing("verification.json")
    blocks = []
    for name in names:
        block = [f"### {name}", ""]
        verification = verifications.get(name)
        if verification is None:
            block.extend(_missing("verification.json"))
        else:
            block.append(f"- 판정: {_label(verification.get('verdict'), VERDICTS)}")
            block.append(_boundary_line(verification))
            block.append("- 채널:")
            block.extend(_bullets(verification.get("channels")))
            block.append(f"- 자동 실행 {_count(verification.get('automated_runs'))}건:")
            block.extend(_bullets(verification.get("automated_runs")))
        blocks.append(block)
    return _blocks(blocks)


def _labelled(prefix, items):
    values = items if isinstance(items, (list, tuple)) else []
    return [f"{prefix}: {value}" for value in values]


def _risk_lines(names, reproduction, diagnosis, implementations, verifications):
    failed = []
    risks = []
    blockers = _labelled("재현", (reproduction or {}).get("blockers"))
    blockers.extend(_labelled("진단", (diagnosis or {}).get("blockers")))
    for name in names:
        implementation = implementations.get(name)
        if implementation is not None:
            blockers.extend(_labelled(f"구현/{name}", implementation.get("blockers")))
        verification = verifications.get(name)
        if verification is None:
            continue
        failed.extend(_labelled(name, verification.get("failed_automated_runs")))
        risks.extend(_labelled(name, verification.get("residual_risks")))
        blockers.extend(_labelled(f"검증/{name}", verification.get("blockers")))
    lines = ["- 실패한 자동 실행:"]
    lines.extend(_bullets(failed))
    lines.append("- 잔여 위험:")
    lines.extend(_bullets(risks))
    lines.append("- 차단 사유:")
    lines.extend(_bullets(blockers))
    return lines


def _time_lines(state, metrics):
    values = dict(state)
    values.update(metrics)
    return [
        f"- 시작 시각: {_timestamp_text(values.get('started_at'))}",
        f"- 해결 시각: {_timestamp_text(values.get('resolved_at'))}",
        f"- 종료 시각: {_timestamp_text(values.get('finished_at'))}",
        f"- 총 경과 시간: {_seconds_text(values.get('elapsed_seconds'))}",
        f"- 실제 작업 시간: {_seconds_text(values.get('work_seconds'))}",
        f"- 대기 시간: {_seconds_text(values.get('wait_seconds'))}",
        f"- 정리 시간: {_seconds_text(values.get('cleanup_seconds'))}",
    ]


def final_report(run_id: str, home=None) -> str:
    directory = _run_dir(run_id, home)
    state = _read_json(directory / "state.json") or {}
    metrics = _read_json(directory / "metrics.json") or {}
    issue_report = _read_json(directory / "issue-report.json")
    reproduction = _read_json(directory / "reproduction.json")
    diagnosis = _read_json(directory / "diagnosis.json")

    names = _repositories(directory)
    implementations = {}
    verifications = {}
    for name in names:
        repository = directory / "repositories" / name
        implementations[name] = _read_json(repository / "implementation.json")
        verifications[name] = _read_json(repository / "verification.json")

    issue = (issue_report or {}).get("issue")
    issue = issue if isinstance(issue, dict) else {}
    environment = (issue_report or {}).get("environment")
    environment = environment if isinstance(environment, dict) else {}
    # environment.target과 repositories[].path는 주소·로컬 경로라 보고서에 넣지 않는다.
    documents = [reproduction, diagnosis]
    documents.extend(implementations.values())
    documents.extend(verifications.values())

    lines = [
        "# 최종 해결 보고서",
        "",
        f"- run: {_redact(run_id)}",
        f"- 이슈: {_redact(issue['id']) if isinstance(issue.get('id'), str) and issue['id'] else '식별자 없음'}",
        f"- 환경: {_text(environment.get('name'))}",
        f"- 최종 상태: {_final_status(documents, verifications)}",
        "",
        "## 증상",
        "",
    ]
    lines.extend(_symptom_lines(issue_report))
    lines.extend(["", "## 재현", ""])
    lines.extend(_reproduction_lines(reproduction))
    lines.extend(["", "## 근본 원인", ""])
    lines.extend(_diagnosis_lines(diagnosis))
    lines.extend(["", "## 해결 조치", ""])
    lines.extend(_implementation_lines(names, implementations))
    lines.extend(["", "## 검증 결과", ""])
    lines.extend(_verification_lines(names, verifications))
    lines.extend(["", "## 실패와 잔여 위험", ""])
    lines.extend(_risk_lines(names, reproduction, diagnosis, implementations, verifications))
    lines.extend(["", "## 시간", ""])
    lines.extend(_time_lines(state, metrics))
    return "\n".join(lines)


def _atomic_write(path, content, reject_symlink=False):
    if reject_symlink and path.is_symlink():
        raise ValueError("report path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return path


def write_final_report(run_id: str, home=None) -> Path:
    report = final_report(run_id, home)
    return _atomic_write(_run_dir(run_id, home) / "final-report.md", report)


# Kept in run_state.py order because report.py is also loaded as a standalone library.
HANDOFF_STAGES = (
    ("issue-report", "입력 정리"),
    ("reproduction", "재현"),
    ("diagnosis", "진단"),
    ("implementation", "구현"),
    ("verification", "검증"),
    ("publication-approval", "게시 승인"),
)
HANDOFF_MARKS = {"pending": " ", "in_progress": "~", "done": "x", "failed": "!", "blocked": "-", "skipped": "/", "unknown": "?"}
HANDOFF_TERMINAL_STATUSES = {"done", "skipped"}
HANDOFF_STAGE_ACTIONS = {"failed": "실패 상태 해소 후 재시도", "blocked": "차단 해소 후 재개", "unknown": "알 수 없는 상태를 복구"}
SHARED_ARTIFACTS = ("state.json", "metrics.json", "issue-report.json", "reproduction.json", "diagnosis.json")
REPOSITORY_ARTIFACTS = ("implementation.json", "verification.json", "commit-gate.json")

RECORDED = "기록됨"
DAMAGED = "손상됨"
UNSAFE = "안전하지 않은 경로"
INVALID = "유효하지 않음"

HANDOFF_MAX_JSON_BYTES = 4 * 1024 * 1024
HANDOFF_MAX_JSON_NESTING = 256
HANDOFF_JSON_SCAN = re.compile(r'"(?:[^"\\]+|\\.)*"?|[][{}]')
SAFE_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
SECRET_NAME = (
    r"(?:[a-z0-9]+[-_])*(?:api[-_]?key|access[-_]?key|private[-_]?key|secret[-_]?key|token|password|secret)"
)
HANDOFF_SECRET_VALUE = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s"',;&#}]+)"""
# 인계 보고서는 fresh session이 그대로 읽는 문서라 final report보다 넓은 credential 표면을 가린다.
HANDOFF_REDACTIONS = (
    (HOME_PATH, REDACTED_PATH),
    (re.compile(r"/home/[A-Za-z0-9._-]+"), REDACTED_PATH),
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@"), r"\1" + REDACTED + "@"),
    (
        re.compile(
            r"(?i)([?&](?:access[-_]?token|api[-_]?key|access[-_]?key|private[-_]?key"
            r"|token|password|secret)=)" + HANDOFF_SECRET_VALUE
        ),
        r"\1" + REDACTED,
    ),
    (
        re.compile(
            r"""(?ix)(?<![\w.])(["']?""" + SECRET_NAME + r"""["']?\s*=\s*)"""
            + HANDOFF_SECRET_VALUE
        ),
        r"\1" + REDACTED,
    ),
    (
        re.compile(
            r"""(?ix)(?<![\w.])(["']?""" + SECRET_NAME + r"""["']?\s*:\s*)"""
            + HANDOFF_SECRET_VALUE
        ),
        r"\1" + REDACTED,
    ),
    (
        re.compile(
            r"""(?ix)(?<![\w-])(["']?(?:set[-_]?cookie|cookie)["']?\s*:\s*)"""
            + HANDOFF_SECRET_VALUE
            + r"""(?:\s*;\s*[^\s"',;]+)*"""
        ),
        r"\1" + REDACTED,
    ),
    (
        re.compile(
            r"""(?ix)(?<![\w.])(["']?(?:x-api-key|api[-_]?key|access[-_]?key|private[-_]?key"""
            r"""|x-auth-token|proxy-authorization|authorization)["']?\s*[:=]\s*)"""
            r"""(?:(?:bearer|basic)\s+)?"""
            + HANDOFF_SECRET_VALUE
        ),
        r"\1" + REDACTED,
    ),
    (re.compile(r"(?i)(\bbearer\s*[:=]\s*)" + HANDOFF_SECRET_VALUE), r"\1" + REDACTED),
    (
        re.compile(
            r"""(?ix)((?:^|\s)--""" + SECRET_NAME + r"""(?:=|\s+))(?!-)"""
            + HANDOFF_SECRET_VALUE
        ),
        r"\1" + REDACTED,
    ),
    (re.compile(r"(?i)((?:^|\s)--user(?:=|\s+))(?!-)[^\s,;:]+:[^\s,;:]+"), r"\1" + REDACTED),
)


def _handoff_value(value, empty=UNRECORDED):
    # 신뢰할 수 없는 값이 heading·체크리스트 행을 위조하지 못하도록 한 줄로 접고 credential을 가린다.
    if not isinstance(value, str) or not value:
        return empty
    text = re.sub(r"\s+", " ", value.replace("\t", " ")).strip()
    for pattern, replacement in HANDOFF_REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _json_nesting_is_bounded(text):
    # 문자열 리터럴 안의 괄호는 세지 않고, 재귀 파서에 넘기기 전에 깊이를 제한한다.
    depth = 0
    for token in HANDOFF_JSON_SCAN.findall(text):
        if token in ("[", "{"):
            depth += 1
            if depth > HANDOFF_MAX_JSON_NESTING:
                return False
        elif token in ("]", "}"):
            depth -= 1
    return True


def _handoff_stat(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_mode


def _read_handoff_json(path):
    path = Path(path)
    try:
        linked = path.lstat()
    except FileNotFoundError:
        return None, UNRECORDED
    except OSError:
        return None, DAMAGED
    if not stat.S_ISREG(linked.st_mode):
        return None, UNSAFE

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None, UNSAFE
    except OSError:
        try:
            current = path.lstat()
        except FileNotFoundError:
            return None, UNSAFE
        except OSError:
            return None, DAMAGED
        return None, UNSAFE if _handoff_stat(current) != _handoff_stat(linked) else DAMAGED

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _handoff_stat(linked) != _handoff_stat(before):
            return None, UNSAFE
        content = bytearray()
        while len(content) <= HANDOFF_MAX_JSON_BYTES:
            chunk = os.read(descriptor, HANDOFF_MAX_JSON_BYTES + 1 - len(content))
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
    except FileNotFoundError:
        return None, UNSAFE
    except (OSError, MemoryError):
        return None, DAMAGED
    finally:
        os.close(descriptor)

    if not stat.S_ISREG(current.st_mode) or not (
        _handoff_stat(before) == _handoff_stat(after) == _handoff_stat(current)
    ):
        return None, UNSAFE
    if len(content) > HANDOFF_MAX_JSON_BYTES:
        return None, DAMAGED
    try:
        text = content.decode("utf-8")
        if not _json_nesting_is_bounded(text):
            return None, DAMAGED
        document = json.loads(text)
    except (UnicodeError, ValueError, RecursionError, MemoryError):
        return None, DAMAGED
    if not isinstance(document, dict):
        return None, DAMAGED
    return document, RECORDED


def _repository_json(run_dir, name, filename):
    if (
        not isinstance(name, str)
        or not SAFE_REPO_NAME.fullmatch(name)
        or not isinstance(filename, str)
        or not SAFE_ARTIFACT_NAME.fullmatch(filename)
    ):
        return None, UNSAFE
    root = Path(run_dir) / "repositories"
    repository = root / name
    if root.is_symlink() or repository.is_symlink():
        return None, UNSAFE
    if not root.is_dir() or not repository.is_dir():
        return None, UNRECORDED
    try:
        repository.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None, UNSAFE
    return _read_handoff_json(repository / filename)


GATE_OPERATION_KINDS = {"add", "copy", "delete", "modify", "rename", "typechange", "unmerged"}
GATE_FILE_MODES = {"file": {"100644", "100755"}, "symlink": {"120000"}}


def _valid_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_gate_fingerprint(fingerprint):
    if not isinstance(fingerprint, dict):
        return False
    mtime = fingerprint.get("mtime_ns")
    if fingerprint.get("type") == "deleted":
        return (
            fingerprint.get("mode") == "deleted" and fingerprint.get("sha256") is None and mtime is None
        )
    return (
        fingerprint.get("mode") in GATE_FILE_MODES.get(fingerprint.get("type"), ())
        and _valid_sha256(fingerprint.get("sha256"))
        and isinstance(mtime, int)
        and not isinstance(mtime, bool)
    )


def _safe_gate_relative(value):
    if not isinstance(value, str) or not value:
        return False
    pure = PurePosixPath(value)
    return not pure.is_absolute() and ".." not in pure.parts


def _valid_gate_operation(operation):
    if not isinstance(operation, dict):
        return False
    kind = operation.get("kind")
    expected = {"kind", "path", "source"} if kind in {"rename", "copy"} else {"kind", "path"}
    return (
        kind in GATE_OPERATION_KINDS
        and set(operation) == expected
        and _safe_gate_relative(operation.get("path"))
        and ("source" not in operation or _safe_gate_relative(operation["source"]))
    )


def _gate_operation_key(operation):
    return operation["path"], operation["kind"], operation.get("source", "")


def _valid_gate_operations(operations):
    return all(_valid_gate_operation(operation) for operation in operations) and operations == sorted(
        operations, key=_gate_operation_key
    )


def _valid_string_list(values):
    return isinstance(values, list) and all(
        isinstance(value, str) and bool(value.strip()) for value in values
    )


def _valid_commit_gate(document):
    if (
        not isinstance(document, dict)
        or document.get("version") != 1
        or isinstance(document.get("version"), bool)
        or not isinstance(document.get("repository_root"), str)
        or not document["repository_root"]
        or not isinstance(document.get("files"), dict)
        or not isinstance(document.get("operations"), list)
        or not _valid_gate_operations(document["operations"])
    ):
        return False
    if any(
        not _safe_gate_relative(relative) or not _valid_gate_fingerprint(fingerprint)
        for relative, fingerprint in document["files"].items()
    ):
        return False
    verification_file = document.get("verification_file")
    verification_digest = document.get("verification_file_sha256")
    if (
        not _valid_gate_fingerprint(verification_file)
        or verification_file.get("type") != "file"
        or not _valid_sha256(verification_digest)
        or verification_file.get("sha256") != verification_digest
    ):
        return False
    verification = document.get("verification")
    if not isinstance(verification, dict):
        return False
    source = verification.get("source")
    channels = verification.get("channels")
    automated_runs = verification.get("automated_runs")
    failed_runs = verification.get("failed_automated_runs")
    residual_risks = verification.get("residual_risks")
    return (
        verification.get("verdict") == "pass"
        and source in SOURCES
        and _valid_string_list(channels)
        and bool(channels)
        and _valid_string_list(automated_runs)
        and _valid_string_list(failed_runs)
        and _valid_string_list(residual_risks)
        and verification.get("blockers") == []
        and (
            (source == "automated" and bool(automated_runs) and not failed_runs)
            or (
                source == "user_confirmed"
                and not automated_runs
                and bool(failed_runs)
                and bool(residual_risks)
            )
        )
    )


def _dict_field(document, key):
    value = document.get(key) if isinstance(document, dict) else None
    return value if isinstance(value, dict) else {}


def _list_field(document, key):
    value = document.get(key) if isinstance(document, dict) else None
    return value if isinstance(value, list) else []


def _handoff_list_values(document, key):
    return [_handoff_value(value) for value in _list_field(document, key) if isinstance(value, str) and value]


def _category_lines(items):
    return [f"- {_handoff_value(item)}" for item in items] if items else [f"- {NONE_TEXT}"]


def _usable_lifecycle(document):
    stages = document.get("stages") if isinstance(document, dict) else None
    return isinstance(stages, dict) and all(isinstance(stage, dict) for stage in stages.values())


def _stage_items(state):
    stages = state["stages"]
    labels = dict(HANDOFF_STAGES)
    names = [name for name, _ in HANDOFF_STAGES]
    names.extend(sorted(name for name in stages if name not in labels))
    items = []
    for name in names:
        stage = stages.get(name, {})
        raw = stage.get("status", "pending")
        if name not in labels and "status" not in stage:
            status, display, actionable = "unknown", "informational (status 미기록)", False
        elif isinstance(raw, str) and raw in HANDOFF_MARKS and raw != "unknown":
            status, display, actionable = raw, raw, True
        else:
            status, display, actionable = "unknown", f"unknown ({_handoff_value(raw)})", True
        items.append({"name": name, "label": labels.get(name, name), "status": status, "status_text": display, "actionable": actionable})
    return items


def _stage_text(item):
    return f"{_handoff_value(item['name'])} — {_handoff_value(item['label'])}"


def _next_stage_action(item):
    note = HANDOFF_STAGE_ACTIONS.get(item["status"])
    return f"{_stage_text(item)} ({note})" if note else _stage_text(item)


def _valid_resolution(state):
    value = state.get("resolved_at")
    return isinstance(value, int) and not isinstance(value, bool) and state.get("resolution_source") in SOURCES


def _apply_artifact_stage_overrides(items, names, reproduction, diagnosis, implementations, verifications):
    overrides = {}

    def mark(stage, status, source):
        existing = overrides.get(stage)
        if existing is None or status == "blocked" or existing[0] != "blocked":
            overrides[stage] = status, source

    if isinstance(reproduction, dict) and reproduction.get("status") in {"blocked", "failed"}:
        mark("reproduction", reproduction["status"], "reproduction.json")
    if isinstance(diagnosis, dict) and diagnosis.get("status") == "blocked":
        mark("diagnosis", "blocked", "diagnosis.json")
    for name in names:
        if isinstance(implementations[name], dict) and implementations[name].get("status") == "blocked":
            mark("implementation", "blocked", f"repositories/{name}/implementation.json")
        verification = verifications[name]
        if not isinstance(verification, dict):
            continue
        source = f"repositories/{name}/verification.json"
        if _list_field(verification, "blockers"):
            mark("verification", "blocked", source)
        elif verification.get("verdict") == "fail":
            mark("verification", "failed", source)
    for item in items:
        if item["name"] in overrides:
            item["status"], source = overrides[item["name"]]
            item["status_text"] = f"{item['status']} ({source} 근거)"
            item["actionable"] = True


def _handoff_context(issue_report, run_id):
    issue = _dict_field(issue_report, "issue")
    environment = _dict_field(issue_report, "environment")
    lines = [
        f"- 이슈: {_handoff_value(issue.get('id'))}",
        f"- 환경 이름: {_handoff_value(environment.get('name'))}",
        f"- 환경 대상: {_handoff_value(environment.get('target'))}",
    ]
    repositories = [value for value in _list_field(issue_report, "repositories") if isinstance(value, dict)]
    for repository in repositories:
        name = repository.get("name")
        worktree = f"`<ISSUE_TUNER_HOME>/worktrees/{run_id}/{name}`" if isinstance(name, str) and SAFE_REPO_NAME.fullmatch(name) else UNRECORDED
        lines.extend(
            [
                f"- 저장소: {_handoff_value(name)}",
                f"  - 브랜치: {_handoff_value(repository.get('branch'))}",
                f"  - 소스 저장소: {_handoff_value(repository.get('path'))}",
                f"  - 작업 트리: {worktree}",
            ]
        )
    if not repositories:
        lines.append(f"- 저장소/브랜치/소스 저장소/작업 트리: {UNRECORDED}")
    return lines


def _repository_names(run_dir, issue_report):
    names = set(_repositories(run_dir))
    names.update(
        value["name"]
        for value in _list_field(issue_report, "repositories")
        if isinstance(value, dict)
        and isinstance(value.get("name"), str)
        and SAFE_REPO_NAME.fullmatch(value["name"])
    )
    return sorted(names)


def _publication_safety(status, gate):
    if status == "skipped":
        if gate == UNRECORDED:
            return "publication-approval skipped — 게시 불필요; commit-gate.json 미기록은 정상이다."
        return f"publication-approval skipped — 게시 불필요; commit-gate.json {gate} 상태는 근거로만 검토한다."
    if status == "done":
        if gate == RECORDED:
            return (
                "commit-gate.json 기록됨 — state.json상 완료된 게시 승인을 반복하지 않는다. "
                "gate만으로 게시 완료를 추론하지 않으며 게시 전 commit_gate.check가 필요하다."
            )
        if gate == INVALID:
            return "commit-gate.json 유효하지 않음 — commit_gate.record로 재생성한 뒤 commit_gate.check 전에는 게시하지 않는다."
        return (
            f"commit-gate.json {gate} — state.json과 gate 근거가 일치하지 않는다. "
            "게시 완료를 추론하지 않으며 gate 복구 후 commit_gate.check 전에는 게시하지 않는다."
        )
    if status in {"pending", "in_progress"}:
        if gate == RECORDED:
            return (
                "commit-gate.json 기록됨 — 구조 확인만 완료; 게시 직전 commit_gate.check가 필요하며 "
                "게시 완료 증거가 아니다."
            )
        if gate == UNRECORDED:
            return "commit-gate.json 미기록 — commit_gate.record 후 게시 직전 commit_gate.check가 필요하다."
        return (
            f"commit-gate.json {gate} — commit_gate.record로 재생성한 뒤 "
            "게시 직전 commit_gate.check 전에는 게시하지 않는다."
        )
    return (
        f"commit-gate.json {gate} — 게시 단계 상태를 직접 확인하고 "
        "게시 전 commit_gate.check를 통과해야 한다."
    )


def handoff_report(run_id: str, home=None) -> str:
    directory = _run_dir(run_id, home)
    artifacts = {name: _read_handoff_json(directory / name) for name in SHARED_ARTIFACTS}
    state_document, metrics, issue_report, reproduction, diagnosis = (
        artifacts[name][0] for name in SHARED_ARTIFACTS
    )
    lifecycle = _usable_lifecycle(state_document)
    state = state_document if lifecycle else {}
    metrics = metrics if lifecycle and isinstance(metrics, dict) else {}
    items = _stage_items(state) if lifecycle else []
    names = _repository_names(directory, issue_report)
    for name in names:
        for filename in REPOSITORY_ARTIFACTS:
            relative = f"repositories/{name}/{filename}"
            artifacts[relative] = _repository_json(directory, name, filename)
        gate_path = f"repositories/{name}/commit-gate.json"
        if artifacts[gate_path][1] == RECORDED and not _valid_commit_gate(artifacts[gate_path][0]):
            artifacts[gate_path] = None, INVALID
    implementations = {name: artifacts[f"repositories/{name}/implementation.json"][0] for name in names}
    verifications = {name: artifacts[f"repositories/{name}/verification.json"][0] for name in names}
    if lifecycle:
        _apply_artifact_stage_overrides(
            items, names, reproduction, diagnosis, implementations, verifications
        )
    current_item = next(
        (item for item in items if item["actionable"] and item["status"] not in HANDOFF_TERMINAL_STATUSES), None
    )
    finished = lifecycle and state.get("status") == "finished"
    if not lifecycle:
        current = next_task = UNRECORDED
    elif finished:
        current = next_task = NONE_TEXT
    elif current_item:
        current, next_task = _stage_text(current_item), _next_stage_action(current_item)
    else:
        current = NONE_TEXT
        next_task = "finish — 실행 종료" if _valid_resolution(state) else "resolve — 해결 상태 기록"

    completed, failed, blocked, risks = [], [], [], []
    for item in items:
        if item["status"] in HANDOFF_TERMINAL_STATUSES:
            suffix = (
                " (게시 불필요로 생략)"
                if item["status"] == "skipped" and item["name"] == "publication-approval"
                else " (불필요로 생략)" if item["status"] == "skipped" else ""
            )
            completed.append(_stage_text(item) + suffix)
        elif item["status"] == "failed":
            failed.append(_stage_text(item))
        elif item["status"] == "blocked":
            blocked.append(_stage_text(item))
        elif item["status"] == "unknown" and item["actionable"]:
            blocked.append(f"{_stage_text(item)} — 알 수 없는 stage status: {item['status_text']}")

    artifact_blockers = []
    for relative, document, failed_status in (
        ("reproduction.json", reproduction, True),
        ("diagnosis.json", diagnosis, True),
        *((f"repositories/{name}/implementation.json", implementations[name], False) for name in names),
    ):
        entries = [f"{relative} — {value}" for value in _handoff_list_values(document, "blockers")]
        blocked.extend(entries)
        artifact_blockers.extend(entries)
        status = document.get("status") if isinstance(document, dict) else None
        if status == "blocked":
            blocked.append(f"{relative} — status=blocked")
        elif failed_status and status == "failed":
            failed.append(f"{relative} — status=failed")
    for name in names:
        relative = f"repositories/{name}/verification.json"
        document = verifications[name]
        entries = [f"{relative} — {value}" for value in _handoff_list_values(document, "blockers")]
        blocked.extend(entries)
        artifact_blockers.extend(entries)
        failed.extend(
            f"{relative} — {value}"
            for value in _handoff_list_values(document, "failed_automated_runs")
        )
        risks.extend(
            f"{relative} — {value}" for value in _handoff_list_values(document, "residual_risks")
        )
        if isinstance(document, dict) and document.get("verdict") == "fail":
            failed.append(f"{relative} — verdict=fail")
    damaged = [path for path, (_, status) in artifacts.items() if status == DAMAGED]
    unsafe = [path for path, (_, status) in artifacts.items() if status == UNSAFE]
    blocked.extend(f"{path} — {DAMAGED}; 산출물 재생성 필요" for path in damaged)
    blocked.extend(f"{path} — {UNSAFE}; 경로 복구 필요" for path in unsafe)
    blocked.extend(
        f"{path} — {INVALID}; 재생성 필요"
        for path, (_, status) in artifacts.items()
        if status == INVALID
    )
    if not lifecycle:
        blocked.append("state.json — 실행 lifecycle을 신뢰할 수 없음; state.json 복구 필요")

    pending, confirmed = [], []
    if lifecycle and not finished:
        paused = state.get("status") == "paused"
        current_blocked = current_item is not None and current_item["status"] == "blocked"
        if paused:
            pending.append("run status=paused — 사용자 조치 후 run_state.resume 필요")
        if current_blocked:
            pending.append(f"{_stage_text(current_item)} — 차단 해소 및 재개 확인 필요")
        if (
            current_item
            and current_item["name"] == "publication-approval"
            and current_item["status"] in {"pending", "in_progress"}
        ):
            pending.append("publication-approval — 게시 승인: 명확한 사용자 게시 승인 필요")
        if paused or current_blocked:
            pending.extend(f"사용자 조치 필요 — {value}" for value in artifact_blockers)
    if isinstance(reproduction, dict) and reproduction.get("source") == "user_confirmed":
        confirmed.append(f"reproduction.json source=user_confirmed — {_handoff_value(reproduction.get('scenario'))}")
    confirmed.extend(
        f"repositories/{name}/verification.json source=user_confirmed"
        for name in names
        if isinstance(verifications[name], dict)
        and verifications[name].get("source") == "user_confirmed"
    )
    if lifecycle and state.get("resolution_source") == "user_confirmed":
        confirmed.append("state.json resolution_source=user_confirmed")

    inventory = [f"- {path}: {artifacts[path][1]}" for path in SHARED_ARTIFACTS]
    for name in names:
        inventory.extend(
            f"- repositories/{name}/{filename}: {artifacts[f'repositories/{name}/{filename}'][1]}"
            for filename in REPOSITORY_ARTIFACTS
        )
    if not names:
        inventory.extend(f"- {filename}: {UNRECORDED}" for filename in REPOSITORY_ARTIFACTS)
    verification_lines = [f"- issue-report.json 확인 채널: {value}" for value in _handoff_list_values(_dict_field(issue_report, "verification"), "channels")]
    changed_lines = []
    for name in names:
        path = f"repositories/{name}/implementation.json"
        verification_lines.extend(
            f"- {path} RED: {value}"
            for value in _handoff_list_values(implementations[name], "red_runs")
        )
        changed_lines.extend(
            f"- {path}: {value}"
            for value in _handoff_list_values(implementations[name], "changed_files")
        )
        path = f"repositories/{name}/verification.json"
        for label, key in (
            ("확인 채널", "channels"),
            ("실행/결과", "automated_runs"),
            ("실패 결과", "failed_automated_runs"),
        ):
            verification_lines.extend(
                f"- {path} {label}: {value}"
                for value in _handoff_list_values(verifications[name], key)
            )
    verification_lines = verification_lines or [f"- {UNRECORDED}"]
    changed_lines = changed_lines or [f"- {UNRECORDED}"]
    if lifecycle:
        checklist = [
            f"- [{HANDOFF_MARKS[item['status']]}] {_handoff_value(item['label'])} ({_handoff_value(item['name'])}) — {_handoff_value(item['status_text'])}"
            for item in items]
        publication = next(item["status"] for item in items if item["name"] == "publication-approval")
    else:
        checklist, publication = ["- [?] state.json lifecycle — unknown"], None
    if not lifecycle:
        resume = [
            "- state.json을 복구하고 lifecycle 상태를 검증하기 전에는 재개·종료·게시를 판단하지 않는다.",
            "- 근거 산출물은 읽기 전용으로 확인하고 누락된 lifecycle 값을 추론하지 않는다.",
        ]
    elif finished:
        resume = [
            "- run status=finished terminal — 읽기 전용 근거 검토만 수행한다.",
            "- 실패·차단·위험 기록은 과거 evidence로 보존하며 새 조치 지시로 해석하지 않는다.",
        ]
    else:
        resume = [
            "- 이 보고서를 먼저 읽고, 위 근거 산출물 JSON을 직접 확인한다.",
            f"- state.json의 현재 단계와 전체 체크리스트를 기준으로 `{_handoff_value(next_task)}`부터 재개한다.",
            "- 미기록 산출물의 내용은 추론하지 말고 해당 단계에서 새로 생성·검증한다.",
            "- 검증 명령과 결과는 verification.json 및 implementation.json의 기록만 사용한다.",
        ]
        if damaged:
            resume.append("- 손상된 산출물을 재생성하고 계약 검증을 통과하기 전에는 다음 단계로 진행하지 않는다.")
        if unsafe:
            resume.append("- 안전하지 않은 산출물 경로를 정상 regular file 경로로 복구하기 전에는 진행하지 않는다.")
        resume.extend(
            f"- {name} 게시 안전 상태: "
            f"{_publication_safety(publication, artifacts[f'repositories/{name}/commit-gate.json'][1])}"
            for name in names
        )
    lines = [
        "# 실행 인계 보고서",
        "",
        f"- run: {_handoff_value(run_id)}",
        f"- 실행 상태: {_handoff_value(state.get('status')) if lifecycle else UNRECORDED}",
        f"- 현재 단계: {_handoff_value(current, NONE_TEXT)}",
        f"- 다음 실행 가능 작업: {_handoff_value(next_task, NONE_TEXT)}",
    ]
    for heading, body in (
        ("저장소와 환경", _handoff_context(issue_report, run_id)),
        ("검증 명령과 결과", verification_lines),
        ("변경 파일", changed_lines),
        ("완료 항목", _category_lines(completed)),
        ("실패 항목", _category_lines(failed)),
        ("잔여 위험", _category_lines(risks)),
        ("차단 항목", _category_lines(blocked)),
        ("사용자 확인 필요", _category_lines(pending)),
        ("사용자 확인 근거", _category_lines(confirmed)),
        ("시간", _time_lines(state, metrics)),
        ("전체 체크리스트", checklist),
        ("근거 산출물", inventory),
        ("재개 방법", resume),
    ):
        lines.extend(["", f"## {heading}", "", *body])
    return "\n".join(lines)


def write_handoff_report(run_id: str, home=None) -> Path:
    directory = _run_dir(run_id, home)
    return _atomic_write(directory / "handoff-report.md", handoff_report(run_id, home), reject_symlink=True)
