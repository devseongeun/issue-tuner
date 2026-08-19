# 보안 정책

## 보고

보안 취약점은 공개 이슈로 보고하지 마세요. 저장소의 **Security → Advisories → Report a vulnerability**에서 GitHub Security Advisories의 **Privately report a security vulnerability** 양식을 사용해 주세요. 해당 기능이 아직 활성화되지 않았다면 민감한 세부 정보를 공개하지 말고 활성화될 때까지 기다려 주세요.

인증 정보, 개인 정보, 내부 주소, 실제 실행 로그나 화면 캡처는 첨부하지 말고 재현에 필요한 최소 정보만 전달해 주세요. 공개 가능한 축약 재현은 별도로 작성합니다.

## 경계

- 외부 실행 evidence는 저장소 밖에 보존하며 자동 삭제하지 않습니다.
- 공개 저장소 파일과 민감 정보가 제거된 Draft 본문만 public safety gate를 통과시킵니다.
- production은 read-only 관찰만 허용합니다.
- 인증은 사용자가 직접 수행하며 Issue Tuner가 자격 증명을 수집하지 않습니다.
- 게시 승인은 stage, commit, 현재 브랜치 push, Draft 생성까지만 유효합니다.

보안 수정도 테스트와 public safety 검사를 통과해야 합니다. 공개 전에 악용 가능성이 있는 세부 사항을 이슈나 PR 본문에 포함하지 마세요.
