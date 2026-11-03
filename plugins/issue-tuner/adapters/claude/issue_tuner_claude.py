#!/usr/bin/env python3
"""Claude Code adapter CLI for Issue Tuner.

Claude Code는 Python library를 직접 import하지 못하고 Bash tool로만 실행한다.
이 CLI는 공통 코어(run_state, report, validate_contract)를 그대로 호출하는 얇은 진입점이며
Codex 전용 로직을 복제하지 않는다. 모든 쓰기는 `<ISSUE_TUNER_HOME>/runs/<run-id>/` 안에서만 일어난다.
"""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time


ADAPTER_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = ADAPTER_DIR.parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"

SESSION_ENV = "ISSUE_TUNER_CLAUDE_SESSION"
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
SAFE_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")

STAGE_ORDER = (
    "issue-report",
    "reproduction",
    "diagnosis",
    "implementation",
    "verification",
    "publication-approval",
)
SHARED_STAGE_FILES = {
    "issue-report": "issue-report.json",
    "reproduction": "reproduction.json",
    "diagnosis": "diagnosis.json",
}
REPOSITORY_STAGE_FILES = {
    "implementation": "implementation.json",
    "verification": "verification.json",
}
# Python 코어만 쓰는 산출물. Claude의 Write/Edit로 직접 바꾸면 gate와 시각 기록을 우회하게 된다.
CORE_OWNED_FILES = frozenset(
    {"state.json", "metrics.json", "handoff-report.md", "final-report.md", "commit-gate.json"}
)


class AdapterError(Exception):
    pass


def _load(name):
    module_name = f"issue_tuner_core_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


run_state = _load("run_state")
report = _load("report")
validate_contract = _load("validate_contract")


# ---------------------------------------------------------------- paths


def home_dir(home=None) -> Path:
    value = home if home is not None else os.environ.get("ISSUE_TUNER_HOME") or Path.home() / ".issue-tuner"
    value = Path(value)
    if not value.is_absolute():
        raise AdapterError("ISSUE_TUNER_HOME must be absolute")
    return value


def run_dir(run_id, home=None) -> Path:
    # 경로 검증은 run_state의 규칙(안전한 run_id, symlink 금지, runs root 이탈 금지)을 그대로 쓴다.
    return run_state._run_dir(run_id, str(home_dir(home)))


def _no_symlink_between(base: Path, target: Path) -> bool:
    try:
        relative = target.relative_to(base)
    except ValueError:
        return False
    current = base
    if current.is_symlink():
        return False
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def path_in_run(path, run_id, home=None):
    """Return the path relative to the run directory, or None when it is outside or unsafe."""
    path = Path(path)
    if not path.is_absolute():
        return None
    try:
        directory = run_dir(run_id, home)
    except ValueError:
        return None
    normalized = Path(os.path.normpath(path))
    # home 자체가 symlink(예: macOS의 /var → /private/var)여도 같은 run으로 판정한다.
    raw = Path(os.path.normpath(home_dir(home) / "runs" / run_id))
    base = raw if _under(normalized, raw) else directory if _under(normalized, directory) else None
    if base is None or normalized == base or not _no_symlink_between(base, normalized):
        return None
    if not _under(Path(os.path.realpath(normalized)), Path(os.path.realpath(directory))):
        return None
    return normalized.relative_to(base)


def stage_path(run_id, stage, repo=None, home=None) -> Path:
    directory = run_dir(run_id, home)
    if stage in SHARED_STAGE_FILES:
        if repo is not None:
            raise AdapterError(f"{stage} is shared and does not take --repo")
        return directory / SHARED_STAGE_FILES[stage]
    if stage in REPOSITORY_STAGE_FILES:
        if not isinstance(repo, str) or not SAFE_REPO.fullmatch(repo):
            raise AdapterError(f"{stage} requires a safe --repo name")
        return directory / "repositories" / repo / REPOSITORY_STAGE_FILES[stage]
    raise AdapterError(f"unknown material stage: {stage}")


# ---------------------------------------------------------------- session binding


def session_id_from_env():
    value = os.environ.get(SESSION_ENV)
    if value and SAFE_ID.fullmatch(value):
        return value
    return None


def _binding_path(session_id, home=None) -> Path:
    if not isinstance(session_id, str) or not SAFE_ID.fullmatch(session_id):
        raise AdapterError("session id must be a safe identifier")
    root = home_dir(home) / "claude-sessions"
    if root.is_symlink():
        raise AdapterError("claude-sessions root must not be a symlink")
    return root / f"{session_id}.json"


def _atomic_json(path: Path, data) -> None:
    if path.is_symlink():
        raise AdapterError(f"refusing to write through symlink: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def bind(session_id, run_id, home=None) -> None:
    run_dir(run_id, home)
    _atomic_json(_binding_path(session_id, home), {"run_id": run_id})


def unbind(session_id, run_id, home=None) -> None:
    path = _binding_path(session_id, home)
    if bound_run(session_id, home) == run_id:
        path.unlink(missing_ok=True)


def bound_run(session_id, home=None):
    try:
        path = _binding_path(session_id, home)
    except AdapterError:
        return None
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run_id = data.get("run_id") if isinstance(data, dict) else None
    if isinstance(run_id, str) and SAFE_ID.fullmatch(run_id):
        return run_id
    return None


def run_status(run_id, home=None):
    path = run_dir(run_id, home) / "state.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state.get("status") if isinstance(state, dict) else None


def read_issue_report(run_id, home=None):
    path = run_dir(run_id, home) / "issue-report.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- stage logic


def _strict_json(path: Path):
    if path.is_symlink() or not path.is_file():
        raise AdapterError(f"{path.name} must be an existing regular file")
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file, parse_constant=validate_contract._reject_constant)
    except (OSError, UnicodeError, ValueError):
        raise AdapterError(f"{path.name} is not valid JSON") from None


def _validated(kind, path):
    data = _strict_json(path)
    errors = validate_contract.validate(kind, data)
    if errors:
        raise AdapterError(f"{path.name} contract errors: " + "; ".join(errors))
    return data


def repository_names(run_id, home=None):
    issue = read_issue_report(run_id, home)
    if not issue:
        raise AdapterError("issue-report.json must be committed before repository stages")
    names = [repo.get("name") for repo in issue.get("repositories", []) if isinstance(repo, dict)]
    return names


def _stage_statuses(run_id, home=None):
    return {item["stage"]: item["status"] for item in run_state.checklist(run_id, str(home_dir(home)))}


def _set(run_id, stage, status, home=None):
    run_state.set_stage_status(run_id, stage, status, home=str(home_dir(home)))


def _advance(run_id, stage, home=None):
    _set(run_id, stage, "done", home)
    index = STAGE_ORDER.index(stage)
    if index + 1 < len(STAGE_ORDER):
        following = STAGE_ORDER[index + 1]
        if _stage_statuses(run_id, home).get(following) == "pending":
            _set(run_id, following, "in_progress", home)


def _require_active(run_id, home=None):
    status = run_status(run_id, home)
    if status is None:
        raise AdapterError(f"run not found or state.json unreadable: {run_id}")
    if status == "finished":
        raise AdapterError("finished runs are read-only")
    return status


def _repo_documents(run_id, stage, home=None):
    documents = {}
    for name in repository_names(run_id, home):
        path = stage_path(run_id, stage, name, home)
        if path.is_file() and not path.is_symlink():
            documents[name] = _validated(stage, path)
    return documents


def commit_stage(run_id, stage, repo=None, now=None, home=None) -> dict:
    """Validate the persisted stage JSON and record the matching checklist transition."""
    now = int(time.time()) if now is None else now
    _require_active(run_id, home)
    path = stage_path(run_id, stage, repo, home)
    data = _validated(stage, path)
    result = {"stage": stage, "path": str(path), "resolved": False}

    if stage == "issue-report":
        names = [repository["name"] for repository in data["repositories"]]
        bad = [name for name in names if not SAFE_REPO.fullmatch(name)]
        if bad or len(set(names)) != len(names):
            raise AdapterError("repository names must be unique safe filename components")
        _advance(run_id, stage, home)
    elif stage == "reproduction":
        if data["status"] == "reproduced":
            _advance(run_id, stage, home)
        else:
            _set(run_id, stage, data["status"], home)
    elif stage == "diagnosis":
        if data["status"] == "diagnosed":
            _advance(run_id, stage, home)
        else:
            _set(run_id, stage, "blocked", home)
    elif stage == "implementation":
        if data["repository"] != repo:
            raise AdapterError("implementation.repository must match --repo")
        documents = _repo_documents(run_id, stage, home)
        names = repository_names(run_id, home)
        if any(document["status"] == "blocked" for document in documents.values()):
            _set(run_id, stage, "blocked", home)
        elif all(documents.get(name, {}).get("status") == "implemented" for name in names):
            _advance(run_id, stage, home)
        else:
            _set(run_id, stage, "in_progress", home)
    elif stage == "verification":
        documents = _repo_documents(run_id, stage, home)
        names = repository_names(run_id, home)
        if any(document["verdict"] == "fail" for document in documents.values()):
            _set(run_id, stage, "failed", home)
        elif all(documents.get(name, {}).get("verdict") == "pass" for name in names):
            _advance(run_id, stage, home)
            sources = {documents[name]["source"] for name in names}
            source = "user_confirmed" if "user_confirmed" in sources else "automated"
            state = run_state.resolve(run_id, now, source, home=str(home_dir(home)))
            result["resolved"] = state.get("resolved_at") == now
        else:
            _set(run_id, stage, "in_progress", home)
    report.write_handoff_report(run_id, home=str(home_dir(home)))
    return result


# ---------------------------------------------------------------- rendering


def _iso(value):
    if not isinstance(value, int) or isinstance(value, bool):
        return "미기록"
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat()


def times_block(run_id, home=None) -> str:
    path = run_dir(run_id, home) / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return "\n".join(
        (
            "## 시각",
            f"- 시작: {_iso(state.get('started_at'))}",
            f"- 해결: {_iso(state.get('resolved_at'))}",
            f"- 종료: {_iso(state.get('finished_at'))}",
        )
    )


def status_block(run_id, home=None) -> str:
    return "\n\n".join(
        (
            run_state.render_checklist(run_id, str(home_dir(home))),
            times_block(run_id, home),
            f"- run 경로: {run_dir(run_id, home)}",
        )
    )


# ---------------------------------------------------------------- commands


def cmd_start(args, home=None):
    now = int(time.time())
    try:
        run_state.create(args.run, now, home=str(home_dir(home)))
    except FileExistsError:
        raise AdapterError(f"run already exists: {args.run} — 재개는 bind를 사용한다") from None
    _set(args.run, "issue-report", "in_progress", home)
    report.write_handoff_report(args.run, home=str(home_dir(home)))
    session = session_id_from_env()
    if session:
        bind(session, args.run, home)
    lines = [
        f"run 시작: {args.run}",
        f"- 세션 연결: {'연결됨' if session else '연결 안 됨(run 산출물 쓰기마다 권한 확인이 필요할 수 있다)'}",
        f"- Issue Report 저장 위치: {run_dir(args.run, home) / 'issue-report.json'}",
        "",
        status_block(args.run, home),
    ]
    return "\n".join(lines)


def cmd_commit_stage(args, home=None):
    result = commit_stage(args.run, args.stage, args.repo, home=home)
    lines = [f"{args.stage} 기록 완료: {result['path']}"]
    if result["resolved"]:
        lines.append("- 해결 시각을 기록했다 (run_state.resolve).")
    lines.extend(["", status_block(args.run, home)])
    return "\n".join(lines)


def cmd_transition(args, home=None):
    _require_active(args.run, home)
    if args.stage not in STAGE_ORDER:
        raise AdapterError(f"unknown stage: {args.stage}")
    if args.stage in SHARED_STAGE_FILES or args.stage in REPOSITORY_STAGE_FILES:
        if args.status in {"done", "failed"}:
            raise AdapterError("material stage는 commit-stage로만 done/failed가 된다")
    _set(args.run, args.stage, args.status, home)
    if args.status in {"done", "skipped"}:
        index = STAGE_ORDER.index(args.stage)
        if index + 1 < len(STAGE_ORDER):
            following = STAGE_ORDER[index + 1]
            if _stage_statuses(args.run, home).get(following) == "pending":
                _set(args.run, following, "in_progress", home)
    report.write_handoff_report(args.run, home=str(home_dir(home)))
    return f"{args.stage} → {args.status}\n\n" + status_block(args.run, home)


def cmd_pause(args, home=None):
    _require_active(args.run, home)
    run_state.pause(args.run, int(time.time()), home=str(home_dir(home)))
    report.write_handoff_report(args.run, home=str(home_dir(home)))
    return "run 일시정지(사용자 대기)\n\n" + status_block(args.run, home)


def cmd_resume(args, home=None):
    _require_active(args.run, home)
    run_state.resume(args.run, int(time.time()), home=str(home_dir(home)))
    report.write_handoff_report(args.run, home=str(home_dir(home)))
    return "run 재개\n\n" + status_block(args.run, home)


def cmd_finish(args, home=None):
    if run_status(args.run, home) is None:
        raise AdapterError(f"run not found or state.json unreadable: {args.run}")
    run_state.finish(args.run, int(time.time()), home=str(home_dir(home)))
    handoff = report.write_handoff_report(args.run, home=str(home_dir(home)))
    final = report.write_final_report(args.run, home=str(home_dir(home)))
    session = session_id_from_env()
    if session:
        unbind(session, args.run, home)
    return "\n".join(
        (
            "run 종료",
            f"- 인계 보고서(AI용): {handoff}",
            f"- 최종 보고서(사람용): {final}",
            "",
            status_block(args.run, home),
        )
    )


def cmd_bind(args, home=None):
    _require_active(args.run, home)
    session = session_id_from_env()
    if not session:
        raise AdapterError(f"{SESSION_ENV} is not set; Issue Tuner SessionStart hook did not run")
    bind(session, args.run, home)
    return f"현재 세션을 run {args.run}에 연결했다.\n\n" + cmd_show(args, home)


def cmd_show(args, home=None):
    directory = run_dir(args.run, home)
    handoff = directory / "handoff-report.md"
    if handoff.is_symlink() or not handoff.is_file():
        body = "handoff-report.md 미기록 — persisted stage artifacts를 직접 확인한다."
    else:
        body = handoff.read_text(encoding="utf-8")
    return body.rstrip() + "\n\n" + status_block(args.run, home)


def cmd_status(args, home=None):
    return status_block(args.run, home)


COMMANDS = {
    "start": cmd_start,
    "commit-stage": cmd_commit_stage,
    "transition": cmd_transition,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "finish": cmd_finish,
    "bind": cmd_bind,
    "show": cmd_show,
    "status": cmd_status,
}
# 실행 중 run 산출물만 갱신하므로 Claude Code hook이 반복 승인 없이 허용하는 subcommand
RUN_SCOPED_COMMANDS = frozenset({"commit-stage", "transition", "pause", "resume", "finish"})
READ_ONLY_COMMANDS = frozenset({"show", "status"})


def parser():
    root = argparse.ArgumentParser(prog="issue_tuner_claude.py", description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = sub.add_parser(name)
        command.add_argument("--run", required=True)
        if name == "commit-stage":
            command.add_argument("stage", choices=tuple(SHARED_STAGE_FILES) + tuple(REPOSITORY_STAGE_FILES))
            command.add_argument("--repo")
        if name == "transition":
            command.add_argument("--stage", required=True, choices=STAGE_ORDER)
            command.add_argument("--status", required=True, choices=tuple(run_state.CHECKLIST_STATUSES))
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if not SAFE_ID.fullmatch(args.run):
            raise AdapterError("--run must be a safe identifier")
        print(COMMANDS[args.command](args))
    except (AdapterError, ValueError, FileNotFoundError) as error:
        print(f"issue-tuner: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
