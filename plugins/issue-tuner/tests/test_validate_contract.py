import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_contract.py"
SPEC = importlib.util.spec_from_file_location("validate_contract", SCRIPT)
validate_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_contract)


class ValidateContractTest(unittest.TestCase):
    def test_accepts_valid_issue_report(self):
        report = {
            "issue": {"expected": "new", "actual": "old", "steps": ["save"]},
            "environment": {"name": "staging", "target": "http://localhost:3000"},
            "repositories": [{"name": "app", "path": ".", "branch": "main"}],
            "verification": {"channels": ["browser"]},
        }

        self.assertEqual(validate_contract.validate("issue-report", report), [])

    def test_rejects_mutating_production_verification(self):
        report = {
            "issue": {"expected": "new", "actual": "old", "steps": ["save"]},
            "environment": {"name": "production", "target": "https://example.invalid"},
            "repositories": [{"name": "app", "path": ".", "branch": "main"}],
            "verification": {"channels": ["browser"], "mutating": True},
        }

        self.assertIn(
            "production verification must be read-only",
            validate_contract.validate("issue-report", report),
        )

    def test_rejects_issue_report_fields_that_diverge_from_schema(self):
        report = {
            "issue": {"id": 123, "expected": "new", "actual": "old", "steps": ["save"]},
            "context": {"product": "demo"},
            "environment": {"name": "staging", "target": "http://localhost:3000"},
            "repositories": [{"name": "app", "path": ".", "branch": "main"}],
            "verification": {"channels": ["browser"]},
            "unexpected": True,
        }

        errors = validate_contract.validate("issue-report", report)

        self.assertIn("issue.id must be a non-empty string", errors)
        self.assertIn("unexpected is not allowed", errors)

    def test_rejects_null_context_when_context_is_present(self):
        report = {
            "issue": {"expected": "new", "actual": "old", "steps": ["save"]},
            "context": None,
            "environment": {"name": "staging", "target": "http://localhost:3000"},
            "repositories": [{"name": "app", "path": ".", "branch": "main"}],
            "verification": {"channels": ["browser"]},
        }

        self.assertIn("context must be an object", validate_contract.validate("issue-report", report))

    def test_rejects_result_array_items_that_do_not_match_schema(self):
        result = {
            "status": "reproduced",
            "source": "report",
            "scenario": "save",
            "limitations": [7],
            "blockers": [],
        }

        self.assertIn(
            "limitations must contain strings",
            validate_contract.validate("reproduction", result),
        )

    def test_accepts_each_result_contract(self):
        results = {
            "reproduction": {"status": "reproduced", "source": "automated", "scenario": "save", "limitations": [], "blockers": []},
            "diagnosis": {"status": "diagnosed", "root_cause": "state", "evidence": [], "symbols": [], "blockers": []},
            "implementation": {"status": "implemented", "repository": "app", "changed_files": [], "red_runs": [], "blockers": []},
            "verification": {"verdict": "pass", "source": "automated", "channels": ["unit"], "automated_runs": ["unit: pass"], "failed_automated_runs": [], "residual_risks": [], "blockers": []},
        }

        for kind, result in results.items():
            with self.subTest(kind=kind):
                self.assertEqual(validate_contract.validate(kind, result), [])

    def test_rejects_invalid_result_enums_and_empty_verification_channels(self):
        invalid = {
            "reproduction": {"status": "ok", "source": "report", "scenario": "save", "limitations": [], "blockers": []},
            "diagnosis": {"status": "ok", "root_cause": "state", "evidence": [], "symbols": [], "blockers": []},
            "implementation": {"status": "ok", "repository": "app", "changed_files": [], "red_runs": [], "blockers": []},
            "verification": {"verdict": "pass | fail", "source": "automated", "channels": ["unit"], "automated_runs": ["unit: pass"], "failed_automated_runs": [], "residual_risks": [], "blockers": []},
        }

        for kind, result in invalid.items():
            with self.subTest(kind=kind):
                self.assertTrue(validate_contract.validate(kind, result))

    def test_rejects_empty_verification_channels_at_runtime(self):
        result = {
            "verdict": "pass",
            "source": "automated",
            "channels": [],
            "automated_runs": ["unit: pass"],
            "failed_automated_runs": [],
            "residual_risks": [],
            "blockers": [],
        }

        self.assertIn(
            "channels must be a non-empty array",
            validate_contract.validate("verification", result),
        )

    def test_pass_requires_evidence_for_its_verification_source(self):
        automated = {
            "verdict": "pass",
            "source": "automated",
            "channels": ["unit"],
            "automated_runs": [],
            "failed_automated_runs": [],
            "residual_risks": [],
            "blockers": [],
        }
        user_confirmed = {
            "verdict": "pass",
            "source": "user_confirmed",
            "channels": ["browser"],
            "automated_runs": [],
            "failed_automated_runs": ["Computer Use unavailable"],
            "residual_risks": ["not automated"],
            "blockers": [],
        }

        self.assertIn(
            "automated pass requires automated_runs",
            validate_contract.validate("verification", automated),
        )
        automated["automated_runs"] = ["synthetic voice: pass"]
        automated["failed_automated_runs"] = ["synthetic voice setup failed"]
        self.assertIn(
            "automated pass must not include failed_automated_runs",
            validate_contract.validate("verification", automated),
        )
        self.assertEqual(validate_contract.validate("verification", user_confirmed), [])
        user_confirmed["residual_risks"] = []
        self.assertIn(
            "user_confirmed pass requires residual_risks",
            validate_contract.validate("verification", user_confirmed),
        )
        user_confirmed["residual_risks"] = ["not automated"]
        user_confirmed["failed_automated_runs"] = []
        self.assertIn(
            "user_confirmed pass requires failed_automated_runs",
            validate_contract.validate("verification", user_confirmed),
        )

    def test_rejects_blank_verification_evidence_items(self):
        result = {
            "verdict": "pass",
            "source": "automated",
            "channels": ["unit"],
            "automated_runs": ["unit: pass"],
            "failed_automated_runs": [],
            "residual_risks": [],
            "blockers": [],
        }

        for field in ("automated_runs", "failed_automated_runs", "residual_risks"):
            for value in ("", "   "):
                with self.subTest(field=field, value=value):
                    invalid = {**result, field: [value]}
                    self.assertIn(
                        f"{field} must contain non-empty strings",
                        validate_contract.validate("verification", invalid),
                    )
    def test_runtime_enums_match_schemas(self):
        expected = {
            "reproduction": {"status": ["reproduced", "failed", "blocked"], "source": ["automated", "user_confirmed"]},
            "diagnosis": {"status": ["diagnosed", "blocked"]},
            "implementation": {"status": ["implemented", "blocked"]},
            "verification": {
                "verdict": ["pass", "fail"],
                "source": ["automated", "user_confirmed"],
            },
        }
        schema_root = Path(__file__).parents[1] / "schemas"
        self.assertEqual(getattr(validate_contract, "RESULT_ENUMS", None), expected)

        for kind, fields in expected.items():
            schema = json.loads((schema_root / f"{kind}.schema.json").read_text())
            with self.subTest(kind=kind):
                for field, values in fields.items():
                    self.assertEqual(schema["properties"][field].get("enum"), values)
        verification = json.loads((schema_root / "verification.schema.json").read_text())
        self.assertEqual(verification["properties"]["channels"].get("minItems"), 1)
        self.assertIn("source", verification["required"])
        self.assertIn("failed_automated_runs", verification["required"])
        for field in ("automated_runs", "failed_automated_runs", "residual_risks"):
            self.assertEqual(
                verification["properties"][field]["items"].get("minLength"),
                1,
            )
        self.assertEqual(len(verification.get("allOf", [])), 2)
        self.assertEqual(
            verification["allOf"][0]["then"]["properties"]
            .get("failed_automated_runs", {})
            .get("maxItems"),
            0,
        )

    def test_rejects_synthetic_voice_automated_pass_with_stale_failure(self):
        result = {
            "verdict": "pass",
            "source": "automated",
            "channels": ["synthetic-voice"],
            "automated_runs": ["synthetic voice: pass"],
            "failed_automated_runs": ["synthetic voice: failed"],
            "residual_risks": [],
            "blockers": [],
        }

        self.assertIn(
            "automated pass must not include failed_automated_runs",
            validate_contract.validate("verification", result),
        )

    def test_returns_errors_for_unknown_kind_and_cli_usage(self):
        self.assertEqual(validate_contract.validate("unknown", {}), ["unknown contract kind"])
        self.assertEqual(validate_contract.main([]), 2)

    def test_accepts_whitespace_strings_allowed_by_schema(self):
        report = {
            "issue": {"expected": " ", "actual": "old", "steps": ["save"]},
            "environment": {"name": "staging", "target": "http://localhost:3000"},
            "repositories": [{"name": "app", "path": ".", "branch": "main"}],
            "verification": {"channels": ["browser"]},
        }

        self.assertEqual(validate_contract.validate("issue-report", report), [])

    def test_handles_malformed_kind_without_throwing(self):
        self.assertEqual(validate_contract.validate([], {}), ["unknown contract kind"])

    def test_cli_rejects_invalid_json_inputs_without_leaking_data(self):
        with tempfile.TemporaryDirectory() as directory:
            files = {
                "malformed.json": b"{",
                "invalid-utf8.json": b"\xff",
                "constant.json": b'{"issue": NaN}',
            }
            for name, contents in files.items():
                path = Path(directory) / name
                path.write_bytes(contents)
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    self.assertEqual(validate_contract.main(["issue-report", str(path)]), 2)
                self.assertEqual(stderr.getvalue(), "could not load JSON file\n")

    def test_cli_rejects_unknown_kind_before_loading_file(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = validate_contract.main(["unknown", "does-not-exist.json"])

        self.assertEqual(result, 2)
        self.assertEqual(stderr.getvalue(), "unknown contract kind\n")


if __name__ == "__main__":
    unittest.main()
