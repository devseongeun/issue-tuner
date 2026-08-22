# Run Artifact Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow one run-scoped approval to cover repeated JSON artifact updates without granting writes outside the current run.

**Architecture:** Extend `run_state.py`, which already owns run path validation and atomic JSON writes, with one narrow `write-artifact` CLI. The approved command prefix fixes the absolute Issue Tuner home and run ID; only a validated relative artifact path and stdin JSON vary. The existing production mutation ban and final commit/push/Draft approval flow remain unchanged.

**Tech Stack:** Python 3 standard library, `unittest`, Markdown skill contract

---

### Task 1: Run-scoped JSON artifact writer

**Files:**
- Modify: `plugins/issue-tuner/scripts/run_state.py`
- Test: `plugins/issue-tuner/tests/test_run_state.py`

- [ ] **Step 1: Add failing behavior tests**

Add tests that create a run and call the wished-for API/CLI, covering repeated updates to `reproduction.json`, nested `repositories/app/verification.json`, a finished run, invalid JSON objects, absolute/traversal paths, and symlink parents/targets. Use `io.StringIO` plus `patch.object(run_state.sys, "stdin", ...)` for CLI input. Assert escaped targets are never created or changed.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest plugins/issue-tuner/tests/test_run_state.py`

Expected: FAIL because `write_artifact`/`main` do not exist.

- [ ] **Step 3: Add the minimum safe API and CLI**

Add `artifact_path(run_id, relative_path, home=None)` and `write_artifact(run_id, relative_path, data, home=None)` to `run_state.py`. The resolver must reject empty, absolute, dot-only, and `..` paths; require an existing matching run; keep the resolved target under that run; and reject symlink parents/targets. `write_artifact` must reject non-object JSON and finished runs, then reuse `_write_json`.

Add this positional CLI shape so an approval prefix cannot be overridden by duplicate options:

```text
python3 <plugin-root>/scripts/run_state.py write-artifact <absolute-home> <run-id> <relative-path>
```

Read exactly one JSON object from stdin, print the written absolute path, and use argparse exit code 2 for invalid input. Do not add dependencies or a second writer module.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest plugins/issue-tuner/tests/test_run_state.py`

Expected: all `RunStateTest` tests pass.

### Task 2: Workflow and approval contract

**Files:**
- Modify: `plugins/issue-tuner/skills/issue-tuner/SKILL.md`
- Test: `plugins/issue-tuner/tests/test_skill_contract.py`

- [ ] **Step 1: Add a failing contract test**

Assert the skill names the exact `run_state.py write-artifact <absolute-home> <run-id> <relative-path>` command, fixes the reusable approval prefix through `<run-id>`, requires all shared/repository role JSON artifacts to use it, and says same-run artifact updates do not ask again after preparation confirmation. Also assert `이전 긍정은 무효다` and the separate final publish approval language remain present.

- [ ] **Step 2: Run the contract test and verify RED**

Run: `python3 -m unittest plugins/issue-tuner/tests/test_skill_contract.py`

Expected: FAIL because the run-scoped writer contract is absent.

- [ ] **Step 3: Update the workflow text minimally**

Replace the statement that `run_state.py` is library-only with the single allowed CLI exception. In the preparation and artifact steps, require one approval for the exact prefix:

```text
python3 <plugin-root>/scripts/run_state.py write-artifact <absolute-home> <run-id>
```

Require JSON via stdin and paths relative to the current run. State explicitly that this authority excludes target worktree edits, production mutation, commit, push, Draft publication, merge, deploy, pipeline execution, and reviewer changes. Preserve steps 12-13 final publication semantics.

- [ ] **Step 4: Run the contract test and verify GREEN**

Run: `python3 -m unittest plugins/issue-tuner/tests/test_skill_contract.py`

Expected: all `SkillContractTest` tests pass.

### Task 3: Independent verification

**Files:**
- Verify all changed files above.

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m unittest discover -s plugins/issue-tuner/tests`

Expected: 0 failures and 0 errors.

- [ ] **Step 2: Run repository safety checks**

Run:

```sh
python3 plugins/issue-tuner/scripts/check_public_safety.py .
python3 -c 'import json,pathlib; [json.load(path.open()) for path in pathlib.Path(".").rglob("*.json") if ".git" not in path.parts]'
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Review acceptance criteria**

Confirm one fixed run-scoped prefix covers repeated writes, outside-run and symlink writes fail, final publication approval wording is unchanged, and production remains read-only. Record that the plugin cannot itself mutate an already-running Codex session's writable roots; the narrow CLI is the enforceable in-repo permission boundary.
