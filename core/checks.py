"""Case checks. Two tiers, kept visually separate in every report:

- deterministic: contains / not_contains / regex over the child's final answer.
  These are the evidence a regression claim can stand on.
- judged: an LLM grades the answer against a rubric. Softer evidence; always
  labeled JUDGE so nobody mistakes it for a measurement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CheckResult:
    kind: str          # contains | not_contains | regex | judge
    passed: bool
    detail: str


def run_deterministic(answer: str, check: dict[str, Any]) -> CheckResult:
    kind = str(check.get("type", ""))
    value = str(check.get("value", ""))
    if kind == "contains":
        ok = value in answer
        return CheckResult(kind, ok, f"answer {'contains' if ok else 'missing'} {value!r}")
    if kind == "not_contains":
        ok = value not in answer
        return CheckResult(kind, ok, f"answer {'correctly omits' if ok else 'wrongly contains'} {value!r}")
    if kind == "regex":
        try:
            ok = re.search(value, answer, re.DOTALL) is not None
        except re.error as exc:
            return CheckResult(kind, False, f"bad regex {value!r}: {exc}")
        return CheckResult(kind, ok, f"pattern {value!r} {'matched' if ok else 'did not match'}")
    return CheckResult(kind or "unknown", False, f"unknown check type {kind!r}")


JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "passed": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["passed", "reason"],
}


def run_judge(llm: Any, *, skill_name: str, brief: str, answer: str, rubric: str) -> CheckResult:
    """Grade via ctx.llm.complete_structured. Any failure is INCONCLUSIVE, never a pass."""
    messages = [
        {"role": "system", "content": (
            "You grade a regression-test answer for the agent skill "
            f"{skill_name!r} against a rubric. Be strict: anything the rubric "
            "requires and the answer lacks is a fail. Reply only with the JSON.")},
        {"role": "user", "content": (
            f"TASK GIVEN TO THE AGENT:\n{brief}\n\n"
            f"AGENT'S ANSWER:\n{answer}\n\n"
            f"RUBRIC:\n{rubric}")},
    ]
    try:
        import json as _json, re as _re
        result = llm.complete(messages=messages, purpose="skillcheck judge")
        text = getattr(result, "text", None) or getattr(result, "content", "") or ""
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        payload = _json.loads(m.group(0)) if m else None
        if not isinstance(payload, dict) or "passed" not in payload:
            return CheckResult("judge", False, "judge returned no verdict (inconclusive)")
        return CheckResult("judge", bool(payload["passed"]),
                           f"judge: {payload.get('reason', '')[:200]}")
    except Exception as exc:  # judge outages must never read as pass or fail
        return CheckResult("judge", False, f"judge unavailable: {exc} (inconclusive)")


def evaluate_case(answer: Optional[str], case: dict[str, Any], llm: Any = None) -> dict[str, Any]:
    """All checks for one case. answer=None means the run itself failed."""
    if answer is None:
        return {"case_id": case.get("id", "?"), "passed": False, "inconclusive": True,
                "checks": [], "detail": "no answer (launch/timeout/error)"}
    results: list[CheckResult] = []
    for check in case.get("checks", []):
        results.append(run_deterministic(answer, check))
    rubric = str(case.get("judge_rubric", "") or "").strip()
    if rubric and llm is not None:
        results.append(run_judge(llm, skill_name=case.get("skill", "?"),
                                 brief=case.get("brief", ""), answer=answer, rubric=rubric))
    elif rubric:
        results.append(CheckResult("judge", False, "judge skipped: no LLM in this context (inconclusive)"))
    inconclusive = any(r.kind == "judge" and "inconclusive" in r.detail for r in results)
    passed = bool(results) and all(r.passed for r in results) and not inconclusive
    return {"case_id": case.get("id", "?"), "passed": passed, "inconclusive": inconclusive,
            "checks": [vars(r) for r in results],
            "detail": "; ".join(r.detail for r in results)}
