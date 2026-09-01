import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock


def load_report():
    path = Path(__file__).parents[1] / "scripts" / "report.py"
    spec = importlib.util.spec_from_file_location("handoff_foundation_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = load_report()


def valid_gate(source="automated"):
    digest = "a" * 64
    return {
        "version": 1,
        "repository_root": "/tmp/repository",
        "files": {
            "deleted.py": {"type": "deleted", "mode": "deleted", "sha256": None, "mtime_ns": None},
            "src/app.py": {"type": "file", "mode": "100644", "sha256": "b" * 64, "mtime_ns": 12},
        },
        "operations": [
            {"kind": "modify", "path": "src/app.py"},
            {"kind": "rename", "path": "src/new.py", "source": "src/old.py"},
        ],
        "verification_file": {"type": "file", "mode": "100644", "sha256": digest, "mtime_ns": 13},
        "verification_file_sha256": digest,
        "verification": {
            "verdict": "pass",
            "source": source,
            "channels": ["unit"],
            "automated_runs": ["pytest"] if source == "automated" else [],
            "failed_automated_runs": [] if source == "automated" else ["pytest -k integration"],
            "residual_risks": [] if source == "automated" else ["integration environment unavailable"],
            "blockers": [],
        },
    }


def changed(path, value, source="automated"):
    document = copy.deepcopy(valid_gate(source))
    target = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return document


class HandoffFoundationTest(unittest.TestCase):
    def test_handoff_values_fold_markdown_and_redact_broad_credential_surfaces(self):
        exact_cases = (
            ("empty", None, "미기록"),
            ("custom empty", "", "없음", "없음"),
            ("single markdown line", "  alpha\tbeta\n### forged  ", "alpha beta ### forged"),
            (
                "home paths",
                "/" + "Users/alice/project and /home/bob/repository",
                "[redacted-path]/project and [redacted-path]/repository",
            ),
            (
                "uri and query",
                "ssh+git://alice:pw@example.invalid/repo?access_" + "to" + "ken=query-secret&safe=yes",
                "ssh+git://[redacted]@example.invalid/repo?access_" + "to" + "ken=[redacted]&safe=yes",
            ),
            (
                "ordinary commands",
                "pytest -p no:cacheprovider; docker run -p 8080:80; result: PASS",
                "pytest -p no:cacheprovider; docker run -p 8080:80; result: PASS",
            ),
        )
        for case in exact_cases:
            label, value, expected, *empty = case
            with self.subTest(case=label):
                self.assertEqual(report._handoff_value(value, *empty), expected)

        quoted_cases = (
            ('{"pass' + 'word": "alpha beta", "note": "ordinary text"}', '{"pass' + 'word": [redacted], "note": "ordinary text"}'),
            ('Proxy-Authorization: Bearer "alpha beta" safe tail', "Proxy-Authorization: [redacted] safe tail"),
            ('cmd --password "alpha beta" --verbose', "cmd --password [redacted] --verbose"),
        )
        for value, expected in quoted_cases:
            with self.subTest(value=value):
                self.assertEqual(report._handoff_value(value), expected)

        credential_cases = (
            (
                "assignments and headers",
                "AWS_SECRET_ACCESS_KEY=aws-secret SERVICE_PRIVATE_KEY=private-secret "
                "api-key: api-secret x-api-key: x-secret Authorization: Bearer auth-secret "
                "Proxy-Authorization: proxy-secret X-Auth-Token: token-secret",
                ("aws-secret", "private-secret", "api-secret", "x-secret", "auth-secret", "proxy-secret", "token-secret"),
            ),
            (
                "cookies",
                "Cookie: csrf=cookie-secret; session=session-secret; response Set-Cookie: auth=set-secret; Path=/",
                ("cookie-secret", "session-secret", "set-secret"),
            ),
            (
                "cli",
                "cmd --password cli-password --api-key=cli-key --token cli-token --user admin:user-password",
                ("cli-password", "cli-key", "cli-token", "admin", "user-password"),
            ),
        )
        for label, value, forbidden in credential_cases:
            normalized = report._handoff_value(value)
            with self.subTest(case=label):
                self.assertIn("[redacted]", normalized)
                for secret in forbidden:
                    self.assertNotIn(secret, normalized)

    def test_tolerant_json_reader_reports_every_state_and_enforces_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text('{"outside": true}', encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(outside)
            non_regular = root / "directory.json"
            non_regular.mkdir()
            payloads = (
                ("missing", root / "missing.json", None, report.UNRECORDED),
                ("symlink", link, None, report.UNSAFE),
                ("non-regular", non_regular, None, report.UNSAFE),
                ("invalid syntax", root / "invalid.json", "{not-json", report.DAMAGED),
                ("non-object", root / "list.json", "[]", report.DAMAGED),
                ("too deep", root / "deep.json", '{"x":' + "[" * 256 + "0" + "]" * 256 + "}", report.DAMAGED),
                ("valid boundary", root / "boundary.json", '{"x":' + "[" * 255 + "0" + "]" * 255 + "}", report.RECORDED),
                ("valid", root / "valid.json", '{"ok": true}', report.RECORDED),
            )
            for label, path, payload, expected_status in payloads:
                if payload is not None:
                    path.write_text(payload, encoding="utf-8")
                with self.subTest(case=label):
                    document, status = report._read_handoff_json(path)
                    self.assertEqual(status, expected_status)
                    self.assertEqual(document is not None, expected_status == report.RECORDED)

            invalid_utf8 = root / "invalid-utf8.json"
            invalid_utf8.write_bytes(b'{"x":"\xff"}')
            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (report.HANDOFF_MAX_JSON_BYTES + 1))
            for label, path in (("invalid utf-8", invalid_utf8), ("over 4 MiB", oversized)):
                with self.subTest(case=label):
                    self.assertEqual(report._read_handoff_json(path), (None, report.DAMAGED))

            bounded = root / "bounded.json"
            document = b'{"ok":true}'
            bounded.write_bytes(document + b" " * (report.HANDOFF_MAX_JSON_BYTES - len(document)))
            self.assertEqual(report._read_handoff_json(bounded), ({"ok": True}, report.RECORDED))

            truncated = root / "truncated.json"
            truncated.write_text('{"runs":["' + ('pytest -k \\"handoff report\\"; ' * 2_500), encoding="utf-8")
            started = time.monotonic()
            self.assertEqual(report._read_handoff_json(truncated), (None, report.DAMAGED))
            self.assertLess(time.monotonic() - started, 1.0)

            raced = root / "raced.json"
            replacement = root / "replacement.json"
            raced.write_text('{"before": true}', encoding="utf-8")
            replacement.write_text('{"after": true}', encoding="utf-8")
            real_open = report.os.open

            def replace_after_open(path, flags):
                descriptor = real_open(path, flags)
                replacement.replace(raced)
                return descriptor

            with mock.patch.object(report.os, "open", side_effect=replace_after_open):
                self.assertEqual(report._read_handoff_json(raced), (None, report.UNSAFE))

    def test_repository_reader_contains_artifacts_and_atomic_writer_can_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            repository = run / "repositories" / "web"
            repository.mkdir(parents=True)
            artifact = repository / "implementation.json"
            artifact.write_text('{"status": "implemented"}', encoding="utf-8")
            run_escape = run / "outside"
            run_escape.mkdir()
            (run_escape / "implementation.json").write_text('{"leak": true}', encoding="utf-8")
            (run / "repositories" / "escape.json").write_text('{"leak": true}', encoding="utf-8")

            cases = (
                ("valid", "web", "implementation.json", report.RECORDED),
                ("missing repository", "api", "implementation.json", report.UNRECORDED),
                ("traversing repository", "../outside", "implementation.json", report.UNSAFE),
                ("absolute repository", str(run_escape), "implementation.json", report.UNSAFE),
                ("traversing artifact", "web", "../escape.json", report.UNSAFE),
                ("absolute artifact", "web", str(artifact), report.UNSAFE),
            )
            for label, name, filename, expected_status in cases:
                with self.subTest(case=label):
                    document, status = report._repository_json(run, name, filename)
                    self.assertEqual(status, expected_status)
                    self.assertEqual(document is not None, expected_status == report.RECORDED)

            linked_repository = run / "repositories" / "linked"
            linked_repository.symlink_to(repository, target_is_directory=True)
            linked_artifact = repository / "linked.json"
            linked_artifact.symlink_to(artifact)
            for label, name, filename in (
                ("repository symlink", "linked", "implementation.json"),
                ("artifact symlink", "web", "linked.json"),
            ):
                with self.subTest(case=label):
                    self.assertEqual(report._repository_json(run, name, filename), (None, report.UNSAFE))

            linked_run = root / "linked-run"
            linked_run.mkdir()
            (linked_run / "repositories").symlink_to(run / "repositories", target_is_directory=True)
            self.assertEqual(
                report._repository_json(linked_run, "web", "implementation.json"), (None, report.UNSAFE)
            )

            outside = root / "outside.md"
            outside.write_text("original", encoding="utf-8")
            output = root / "report.md"
            output.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                report._atomic_write(output, "blocked", reject_symlink=True)
            self.assertEqual(outside.read_text(encoding="utf-8"), "original")
            report._atomic_write(output, "replacement")
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_text(encoding="utf-8"), "replacement")
            self.assertEqual(outside.read_text(encoding="utf-8"), "original")

    def test_commit_gate_validation_accepts_the_contract_and_rejects_structural_mutations(self):
        self.assertTrue(report._valid_commit_gate(valid_gate()))
        self.assertTrue(report._valid_commit_gate(valid_gate("user_confirmed")))
        invalid = (
            ("boolean version", changed(("version",), True)),
            ("wrong version", changed(("version",), 2)),
            ("empty repository", changed(("repository_root",), "")),
            ("files not mapping", changed(("files",), [])),
            ("absolute file", changed(("files",), {"/etc/passwd": valid_gate()["files"]["src/app.py"]})),
            ("traversing file", changed(("files",), {"../secret": valid_gate()["files"]["src/app.py"]})),
            ("bad file mode", changed(("files", "src/app.py", "mode"), "100600")),
            ("bad digest", changed(("files", "src/app.py", "sha256"), "A" * 64)),
            ("boolean mtime", changed(("files", "src/app.py", "mtime_ns"), True)),
            ("deleted digest", changed(("files", "deleted.py", "sha256"), "b" * 64)),
            ("unknown operation", changed(("operations", 0, "kind"), "chmod")),
            ("operation extra key", changed(("operations", 0), {"kind": "modify", "path": "src/app.py", "source": "x"})),
            ("operation traversal", changed(("operations", 0, "path"), "../app.py")),
            ("rename source traversal", changed(("operations", 1, "source"), "../old.py")),
            ("unsorted operations", changed(("operations",), list(reversed(valid_gate()["operations"])))),
            ("verification symlink", changed(("verification_file", "type"), "symlink")),
            ("verification digest mismatch", changed(("verification_file_sha256",), "c" * 64)),
            ("failed verification", changed(("verification", "verdict"), "fail")),
            ("unknown source", changed(("verification", "source"), "manual")),
            ("empty channels", changed(("verification", "channels"), [])),
            ("non-string channel", changed(("verification", "channels"), [1])),
            ("blank channel", changed(("verification", "channels"), [" "])),
            ("runs not list", changed(("verification", "automated_runs"), {})),
            ("empty automated runs", changed(("verification", "automated_runs"), [])),
            ("non-string automated run", changed(("verification", "automated_runs"), [1])),
            ("failed automated run", changed(("verification", "failed_automated_runs"), ["pytest -k failed"])),
            ("failed runs not list", changed(("verification", "failed_automated_runs"), {})),
            ("blank failed run", changed(("verification", "failed_automated_runs"), [" "])),
            ("risks not list", changed(("verification", "residual_risks"), {})),
            ("non-string risk", changed(("verification", "residual_risks"), [1])),
            ("user-confirmed successful run", changed(("verification", "automated_runs"), ["pytest"], "user_confirmed")),
            ("user-confirmed without failed run", changed(("verification", "failed_automated_runs"), [], "user_confirmed")),
            ("user-confirmed without risk", changed(("verification", "residual_risks"), [], "user_confirmed")),
            ("blockers present", changed(("verification", "blockers"), ["blocked"])),
        )
        for label, document in invalid:
            with self.subTest(case=label):
                self.assertFalse(report._valid_commit_gate(document))


if __name__ == "__main__":
    unittest.main()
