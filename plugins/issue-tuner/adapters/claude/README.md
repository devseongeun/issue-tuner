# Claude adapter

Claude Code에서 Issue Tuner 전체 흐름(재현 → 진단 → 구현 → 독립 검증 → 게시 승인)을 실행하기 위한 통합입니다. 역할 계약, JSON Schema, Python 안전 게이트는 Codex와 같은 파일을 그대로 쓰며 이 디렉터리는 Claude Code host에 필요한 연결부만 둡니다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `../../.claude-plugin/plugin.json` | Claude Code plugin manifest. Serena를 `claude-code` context로 시작한다 |
| `../../agents/*.md` | Reproducer·Diagnoser·Implementer·Verifier subagent. 각자 `skills/issue-tuner/references/<role>.md`를 유일한 계약으로 읽는다 |
| `issue_tuner_claude.py` | `run_state`·`report`·`validate_contract`를 호출하는 Orchestrator CLI |
| `hook.py`, `hooks.json` | 세션-run 연결, run 범위 자동 승인, 역할 경계, production read-only 강제 |
| `../../skills/issue-tuner/references/claude-code.md` | Skill이 Claude Code에서 따르는 host 대응표 |

## 설치

```text
/plugin marketplace add devseongeun/issue-tuner
/plugin install issue-tuner@issue-tuner
```

설치 후 새 세션을 시작하세요. hook의 세션 연결은 SessionStart에서 준비됩니다. `claude plugin validate plugins/issue-tuner`로 manifest를 확인할 수 있습니다.

## 사용

Jira 키·URL이나 Issue Report를 주고 "Issue Tuner로 이 이슈를 조치해줘"라고 요청하거나 `/issue-tuner:issue-tuner`를 실행합니다. Orchestrator는 단계마다 새 subagent를 띄우고, 반환된 JSON을 run 디렉터리에 저장한 뒤 CLI로 기록합니다.

```text
python3 <plugin-root>/adapters/claude/issue_tuner_claude.py start --run <run-id>
python3 <plugin-root>/adapters/claude/issue_tuner_claude.py commit-stage reproduction --run <run-id>
python3 <plugin-root>/adapters/claude/issue_tuner_claude.py commit-stage verification --run <run-id> --repo <repo-name>
python3 <plugin-root>/adapters/claude/issue_tuner_claude.py finish --run <run-id>
```

각 명령은 진행 체크리스트와 시작·해결·종료 시각을 출력하고 `handoff-report.md`를 갱신합니다. `finish`는 사람이 읽는 `final-report.md`도 씁니다.

### 다른 세션에서 재개

```text
python3 <plugin-root>/adapters/claude/issue_tuner_claude.py show --run <run-id>
python3 <plugin-root>/adapters/claude/issue_tuner_claude.py bind --run <run-id>
```

`show`는 읽기 전용으로 인계 보고서와 체크리스트를 보여 줍니다. 이어서 작업할 때만 `bind`로 새 세션을 run에 연결합니다. `/clear`로 세션 id가 바뀐 경우에도 `bind`가 필요합니다.

## 권한 범위

`start`와 `bind`는 run 범위를 정하므로 한 번 권한을 확인받습니다. 그 뒤 연결된 실행 중 run 안의 stage JSON 쓰기와 CLI 갱신은 반복 승인 없이 진행됩니다. 다른 run, 종료된 run, Python 코어 소유 파일(`state.json`, `metrics.json`, `handoff-report.md`, `final-report.md`, `commit-gate.json`) 직접 쓰기는 거부됩니다. worktree 코드 수정, Git, Draft 게시는 Claude Code의 일반 권한 흐름과 Issue Tuner의 최종 게시 승인 정책을 그대로 따릅니다.

## Codex와의 차이

| 항목 | Codex | Claude Code |
| --- | --- | --- |
| 설치 | `codex plugin marketplace add` | `/plugin marketplace add`, `/plugin install` |
| 역할 격리 | fresh context | plugin subagent, 읽기 전용 역할은 쓰기 도구 제거 + hook 거부 |
| UI 자동화 | host `Computer Use` | 세션에 연결된 Claude in Chrome. 없으면 자동화 채널 실패 기록 후 사용자 직접 확인 |
| production UI | 정책으로 read-only | 정책 + hook이 click·type·form·script 조작 거부 |
| 코어 호출 | Python library | 같은 library를 감싼 adapter CLI |
| 반복 승인 제거 | host 설정 | hook이 세션에 연결된 run으로 범위를 제한해 허용 |
| Serena context | `codex` | `claude-code` |
| 코드 검색 fallback | Codex search + `rg` | Grep·Glob + `rg` |

## 제한

- Claude in Chrome이 없는 환경(예: 원격 세션)에서는 UI 채널이 항상 사용자 직접 확인으로 전환됩니다.
- hook은 shell 연산자나 변수 확장이 섞인 CLI 호출을 자동 승인하지 않습니다.
- 관리 정책으로 plugin hook이 막힌 조직에서는 run 산출물 쓰기마다 일반 권한 확인이 나타납니다. 안전 경계는 CLI와 공통 코어가 그대로 지킵니다.
