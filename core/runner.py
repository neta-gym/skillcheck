"""Run saved cases against the skill's CURRENT on-disk revision.

Each case runs as a fresh child session through the public Subagent Lifecycle
API (agent/subagent_lifecycle.py), never through Hermes internals. Children are
launched with allowed_toolsets=() - no file, shell, browser or network tools -
and every fixture travels inline in the prompt as synthetic text, so a test
cannot touch real state. Honest limit: this is a narrowed-tool session, not a
sandbox; the prompt also tells the child not to write anything.

Known platform limits (verified in docs/source):
- launch works only inside an active agent turn; outside one it fails closed
  with NoActiveParentSession - we surface that as an inconclusive case.
- terminal results are bounded to 32k chars and carry no transcript.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from . import store
from .checks import evaluate_case

CASE_TIMEOUT_S = 240.0
MAX_SKILL_CHARS = 8000
MAX_ANSWER_CHARS = 32000  # platform bound on terminal results


def _build_goal(skill: str, skill_text: str, case: dict[str, Any]) -> str:
    fixtures = str(case.get("fixtures", "") or "").strip()
    return (
        f'You are executing one regression test for the agent skill "{skill}".\n'
        "The skill's full current definition is inlined below - apply it exactly.\n\n"
        "<skill-definition>\n" + skill_text[:MAX_SKILL_CHARS] + "\n</skill-definition>\n\n"
        f"TASK:\n{case.get('brief', '')}\n\n"
        + (f"SYNTHETIC FIXTURE DATA (test data, not real user data):\n{fixtures}\n\n" if fixtures else "")
        + "RULES:\n"
        "- Do not use any tools. Do not read or write files. Do not browse.\n"
        "- Base the answer solely on the skill definition and the fixture data above.\n"
        "- Reply with only the final deliverable the skill would produce for this task.\n"
    )


def run_cases(ctx: Any, skill: str, cases: list[dict[str, Any]],
              *, judge_llm: Any = None, timeout_s: float = CASE_TIMEOUT_S,
              progress: Any = None) -> dict[str, Any]:
    """Launch one child per case and evaluate. Never raises into the agent loop."""
    from agent.subagent_lifecycle import SubagentLaunchRequest  # host module at runtime

    files = store.skill_files(skill)
    skill_text = files.get("SKILL.md", "")
    if not skill_text:
        skill_text = "\n\n".join(f"# {rel}\n{text}" for rel, text in files.items())
    revision = store.revision_hash(skill)

    service = ctx.subagent_lifecycle
    results: list[dict[str, Any]] = []
    run_id = uuid.uuid4().hex[:12]
    started = time.time()

    for i, case in enumerate(cases):
        case_id = case.get("id", f"case-{i+1}")
        if progress:
            progress(f"running {case_id} ({i+1}/{len(cases)})")
        answer: Optional[str] = None
        try:
            handle = service.launch(SubagentLaunchRequest(
                goal=_build_goal(skill, skill_text, case),
                context=(
                    f"Regression case {case_id} for skill {skill}, run {run_id}. "
                    "Answer the task in the goal; that is the whole job."),
                role="leaf",
                allowed_toolsets=(),   # no tools: fixtures are inline, nothing real is touched
                correlation_id=f"skillcheck-{run_id}-{case_id}",
                # no per-launch timeout: the platform rejects it (verified); the
                # wait() below bounds how long we block instead.
            ))
            waited = service.wait(handle, timeout_seconds=timeout_s)
            cancel_note = ""
            if getattr(waited, "timed_out", False):
                answer = None
                # A wait timeout only stops US waiting; the child session would keep
                # running otherwise. Cancel it so a timed-out case cannot continue
                # working in the background. (cancel() is a hard-interrupt request;
                # the platform may report it as not accepted if the run finished
                # concurrently - either way we report what happened.)
                try:
                    cancel_res = service.cancel(
                        handle, reason=f"skillcheck case timeout after {timeout_s}s")
                    accepted = getattr(cancel_res, "accepted", False)
                    if accepted:
                        cancel_note = "child session cancelled after timeout"
                    else:
                        cancel_note = ("cancel requested but not accepted "
                                       "(run may have completed concurrently)")
                except Exception as cancel_exc:
                    cancel_note = f"cancel after timeout failed: {cancel_exc}"
            else:
                res = service.result(handle)
                if getattr(res, "ready", False) and getattr(res, "summary", None):
                    answer = str(res.summary)[:MAX_ANSWER_CHARS]
        except Exception as exc:  # e.g. NoActiveParentSession outside an agent turn
            answer = None
            results.append({"case_id": case_id, "passed": False, "inconclusive": True,
                            "checks": [], "detail": f"launch failed: {exc}"})
            continue
        outcome = evaluate_case(answer, case, llm=judge_llm)
        if case.get("fixture_warning"):
            outcome["fixture_warning"] = case["fixture_warning"]
        if cancel_note:
            outcome["detail"] = f"{outcome.get('detail', '')} [{cancel_note}]".strip()
        outcome["case_id"] = case_id
        results.append(outcome)

    run = {
        "run_id": run_id, "skill": skill, "ts": started,
        "revision_hash": revision, "results": results,
        "passed": sum(1 for r in results if r["passed"]),
        "total": len(results),
    }
    store.record_run(skill, run)
    return run
