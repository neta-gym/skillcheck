"""Verdicts and rendering. A REGRESSION claim requires a promoted baseline:
the case passed then and fails now. Without a baseline every run is FIRST-RUN
evidence for a human to review - the plugin never calls anything a regression
on its own first look.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from . import store


def verdict(skill: str, run: dict[str, Any]) -> tuple[str, list[str]]:
    """(GREEN | REGRESSION | FIRST-RUN | INCONCLUSIVE, notes)."""
    baseline = store.load_baseline(skill)
    results = {r["case_id"]: r for r in run.get("results", [])}
    notes: list[str] = []
    if not results:
        return "INCONCLUSIVE", ["no cases ran"]
    if any(r.get("inconclusive") for r in results.values()):
        notes.append("some cases inconclusive (judge offline / launch failure / timeout)")
    if not baseline:
        return "FIRST-RUN", notes + ["no promoted baseline yet - review, then /skillcheck promote"]
    base_map = {r["case_id"]: bool(r.get("passed")) for r in baseline.get("results", [])}
    regressions = [cid for cid, r in results.items()
                   if base_map.get(cid) is True and not r["passed"]]
    if regressions:
        notes.insert(0, "regressed vs baseline run " + str(baseline.get("run_id")) + ": "
                     + ", ".join(regressions))
        return "REGRESSION", notes
    new_cases = [cid for cid in results if cid not in base_map]
    if new_cases:
        notes.append("new cases without baseline history: " + ", ".join(new_cases))
    return "GREEN", notes


def render_run(skill: str, run: dict[str, Any], v: Optional[str] = None,
               notes: Optional[list[str]] = None) -> str:
    if v is None:
        v, notes = verdict(skill, run)
    notes = notes or []
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.get("ts", 0)))
    flagged = [r["case_id"] for r in run.get("results", []) if r.get("fixture_warning")]
    lines = [
        f"skillcheck: {skill} - {v}",
        f"run {run.get('run_id')} | {ts} | revision {str(run.get('revision_hash'))[:12]}"
        f" | {run.get('passed', 0)}/{run.get('total', 0)} passed",
        "",
        f"{'case':<24} {'result':<14} detail",
        "-" * 72,
    ]
    for r in run.get("results", []):
        label = "PASS" if r["passed"] else ("INCONCLUSIVE" if r.get("inconclusive") else "FAIL")
        judged = any(c.get("kind") == "judge" for c in r.get("checks", []))
        if judged:
            label += " (JUDGE)" if any(c.get("kind") == "judge" and c.get("passed") for c in r["checks"]) else ""
        lines.append(f"{r['case_id']:<24} {label:<14} {r.get('detail', '')[:80]}")
    if flagged:
        lines += ["", "warning: cases with filename-like fixtures (not inline data): "
                  + ", ".join(flagged)
                  + " - child sessions cannot open files; repair these cases before promoting"]
    if notes:
        lines += ["", *[f"note: {n}" for n in notes]]
    if v == "REGRESSION":
        lines += ["", "next: review the failing cases. `/skillcheck revert` restores the last "
                  "pre-write snapshot (manual, reversible, user-invoked only); "
                  "`/skillcheck promote` accepts the candidate once fixed."]
    elif v == "FIRST-RUN":
        lines += ["", "next: read the cases (`skillcheck_cases`), then `/skillcheck promote` "
                  "to set this run as the baseline."]
    return "\n".join(lines)


def render_status() -> str:
    skills = store.tracked_skills()
    if not skills:
        return ("skillcheck: no skills tracked yet. Tracking starts automatically on the "
                "next skill write (skill_manage or a file write into the skills directory).")
    lines = ["skillcheck status", ""]
    for skill in skills:
        state = store.load_state(skill)
        cases = store.load_cases(skill)
        baseline = store.load_baseline(skill)
        runs = store.load_runs(skill, limit=1)
        last = runs[-1] if runs else None
        v = verdict(skill, last)[0] if last else "NO RUNS"
        pending = state.get("pending_candidate") or {}
        flag = f" | unverified write: {pending.get('action')} at revision {str(pending.get('revision_hash'))[:12]}" if pending else ""
        lines.append(
            f"- {skill}: {len(cases)} cases | baseline {'set' if baseline else 'unset'}"
            f" | last run {v}{flag}")
    return "\n".join(lines)
