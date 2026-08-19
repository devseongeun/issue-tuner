# 기여하기

Issue Tuner는 도구 중립적인 Issue Report와 작은 Python 표준 라이브러리 스크립트를 유지합니다. Jira 외 입력도 같은 계약을 통과해야 하며, 특정 조직의 경로·티켓·인증 정보·실행 원본을 커밋하지 마세요. 테스트와 예제에는 실제 데이터를 변형한 값이 아닌 완전한 합성 fixture만 허용합니다.

변경 전 관련 호출 경로를 확인하고, 실패하는 최소 테스트를 먼저 추가한 뒤 root cause만 수정합니다. 새 의존성은 표준 라이브러리로 해결할 수 없고 현재 요구에 꼭 필요할 때만 제안합니다.

제출 전 다음을 실행하세요.

```sh
python3 -m unittest discover -s plugins/issue-tuner/tests
python3 plugins/issue-tuner/scripts/check_public_safety.py .
python3 -c 'import json,pathlib; [json.load(path.open()) for path in pathlib.Path(".").rglob("*.json") if ".git" not in path.parts]'
git diff --check
```

변경 설명에는 검증 명령과 결과, 안전 경계에 미치는 영향, 의도적으로 제외한 범위를 적어 주세요. 공개 전에는 staged diff를 직접 확인합니다.
