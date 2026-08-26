import importlib.util
import inspect
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "publish.py"
SPEC = importlib.util.spec_from_file_location("publish", SCRIPT)
publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish)


def command(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def numbered_lines(count, prefix="line"):
    # 마지막 줄까지 개행으로 끝나야 git이 세는 추가 줄 수와 정확히 일치한다.
    return "".join(f"{prefix} {index}\n" for index in range(count))


def raw_numstat(repo, base, head):
    # 구현을 거치지 않은 git 자체 집계로, fixture가 정말 그 줄 수인지 교차 검증한다.
    output = subprocess.run(
        ["git", "diff", "--numstat", f"{base}...{head}", "--"],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    added = 0
    deleted = 0
    binaries = []
    for line in output.splitlines():
        left, right, path = line.split("\t", 2)
        if left == "-" or right == "-":
            binaries.append(path)
            continue
        added += int(left)
        deleted += int(right)
    return added, deleted, binaries


def sample_budget(base="main", total=42, within_limit=True, **overrides):
    budget = {
        "repository_root": "repo",
        "base": base,
        "head": "fix/demo",
        "added": total,
        "deleted": 0,
        "total": total,
        "limit": publish.REVIEW_LINE_LIMIT,
        "within_limit": within_limit,
        "files": [{"path": "src/app.py", "added": total, "deleted": 0}],
        "unmeasurable": [],
    }
    budget.update(overrides)
    return budget


class PublishTest(unittest.TestCase):
    def repo(self, directory, name="repo", initial=None):
        repo = Path(directory) / name
        command(directory, "git", "init", "-b", "main", str(repo))
        command(repo, "git", "config", "user.name", "Test User")
        command(repo, "git", "config", "user.email", "test@example.com")
        (repo / "README.md").write_text("initial\n", encoding="utf-8")
        for path, text in (initial or {}).items():
            self.write(repo, path, text)
        command(repo, "git", "add", "-A")
        command(repo, "git", "commit", "-m", "initial")
        return repo

    def write(self, repo, name, text):
        path = Path(repo) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
            return None
        body = text if text.endswith("\n") else text + "\n"
        path.write_text(body, encoding="utf-8")
        return len(body.splitlines())

    def branch(self, repo, name, files, message="change"):
        command(repo, "git", "checkout", "-b", name)
        written = {}
        for path, text in files.items():
            written[path] = self.write(repo, path, text)
        command(repo, "git", "add", "-A")
        command(repo, "git", "commit", "-m", message)
        return written

    def body_file(self, directory):
        body = Path(directory) / "body.md"
        body.write_text("Draft body\n", encoding="utf-8")
        return body.resolve()

    def test_maps_supported_hosts_without_accepting_spoofs(self):
        cases = {
            "git@github.com:demo/repo.git": "github",
            "https://gitlab.com/demo/repo.git": "gitlab",
            "ssh://git.example.invalid/repo.git": "manual",
            "git@evilgithub.com:demo/repo.git": "manual",
            "https://gitlab.com.evil.invalid/demo/repo.git": "manual",
            "file://github.com/demo/repo.git": "manual",
            "ftp://gitlab.com/demo/repo.git": "manual",
            "custom://github.com/demo/repo.git": "manual",
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote):
                self.assertEqual(publish.host_kind(remote), expected)

    def test_builds_exact_github_draft_command(self):
        with tempfile.TemporaryDirectory() as directory:
            body = self.body_file(directory)
            with patch.object(publish.shutil, "which", return_value="/usr/bin/gh"):
                self.assertEqual(
                    publish.draft_command(
                        "git@github.com:demo/repo.git", "main", "Fix bug", body, sample_budget()
                    ),
                    [
                        "gh",
                        "pr",
                        "create",
                        "--draft",
                        "--base",
                        "main",
                        "--title",
                        "Fix bug",
                        "--body-file",
                        str(body),
                    ],
                )

    def test_builds_exact_gitlab_draft_command(self):
        with tempfile.TemporaryDirectory() as directory:
            body = self.body_file(directory)
            with patch.object(publish.shutil, "which", return_value="/usr/bin/glab"):
                self.assertEqual(
                    publish.draft_command(
                        "https://gitlab.com/demo/repo.git", "main", "Fix bug", body, sample_budget()
                    ),
                    [
                        "glab",
                        "mr",
                        "create",
                        "--draft",
                        "--target-branch",
                        "main",
                        "--title",
                        "Fix bug",
                        "--description-file",
                        str(body),
                    ],
                )

    def test_returns_none_for_manual_host_or_missing_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            body = self.body_file(directory)
            with patch.object(publish.shutil, "which", return_value=None):
                self.assertIsNone(
                    publish.draft_command(
                        "git@github.com:demo/repo.git", "main", "Fix bug", body, sample_budget()
                    )
                )
            with patch.object(publish.shutil, "which", return_value="/usr/bin/tool"):
                self.assertIsNone(
                    publish.draft_command(
                        "ssh://git.example.invalid/repo.git", "main", "Fix bug", body, sample_budget()
                    )
                )

    def test_validates_command_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            body = directory / "body.md"
            body.write_text("Draft body\n", encoding="utf-8")
            link = directory / "body-link.md"
            link.symlink_to(body)
            invalid = (
                ("", "Fix bug", body.resolve()),
                ("main", " ", body.resolve()),
                ("main", "Fix bug", Path("body.md")),
                ("main", "Fix bug", (directory / "missing.md").resolve()),
                ("main", "Fix bug", link.absolute()),
            )
            for base, title, path in invalid:
                with self.subTest(base=base, title=title, path=path), self.assertRaises(ValueError):
                    publish.draft_command(
                        "git@github.com:demo/repo.git", base, title, path, sample_budget(base=base)
                    )

    def test_allows_exactly_six_hundred_lines_and_blocks_six_hundred_one_without_building_a_command(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            self.assertEqual(self.branch(repo, "fix/limit", {"exact.txt": numbered_lines(600)}), {"exact.txt": 600})
            command(repo, "git", "checkout", "main")
            self.assertEqual(self.branch(repo, "fix/over", {"over.txt": numbered_lines(601)}), {"over.txt": 601})

            self.assertEqual(raw_numstat(repo, "main", "fix/limit"), (600, 0, []))
            self.assertEqual(raw_numstat(repo, "main", "fix/over"), (601, 0, []))

            allowed = publish.review_budget(repo, "main", "fix/limit")
            blocked = publish.review_budget(repo, "main", "fix/over")
            self.assertEqual(allowed["total"], 600)
            self.assertEqual(allowed["limit"], 600)
            self.assertIs(allowed["within_limit"], True)
            self.assertEqual(blocked["total"], 601)
            self.assertIs(blocked["within_limit"], False)

            body = self.body_file(directory)
            with patch.object(publish.shutil, "which", return_value="/usr/bin/gh"):
                self.assertEqual(
                    publish.draft_command("git@github.com:demo/repo.git", "main", "Fix bug", body, allowed)[:4],
                    ["gh", "pr", "create", "--draft"],
                )
                with self.assertRaises(ValueError):
                    publish.draft_command("git@github.com:demo/repo.git", "main", "Fix bug", body, blocked)

    def test_sums_added_and_deleted_lines_without_moving_the_checked_out_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory, initial={"notes.txt": numbered_lines(30)})
            self.branch(repo, "fix/edit", {"notes.txt": numbered_lines(18), "extra.txt": numbered_lines(25)})
            self.assertEqual(raw_numstat(repo, "main", "fix/edit"), (25, 12, []))

            budget = publish.review_budget(repo, "main", "fix/edit")
            self.assertEqual(budget["added"], 25)
            self.assertEqual(budget["deleted"], 12)
            self.assertEqual(budget["total"], 37)
            self.assertEqual(budget["base"], "main")
            self.assertEqual(budget["head"], "fix/edit")

            head = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout
            self.assertEqual(head, "fix/edit")
            self.assertEqual(status, "")

    def test_counts_every_text_change_including_docs_configs_and_lockfiles_without_extension_exclusions(self):
        contents = {
            "src/app.py": numbered_lines(12, "code"),
            "tests/test_app.py": numbered_lines(9, "check"),
            "docs/guide.md": numbered_lines(7, "- doc"),
            "config/settings.json": numbered_lines(5, "// setting"),
            "package-lock.json": numbered_lines(21, "// pinned"),
            "Makefile": numbered_lines(4, "# target"),
        }
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            written = self.branch(repo, "fix/mixed", contents)
            expected = sum(written.values())
            self.assertEqual(expected, 58)
            self.assertEqual(raw_numstat(repo, "main", "fix/mixed"), (58, 0, []))

            budget = publish.review_budget(repo, "main", "fix/mixed")
            self.assertEqual([entry["path"] for entry in budget["files"]], sorted(contents))
            self.assertEqual(budget["added"], 58)
            self.assertEqual(budget["deleted"], 0)
            self.assertEqual(budget["total"], 58)
            self.assertEqual(budget["unmeasurable"], [])
            for entry in budget["files"]:
                with self.subTest(path=entry["path"]):
                    self.assertEqual(entry["added"], written[entry["path"]])
                    self.assertEqual(entry["deleted"], 0)

    def test_reports_binary_changes_as_unmeasurable_without_adding_them_to_the_line_total(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            self.branch(
                repo,
                "fix/binary",
                {
                    "assets/logo.bin": bytes([0, 1, 2, 3, 0, 255]) * 40,
                    "src/app.py": numbered_lines(11, "code"),
                },
            )
            self.assertEqual(raw_numstat(repo, "main", "fix/binary"), (11, 0, ["assets/logo.bin"]))

            budget = publish.review_budget(repo, "main", "fix/binary")
            self.assertEqual(budget["unmeasurable"], ["assets/logo.bin"])
            self.assertEqual([entry["path"] for entry in budget["files"]], ["src/app.py"])
            self.assertEqual(budget["added"], 11)
            self.assertEqual(budget["deleted"], 0)
            self.assertEqual(budget["total"], 11)
            self.assertIs(budget["within_limit"], True)

            rendered = publish.render_budget(budget).splitlines()
            notice = [line for line in rendered if line.startswith("- 줄 수 측정 불가:")]
            self.assertEqual(len(notice), 1)
            self.assertIn("assets/logo.bin", notice[0])

    def test_measures_each_repository_independently_without_leaking_results_between_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            small = self.repo(directory, name="small")
            large = self.repo(directory, name="large")
            self.branch(small, "fix/small", {"src/small.py": numbered_lines(40, "code")})
            self.branch(large, "fix/large", {"src/large.py": numbered_lines(900, "code")})

            small_budget = publish.review_budget(small, "main", "fix/small")
            large_budget = publish.review_budget(large, "main", "fix/large")

            self.assertEqual(small_budget["repository_root"], str(small.resolve()))
            self.assertEqual(large_budget["repository_root"], str(large.resolve()))
            self.assertNotEqual(small_budget["repository_root"], large_budget["repository_root"])
            self.assertEqual(small_budget["total"], 40)
            self.assertEqual(large_budget["total"], 900)
            self.assertIs(small_budget["within_limit"], True)
            self.assertIs(large_budget["within_limit"], False)
            self.assertEqual([entry["path"] for entry in small_budget["files"]], ["src/small.py"])
            self.assertEqual([entry["path"] for entry in large_budget["files"]], ["src/large.py"])

    def test_splits_an_oversized_budget_into_deterministic_groups_without_dropping_files(self):
        contents = {
            "docs/guide.md": numbered_lines(100, "- doc"),
            "huge.txt": numbered_lines(700),
            "src/a.py": numbered_lines(200, "code"),
            "src/b.py": numbered_lines(200, "code"),
            "src/c.py": numbered_lines(300, "code"),
        }
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            self.branch(repo, "fix/big", contents)
            budget = publish.review_budget(repo, "main", "fix/big")
            self.assertEqual(budget["total"], 1500)
            self.assertIs(budget["within_limit"], False)

            plan = publish.split_plan(budget)
            self.assertEqual(
                plan,
                [
                    {"paths": ["docs/guide.md"], "total": 100, "oversized": False},
                    {"paths": ["huge.txt"], "total": 700, "oversized": True},
                    {"paths": ["src/a.py", "src/b.py"], "total": 400, "oversized": False},
                    {"paths": ["src/c.py"], "total": 300, "oversized": False},
                ],
            )
            self.assertEqual(plan, publish.split_plan(budget))
            grouped = [path for group in plan for path in group["paths"]]
            self.assertEqual(sorted(grouped), sorted(contents))
            self.assertEqual(len(grouped), len(set(grouped)))
            self.assertEqual(sum(group["total"] for group in plan), budget["total"])
            for group in plan:
                with self.subTest(paths=group["paths"]):
                    self.assertEqual(group["paths"], sorted(group["paths"]))
                    self.assertEqual(len({path.split("/", 1)[0] for path in group["paths"]}), 1)
                    if not group["oversized"]:
                        self.assertLessEqual(group["total"], budget["limit"])

            tighter = publish.split_plan(budget, limit=250)
            self.assertEqual(tighter, publish.split_plan(budget, limit=250))
            self.assertGreater(len(tighter), len(plan))
            for group in tighter:
                with self.subTest(limit=250, paths=group["paths"]):
                    if group["oversized"]:
                        self.assertEqual(len(group["paths"]), 1)
                        self.assertGreater(group["total"], 250)
                    else:
                        self.assertLessEqual(group["total"], 250)

            self.assertEqual(publish.split_plan(sample_budget(files=[])), [])

    def test_rejects_every_gate_bypass_attempt_without_returning_a_command(self):
        with tempfile.TemporaryDirectory() as directory:
            body = self.body_file(directory)
            rejected = (
                sample_budget(within_limit=False, total=601),
                sample_budget(within_limit="yes"),
                sample_budget(within_limit=1),
                sample_budget(base="develop"),
                sample_budget(base=""),
                None,
                [],
                "main",
                601,
                sample_budget().items(),
            )
            with patch.object(publish.shutil, "which", return_value="/usr/bin/gh"):
                self.assertIsInstance(
                    publish.draft_command(
                        "git@github.com:demo/repo.git", "main", "Fix bug", body, sample_budget()
                    ),
                    list,
                )
                for budget in rejected:
                    with self.subTest(budget=budget), self.assertRaises(ValueError):
                        publish.draft_command(
                            "git@github.com:demo/repo.git", "main", "Fix bug", body, budget
                        )

    def test_exposes_no_force_or_override_parameter_on_draft_command(self):
        parameters = inspect.signature(publish.draft_command).parameters
        self.assertEqual(list(parameters), ["remote", "base", "title", "body_file", "budget"])
        for name, parameter in parameters.items():
            with self.subTest(parameter=name):
                self.assertIs(parameter.default, inspect.Parameter.empty)
                self.assertEqual(parameter.kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        source = inspect.getsource(publish.draft_command)
        for escape in ("force", "override", "bypass", "skip_", "allow_large"):
            with self.subTest(escape=escape):
                self.assertNotIn(escape, source)

    def test_rejects_option_like_or_empty_refs_without_passing_them_to_git(self):
        hostile = "--upload-pack=evil"
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repo(directory)
            self.branch(repo, "fix/refs", {"src/app.py": numbered_lines(5, "code")})
            invalid = (
                (hostile, "fix/refs"),
                ("main", hostile),
                ("", "fix/refs"),
                ("main", ""),
                ("   ", "fix/refs"),
                ("-", "fix/refs"),
                ("main", "does/not/exist"),
                ("missing-base", "fix/refs"),
            )
            for base, head in invalid:
                with self.subTest(base=base, head=head):
                    with patch.object(publish.subprocess, "run", wraps=subprocess.run) as spawned:
                        with self.assertRaises(ValueError):
                            publish.review_budget(repo, base, head)
                    for call in spawned.call_args_list:
                        arguments = call.args[0] if call.args else call.kwargs.get("args", [])
                        for argument in arguments:
                            # 옵션형 ref는 어떤 형태로도 git argv에 실리면 안 된다.
                            self.assertNotIn("upload-pack", str(argument))

    def test_rejects_a_plain_directory_that_is_not_a_git_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / "plain"
            plain.mkdir()
            (plain / "notes.txt").write_text("nothing tracked\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "worktree"):
                publish.review_budget(plain, "main", "fix/demo")
            with self.assertRaises(ValueError):
                publish.review_budget(plain / "missing", "main", "fix/demo")

    def test_renders_budget_headline_and_review_scope_for_both_sides_of_the_limit(self):
        within = sample_budget(total=600, added=580, deleted=20)
        within["files"] = [
            {"path": "src/app.py", "added": 580, "deleted": 0},
            {"path": "tests/test_app.py", "added": 0, "deleted": 20},
        ]
        rendered = publish.render_budget(within).splitlines()
        self.assertEqual(rendered[0], "## 리뷰 분량")
        self.assertEqual(rendered[1], "- 변경 줄 수: 600줄 (추가 580, 삭제 20) / 제한 600줄")
        self.assertEqual(rendered[2], "- 변경 파일: 2개")
        self.assertEqual(rendered[-1], "- 예상 리뷰 범위: 30분 내 검토 가능")
        self.assertNotIn("줄 수 측정 불가", "\n".join(rendered))
        self.assertNotIn("### 분할안", "\n".join(rendered))

        over = sample_budget(total=1500, added=1200, deleted=300, within_limit=False)
        over["files"] = [{"path": "src/app.py", "added": 1200, "deleted": 300}]
        blocked = publish.render_budget(over).splitlines()
        self.assertEqual(blocked[0], "## 리뷰 분량")
        self.assertEqual(blocked[1], "- 변경 줄 수: 1500줄 (추가 1200, 삭제 300) / 제한 600줄")
        self.assertEqual(blocked[2], "- 변경 파일: 1개")
        self.assertEqual(blocked[-1], "- 예상 리뷰 범위: 제한 초과 — 게시 차단, 분할 필요")

        plan = [
            {"paths": ["src/app.py"], "total": 1500, "oversized": True},
        ]
        with_plan = publish.render_budget(over, plan).splitlines()
        self.assertEqual(with_plan[0], "## 리뷰 분량")
        self.assertIn("### 분할안", with_plan)
        self.assertLess(with_plan.index("- 예상 리뷰 범위: 제한 초과 — 게시 차단, 분할 필요"), with_plan.index("### 분할안"))
        self.assertTrue(any("src/app.py" in line for line in with_plan[with_plan.index("### 분할안") + 1 :]))


if __name__ == "__main__":
    unittest.main()
