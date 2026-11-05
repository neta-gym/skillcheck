"""Durable state. JSON files under the plugin data dir; writes go
tmp-then-rename so a crash mid-write never leaves a torn store.

Layout per tracked skill:
  <data>/skills/<skill>/cases.json       generated regression cases
  <data>/skills/<skill>/runs.jsonl       one run record per line, append-only
  <data>/skills/<skill>/baseline.json    the promoted ("green") run pointer
  <data>/skills/<skill>/state.json       pending candidate / tracking state
  <data>/skills/<skill>/snapshots/<id>/  pre-write copies + meta.json
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .paths import data_dir, skill_dir

_lock = threading.RLock()
_SAFE = re.compile(r"[^a-z0-9_-]+")


def safe_name(skill: str) -> str:
    s = _SAFE.sub("-", str(skill).strip().lower()).strip("-")
    return s[:64] or "unnamed"


def _root(skill: str) -> Path:
    return data_dir() / "skills" / safe_name(skill)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def revision_hash(skill: str) -> str:
    """sha256 over sorted (relpath, content-hash) of every file in the skill dir."""
    root = skill_dir(skill)
    h = hashlib.sha256()
    if not root.is_dir():
        return "absent"
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts):
            h.update(str(p.relative_to(root)).encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def skill_files(skill: str) -> dict[str, str]:
    """{relative path: text} for every file in the skill dir (best effort)."""
    root = skill_dir(skill)
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts):
            try:
                out[str(p.relative_to(root))] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out


# --- cases ---------------------------------------------------------------

def load_cases(skill: str) -> list[dict[str, Any]]:
    with _lock:
        return _read_json(_root(skill) / "cases.json", {}).get("cases", [])


def save_cases(skill: str, cases: list[dict[str, Any]]) -> None:
    with _lock:
        _atomic_write(_root(skill) / "cases.json",
                      json.dumps({"skill": skill, "cases": cases}, indent=2))


# --- runs ----------------------------------------------------------------

def record_run(skill: str, run: dict[str, Any]) -> None:
    with _lock:
        path = _root(skill) / "runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(run) + "\n")


def load_runs(skill: str, limit: int = 50) -> list[dict[str, Any]]:
    path = _root(skill) / "runs.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    runs = []
    for line in lines[-limit:]:
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def get_run(skill: str, run_id: str) -> Optional[dict[str, Any]]:
    for run in reversed(load_runs(skill, limit=500)):
        if run.get("run_id") == run_id:
            return run
    return None


# --- baseline ------------------------------------------------------------

def load_baseline(skill: str) -> Optional[dict[str, Any]]:
    with _lock:
        return _read_json(_root(skill) / "baseline.json", None)


def save_baseline(skill: str, baseline: dict[str, Any]) -> None:
    with _lock:
        _atomic_write(_root(skill) / "baseline.json", json.dumps(baseline, indent=2))


# --- state ---------------------------------------------------------------

def load_state(skill: str) -> dict[str, Any]:
    with _lock:
        return _read_json(_root(skill) / "state.json", {})


def save_state(skill: str, state: dict[str, Any]) -> None:
    with _lock:
        _atomic_write(_root(skill) / "state.json", json.dumps(state, indent=2))


def tracked_skills() -> list[str]:
    root = data_dir() / "skills"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# --- snapshots -----------------------------------------------------------

def new_snapshot(skill: str, trigger: str) -> Optional[str]:
    """Copy the skill's current files aside. Returns snapshot id, or None if
    the skill does not exist on disk yet (a create writes a new skill)."""
    files = skill_files(skill)
    if not files:
        return None
    sid = f"{int(time.time())}-{os.getpid()}"
    dest = _root(skill) / "snapshots" / sid
    with _lock:
        for rel, text in files.items():
            _atomic_write(dest / rel, text)
        _atomic_write(dest / "meta.json", json.dumps({
            "skill": skill, "snapshot_id": sid, "trigger": trigger,
            "created_at": time.time(), "revision_hash": revision_hash(skill),
        }, indent=2))
    return sid


def latest_snapshot(skill: str) -> Optional[str]:
    root = _root(skill) / "snapshots"
    if not root.is_dir():
        return None
    ids = sorted(p.name for p in root.iterdir() if p.is_dir())
    return ids[-1] if ids else None


def snapshot_files(skill: str, snapshot_id: str) -> dict[str, str]:
    root = _root(skill) / "snapshots" / snapshot_id
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "meta.json":
            try:
                out[str(p.relative_to(root))] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out
