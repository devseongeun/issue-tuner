# Claude Code host

이 문서는 Issue Tuner를 Claude Code에서 실행할 때의 host 대응만 정한다. workflow 순서, 역할 계약, gate, 게시 승인, production 정책은 `SKILL.md`와 역할 reference가 그대로 적용되며 이 문서가 완화하지 않는다. `<plugin-root>`는 Claude Code가 `SKILL.md`에 치환해 보여 준 plugin 경로다.

## Host 대응

| 공통 계약 | Codex | Claude Code |
| --- | --- | --- |
| fresh role context | 새 context | 단계마다 새 `Agent` 호출로 `issue-tuner:reproducer`, `issue-tuner:diagnoser`, `issue-tuner:implementer`, `issue-tuner:verifier` subagent를 실행 |
| UI 자동화 | host `Computer Use` | 세션에 이미 연결된 host 제공 Claude in Chrome 도구(`mcp__claude-in-chrome__*`)만 사용 |
| 코드 탐색 fallback | Codex search + `rg` | Grep·Glob tool + `rg` |
| `run_state`·`report` 호출 | library 함수 | `<plugin-root>/adapters/claude/issue_tuner_claude.py` CLI |
| run 산출물 반복 승인 제거 | host 설정 | Issue Tuner `PreToolUse` hook이 세션에 연결된 run으로 범위를 제한해 허용 |

## 역할 subagent

- 이전 역할 subagent를 재사용하거나 이어서 호출하지 않는다. Verifier는 반드시 Implementer와 다른 새 subagent다.
- subagent prompt에는 run 경로, 검증된 이전 stage JSON 경로, 확인된 environment·채널·declared commands, 대상 worktree만 넣는다. Orchestrator의 추론, 다른 역할의 작업 기록, 구현 diff 설명을 넣지 않는다. Verifier에는 구현 diff를 `git diff`로 직접 보게 한다.
- Reproducer·Diagnoser·Verifier는 파일 쓰기 도구가 없다. 모든 역할은 Git 변경·게시와 run 디렉터리 쓰기를 hook으로 거부당한다.
- 역할이 반환한 JSON 객체만 Orchestrator가 정해진 경로에 그대로 저장한다. 내용을 고치거나 보충하지 않는다.

## Orchestrator CLI

`CLI`는 `python3 "<plugin-root>/adapters/claude/issue_tuner_claude.py"`다. 출력의 체크리스트와 시각 블록은 전환마다 한 번만 사용자에게 보여 준다.

- `CLI start --run <run-id>` — `run_state.create`, 최초 `report.write_handoff_report`, 현재 세션과 run 연결. run-id는 `<issue-id>-<YYYYMMDDHHMM>`처럼 영문·숫자·`-`·`_`만 쓴다. 첫 실행에서 한 번 권한을 확인받는다.
- Issue Report를 `<run>/issue-report.json`에 Write로 저장한 뒤 `CLI commit-stage issue-report --run <run-id>`.
- 역할 결과를 `<run>/reproduction.json`, `<run>/diagnosis.json`, `<run>/repositories/<repo-name>/implementation.json`, `<run>/repositories/<repo-name>/verification.json`에 저장한 뒤 `CLI commit-stage <stage> --run <run-id> [--repo <repo-name>]`. CLI가 `validate_contract.py` 계약 검증, `run_state.set_stage_status`, 다음 stage `in_progress`, `report.write_handoff_report`를 이 순서로 수행한다.
- 모든 저장소의 verification이 `pass`가 되면 `commit-stage verification`이 `run_state.resolve`로 해결 시각과 source를 한 번만 기록한다. 하나라도 `user_confirmed`면 source는 `user_confirmed`다.
- 코드 변경이 필요 없으면 `CLI transition --run <run-id> --stage implementation --status skipped`. 게시 승인 결과, 사용자 확인 대기 같은 비-material 전환도 `transition`을 쓴다. material stage의 `done`·`failed`는 `commit-stage`로만 기록된다.
- 로그인 대기는 `CLI pause --run <run-id>`, 사용자 직접 로그인 뒤 같은 task/session에서 `CLI resume --run <run-id>`.
- 종료는 `CLI finish --run <run-id>`. `run_state.finish` 직후 handoff-report를 쓰고 이어서 final-report를 쓰며 두 경로와 시작·해결·종료 시각을 출력한다.
- `git_context`, `runtime`, `publish`, `commit_gate`는 공통 코어를 그대로 쓴다. 이들은 사용자 확인 또는 최종 게시 승인 대상이므로 Claude Code의 일반 권한 확인을 거친다.

## 권한 범위

| 대상 | 처리 |
| --- | --- |
| 세션에 연결된 실행 중 run 안의 stage JSON·public-artifacts 쓰기 | 반복 승인 없이 허용 |
| 연결된 run에 대한 `commit-stage`·`transition`·`pause`·`resume`·`finish`, `validate_contract.py`, `show`·`status` | 반복 승인 없이 허용 |
| `start`, `bind` | 일반 권한 확인(run 범위를 정하는 동작) |
| 다른 run, 종료된 run, `state.json`·`metrics.json`·`handoff-report.md`·`final-report.md`·`commit-gate.json` 직접 쓰기 | 거부 |
| 역할 subagent의 run 쓰기, Reproducer·Diagnoser·Verifier의 파일 쓰기, 모든 역할의 Git 변경·게시 | 거부 |
| production run의 Claude in Chrome click·type·form·script 조작 | 거부(관찰·snapshot만 허용) |
| worktree 코드 수정, Git, Draft 게시 등 나머지 | Claude Code 일반 권한 흐름과 Issue Tuner 승인 정책 |

hook은 shell 연산자, 변수 확장, 리다이렉션이 섞인 명령을 허용하지 않는다. CLI는 인자만 붙여 단독으로 실행한다.

## UI 자동화가 없을 때

Claude in Chrome 도구가 세션에 없거나 연결·조작이 실패하면 자동화 채널 실패로 기록한다. Reproducer는 `limitations`, Verifier는 `failed_automated_runs`와 `residual_risks`에 실패 근거를 남긴다. 이어 사용자 직접 재현 또는 검증을 요청하고, 명확한 확인만 `source: user_confirmed`로 기록한다. 별도 브라우저 자동화 도구 설치, headless browser, 임의 스크립트 같은 대체 browser automation은 금지한다.

## 다른 세션에서 재개

1. 첫 응답 전에 `CLI show --run <run-id>`로 `<run>/handoff-report.md`와 체크리스트·시각을 먼저 읽는다.
2. persisted stage artifacts를 Read로 직접 확인한다. conversation history에 의존하거나 누락값을 보충하지 않는다.
3. 이어서 작업할 때만 `CLI bind --run <run-id>`로 새 세션을 run에 연결한다. 이후 run 산출물 갱신은 다시 반복 승인 없이 진행된다.
4. 종료된 run은 bind할 수 없으며 읽기 전용 근거 검토만 한다.
