#!/usr/bin/env python3
"""Claude Code hook handler for Issue Tuner.

- session-start: 세션 id를 Bash 환경(ISSUE_TUNER_CLAUDE_SESSION)에 노출해 run과 세션을 연결할 수 있게 한다.
- pre-tool-use: 세션에 연결된 실행 중 run의 산출물 갱신만 반복 승인 없이 허용하고,
  다른 run·종료된 run·코어 소유 파일 쓰기와 역할 경계 위반, production 변경 조작을 거부한다.

결정이 없으면 아무것도 출력하지 않고 Claude Code의 일반 권한 흐름에 맡긴다.
"""
import json
import os
from pathlib import Path
import re
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import issue_tuner_claude as adapter  # noqa: E402


PLUGIN_NAME = "issue-tuner"
READ_ONLY_ROLES = frozenset({"reproducer", "diagnoser", "verifier"})
ROLES = READ_ONLY_ROLES | {"implementer"}
FILE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
CHROME_PREFIX = "mcp__claude-in-chrome__"
# production에서도 허용하는 관찰 전용 브라우저 조작
CHROME_READ_ONLY_TOOLS = frozenset(
    {
        "get_page_text",
        "read_page",
        "find",
        "navigate",
        "tabs_context_mcp",
        "tabs_create_mcp",
        "read_console_messages",
        "read_network_requests",
        "list_connected_browsers",
        "select_browser",
        "switch_browser",
        "resize_window",
        "gif_creator",
    }
)
CHROME_READ_ONLY_ACTIONS = frozenset({"screenshot", "scroll", "zoom", "wait", "mouse_move", "cursor_position"})
SHELL_CONTROL = re.compile(r"[;&|<>`$\n\r(){}*?!~\\]")
GIT_MUTATION = re.compile(
    r"(?:^|[\s;&|(])git(?:\s+-C\s+\S+)?\s+"
    r"(?:add|commit|push|reset|checkout|switch|rebase|merge|cherry-pick|revert|stash|tag|restore|clean|rm|mv|am|apply|worktree)\b"
)
PUBLISH_COMMAND = re.compile(r"(?:^|[\s;&|(])(?:gh\s+pr|glab\s+mr)\b")


def _decision(kind, reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": kind,
            "permissionDecisionReason": reason,
        }
    }


def role_of(event):
    agent_type = event.get("agent_type")
    if not isinstance(agent_type, str) or not agent_type.startswith(f"{PLUGIN_NAME}:"):
        return None
    role = agent_type.rsplit(":", 1)[-1]
    return role if role in ROLES else None


def _bound(event, home):
    session = event.get("session_id")
    return adapter.bound_run(session, home) if isinstance(session, str) else None


def _runs_root(home):
    return adapter.home_dir(home) / "runs"


def _inside(path: Path, root: Path) -> bool:
    try:
        Path(os.path.normpath(path)).relative_to(Path(os.path.normpath(root)))
        return True
    except ValueError:
        return False


def file_decision(event, home=None):
    tool = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    value = tool_input.get(FILE_TOOLS[tool])
    role = role_of(event)
    if role in READ_ONLY_ROLES:
        return _decision("deny", f"Issue Tuner {role}는 파일을 수정하지 않는다(역할 경계).")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = Path(event.get("cwd") or os.getcwd()) / path
    runs_root = _runs_root(home)
    if not _inside(path, runs_root) and not _inside(path.resolve(), runs_root.resolve()):
        return None
    if role is not None:
        return _decision("deny", "run 산출물은 Orchestrator만 기록한다. 역할은 결과 JSON을 응답으로 반환한다.")
    if Path(path).name in adapter.CORE_OWNED_FILES:
        return _decision("deny", f"{Path(path).name}은 Python 코어가 소유한 산출물이라 직접 수정할 수 없다.")
    run_id = _bound(event, home)
    if run_id is None:
        return None
    relative = adapter.path_in_run(path, run_id, home)
    if relative is None:
        return _decision("deny", f"현재 세션에 연결된 run({run_id}) 밖의 Issue Tuner 산출물은 쓸 수 없다.")
    if adapter.run_status(run_id, home) not in {"running", "paused"}:
        return _decision("deny", f"run {run_id}는 실행 중이 아니라 산출물을 갱신할 수 없다.")
    return _decision("allow", f"Issue Tuner run {run_id} 산출물 갱신({relative.as_posix()})")


def _adapter_invocation(command):
    if SHELL_CONTROL.search(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) < 3 or Path(tokens[0]).name not in {"python3", "python"}:
        return None
    script = Path(os.path.normpath(tokens[1]))
    return tokens[1:], script


def bash_decision(event, home=None, plugin_root=None):
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return None
    role = role_of(event)
    if role is not None and (GIT_MUTATION.search(command) or PUBLISH_COMMAND.search(command)):
        return _decision("deny", f"Issue Tuner {role}는 Git 변경·게시를 하지 않는다. 게시는 최종 게시 승인 뒤 Orchestrator만 한다.")
    parsed = _adapter_invocation(command)
    if parsed is None:
        return None
    arguments, script = parsed
    root = Path(plugin_root or adapter.PLUGIN_ROOT)
    adapter_cli = Path(os.path.normpath(root / "adapters" / "claude" / "issue_tuner_claude.py"))
    validator = Path(os.path.normpath(root / "scripts" / "validate_contract.py"))
    if script == validator and len(arguments) == 3:
        return _decision("allow", "Issue Tuner 계약 검증(읽기 전용)")
    if script != adapter_cli or len(arguments) < 2:
        return None
    subcommand = arguments[1]
    try:
        run_id = arguments[arguments.index("--run") + 1]
    except (ValueError, IndexError):
        return None
    if subcommand in adapter.READ_ONLY_COMMANDS:
        return _decision("allow", "Issue Tuner run 상태 조회(읽기 전용)")
    if subcommand not in adapter.RUN_SCOPED_COMMANDS or role is not None:
        return None
    if _bound(event, home) != run_id:
        return None
    return _decision("allow", f"Issue Tuner run {run_id} 산출물 갱신(adapter CLI)")


def chrome_decision(event, home=None):
    run_id = _bound(event, home)
    if run_id is None:
        return None
    issue = adapter.read_issue_report(run_id, home) or {}
    environment = issue.get("environment") if isinstance(issue.get("environment"), dict) else {}
    if environment.get("name") != "production":
        return None
    tool = event["tool_name"][len(CHROME_PREFIX):]
    if tool in CHROME_READ_ONLY_TOOLS:
        return None
    if tool == "computer":
        action = (event.get("tool_input") or {}).get("action")
        if action in CHROME_READ_ONLY_ACTIONS:
            return None
    return _decision("deny", "production은 read-only 관찰만 허용한다. click/type/form/script 조작은 금지된다.")


def pre_tool_use(event, home=None, plugin_root=None):
    tool = event.get("tool_name")
    if tool in FILE_TOOLS:
        return file_decision(event, home)
    if tool == "Bash":
        return bash_decision(event, home, plugin_root)
    if isinstance(tool, str) and tool.startswith(CHROME_PREFIX):
        return chrome_decision(event, home)
    return None


def session_start(event, env_file=None):
    session = event.get("session_id")
    env_file = env_file if env_file is not None else os.environ.get("CLAUDE_ENV_FILE")
    if not env_file or not isinstance(session, str) or not adapter.SAFE_ID.fullmatch(session):
        return None
    with open(env_file, "a", encoding="utf-8") as file:
        file.write(f"export {adapter.SESSION_ENV}={session}\n")
    return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in {"session-start", "pre-tool-use"}:
        print("usage: hook.py session-start|pre-tool-use", file=sys.stderr)
        return 1
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 1
    if not isinstance(event, dict):
        return 1
    try:
        if argv[0] == "session-start":
            result = session_start(event)
        else:
            result = pre_tool_use(event, plugin_root=os.environ.get("CLAUDE_PLUGIN_ROOT"))
    except (adapter.AdapterError, ValueError, OSError) as error:
        # 판단할 수 없으면 허용하지 않고 일반 권한 흐름으로 넘긴다.
        print(f"issue-tuner hook: {error}", file=sys.stderr)
        return 1
    if result is not None:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
