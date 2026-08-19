# Issue Tuner 설계

## 목적

Issue Tuner는 팀이 반복 가능한 이슈 조치 흐름을 공유하고 AIOSS 공개 프로젝트로 개선할 수 있도록 만든 Codex Marketplace Plugin이다. Jira를 첫 입력 채널로 제공하되 핵심 계약은 도구 중립적인 Issue Report Form이다.

## 구성

- Marketplace manifest와 Codex Skill이 전체 흐름을 설명한다.
- JSON Schema와 작은 Python 스크립트가 입력, 실행 상태, 저장소 격리, runtime, commit gate, Draft 명령을 결정적으로 검증한다.
- 선택적 Serena MCP는 코드 탐색을 보조한다. 실패해도 Codex 검색과 `rg`로 진행하며 낮아진 근거 수준을 남긴다.
- Claude adapter 디렉터리는 호환성 검토를 위한 자리만 제공하며 현재 동작을 약속하지 않는다.

## 역할과 데이터 흐름

Orchestrator는 `Reproducer → Diagnoser → Implementer → Verifier` 순서를 관리한다. 각 역할은 이전 역할의 검증된 JSON 결과만 받는다. 공유 reproduction/diagnosis와 달리 implementation/verification/commit gate/publication은 저장소별로 분리해 다른 저장소의 승인을 재사용하지 않는다.

UI 채널은 Codex host의 Computer Use를 사용할 수 있을 때만 사용한다. unavailable 또는 blocked이면 실패 사실을 보존하고 사용자 직접 확인으로 완화한다. production에서는 모든 UI와 API가 read-only 경계를 지킨다.

## 안전 경계

실행 evidence와 원본 Issue Report는 외부 경로에 남긴다. 공개 저장소로 들어오는 파일과 정제된 Draft 본문만 public safety gate의 대상이다. 검증 후 한 번의 최종 게시 승인으로 exact stage, commit, 현재 branch push, Draft 생성만 허용하며 merge·deploy 등은 포함하지 않는다.

새 프레임워크나 서비스 계층은 두지 않는다. 현재 계약은 Python 표준 라이브러리와 호스트가 제공하는 기능으로 충분하며, 실제로 부족함이 확인될 때만 확장한다.
