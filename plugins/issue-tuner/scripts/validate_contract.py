#!/usr/bin/env python3
import json
import sys


RESULT_FIELDS = {
    "reproduction": {
        "status": str,
        "source": str,
        "scenario": str,
        "limitations": list,
        "blockers": list,
    },
    "diagnosis": {
        "status": str,
        "root_cause": str,
        "evidence": list,
        "symbols": list,
        "blockers": list,
    },
    "implementation": {
        "status": str,
        "repository": str,
        "changed_files": list,
        "red_runs": list,
        "blockers": list,
    },
    "verification": {
        "verdict": str,
        "source": str,
        "channels": list,
        "automated_runs": list,
        "failed_automated_runs": list,
        "residual_risks": list,
        "blockers": list,
    },
}
RESULT_ENUMS = {
    "reproduction": {
        "status": ["reproduced", "failed", "blocked"],
        "source": ["automated", "user_confirmed"],
    },
    "diagnosis": {"status": ["diagnosed", "blocked"]},
    "implementation": {"status": ["implemented", "blocked"]},
    "verification": {
        "verdict": ["pass", "fail"],
        "source": ["automated", "user_confirmed"],
    },
}


def _is_nonempty_string(value):
    return isinstance(value, str) and bool(value)


def _is_supported_kind(kind):
    return isinstance(kind, str) and (kind == "issue-report" or kind in RESULT_FIELDS)


def _reject_constant(_):
    raise ValueError


def _required_string(data, field, errors):
    if not _is_nonempty_string(data.get(field)):
        errors.append(f"{field} must be a non-empty string")


def _string_list(data, field, errors, nonempty=False):
    value = data.get(field)
    if not isinstance(value, list) or (nonempty and not value):
        errors.append(f"{field} must be a{' non-empty' if nonempty else ''} array")
    elif not all(_is_nonempty_string(item) for item in value):
        errors.append(f"{field} must contain non-empty strings")


def _allowed_fields(data, allowed, path, errors):
    for field in data:
        if field not in allowed:
            errors.append(f"{path + '.' if path else ''}{field} is not allowed")


def _string_array(data, field, errors, nonempty_items=False):
    value = data.get(field)
    if not isinstance(value, list):
        errors.append(f"{field} must be a list")
    elif not all(isinstance(item, str) for item in value):
        errors.append(f"{field} must contain strings")
    elif nonempty_items and not all(item.strip() for item in value):
        errors.append(f"{field} must contain non-empty strings")


def _validate_issue_report(data):
    errors = []
    _allowed_fields(
        data,
        {"issue", "context", "environment", "repositories", "verification"},
        "",
        errors,
    )
    issue = data.get("issue")
    if not isinstance(issue, dict):
        errors.append("issue must be an object")
    else:
        _allowed_fields(issue, {"id", "expected", "actual", "steps"}, "issue", errors)
        _required_string(issue, "expected", errors)
        _required_string(issue, "actual", errors)
        _string_list(issue, "steps", errors, nonempty=True)
        if "id" in issue and not _is_nonempty_string(issue["id"]):
            errors.append("issue.id must be a non-empty string")

    if "context" in data:
        context = data["context"]
        if not isinstance(context, dict):
            errors.append("context must be an object")
        else:
            _allowed_fields(
                context,
                {"product", "product_version", "business_project"},
                "context",
                errors,
            )
            for field in ("product", "product_version", "business_project"):
                if field in context and not _is_nonempty_string(context[field]):
                    errors.append(f"context.{field} must be a non-empty string")

    environment = data.get("environment")
    if not isinstance(environment, dict):
        errors.append("environment must be an object")
    else:
        _allowed_fields(environment, {"name", "target"}, "environment", errors)
        _required_string(environment, "name", errors)
        _required_string(environment, "target", errors)

    repositories = data.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        errors.append("repositories must be a non-empty array")
    else:
        for index, repository in enumerate(repositories):
            if not isinstance(repository, dict):
                errors.append("repositories must contain objects")
                continue
            _allowed_fields(repository, {"name", "path", "branch"}, f"repositories.{index}", errors)
            for field in ("name", "path", "branch"):
                if not _is_nonempty_string(repository.get(field)):
                    errors.append(f"repositories.{index}.{field} must be a non-empty string")

    verification = data.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification must be an object")
    else:
        _allowed_fields(verification, {"channels", "mutating"}, "verification", errors)
        _string_list(verification, "channels", errors, nonempty=True)
        if "mutating" in verification and not isinstance(verification["mutating"], bool):
            errors.append("verification.mutating must be a boolean")
        if (
            isinstance(environment, dict)
            and environment.get("name") == "production"
            and verification.get("mutating") is True
        ):
            errors.append("production verification must be read-only")
    return errors


def _validate_result(kind, data):
    errors = []
    fields = RESULT_FIELDS[kind]
    _allowed_fields(data, fields, "", errors)
    for field, expected_type in fields.items():
        value = data.get(field)
        if not isinstance(value, expected_type):
            errors.append(f"{field} must be a {expected_type.__name__}")
        elif expected_type is str and not _is_nonempty_string(value):
            errors.append(f"{field} must be a non-empty string")
        elif expected_type is list:
            _string_array(
                data,
                field,
                errors,
                nonempty_items=kind == "verification"
                and field in {"automated_runs", "failed_automated_runs", "residual_risks"},
            )
    for field, allowed in RESULT_ENUMS[kind].items():
        value = data.get(field)
        if isinstance(value, str) and value not in allowed:
            errors.append(f"{field} must be one of: {', '.join(allowed)}")
    if kind == "verification" and isinstance(data.get("channels"), list) and not data["channels"]:
        errors.append("channels must be a non-empty array")
    if kind == "verification" and data.get("verdict") == "pass":
        source = data.get("source")
        automated_runs = data.get("automated_runs")
        failed_automated_runs = data.get("failed_automated_runs")
        residual_risks = data.get("residual_risks")
        if source == "automated" and isinstance(automated_runs, list) and not automated_runs:
            errors.append("automated pass requires automated_runs")
        if (
            source == "automated"
            and isinstance(failed_automated_runs, list)
            and failed_automated_runs
        ):
            errors.append("automated pass must not include failed_automated_runs")
        if source == "user_confirmed":
            if isinstance(automated_runs, list) and automated_runs:
                errors.append("user_confirmed pass must not use automated_runs")
            if isinstance(failed_automated_runs, list) and not failed_automated_runs:
                errors.append("user_confirmed pass requires failed_automated_runs")
            if isinstance(residual_risks, list) and not residual_risks:
                errors.append("user_confirmed pass requires residual_risks")
    return errors


def validate(kind: str, data: dict) -> list[str]:
    if not _is_supported_kind(kind):
        return ["unknown contract kind"]
    if not isinstance(data, dict):
        return ["data must be an object"]
    if kind == "issue-report":
        return _validate_issue_report(data)
    return _validate_result(kind, data)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: validate_contract.py <kind> <json-file>", file=sys.stderr)
        return 2
    if not _is_supported_kind(argv[0]):
        print("unknown contract kind", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as json_file:
            data = json.load(json_file, parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        print("could not load JSON file", file=sys.stderr)
        return 2
    errors = validate(argv[0], data)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
