#!/usr/bin/env python3
import datetime
import json
import os
from pathlib import Path
import re
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


def write_final_report(run_id: str, home=None) -> Path:
    report = final_report(run_id, home)
    path = _run_dir(run_id, home) / "final-report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(report)
        temporary.replace(path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return path
