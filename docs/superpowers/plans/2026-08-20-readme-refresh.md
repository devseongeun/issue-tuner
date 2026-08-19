# README Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** README의 지원 범위와 Issue Tuner 처리 흐름을 자연스러운 한국어와 정적 이미지로 설명한다.

**Architecture:** 제품 동작은 건드리지 않는다. `README.md`는 설명과 이미지 참조만 맡고, `docs/assets/issue-tuner-flow.svg`는 입력부터 승인까지의 수직 흐름과 미승인 반복 경로를 담는다.

**Tech Stack:** Markdown, SVG 1.1, Python 표준 라이브러리 XML parser

---

### Task 1: 처리 흐름 이미지

**Files:**
- Create: `docs/assets/issue-tuner-flow.svg`

- [x] **Step 1: SVG를 작성한다**

  720×1040 캔버스에 `Jira / Issue Report 입력`, `Reproducer · 재현`, `Diagnoser · 진단`, `Implementer · 수정`, `Verifier · 독립 검증`, `게시 승인 요청`을 세로로 배치한다. 승인 화살표는 `Draft PR/MR`로, 미승인 화살표는 `요청 보완`을 거쳐 `게시 승인 요청`으로 돌아가게 한다. `<title>`과 `<desc>`를 포함한다.

- [x] **Step 2: XML 문법을 검사한다**

  Run: `python3 -c 'import xml.etree.ElementTree as ET; ET.parse("docs/assets/issue-tuner-flow.svg")'`

  Expected: exit code 0, no output

### Task 2: README 개편과 윤문

**Files:**
- Modify: `README.md`
- Modify: `plugins/issue-tuner/tests/test_public_docs.py`
- Create: `_workspace/2026-08-20-001/final.md`

- [x] **Step 1: 지원 상태와 이미지 참조를 반영한다**

  첫 문단에서 AIOSS 문장을 삭제하고 다음 뜻을 담는다.

  ```markdown
  Issue Tuner는 Codex에서 이슈 재현부터 Draft PR/MR 준비까지 이어 주는 Marketplace Plugin입니다. 현재 Codex 기준으로 만들었으며 Claude 지원은 준비 중입니다.
  ```

  `## 사용` 앞에 다음 이미지 참조를 추가한다.

  ```markdown
  ## 처리 흐름

  <img src="docs/assets/issue-tuner-flow.svg" alt="Issue 입력부터 재현, 진단, 수정, 독립 검증, 게시 승인까지 이어지며 미승인 시 보완 후 승인을 다시 요청하는 Issue Tuner 흐름" width="720">
  ```

- [x] **Step 2: 한국어 문장을 보수적으로 윤문한다**

  제품명, 명령, 수치, 경로, 승인 범위는 그대로 둔다. 번역투와 기계적인 병렬 표현만 고치고 변경률은 30% 이하로 유지한다. 결과와 `HUMANIZE-SUMMARY`를 `_workspace/2026-08-20-001/final.md`에 저장한다.

- [x] **Step 3: 요구사항과 문서 형식을 검사한다**

  README의 고정 문구를 검사하는 테스트는 새 지원 문구와 이미지 경로를 확인하고, README에 `AIOSS`가 남지 않았는지 검사하도록 갱신한다.

  Run: `rg -n "Codex 기준|Claude 지원|issue-tuner-flow.svg" README.md && ! rg -n "AIOSS" README.md`

  Expected: 앞의 세 패턴은 검색되고 `AIOSS`는 검색되지 않는다.

  Run: `git diff --check -- README.md docs/assets/issue-tuner-flow.svg`

  Expected: exit code 0, no output
