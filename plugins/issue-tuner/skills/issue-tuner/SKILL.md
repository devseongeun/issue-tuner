---
name: issue-tuner
description: Use when Jira 키·URL 또는 Issue Report로 재현 가능한 버그, QA 결함, 운영 오류와 회귀 문제를 조치하고 사용자 승인 후 Draft PR/MR을 준비해야 할 때 사용한다.
---

# Issue Tuner

`Reproducer`/`Diagnoser`/`Implementer`/`Verifier`가 재현→진단→구현→검증한다. `Orchestrator`는 state, gate, 사용자 확인·승인을 관리하지만 role 판단을 대체하지 않는다. `references/*.md`를 따른다.

run_state.py, git_context.py, runtime.py, publish.py, report.py만 library-only다. `commit_gate.py`는 CLI다; `run_state.create/pause/resume/resolve/finish`, `git_context.detect/create_worktree`, `runtime.start_runtime/stop_owned_process`, `commit_gate.record/check`, `publish.host_kind/draft_command/review_budget/split_plan/render_budget`, `report.handoff_report/write_handoff_report`, `report.final_report/write_final_report`를 쓰고 모든 return과 error를 확인한다.

## Workflow

1. Jira 키·URL은 Jira connector, 없으면 `Issue Report Form`으로 받는다. 누락값을 만들지 말고 `<run>/issue-report.json`으로 정규화해 `python3 <plugin-root>/scripts/validate_contract.py issue-report <run>/issue-report.json`을 실행한다.
2. `git_context.detect`로 저장소·현재 branch를 탐지하고 환경·issue별 채널과 `fix/<issue-id>`를 제안한다. worktree/runtime/수정 전 사용자 확인은 확인된 준비·조치만 승인하며 게시 승인이 아니다.
3. `production`은 read-only 관찰/snapshot/비변경 log만 허용한다. 승인해도 click/type/상태 변경 API/data mutation은 금지한다.
4. `run_state.create`가 반환해 run이 시작된 직후 `report.write_handoff_report`로 `<run>/handoff-report.md`를 쓴다. 그 뒤 repo마다 `git_context.create_worktree`를 호출한다. 공유: `<run>/reproduction.json`, `<run>/diagnosis.json`; repo별: `<run>/repositories/<repo-name>/implementation.json`, `<run>/repositories/<repo-name>/verification.json`, `<run>/repositories/<repo-name>/commit-gate.json`. verification/gate를 다른 repo root에 재사용하지 않는다.
5. `.issue-tuner.json`이 없으면 한 번 묻고 추론하지 않는다. `runtime.start_runtime`을 쓴다. UI는 host Codex `Computer Use`를 fresh Reproducer/Verifier context에서 사용한다. 이동/redirect 전후 `origin`과 `environment.target`이 다르면 중단한다. credential은 취급하지 않는다. 로그인 시 `run_state.pause`, 사용자 직접 로그인, 긍정 뒤 같은 task/session만 `run_state.resume`한다. Computer Use 불가/차단은 자동화 채널 실패로 기록하고 사용자 직접 확인을 요청하며 대체 browser automation은 금지한다.
6. fresh Reproducer 후 `python3 <plugin-root>/scripts/validate_contract.py reproduction <run>/reproduction.json`을 실행한다.
7. 자동 실패는 `failed`다. 직접 재현 확인은 `source: user_confirmed`와 limitations를 보존하고 진단까지만 허용한다.
8. fresh Diagnoser는 policy 모호성을 사용자에게 묻고 Serena 실패 시 Codex search+`rg`와 낮은 evidence level을 기록한다. `python3 <plugin-root>/scripts/validate_contract.py diagnosis <run>/diagnosis.json`을 실행한다.
9. fresh Implementer는 confirmed worktree에서 test first다. 결정적 assertion RED 1회, 비결정적 동일 조건 RED 3회; 환경/설정 실패는 RED가 아니다. callers 전체의 최소 root-cause fix만 하며 무관 refactor/dependency는 금지한다. `python3 <plugin-root>/scripts/validate_contract.py implementation <run>/repositories/<repo-name>/implementation.json`을 실행한다.
10. fresh Verifier는 구현 context 없이 확인된 채널·declared commands만 실행한다. 미해소 channel failure/evidence 부족은 `fail`; Computer Use 자동화 수단 실패는 사용자 직접 검증으로 대체 가능하며 `source: user_confirmed`, `failed_automated_runs`·`residual_risks`를 보존한다. `python3 <plugin-root>/scripts/validate_contract.py verification <run>/repositories/<repo-name>/verification.json`을 실행한다. 최초 `pass` 직후 `run_state.resolve`에 검증 시각과 verification의 `source`를 넘겨 해결 근거를 한 번만 기록하고, 최초 resolve가 기록된 직후 `report.write_handoff_report`를 실행한다.
11. repo별 `pass`, nonempty channels, 빈 blockers 뒤에만 `commit_gate.record`로 commit gate를 만들고 게시 직전 `commit_gate.check`한다. dependency order대로 각 repo를 독립적으로 gate와 publish한다. gate 직후 repo마다 `publish.review_budget`으로 Review Budget을 측정하고 통과한 repo만 승인 요청으로 넘긴다.
12. 한 번의 최종 게시 승인 prompt에 repo별 exact stage files/excluded changes, commit message, 현재 브랜치 push, Draft PR/Draft MR, dependency order, 기존 CI 시작 가능성과 `publish.render_budget(budget, plan)` 블록을 적는다. 이전 긍정은 무효다.
13. 직후 명확한 긍정(`승인`,`응`,`좋아`,`진행해`)만 exact stage→gate check→commit→push→Draft를 연속 승인한다. `publish.draft_command`에서 반환된 command만 `subprocess.run(command, cwd=<confirmed repo worktree>, shell=False, check=True, capture_output=True, text=True)`로 실행하고 expected remote, branch, repo와 일치하는 Draft URL인지 검증한다. URL은 최종 보고/run evidence에 남긴다. command가 없으면 manual command/body를 제시하고 생성 성공으로 보고하지 않는다. `publish.draft_command`에는 budget을 필수 인자로 넘기고 제한 초과 예외면 Draft 생성과 push를 하지 않는다. 조건부 답변은 다시 묻는다. force push 금지, merge 금지, deploy 금지, pipeline 수동 실행 금지, reviewer 변경 금지.
14. `runtime.stop_owned_process`로 owned runtime만 멈추고 `run_state.finish`로 metrics를 마감한 직후 `report.write_handoff_report`를 실행한다. `work_seconds`와 `wait_seconds`는 해결 시각까지, `cleanup_seconds`는 해결부터 종료까지의 시간이며 세 값의 합은 `elapsed_seconds`다. run evidence를 보존하고 외부 run path를 보고한다. 이어 `report.write_final_report`로 `<run>/final-report.md`를 쓰고 그 경로를 보고한다. final-report의 생성 시점과 strictness는 handoff-report와 독립적으로 유지한다.

- external raw evidence인 Issue Report/stage JSON에는 `check_public_safety.py`를 실행하지 않는다.
- `<run>/repositories/<repo-name>/public-artifacts`에는 public Issue Tuner repo로 넘길 raw artifact가 아닌 sanitized summary인 sanitized Draft PR/MR body만 두고 `python3 <plugin-root>/scripts/check_public_safety.py <run>/repositories/<repo-name>/public-artifacts`로 검사한다. raw run dir 검사는 금지한다.

## Review Budget

- 게시 승인 요청 직전 `publish.review_budget(repo, base, head)`로 확인된 base와 현재 branch 사이 추가·삭제 줄 합계를 계산한다. 측정 없이 승인 요청하지 않는다.
- 한계는 600줄이다. 600줄은 허용하고 601줄 이상은 Draft 생성과 push를 차단한다.
- 코드·테스트·문서·설정·lockfile 등 모든 텍스트 변경을 합산하고 어떤 경로도 임의로 제외하지 않는다.
- 여러 저장소를 다루면 저장소별로 독립 측정·판정한다. 합산하거나 한 repo의 판정을 다른 repo에 옮기지 않는다.
- 초과 시 `publish.split_plan(budget)`으로 기능 단위의 독립 검증 가능한 분할안을 만들어 순서와 조각별 검증 방법과 함께 사용자에게 제시한다.
- 최종 승인 요청에는 변경 줄 수, 변경 파일, 검증 결과, 위험 요소, 예상 리뷰 범위를 표시한다.
- 600줄 이하여도 한 명이 30분 안에 검토하기 어려우면 더 작게 분할한다.
- 바이너리 등 줄 수로 측정 불가한 변경은 `unmeasurable`로 따로 표시하고 30분 검토 가능 여부를 사용자에게 확인한다.
- 제한을 우회하는 자동 예외나 강제 게시 옵션은 제공하지 않는다. 초과는 분할 후 재측정으로만 푼다.

## Stage Checklist

- 표준 stage 전환 순서는 `issue-report` → `reproduction` → `diagnosis` → `implementation` → `verification` → `publication-approval`이다.
- 각 material stage JSON을 저장하고 `run_state.set_stage_status`로 이전 stage를 종료 상태, 다음 stage를 `in_progress`로 기록한 직후 `report.write_handoff_report`로 원자적으로 갱신한다. 이어 `run_state.render_checklist` 결과를 그 전환당 한 번만 표시하며 같은 전환을 반복 출력하지 않는다.
- 상태 vocabulary는 6개를 구분해 쓴다: `pending`은 미착수, `in_progress`는 수행 중, `done`은 정상 완료, `failed`는 재현 실패나 검증 fail처럼 결과가 부정인 종료, `blocked`는 사용자 확인·로그인·환경 대기로 진행 불가, `skipped`는 수행 자체가 불필요한 경우다.
- 진단 결과 코드 변경이 필요 없으면 `implementation`을 `skipped`로 표시하고 다음 stage로 넘어간다.
- fresh session의 resume/recovery는 첫 응답 전에 `<run>/handoff-report.md`를 먼저 읽고, 이후 `report.handoff_report`의 authority인 persisted stage artifacts를 직접 확인해 저장된 전체 체크리스트와 다음 실행 가능 작업을 복원한다. conversation history에 의존하거나 누락값을 보충하지 않는다.
- `render_checklist`는 읽기 전용이다. 체크리스트 기록·표시는 기존 run 산출물 쓰기 정책을 따르며 추가 사용자 승인을 요구하지 않는다.

## Handoff Report

- `report.handoff_report`는 persisted `state.json`, `metrics.json`, Issue Report, 공유 stage JSON, 저장소별 stage JSON과 `commit-gate.json`만 근거로 삼는다. 누락은 `미기록`, 파싱 불가는 `손상됨`과 재생성 차단 항목으로 표시하고 추론하지 않는다.
- `report.write_handoff_report`는 `<run>/handoff-report.md`를 원자적으로 교체한다. run 시작, 각 material stage artifact/state transition, 최초 `run_state.resolve`, `run_state.finish` 직후 다시 쓴다.
- 상태·현재 stage·다음 작업, source repository와 symbolic worktree, branch·environment, 변경 파일, 검증 명령·결과, 실패·잔여 위험·차단 항목, 남은 사용자 확인과 persisted `user_confirmed` 근거, 시각, 체크리스트, commit gate 기반 재개 절차를 구분한다.
- `<ISSUE_TUNER_HOME>/worktrees/<run-id>/<repo-name>`는 symbolic worktree invariant이며 실제 home 경로를 기록하지 않는다. credential, auth header, token, password, secret 값은 stage JSON, handoff-report, final-report 어디에도 기록하지 않는다.
- 누락되거나 손상된 `state.json`에서 lifecycle, 현재 stage, 다음 작업, 게시 상태를 추론하지 않는다. `사용자 확인 필요`에는 남은 action만, `사용자 확인 근거`에는 persisted evidence만 둔다.
- `commit-gate.json`은 게시 완료 증거가 아니다. 게시 전 `commit_gate.check`를 다시 통과하며 누락·손상·무효 gate는 복구 전 게시를 차단한다.

## Final Report

- `report.final_report`로 본문을 만들고 `report.write_final_report`로 `<run>/final-report.md`에 쓴다. 본문을 손으로 짓지 않는다.
- 근거는 단계별 산출물 파일뿐이다. issue-report, reproduction, diagnosis, implementation, verification, commit-gate에 없는 사실을 추가하지 않는다.
- 증상, 근본 원인, 해결 조치, 검증 결과를 각각 구분해 적는다.
- 시작 시각, 해결 시각, `elapsed_seconds`의 총 경과 시간, `work_seconds`의 실제 작업 시간을 함께 적는다.
- `implementation`이 `skipped`거나 변경 파일이 없으면 코드 변경이 없었음을 명시한다.
- `failed_automated_runs`의 무관한 실패 테스트와 `residual_risks`를 그대로 남긴다. 지우거나 뭉뚱그려 숨기지 않는다.
- 항목마다 `source: user_confirmed`와 `source: automated`를 표시해 사용자 확인과 자동 검증의 근거 경계를 구분한다.
- 민감정보는 담지 않는다. credential, token, 내부 URL, 개인정보는 제외하고 sanitized summary만 적는다.
