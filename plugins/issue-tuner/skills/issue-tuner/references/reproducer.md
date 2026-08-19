# Reproducer

# Input

- 검증된 `issue-report.json`, 확인된 environment와 재현 채널
- UI이면 확인된 `origin`과 `environment.target`

# Allowed

- fresh context에서 보고된 scenario를 그대로 자동 재현한다.
- UI는 confirmed origin과 production read-only 정책 안에서 host Codex `Computer Use`만 쓴다.
- 결정적 재현은 필요한 최소 횟수만, 비결정적 재현은 같은 조건에서 최대 3회 시도한다.
- 자동 재현 실패 뒤 사용자의 명확한 직접 재현 확인은 `source: user_confirmed`로 기록하고 limitations를 보존한다. 이는 진단 진행만 허용한다.

# Forbidden

- 코드 또는 Git 변경, 게시, 누락 evidence 추론 금지
- production click/type/상태 변경과 대체 browser automation 금지
- 자동 실패를 성공으로 바꾸거나 user_confirmed의 제한을 숨기지 않는다.

# Output

허용값은 status: `reproduced`, `failed`, `blocked`; source: `automated`, `user_confirmed`다.

```json
{
  "status": "reproduced",
  "source": "automated",
  "scenario": "save form",
  "limitations": [],
  "blockers": []
}
```
