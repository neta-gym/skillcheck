"""skillcheck - CI for agent skills.

Hooks watch skill writes (skill_manage operations and plain file writes into
the skills directory, plus the on_skill_lifecycle hook) and snapshot the
previous revision. Tools generate regression cases, run them against the
candidate revision in narrowed child sessions, and report GREEN / REGRESSION /
FIRST-RUN. Promotion and revert are manual, user-invoked actions only - the
plugin never blocks a write and never rewrites a skill on its own.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .core import cases as cases_mod
from .core import detect, report, store
from .core.paths import skill_dir
from .core.runner import run_cases

logger = logging.getLogger(__name__)

_PENDING_TTL_S = 3600.0
# (owner, tool_call_id, skill) -> (ts, snapshot_id): pre-write snapshots awaiting their post hook.
_pending: dict[tuple[str, str, str], tuple[float, Optional[str]]] = {}
_lock = threading.Lock()


def _owner(task_id: str, session_id: str) -> str:
    return task_id or session_id or "default"


def _prune_pending() -> None:
    now = time.time()
    for key in [k for k, (ts, _) in _pending.items() if now - ts > _PENDING_TTL_S]:
        _pending.pop(key, None)


# --- hooks ---------------------------------------------------------------

def _on_pre_tool_call(tool_name: str = "", args: Optional[dict] = None, task_id: str = "",
                      session_id: str = "", tool_call_id: str = "", **_: Any) -> None:
    """Snapshot the current revision of any skill a call is about to write.
    Never returns a block/modify - observation only."""
    try:
        writes = detect.detect_pre(tool_name, args)
        if not writes:
            return
        owner = _owner(task_id, session_id)
        with _lock:
            _prune_pending()
            for w in writes:
                if (owner, tool_call_id, w.skill) in _pending:
                    continue
                sid = store.new_snapshot(w.skill, trigger=f"{w.via}:{w.action}")
                _pending[(owner, tool_call_id, w.skill)] = (time.time(), sid)
    except Exception:
        logger.debug("skillcheck pre_tool_call failed", exc_info=True)


def _mark_candidate(write: detect.SkillWrite, session_id: str) -> None:
    state = store.load_state(write.skill)
    state["pending_candidate"] = {
        "action": write.action, "via": write.via, "ts": time.time(),
        "revision_hash": store.revision_hash(write.skill), "session_id": session_id,
    }
    store.save_state(write.skill, state)


def _on_post_tool_call(tool_name: str = "", args: Optional[dict] = None, status: str = "",
                       task_id: str = "", session_id: str = "", tool_call_id: str = "",
                       **_: Any) -> None:
    """Confirm a landed write and mark the revision as an unverified candidate."""
    try:
        writes = detect.detect_post(tool_name, args, status or "ok")
        if not writes:
            return
        owner = _owner(task_id, session_id)
        with _lock:
            for w in writes:
                _pending.pop((owner, tool_call_id, w.skill), None)  # snapshot stays on disk
                _mark_candidate(w, session_id)
    except Exception:
        logger.debug("skillcheck post_tool_call failed", exc_info=True)


def _on_skill_lifecycle(action: str = "", skill_name: str = "", session_id: str = "",
                        **_: Any) -> None:
    """Authoritative landed-mutation signal from Hermes itself."""
    try:
        write = detect.from_lifecycle(action, skill_name)
        if write is not None:
            _mark_candidate(write, session_id)
    except Exception:
        logger.debug("skillcheck on_skill_lifecycle failed", exc_info=True)


# --- tool handlers ---------------------------------------------------------

def _h_status(args: dict, **_: Any) -> str:
    name = str(args.get("name", "") or "").strip()
    if name:
        state = store.load_state(name)
        cases = store.load_cases(name)
        baseline = store.load_baseline(name)
        runs = store.load_runs(name, limit=1)
        out = [f"skillcheck: {name}",
               f"cases: {len(cases)} | baseline: {'set (run ' + baseline['run_id'] + ')' if baseline else 'unset'}",
               f"revision on disk: {store.revision_hash(name)[:12]}"]
        pending = state.get("pending_candidate")
        if pending:
            out.append(f"UNVERIFIED write pending: {pending.get('action')} via {pending.get('via')}")
        if runs:
            out.append("")
            out.append(report.render_run(name, runs[-1]))
        return "\n".join(out)
    return report.render_status()


def _h_generate(args: dict, ctx: Any = None, **_: Any) -> str:
    name = str(args.get("name", "") or "").strip()
    if not name:
        return "skillcheck_generate needs a skill name."
    if ctx is None:
        return "case generation needs the host LLM; call this tool from inside a session."
    count = int(args.get("count", 4) or 4)
    new_cases, note = cases_mod.generate_cases(ctx, name, count=count)
    try:  # TEMP debug: surface the outcome where tests can read it
        from .core.paths import data_dir as _dd
        (_dd() / "debug.log").write_text(note, encoding="utf-8")
    except Exception:
        pass
    if not new_cases:
        return f"skillcheck: {note}"
    existing = {c["id"]: c for c in store.load_cases(name)}
    for c in new_cases:
        existing.setdefault(c["id"], c)
    store.save_cases(name, list(existing.values()))
    ids = ", ".join(c["id"] for c in new_cases)
    return (f"skillcheck: {note} for {name}: {ids}\n"
            "Review them with skillcheck_cases, then run them with skillcheck_run.")


def _h_cases(args: dict, **_: Any) -> str:
    name = str(args.get("name", "") or "").strip()
    if not name:
        return "skillcheck_cases needs a skill name."
    cases = store.load_cases(name)
    if not cases:
        return f"skillcheck: no cases for {name} yet - run skillcheck_generate first."
    lines = [f"skillcheck cases: {name} ({len(cases)})", ""]
    for c in cases:
        kinds = ",".join(ch["type"] for ch in c.get("checks", [])) or "no checks"
        judge = " + judge" if c.get("judge_rubric") else ""
        warn = " [FIXTURE WARNING]" if c.get("fixture_warning") else ""
        lines.append(f"- {c['id']} [{c.get('status', 'draft')}]{warn} {c.get('title', '')}")
        lines.append(f"  brief: {c.get('brief', '')[:120]}")
        lines.append(f"  checks: {kinds}{judge}")
    return "\n".join(lines)


def _h_run(args: dict, ctx: Any = None, **_: Any) -> str:
    name = str(args.get("name", "") or "").strip()
    if not name:
        return "skillcheck_run needs a skill name."
    if ctx is None:
        return "runs need child sessions and the host LLM; call this tool from inside a session."
    cases = store.load_cases(name)
    if not cases:
        return f"skillcheck: no cases for {name} yet - run skillcheck_generate first."
    wanted = args.get("case_ids")
    if isinstance(wanted, list) and wanted:
        cases = [c for c in cases if c["id"] in set(map(str, wanted))] or cases
    judge_llm = getattr(ctx, "llm", None)
    run = run_cases(ctx, name, cases, judge_llm=judge_llm)
    v, notes = report.verdict(name, run)
    state = store.load_state(name)
    if v == "GREEN" and state.get("pending_candidate"):
        state["pending_candidate"]["last_verdict"] = "GREEN"
        store.save_state(name, state)
        notes.append("candidate passed - promote it with skillcheck_promote to make it the baseline")
    return report.render_run(name, run, v, notes)


def _h_promote(args: dict, **_: Any) -> str:
    name = str(args.get("name", "") or "").strip()
    run_id = str(args.get("run_id", "") or "").strip()
    if not name:
        return "skillcheck_promote needs a skill name."
    run = store.get_run(name, run_id) if run_id else (store.load_runs(name, limit=1) or [None])[-1]
    if not run:
        return f"skillcheck: no run to promote for {name} - run skillcheck_run first."
    if run.get("inconclusive") or any(r.get("inconclusive") for r in run.get("results", [])):
        return (f"skillcheck: run {run['run_id']} has inconclusive cases; promoting it would "
                "bake in guesswork. Re-run when the judge/children are available.")
    store.save_baseline(name, {
        "run_id": run["run_id"], "ts": run.get("ts"), "revision_hash": run.get("revision_hash"),
        "results": [{"case_id": r["case_id"], "passed": r["passed"]} for r in run.get("results", [])],
    })
    state = store.load_state(name)
    state.pop("pending_candidate", None)
    store.save_state(name, state)
    return (f"skillcheck: {name} baseline set to run {run['run_id']} "
            f"({run.get('passed', 0)}/{run.get('total', 0)} passed, revision "
            f"{str(run.get('revision_hash'))[:12]}). Future writes are checked against this.")


def _h_revert(args: dict, **_: Any) -> str:
    """Manual, user-invoked restore of the latest pre-write snapshot. Takes a
    safety snapshot of the CURRENT state first, so a revert is itself reversible."""
    name = str(args.get("name", "") or "").strip()
    if not name:
        return "skillcheck_revert needs a skill name."
    sid = store.latest_snapshot(name)
    if not sid:
        return f"skillcheck: no snapshot stored for {name}; nothing to revert to."
    files = store.snapshot_files(name, sid)
    if not files:
        return f"skillcheck: snapshot {sid} is empty; refusing to revert."
    safety = store.new_snapshot(name, trigger="pre-revert")
    root = skill_dir(name)
    keep = set(files)
    removed, written = [], []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and str(p.relative_to(root)) not in keep:
                try:
                    p.unlink()
                    removed.append(str(p.relative_to(root)))
                except OSError:
                    pass
    for rel, text in files.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        written.append(rel)
    state = store.load_state(name)
    state.pop("pending_candidate", None)
    store.save_state(name, state)
    return (f"skillcheck: {name} reverted to snapshot {sid} "
            f"({len(written)} files restored, {len(removed)} removed)."
            + (f" Pre-revert state kept as snapshot {safety}." if safety else "")
            + " Run skillcheck_run to confirm the restored revision still passes.")


# --- registration ----------------------------------------------------------

def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "additionalProperties": False,
                           "properties": props, "required": required}}


_NAME_PROP = {"name": {"type": "string", "description": "Skill directory name (e.g. 'axolotl')."}}


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("on_skill_lifecycle", _on_skill_lifecycle)

    ctx.register_tool(
        "skillcheck_status", "skillcheck",
        _schema("skillcheck_status",
                "Show skillcheck tracking state: watched skills, case counts, baselines, "
                "unverified writes, last run verdicts.",
                {}, []),
        lambda args, **kw: _h_status(args, **kw),
        description="Skillcheck tracking status", emoji="🧪")
    ctx.register_tool(
        "skillcheck_generate", "skillcheck",
        _schema("skillcheck_generate",
                "Generate synthetic regression cases for a skill from its on-disk definition. "
                "Cases are drafts for review until the skill is run.",
                {**_NAME_PROP, "count": {"type": "integer", "description": "Cases to generate (1-10, default 4)."}},
                ["name"]),
        lambda args, **kw: _h_generate(args, ctx=ctx, **kw),
        description="Generate regression cases for a skill", emoji="🧪")
    ctx.register_tool(
        "skillcheck_cases", "skillcheck",
        _schema("skillcheck_cases", "List the stored regression cases for a skill.",
                {**_NAME_PROP}, ["name"]),
        lambda args, **kw: _h_cases(args, **kw),
        description="List regression cases", emoji="🧪")
    ctx.register_tool(
        "skillcheck_run", "skillcheck",
        _schema("skillcheck_run",
                "Run a skill's regression cases against its current on-disk revision in "
                "tool-less child sessions and report GREEN / REGRESSION / FIRST-RUN against "
                "the promoted baseline.",
                {**_NAME_PROP, "case_ids": {"type": "array", "items": {"type": "string"},
                                            "description": "Optional subset of case ids."}},
                ["name"]),
        lambda args, **kw: _h_run(args, ctx=ctx, **kw),
        description="Run regression cases for a skill", emoji="🧪")
    ctx.register_tool(
        "skillcheck_promote", "skillcheck",
        _schema("skillcheck_promote",
                "Manually accept a run as the new green baseline for a skill. Refuses runs "
                "with inconclusive cases.",
                {**_NAME_PROP, "run_id": {"type": "string", "description": "Run to promote (default: latest)."}},
                ["name"]),
        lambda args, **kw: _h_promote(args, **kw),
        description="Promote a run to baseline", emoji="🧪")
    ctx.register_tool(
        "skillcheck_revert", "skillcheck",
        _schema("skillcheck_revert",
                "Manually restore a skill's latest pre-write snapshot. Snapshots the current "
                "state first so the revert is itself reversible.",
                {**_NAME_PROP}, ["name"]),
        lambda args, **kw: _h_revert(args, **kw),
        description="Restore last pre-write snapshot", emoji="🧪")

    def _slash(raw_args: str) -> str:
        parts = (raw_args or "").split()
        sub = parts[0].lower() if parts else "status"
        name = parts[1] if len(parts) > 1 else ""
        if sub == "status":
            return _h_status({"name": name})
        if sub == "generate":
            return _h_generate({"name": name}, ctx=ctx)
        if sub == "cases":
            return _h_cases({"name": name})
        if sub == "run":
            return _h_run({"name": name}, ctx=ctx)
        if sub == "promote":
            return _h_promote({"name": name})
        if sub == "revert":
            return _h_revert({"name": name})
        return ("skillcheck commands: /skillcheck status [name] | generate <name> | cases <name> "
                "| run <name> | promote <name> | revert <name>")

    ctx.register_command("skillcheck", _slash,
                         description="Skill regression CI: status/generate/cases/run/promote/revert",
                         args_hint="<action> [skill]")

    def _cli_setup(sub: Any) -> None:
        sub.add_argument("action", choices=["status", "cases"], nargs="?", default="status")
        sub.add_argument("name", nargs="?", default="")

    def _cli_handler(ns: Any) -> None:
        action = getattr(ns, "action", "status")
        name = getattr(ns, "name", "") or ""
        if action == "cases" and name:
            print(_h_cases({"name": name}))
        else:
            print(_h_status({"name": name}))

    try:
        ctx.register_cli_command("skillcheck", "Skill regression CI (read-only status)",
                                 _cli_setup, _cli_handler)
    except Exception:
        logger.debug("skillcheck CLI command registration failed", exc_info=True)
