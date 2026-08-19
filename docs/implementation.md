# 구현 개요

## 저장소 구조

```text
.agents/plugins/marketplace.json       Marketplace 진입점
plugins/issue-tuner/.codex-plugin/     Plugin metadata
plugins/issue-tuner/skills/            Orchestrator와 역할 계약
plugins/issue-tuner/schemas/           Issue/stage JSON Schema
plugins/issue-tuner/scripts/           표준 라이브러리 안전 게이트
plugins/issue-tuner/tests/             실행 가능한 계약 테스트
plugins/issue-tuner/adapters/claude/   향후 adapter 자리
```

## 실행 개요

1. Jira connector 또는 Issue Report Form을 공통 JSON으로 정규화하고 검증한다.
2. 저장소와 현재 branch를 감지한 뒤 사용자에게 환경, 채널, `fix/<issue-id>` 제안을 확인받는다.
3. 외부 run 디렉터리를 만들고 저장소마다 격리 worktree를 준비한다.
4. 재현, 진단, test-first 최소 수정, 독립 검증 결과를 각각 계약 검증한다.
5. 저장소별 verification이 통과한 경우에만 commit gate를 기록한다.
6. 최종 게시 승인을 받은 정확한 파일만 stage하고 gate를 다시 확인한 뒤 commit, 현재 branch push, Draft를 순서대로 실행한다.
7. owned runtime만 종료하고 metrics와 evidence 경로를 보고한다.

`.issue-tuner.json`은 저장소별 runtime 시작 명령과 준비 경로만 선언한다. 설정이 없으면 추론하지 않고 사용자에게 한 번 묻는다. UI 검증은 Codex host의 Computer Use를 사용하며 대체 브라우저 자동화 계층은 두지 않는다.

## 검증

CI와 로컬 검증은 macOS에서 unit test, public safety scan, JSON 구문 확인만 수행한다. 실제 Jira, UI, Git host 게시를 CI에서 호출하지 않는다.
