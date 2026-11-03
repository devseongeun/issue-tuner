import copy
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[1]
ROOT = PLUGIN.parents[1]
ADAPTER = PLUGIN / "adapters" / "claude"
CLI = ADAPTER / "issue_tuner_claude.py"
HOOK = ADAPTER / "hook.py"
VALIDATOR = PLUGIN / "scripts" / "validate_contract.py"
ROLES = ("reproducer", "diagnoser", "implementer", "verifier")
EXAMPLE = json.loads((PLUGIN / "examples" / "issue-report.json").read_text(encoding="utf-8"))

REPRODUCTION = {"status": "reproduced", "source": "automated", "scenario": "save form", "limitations": [], "blockers": []}
DIAGNOSIS = {"status": "diagnosed", "root_cause": "stale state", "evidence": ["form.py:12"], "symbols": ["save"], "blockers": []}
IMPLEMENTATION = {"status": "implemented", "repository": "web-app", "changed_files": ["form.py"], "red_runs": ["unit: 1 failed"], "blockers": []}
VERIFICATION = {
    "verdict": "pass",
    "source": "automated",
    "channels": ["unit"],
    "automated_runs": ["unit: pass"],
    "failed_automated_runs": [],
    "residual_risks": [],
    "blockers": [],
}
USER_CONFIRMED_VERIFICATION = {
    "verdict": "pass",
    "source": "user_confirmed",
    "channels": ["browser"],
    "automated_runs": [],
    "failed_automated_runs": ["browser: Claude in Chrome unavailable"],
    "residual_risks": ["UI verified by user only"],
    "blockers": [],
}


def frontmatter(text):
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    return {key.strip(): value.strip() for key, value in (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)}


class Sandbox:
    """A temporary ISSUE_TUNER_HOME with helpers that drive the real CLI and hook as Claude Code would."""

    def __init__(self, test):
        self.directory = tempfile.TemporaryDirectory()
        test.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name) / "home"
        self.home.mkdir()
        self.env_file = Path(self.directory.name) / "claude-env"

    def env(self, session="session-a"):
        env = dict(os.environ)
        env["ISSUE_TUNER_HOME"] = str(self.home)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN)
        env.pop("ISSUE_TUNER_CLAUDE_SESSION", None)
        if session:
            env["ISSUE_TUNER_CLAUDE_SESSION"] = session
        return env

    def cli(self, *args, session="session-a"):
        return subprocess.run(
            [sys.executable, str(CLI), *args], env=self.env(session), capture_output=True, text=True, timeout=60
        )

    def run_dir(self, run_id):
        return self.home / "runs" / run_id

    def write(self, run_id, relative, data):
        path = self.run_dir(run_id) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def state(self, run_id):
        return json.loads((self.run_dir(run_id) / "state.json").read_text(encoding="utf-8"))

    def statuses(self, run_id):
        return {name: stage.get("status") for name, stage in self.state(run_id)["stages"].items()}

    def hook(self, event, mode="pre-tool-use", extra_env=None):
        env = self.env(None)
        env.update(extra_env or {})
        event = {"session_id": "session-a", "hook_event_name": "PreToolUse", "cwd": str(self.home), **event}
        result = subprocess.run(
            [sys.executable, str(HOOK), mode], input=json.dumps(event), env=env, capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        if not result.stdout.strip():
            return None
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["hookEventName"] == "PreToolUse"
        return output["permissionDecision"]

    def start(self, run_id, issue=None, session="session-a"):
        result = self.cli("start", "--run", run_id, session=session)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        self.write(run_id, "issue-report.json", issue or EXAMPLE)
        committed = self.cli("commit-stage", "issue-report", "--run", run_id, session=session)
        if committed.returncode != 0:
            raise AssertionError(committed.stderr)
        return result

    def commit(self, run_id, stage, data, repo=None, session="session-a"):
        relative = f"repositories/{repo}/{stage}.json" if repo else f"{stage}.json"
        self.write(run_id, relative, data)
        args = ["commit-stage", stage, "--run", run_id] + (["--repo", repo] if repo else [])
        return self.cli(*args, session=session)


def command(*parts):
    return " ".join(shlex.quote(str(part)) for part in parts)


class ClaudePluginPackagingTest(unittest.TestCase):
    def test_manifest_marketplace_and_components_are_wired(self):
        manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        codex = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "issue-tuner")
        self.assertEqual(manifest["version"], codex["version"])
        hooks = (PLUGIN / manifest["hooks"]).resolve()
        self.assertTrue(hooks.is_file())
        hooks.relative_to(PLUGIN)

        codex_serena = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["serena"]["args"]
        claude_serena = manifest["mcpServers"]["serena"]["args"]
        self.assertEqual(claude_serena[claude_serena.index("--from") + 1], codex_serena[codex_serena.index("--from") + 1])
        self.assertEqual(claude_serena[claude_serena.index("--context") + 1], "claude-code")

        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        entry = next(plugin for plugin in marketplace["plugins"] if plugin["name"] == "issue-tuner")
        self.assertEqual((ROOT / entry["source"]).resolve(), PLUGIN)

    def test_role_subagents_reuse_shared_contracts_and_keep_boundaries(self):
        for role in ROLES:
            with self.subTest(role=role):
                text = (PLUGIN / "agents" / f"{role}.md").read_text(encoding="utf-8")
                meta = frontmatter(text)
                self.assertEqual(meta["name"], role)
                self.assertIn(f"${{CLAUDE_PLUGIN_ROOT}}/skills/issue-tuner/references/{role}.md", text)
                self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/issue-tuner/references/claude-code.md", text)
                self.assertTrue((PLUGIN / "skills" / "issue-tuner" / "references" / f"{role}.md").is_file())
                disallowed = {tool.strip() for tool in meta.get("disallowedTools", "").split(",") if tool.strip()}
                if role == "implementer":
                    self.assertFalse(disallowed & {"Write", "Edit"})
                else:
                    self.assertLessEqual({"Write", "Edit", "MultiEdit", "NotebookEdit"}, disallowed)

    def test_hooks_use_exec_form_and_existing_scripts(self):
        config = json.loads((ADAPTER / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(config), {"SessionStart", "PreToolUse"})
        matcher = re.compile(config["PreToolUse"][0]["matcher"])
        for tool in ("Write", "Edit", "NotebookEdit", "Bash", "mcp__claude-in-chrome__computer"):
            self.assertTrue(matcher.search(tool), tool)
        self.assertFalse(matcher.search("Read"))
        for event in config.values():
            for handler in event[0]["hooks"]:
                self.assertEqual(handler["command"], "python3")
                script = handler["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))
                self.assertTrue(Path(script).is_file())

    def test_adapter_reuses_core_without_duplicating_it(self):
        core = {path.name for path in (PLUGIN / "scripts").glob("*.py")}
        adapter = {path.name for path in ADAPTER.glob("*.py")}
        self.assertFalse(core & adapter)
        source = CLI.read_text(encoding="utf-8")
        for module in ("run_state", "report", "validate_contract"):
            self.assertIn(f'_load("{module}")', source)

    def test_skill_routes_claude_code_to_host_mapping(self):
        skill = (PLUGIN / "skills" / "issue-tuner" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Host", skill)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", skill)
        self.assertIn("references/claude-code.md", skill)
        host = (PLUGIN / "skills" / "issue-tuner" / "references" / "claude-code.md").read_text(encoding="utf-8")
        for phrase in (
            "issue-tuner:verifier",
            "Claude in Chrome",
            "자동화 채널 실패",
            "user_confirmed",
            "대체 browser automation",
            "production",
            "handoff-report.md",
            "conversation history",
            "commit-stage",
            "finish",
            "bind",
        ):
            self.assertIn(phrase, host)

    @unittest.skipUnless(shutil.which("claude"), "Claude Code CLI not installed")
    def test_claude_code_validates_plugin_and_marketplace(self):
        for target in (PLUGIN, ROOT):
            with self.subTest(target=target.name):
                result = subprocess.run(
                    ["claude", "plugin", "validate", str(target)], capture_output=True, text=True, timeout=120
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Validation passed", result.stdout)


class ClaudeCliWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self)

    def test_full_flow_records_checklist_times_and_both_reports(self):
        box = self.box
        started = box.start("DEMO-1-run")
        self.assertIn("진행 체크리스트", started.stdout)
        self.assertIn("- 시작:", started.stdout)
        self.assertTrue((box.home / "claude-sessions" / "session-a.json").is_file())
        self.assertTrue((box.run_dir("DEMO-1-run") / "handoff-report.md").is_file())
        self.assertEqual(box.statuses("DEMO-1-run")["reproduction"], "in_progress")

        for stage, data, repo in (
            ("reproduction", REPRODUCTION, None),
            ("diagnosis", DIAGNOSIS, None),
            ("implementation", IMPLEMENTATION, "web-app"),
        ):
            result = box.commit("DEMO-1-run", stage, data, repo)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("진행 체크리스트", result.stdout)
            self.assertNotIn("resolved_at", box.state("DEMO-1-run"))

        verified = box.commit("DEMO-1-run", "verification", VERIFICATION, "web-app")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("해결 시각을 기록했다", verified.stdout)
        state = box.state("DEMO-1-run")
        self.assertEqual(state["resolution_source"], "automated")
        self.assertEqual(box.statuses("DEMO-1-run")["publication-approval"], "in_progress")
        self.assertNotIn("- 해결: 미기록", verified.stdout)

        approved = box.cli("transition", "--run", "DEMO-1-run", "--stage", "publication-approval", "--status", "done")
        self.assertEqual(approved.returncode, 0, approved.stderr)

        finished = box.cli("finish", "--run", "DEMO-1-run")
        self.assertEqual(finished.returncode, 0, finished.stderr)
        run = box.run_dir("DEMO-1-run")
        for name in ("handoff-report.md", "final-report.md", "metrics.json"):
            self.assertTrue((run / name).is_file(), name)
        self.assertIn("최종 보고서", finished.stdout)
        self.assertNotIn("- 종료: 미기록", finished.stdout)
        self.assertEqual(box.state("DEMO-1-run")["status"], "finished")
        self.assertFalse((box.home / "claude-sessions" / "session-a.json").exists())

        late = box.commit("DEMO-1-run", "diagnosis", DIAGNOSIS)
        self.assertNotEqual(late.returncode, 0)
        self.assertIn("finished", late.stderr)

    def test_invalid_stage_json_is_rejected_without_state_change(self):
        box = self.box
        box.start("DEMO-2-run")
        before = box.statuses("DEMO-2-run")
        bad = dict(REPRODUCTION, status="maybe")
        result = box.commit("DEMO-2-run", "reproduction", bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status must be one of", result.stderr)
        self.assertEqual(box.statuses("DEMO-2-run"), before)

    def test_start_refuses_existing_run(self):
        self.box.start("DEMO-3-run")
        again = self.box.cli("start", "--run", "DEMO-3-run")
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("bind", again.stderr)

    def test_failures_and_user_confirmation_are_preserved(self):
        box = self.box
        box.start("DEMO-4-run")
        failed = box.commit("DEMO-4-run", "reproduction", dict(REPRODUCTION, status="failed"))
        self.assertEqual(failed.returncode, 0, failed.stderr)
        self.assertEqual(box.statuses("DEMO-4-run")["reproduction"], "failed")

        confirmed = dict(REPRODUCTION, source="user_confirmed", limitations=["Claude in Chrome unavailable"])
        box.commit("DEMO-4-run", "reproduction", confirmed)
        box.commit("DEMO-4-run", "diagnosis", DIAGNOSIS)
        skipped = box.cli("transition", "--run", "DEMO-4-run", "--stage", "implementation", "--status", "skipped")
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertEqual(box.statuses("DEMO-4-run")["verification"], "in_progress")

        fail = box.commit("DEMO-4-run", "verification", dict(VERIFICATION, verdict="fail", automated_runs=["unit: fail"]), "web-app")
        self.assertEqual(fail.returncode, 0, fail.stderr)
        self.assertEqual(box.statuses("DEMO-4-run")["verification"], "failed")
        self.assertNotIn("resolved_at", box.state("DEMO-4-run"))

        passed = box.commit("DEMO-4-run", "verification", USER_CONFIRMED_VERIFICATION, "web-app")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(box.state("DEMO-4-run")["resolution_source"], "user_confirmed")

    def test_material_stages_only_finish_through_commit_stage(self):
        self.box.start("DEMO-5-run")
        result = self.box.cli("transition", "--run", "DEMO-5-run", "--stage", "reproduction", "--status", "done")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.box.statuses("DEMO-5-run")["reproduction"], "in_progress")

    def test_each_repository_must_pass_before_resolution(self):
        box = self.box
        issue = copy.deepcopy(EXAMPLE)
        issue["repositories"].append({"name": "api", "path": "./api", "branch": "main"})
        box.start("DEMO-6-run", issue)
        box.commit("DEMO-6-run", "reproduction", REPRODUCTION)
        box.commit("DEMO-6-run", "diagnosis", DIAGNOSIS)
        box.commit("DEMO-6-run", "implementation", IMPLEMENTATION, "web-app")
        self.assertEqual(box.statuses("DEMO-6-run")["implementation"], "in_progress")
        mismatch = box.commit("DEMO-6-run", "implementation", IMPLEMENTATION, "api")
        self.assertNotEqual(mismatch.returncode, 0)
        box.commit("DEMO-6-run", "implementation", dict(IMPLEMENTATION, repository="api"), "api")
        self.assertEqual(box.statuses("DEMO-6-run")["implementation"], "done")

        box.commit("DEMO-6-run", "verification", VERIFICATION, "web-app")
        self.assertNotIn("resolved_at", box.state("DEMO-6-run"))
        box.commit("DEMO-6-run", "verification", VERIFICATION, "api")
        self.assertIn("resolved_at", box.state("DEMO-6-run"))

    def test_another_session_resumes_from_saved_handoff(self):
        box = self.box
        box.start("DEMO-7-run")
        box.commit("DEMO-7-run", "reproduction", REPRODUCTION)

        shown = box.cli("show", "--run", "DEMO-7-run", session="session-b")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("# 실행 인계 보고서", shown.stdout)
        self.assertIn("다음 실행 가능 작업", shown.stdout)
        self.assertFalse((box.home / "claude-sessions" / "session-b.json").exists())

        target = str(box.run_dir("DEMO-7-run") / "diagnosis.json")
        write = {"tool_name": "Write", "tool_input": {"file_path": target}, "session_id": "session-b"}
        self.assertIsNone(box.hook(write))

        bound = box.cli("bind", "--run", "DEMO-7-run", session="session-b")
        self.assertEqual(bound.returncode, 0, bound.stderr)
        self.assertEqual(box.hook(write), "allow")
        resumed = box.commit("DEMO-7-run", "diagnosis", DIAGNOSIS, session="session-b")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(box.statuses("DEMO-7-run")["implementation"], "in_progress")

    def test_finished_runs_cannot_be_rebound(self):
        box = self.box
        box.start("DEMO-8-run")
        box.cli("finish", "--run", "DEMO-8-run")
        result = box.cli("bind", "--run", "DEMO-8-run", session="session-b")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(box.cli("show", "--run", "DEMO-8-run", session="session-b").returncode, 0)


class ClaudeHookTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self)
        self.box.start("DEMO-10-run")
        self.run = self.box.run_dir("DEMO-10-run")

    def write_event(self, path, **extra):
        return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "{}"}, **extra}

    def bash_event(self, text, **extra):
        return {"tool_name": "Bash", "tool_input": {"command": text}, **extra}

    def test_run_artifact_writes_are_allowed_only_inside_the_bound_running_run(self):
        box = self.box
        self.assertEqual(box.hook(self.write_event(self.run / "reproduction.json")), "allow")
        self.assertEqual(box.hook(self.write_event(self.run / "repositories" / "web-app" / "verification.json")), "allow")
        edit = {"tool_name": "Edit", "tool_input": {"file_path": str(self.run / "diagnosis.json")}}
        self.assertEqual(box.hook(edit), "allow")
        relative = self.write_event("diagnosis.json", cwd=str(self.run))
        self.assertEqual(box.hook(relative), "allow")

        box.start("DEMO-11-run", session="session-z")
        self.assertEqual(box.hook(self.write_event(box.run_dir("DEMO-11-run") / "diagnosis.json")), "deny")
        self.assertIsNone(box.hook(self.write_event(Path(box.directory.name) / "project" / "app.py")))

    def test_core_owned_files_and_symlink_escapes_are_denied(self):
        box = self.box
        for name in ("state.json", "metrics.json", "handoff-report.md", "final-report.md", "repositories/web-app/commit-gate.json"):
            with self.subTest(name=name):
                self.assertEqual(box.hook(self.write_event(self.run / name)), "deny")
        outside = Path(box.directory.name) / "outside"
        outside.mkdir()
        (self.run / "escape").symlink_to(outside, target_is_directory=True)
        self.assertEqual(box.hook(self.write_event(self.run / "escape" / "reproduction.json")), "deny")
        self.assertEqual(box.hook(self.write_event(self.run / ".." / "DEMO-x" / "reproduction.json")), "deny")

    def test_finished_run_is_read_only(self):
        self.box.cli("finish", "--run", "DEMO-10-run")
        self.box.cli("bind", "--run", "DEMO-10-run")  # refused; binding removed by finish
        self.assertIsNone(self.box.hook(self.write_event(self.run / "reproduction.json")))
        # 이전 binding이 남아 있어도 종료된 run은 갱신을 거부한다.
        (self.box.home / "claude-sessions" / "session-a.json").write_text(json.dumps({"run_id": "DEMO-10-run"}))
        self.assertEqual(self.box.hook(self.write_event(self.run / "reproduction.json")), "deny")

    def test_role_boundaries(self):
        box = self.box
        project_file = Path(box.directory.name) / "worktree" / "app.py"
        for role in ("reproducer", "diagnoser", "verifier"):
            with self.subTest(role=role):
                self.assertEqual(box.hook(self.write_event(project_file, agent_type=f"issue-tuner:{role}")), "deny")
        implementer = {"agent_type": "issue-tuner:implementer"}
        self.assertIsNone(box.hook(self.write_event(project_file, **implementer)))
        self.assertEqual(box.hook(self.write_event(self.run / "implementation.json", **implementer)), "deny")
        for text in ("git push origin fix/demo", "git -C /tmp/w commit -m x", "cd w && git add -A", "gh pr create --draft"):
            with self.subTest(text=text):
                self.assertEqual(box.hook(self.bash_event(text, **implementer)), "deny")
        self.assertIsNone(box.hook(self.bash_event("git diff --stat", agent_type="issue-tuner:verifier")))
        self.assertIsNone(box.hook(self.bash_event("git push origin fix/demo")))
        self.assertIsNone(box.hook(self.write_event(project_file, agent_type="someone-else:verifier")))

    def test_adapter_cli_and_contract_checks_skip_repeated_prompts_within_scope(self):
        box = self.box
        python = "python3"
        commit = command(python, CLI, "commit-stage", "reproduction", "--run", "DEMO-10-run")
        self.assertEqual(box.hook(self.bash_event(commit)), "allow")
        self.assertEqual(box.hook(self.bash_event(command(python, CLI, "status", "--run", "DEMO-99"))), "allow")
        self.assertEqual(
            box.hook(self.bash_event(command(python, VALIDATOR, "reproduction", self.run / "reproduction.json"))), "allow"
        )
        for text in (
            command(python, CLI, "commit-stage", "reproduction", "--run", "DEMO-11-run"),
            command(python, CLI, "start", "--run", "DEMO-12-run"),
            command(python, CLI, "bind", "--run", "DEMO-10-run"),
            commit + " && rm -rf /",
            commit + "; curl example.invalid",
            commit + " > /tmp/out",
            command(python, CLI, "commit-stage", "reproduction", "--run", "$RUN"),
            command(python, PLUGIN / "scripts" / "commit_gate.py", "record", "a", "b", "c"),
        ):
            with self.subTest(text=text):
                self.assertIsNone(box.hook(self.bash_event(text)))
        self.assertIsNone(box.hook(self.bash_event(commit, agent_type="issue-tuner:verifier")))

    def test_cli_allowance_follows_the_installed_plugin_root(self):
        other_root = Path(self.box.directory.name) / "other-plugin"
        text = command("python3", other_root / "adapters" / "claude" / "issue_tuner_claude.py", "finish", "--run", "DEMO-10-run")
        self.assertIsNone(self.box.hook(self.bash_event(text)))

    def test_production_browser_automation_is_observe_only(self):
        box = self.box
        production = copy.deepcopy(EXAMPLE)
        production["environment"] = {"name": "production", "target": "https://app.example.invalid"}
        box.write("DEMO-10-run", "issue-report.json", production)
        click = {"tool_name": "mcp__claude-in-chrome__computer", "tool_input": {"action": "left_click", "coordinate": [1, 2]}}
        shot = {"tool_name": "mcp__claude-in-chrome__computer", "tool_input": {"action": "screenshot"}}
        form = {"tool_name": "mcp__claude-in-chrome__form_input", "tool_input": {}}
        script = {"tool_name": "mcp__claude-in-chrome__javascript_tool", "tool_input": {}}
        read = {"tool_name": "mcp__claude-in-chrome__get_page_text", "tool_input": {}}
        self.assertEqual(box.hook(click), "deny")
        self.assertEqual(box.hook(form), "deny")
        self.assertEqual(box.hook(script), "deny")
        self.assertIsNone(box.hook(shot))
        self.assertIsNone(box.hook(read))
        box.write("DEMO-10-run", "issue-report.json", EXAMPLE)
        self.assertIsNone(box.hook(click))

    def test_session_start_exports_session_id_for_bash(self):
        box = self.box
        env = {"CLAUDE_ENV_FILE": str(box.env_file)}
        box.hook({"hook_event_name": "SessionStart", "session_id": "session-q"}, mode="session-start", extra_env=env)
        self.assertEqual(box.env_file.read_text(encoding="utf-8"), "export ISSUE_TUNER_CLAUDE_SESSION=session-q\n")
        box.hook({"hook_event_name": "SessionStart", "session_id": "bad id;rm"}, mode="session-start", extra_env=env)
        self.assertEqual(box.env_file.read_text(encoding="utf-8").count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
