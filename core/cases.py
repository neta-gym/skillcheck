"""Generate regression cases for a skill via ctx.llm.

Cases are synthetic by construction: the prompt demands invented fixture data
and forbids drawing on anything outside the skill text handed to the model.
Generation only ever PROPOSES - cases land in the store as drafts the user can
read with skillcheck_cases before any run.
"""
from __future__ import annotations

import time
from typing import Any

from . import store

CASES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "brief": {"type": "string"},
                    "fixtures": {"type": "string"},
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "type": {"type": "string", "enum": ["contains", "not_contains", "regex"]},
                                "value": {"type": "string"},
                            },
                            "required": ["type", "value"],
                        },
                    },
                    "judge_rubric": {"type": "string"},
                },
                "required": ["id", "title", "brief", "fixtures", "checks"],
            },
        }
    },
    "required": ["cases"],
}

_PROMPT = """You are writing regression tests for an agent skill. A regression test pins down
behavior the skill MUST keep, so that a future edit or model swap that breaks it gets caught.

Skill name: {name}

Skill definition:
<skill>
{skill_text}
</skill>

Write {count} test cases. Rules:
- Each case is a small, concrete task the skill should handle, plus the expected behavior.
- Every case must be self-contained: invent synthetic fixture data (placeholder names,
  and put the ACTUAL DATA inline in "fixtures". NEVER put a filename or file path in
  "fixtures" - the test runner cannot open files; it passes the fixtures text verbatim.
  dates, figures). Never reference real people, real user data, or anything outside the skill text.
- Prefer deterministic checks (contains / not_contains / regex on the final answer) whose
  pass condition follows directly from the skill text. Add judge_rubric only when the
  expected behavior cannot be captured deterministically.
- Cover the skill's main path, one edge case the skill text implies, and one misuse or
  out-of-scope input the skill should refuse or handle gracefully.
- Keep ids short and stable: kebab-case, e.g. "main-flow", "empty-input".
"""


def generate_cases(ctx: Any, skill: str, count: int = 4) -> tuple[list[dict[str, Any]], str]:
    """Returns (cases, note). Raises nothing into the agent loop; on failure returns
    ([], reason)."""
    files = store.skill_files(skill)
    if not files:
        return [], f"skill {skill!r} not found on disk"
    skill_text = files.get("SKILL.md") or "\n\n".join(f"# {r}\n{t}" for r, t in files.items())
    prompt = _PROMPT.format(name=skill, skill_text=skill_text[:8000], count=max(1, min(count, 10)))
    try:
        result = ctx.llm.complete_structured(
            instructions="You write regression-test cases as JSON that matches the schema.",
            input=[{"type": "text", "text": prompt}], json_schema=CASES_SCHEMA, purpose="skillcheck case generation")
        payload = getattr(result, "parsed", None)
        if payload is None and isinstance(result, dict):
            payload = result.get("parsed", result)
        raw = (payload or {}).get("cases", [])
    except Exception as exc:
        return [], f"case generation failed: {exc}"
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, c in enumerate(raw):
        if not isinstance(c, dict) or not c.get("brief"):
            continue
        cid = str(c.get("id") or f"case-{i+1}").strip().lower().replace(" ", "-")[:48]
        while cid in seen:
            cid += "-x"
        seen.add(cid)
        checks = [ch for ch in c.get("checks", [])
                  if isinstance(ch, dict) and ch.get("type") in ("contains", "not_contains", "regex")
                  and isinstance(ch.get("value"), str)]
        fixtures = str(c.get("fixtures", ""))
        case = {
            "id": cid, "skill": skill, "title": str(c.get("title", ""))[:120],
            "brief": str(c["brief"]), "fixtures": fixtures,
            "checks": checks, "judge_rubric": str(c.get("judge_rubric", "") or ""),
            "created_at": time.time(), "status": "draft",
        }
        if fixture_looks_like_filename(fixtures):
            case["fixture_warning"] = (
                "fixtures look like a filename, not inline data - child sessions "
                "receive fixtures verbatim and cannot open files; repair before promoting")
        cases.append(case)
    if not cases:
        return [], "model returned no usable cases"
    flagged = sum(1 for c in cases if c.get("fixture_warning"))
    note = f"generated {len(cases)} draft cases"
    if flagged:
        note += f" ({flagged} flagged: filename-like fixtures need inline data)"
    return cases, note

import re as _re

_FILENAME_RE = _re.compile(r"^[\w .-]+\.(csv|tsv|txt|json|jsonl|md|xml|yaml|yml|log)$", _re.IGNORECASE)


def fixture_looks_like_filename(fixtures: str) -> bool:
    """True when `fixtures` is a bare filename reference instead of inline data.

    Child sessions receive the fixtures string verbatim in their prompt; they
    have no tools and cannot open files, so a filename-only fixture makes the
    case unrunnable as written. Flag it at generation time so review can
    repair it before any run/promote treats the case as meaningful.
    """
    text = (fixtures or "").strip()
    if not text or "\n" in text:
        return False
    return bool(_FILENAME_RE.match(text))
