---
name: issue-tuner
description: Use when Jira 키·URL 또는 Issue Report로 재현 가능한 버그, QA 결함, 운영 오류와 회귀 문제를 조치하고 사용자 승인 후 Draft PR/MR을 준비해야 할 때 사용한다.
---

# Issue Tuner

`Reproducer`/`Diagnoser`/`Implementer`/`Verifier`가 재현→진단→구현→검증한다. `Orchestrator`는 state, gate, 사용자 확인·승인을 관리하지만 role 판단을 대체하지 않는다. `references/*.md`를 따른다.

run_state.py, git_context.py, runtime.py, publish.py만 library-only다. `commit_gate.py`는 CLI다; `run_state.create/pause/resume/resolve/finish`, `git_context.detect/create_worktree`, `runtime.start_runtime/stop_owned_process`, `commit_gate.record/check`, `publish.host_kind/draft_command`를 쓰고 모든 return과 error를 확인한다.

## Workflow

1. Jira 키·URL은 Jira connector, 없으면 `Issue Report Form`으로 받는다. 누락값을 만들지 말고 `<run>/issue-report.json`으로 정규화해 `python3 <plugin-root>/scripts/validate_contract.py issue-report <run>/issue-report.json`을 실행한다.
2. `git_context.detect`로 저장소·현재 branch를 탐지하고 환경·issue별 채널과 `fix/<issue-id>`를 제안한다. worktree/runtime/수정 전 사용자 확인은 확인된 준비·조치만 승인하며 게시 승인이 아니다.
3. `production`은 read-only 관찰/snapshot/비변경 log만 허용한다. 승인해도 click/type/상태 변경 API/data mutation은 금지한다.
4. `run_state.create` 후 repo마다 `git_context.create_worktree`를 호출한다. 공유: `<run>/reproduction.json`, `<run>/diagnosis.json`; repo별: `<run>/repositories/<repo-name>/implementation.json`, `<run>/repositories/<repo-name>/verification.json`, `<run>/repositories/<repo-name>/commit-gate.json`. verification/gate를 다른 repo root에 재사용하지 않는다.
5. `.issue-tuner.json`이 없으면 한 번 묻고 추론하지 않는다. `runtime.start_runtime`을 쓴다. UI는 host Codex `Computer Use`를 fresh Reproducer/Verifier context에서 사용한다. 이동/redirect 전후 `origin`과 `environment.target`이 다르면 중단한다. credential은 취급하지 않는다. 로그인 시 `run_state.pause`, 사용자 직접 로그인, 긍정 뒤 같은 task/session만 `run_state.resume`한다. Computer Use 불가/차단은 자동화 채널 실패로 기록하고 사용자 직접 확인을 요청하며 대체 browser automation은 금지한다.
6. fresh Reproducer 후 `python3 <plugin-root>/scripts/validate_contract.py reproduction <run>/reproduction.json`을 실행한다.
7. 자동 실패는 `failed`다. 직접 재현 확인은 `source: user_confirmed`와 limitations를 보존하고 진단까지만 허용한다.
8. fresh Diagnoser는 policy 모호성을 사용자에게 묻고 Serena 실패 시 Codex search+`rg`와 낮은 evidence level을 기록한다. `python3 <plugin-root>/scripts/validate_contract.py diagnosis <run>/diagnosis.json`을 실행한다.
9. fresh Implementer는 confirmed worktree에서 test first다. 결정적 assertion RED 1회, 비결정적 동일 조건 RED 3회; 환경/설정 실패는 RED가 아니다. callers 전체의 최소 root-cause fix만 하며 무관 refactor/dependency는 금지한다. `python3 <plugin-root>/scripts/validate_contract.py implementation <run>/repositories/<repo-name>/implementation.json`을 실행한다.
10. fresh Verifier는 구현 context 없이 확인된 채널·declared commands만 실행한다. 미해소 channel failure/evidence 부족은 `fail`; Computer Use 자동화 수단 실패는 사용자 직접 검증으로 대체 가능하며 `source: user_confirmed`, `failed_automated_runs`·`residual_risks`를 보존한다. `python3 <plugin-root>/scripts/validate_contract.py verification <run>/repositories/<repo-name>/verification.json`을 실행한다. 최초 `pass` 직후 `run_state.resolve`에 검증 시각과 verification의 `source`를 넘겨 해결 근거를 한 번만 기록한다.
11. repo별 `pass`, nonempty channels, 빈 blockers 뒤에만 `commit_gate.record`로 commit gate를 만들고 게시 직전 `commit_gate.check`한다. dependency order대로 각 repo를 독립적으로 gate와 publish한다.
12. 한 번의 최종 게시 승인 prompt에 repo별 exact stage files/excluded changes, commit message, 현재 브랜치 push, Draft PR/Draft MR, dependency order, 기존 CI 시작 가능성을 적는다. 이전 긍정은 무효다.
13. 직후 명확한 긍정(`승인`,`응`,`좋아`,`진행해`)만 exact stage→gate check→commit→push→Draft를 연속 승인한다. `publish.draft_command`에서 반환된 command만 `subprocess.run(command, cwd=<confirmed repo worktree>, shell=False, check=True, capture_output=True, text=True)`로 실행하고 expected remote, branch, repo와 일치하는 Draft URL인지 검증한다. URL은 최종 보고/run evidence에 남긴다. command가 없으면 manual command/body를 제시하고 생성 성공으로 보고하지 않는다. 조건부 답변은 다시 묻는다. force push 금지, merge 금지, deploy 금지, pipeline 수동 실행 금지, reviewer 변경 금지.
14. `runtime.stop_owned_process`로 owned runtime만 멈추고 `run_state.finish`로 metrics를 마감한다. `work_seconds`와 `wait_seconds`는 해결 시각까지, `cleanup_seconds`는 해결부터 종료까지의 시간이며 세 값의 합은 `elapsed_seconds`다. run evidence를 보존하고 외부 run path를 보고한다.

- external raw evidence인 Issue Report/stage JSON에는 `check_public_safety.py`를 실행하지 않는다.
- `<run>/repositories/<repo-name>/public-artifacts`에는 public Issue Tuner repo로 넘길 raw artifact가 아닌 sanitized summary인 sanitized Draft PR/MR body만 두고 `python3 <plugin-root>/scripts/check_public_safety.py <run>/repositories/<repo-name>/public-artifacts`로 검사한다. raw run dir 검사는 금지한다.
