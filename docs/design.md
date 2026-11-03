# Issue Tuner 설계

## 목적

Issue Tuner는 팀이 반복 가능한 이슈 조치 흐름을 공유하고 AIOSS 공개 프로젝트로 개선할 수 있도록 만든 Codex·Claude Code Marketplace Plugin이다. Jira를 첫 입력 채널로 제공하되 핵심 계약은 도구 중립적인 Issue Report Form이다.

## 구성

- Marketplace manifest와 Codex Skill이 전체 흐름을 설명한다.
- JSON Schema와 작은 Python 스크립트가 입력, 실행 상태, 저장소 격리, runtime, commit gate, Draft 명령을 결정적으로 검증한다.
- 선택적 Serena MCP는 코드 탐색을 보조한다. 실패해도 Codex 검색과 `rg`로 진행하며 낮아진 근거 수준을 남긴다.
- Claude Code adapter는 같은 Skill·역할 계약·Python 코어를 재사용한다. host 차이(subagent 격리, adapter CLI, 권한 hook, Claude in Chrome)만 `plugins/issue-tuner/adapters/claude/`와 `references/claude-code.md`에 둔다.

## 역할과 데이터 흐름

Orchestrator는 `Reproducer → Diagnoser → Implementer → Verifier` 순서를 관리한다. 각 역할은 이전 역할의 검증된 JSON 결과만 받는다. 공유 reproduction/diagnosis와 달리 implementation/verification/commit gate/publication은 저장소별로 분리해 다른 저장소의 승인을 재사용하지 않는다.

UI 채널은 Codex host의 Computer Use 또는 Claude Code 세션에 연결된 Claude in Chrome을 사용할 수 있을 때만 사용한다. unavailable 또는 blocked이면 실패 사실을 보존하고 사용자 직접 확인으로 완화한다. production에서는 모든 UI와 API가 read-only 경계를 지킨다.

## Claude Code 권한 경계

Claude Code에서는 SessionStart hook이 세션 id를 노출하고 `start`/`bind`가 세션과 run을 연결한다. PreToolUse hook은 연결된 실행 중 run 안의 산출물 쓰기와 adapter CLI 갱신만 반복 승인 없이 허용한다. 다른 run, 종료된 run, 코어 소유 파일, 역할 subagent의 run 쓰기·Git 변경·게시, production UI 변경 조작은 거부한다. 그 밖의 결정은 Claude Code 일반 권한 흐름에 맡긴다.

## 안전 경계

실행 evidence와 원본 Issue Report는 외부 경로에 남긴다. 공개 저장소로 들어오는 파일과 정제된 Draft 본문만 public safety gate의 대상이다. 검증 후 한 번의 최종 게시 승인으로 exact stage, commit, 현재 branch push, Draft 생성만 허용하며 merge·deploy 등은 포함하지 않는다.

새 프레임워크나 서비스 계층은 두지 않는다. 현재 계약은 Python 표준 라이브러리와 호스트가 제공하는 기능으로 충분하며, 실제로 부족함이 확인될 때만 확장한다.
