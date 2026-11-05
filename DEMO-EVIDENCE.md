# SkillCheck - verified demo evidence (2026-11-04)

Environment: fresh clone of NousResearch/hermes-agent @ main (uv sync
--frozen), HERMES_HOME=/tmp/hermes-demo, plugin installed + enabled, all 15
plugin_validate admission checks green.

Model side: Nous Portal device-code OAuth on an agent-operated free account
(no card, no subscription, $0 balance, no API key). Model
upstage/solar-mini4:free (free catalog; stepfun/step-3.7-flash:free was
429 fair-share saturated). Every leg below was driven by this real model
through Hermes chat. Demo skill: csv-sum (sum comma-separated numbers;
reply "<sum> TOTAL"; refuse non-numeric input with exactly "NOT NUMBERS").

## Real-model loop (provenance: run IDs from plugin runs.jsonl)

1. In-band skill_manage create -> on_skill_lifecycle hook marked the
   candidate; pre-write snapshot recorded (revision 70771be3fe3c).
2. skillcheck_generate -> 6 draft cases from the real model (contains +
   regex checks, edge behaviors).
3. RAW FIRST-RUN of the unreviewed generated drafts: run 26090b02ac14,
   3/6 - PRESERVED FAILURE EVIDENCE:
   - main-flow / whitespace-roundtrip FAIL: the generator put fixture
     FILENAMES ("data.csv", "spacing.txt") in the fixtures field; child
     sessions receive fixtures verbatim, cannot open files, and refused.
   - negative-decimals FAIL: anchored regex ^-?\d+(\.\d+)? TOTAL$ vs the
     real answer "`-2 TOTAL`" (markdown backticks) - brittle case, not a
     skill defect.
   - non-numeric-item, empty-input, out-of-scope-letters PASS.
   This is the intended first-run review value: generated cases are drafts.
4. Human review/repair of the drafts (inline fixture data, relaxed the one
   brittle anchor; did NOT weaken the output contract - TOTAL/NOT NUMBERS
   checks intact). The 6/6 result below is HUMAN-REVIEWED-AND-REPAIRED,
   not automatic generation success.
5. Rerun: 6/6 PASS (FIRST-RUN). Promote -> baseline run 5dda06ecd826
   (6/6, revision 70771be3fe3c). Fresh REAL-model baseline.
6. In-band skill_manage patch TOTAL->RESULT -> pre-write snapshot
   1793837733-8919 -> run 81e59b2fefa7: REGRESSION (2/6). Exactly the 4
   TOTAL-dependent cases failed; both NOT NUMBERS cases still passed -
   precise signal, no collateral noise.
7. Live skillcheck_revert (real model drove the tool): restored snapshot
   1793837733-8919 (1 file); pre-revert state kept as 1793837920-8919
   (revert is reversible).
8. Post-revert run 54c139f9d478: GREEN (6/6), revision back to baseline.
9. Unchanged rerun 7261c7eed3de: GREEN (6/6) - no false positive.

## Product fixes shipped from this evidence
- Fixture contract: generation prompt now forbids filename-only fixtures;
  validation flags them (fixture_warning) at generation, in
  skillcheck_cases, and in every run report. 24/24 unit tests green.
- Generation is draft + human review by design; the demo shows the repair
  step openly instead of hiding it.

## Known limits (verified, not assumed)
- Judge: real-model generated judge_rubric cases returned INCONCLUSIVE
  ("judge returned no verdict") on solar-mini4 - the small free model is
  not a reliable judge. Deterministic checks worked throughout. No claim
  is made about judge quality on capable models (untested here).
- Free-catalog models can be 429-saturated (observed on
  stepfun/step-3.7-flash:free); model choice matters per demo.
- Hermes retries rate-limited calls on a 600s backoff, which looks like a
  hang; check errors.log before assuming failure.
- Plugin tools are invisible to the model while tools.tool_search is on;
  the demo config sets tools.tool_search.enabled = off.
- Out-of-band edits (editor/sed/git) are detected at run time via revision
  hash but have no snapshot; revert covers in-band writes.
- Wait timeouts cancel the child via the lifecycle API; the cancel outcome
  is reported in the case detail.

## Earlier stub-plumbing evidence (separate, labeled)
With a scripted stub provider the same install exercised: hook marking,
generate -> FIRST-RUN -> promote -> mutate -> REGRESSION -> fix -> GREEN.
Stub legs prove plumbing only, not real regression detection. The
real-model loop above supersedes them; no stub-derived baseline remains
(the current baseline 5dda06ecd826 is real-model).
