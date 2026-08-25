#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import tempfile


SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def run_root() -> Path:
    home = os.environ.get("ISSUE_TUNER_HOME") or Path.home() / ".issue-tuner"
    home = Path(home)
    if not home.is_absolute():
        raise ValueError("ISSUE_TUNER_HOME must be absolute")
    return home / "runs"


def new_state(run_id: str, now: int) -> dict:
    return {
        "run_id": run_id,
        "status": "running",
        "started_at": now,
        "active_started_at": now,
        "active_seconds": 0,
        "stages": {},
    }


def _runs(home):
    root = run_root() if home is None else Path(home) / "runs"
    if not root.is_absolute():
        raise ValueError("home must be absolute")
    if root.is_symlink():
        raise ValueError("runs root must not be a symlink")
    configured_home = root.parent.resolve()
    resolved_root = root.resolve()
    try:
        resolved_root.relative_to(configured_home)
    except ValueError:
        raise ValueError("runs root escapes configured home") from None
    return resolved_root


def _run_dir(run_id, home):
    if not isinstance(run_id, str) or not SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be a safe filename component")
    root = _runs(home)
    directory = root / run_id
    if directory.is_symlink():
        raise ValueError("run directory must not be a symlink")
    try:
        directory.resolve().relative_to(root)
    except ValueError:
        raise ValueError("run directory escapes runs root") from None
    return directory


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, sort_keys=True)
            file.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _load(run_id, home):
    path = _run_dir(run_id, home) / "state.json"
    with path.open(encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict) or state.get("run_id") != run_id:
        raise ValueError("state run_id does not match requested run")
    return state


def _save(state, run_id, home):
    if state.get("run_id") != run_id:
        raise ValueError("state run_id does not match requested run")
    _write_json(_run_dir(run_id, home) / "state.json", state)
    return state


def _timestamp(now):
    if not isinstance(now, int) or isinstance(now, bool):
        raise ValueError("timestamp must be an integer")


def _check_time(state, now):
    _timestamp(now)
    if now < state["updated_at"]:
        raise ValueError("timestamp cannot move backward")


def _metrics(state):
    metrics = {
        "elapsed_seconds": state["elapsed_seconds"],
        "active_seconds": state["active_seconds"],
        "stages": {
            name: {key: value for key, value in stage.items() if key in {"attempts", "outcome"}}
            for name, stage in state["stages"].items()
        },
    }
    if "resolved_at" in state:
        metrics.update(
            {
                key: state[key]
                for key in (
                    "started_at",
                    "resolved_at",
                    "finished_at",
                    "resolution_source",
                    "work_seconds",
                    "wait_seconds",
                )
            }
        )
        metrics["cleanup_seconds"] = state["finished_at"] - state["resolved_at"]
    return metrics


def create(run_id: str, now: int, home=None) -> dict:
    _timestamp(now)
    directory = _run_dir(run_id, home)
    path = directory / "state.json"
    if path.exists():
        raise FileExistsError(f"run already exists: {run_id}")
    state = new_state(run_id, now)
    state["updated_at"] = now
    _write_json(path, state)
    return state


def start(run_id: str, now: int, home=None) -> dict:
    return create(run_id, now, home)


def pause(run_id: str, now: int, home=None) -> dict:
    _timestamp(now)
    state = _load(run_id, home)
    _check_time(state, now)
    if state["status"] != "running":
        raise ValueError("only running runs can pause")
    state["active_seconds"] += now - state["active_started_at"]
    state["active_started_at"] = None
    state["status"] = "paused"
    state["updated_at"] = now
    return _save(state, run_id, home)


def resume(run_id: str, now: int, home=None) -> dict:
    _timestamp(now)
    state = _load(run_id, home)
    _check_time(state, now)
    if state["status"] != "paused":
        raise ValueError("only paused runs can resume")
    state["active_started_at"] = now
    state["status"] = "running"
    state["updated_at"] = now
    return _save(state, run_id, home)


def _stage(state, stage):
    if state["status"] == "finished":
        raise ValueError("finished runs cannot change stages")
    if not isinstance(stage, str) or not stage:
        raise ValueError("stage must be a non-empty string")
    return state["stages"].setdefault(stage, {"attempts": 0})


def record_attempt(run_id: str, stage: str, home=None) -> dict:
    state = _load(run_id, home)
    _stage(state, stage)["attempts"] += 1
    return _save(state, run_id, home)


def set_outcome(run_id: str, stage: str, outcome: str, home=None) -> dict:
    if not isinstance(outcome, str) or not outcome:
        raise ValueError("outcome must be a non-empty string")
    state = _load(run_id, home)
    _stage(state, stage)["outcome"] = outcome
    return _save(state, run_id, home)


def resolve(run_id: str, now: int, source: str, home=None) -> dict:
    _timestamp(now)
    if source not in {"automated", "user_confirmed"}:
        raise ValueError("source must be automated or user_confirmed")
    state = _load(run_id, home)
    if "resolved_at" in state:
        return state
    _check_time(state, now)
    if state["status"] not in {"running", "paused"}:
        raise ValueError("only running or paused runs can resolve")
    work_seconds = state["active_seconds"]
    if state["status"] == "running":
        work_seconds += now - state["active_started_at"]
    state.update(
        {
            "resolved_at": now,
            "resolution_source": source,
            "work_seconds": work_seconds,
            "wait_seconds": now - state["started_at"] - work_seconds,
            "updated_at": now,
        }
    )
    return _save(state, run_id, home)


def finish(run_id: str, now: int, home=None) -> dict:
    _timestamp(now)
    state = _load(run_id, home)
    _check_time(state, now)
    if state["status"] == "finished":
        _write_json(_run_dir(run_id, home) / "metrics.json", _metrics(state))
        return state
    if state["status"] not in {"running", "paused"}:
        raise ValueError("only running or paused runs can finish")
    if state["status"] == "running":
        state["active_seconds"] += now - state["active_started_at"]
        state["active_started_at"] = None
    state["status"] = "finished"
    state["finished_at"] = now
    state["elapsed_seconds"] = now - state["started_at"]
    state["updated_at"] = now
    _save(state, run_id, home)
    _write_json(_run_dir(run_id, home) / "metrics.json", _metrics(state))
    return state
