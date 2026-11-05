# skillcheck

CI for Hermes agent skills. Hermes learns skills as it works; nothing checks
that a new or edited skill still does what the old one did. skillcheck closes
that loop:

1. **Watch** - plugin hooks (`pre_tool_call` / `post_tool_call` on
   `skill_manage` and on file writes into the skills directory, plus the
   `on_skill_lifecycle` hook) notice supported in-band skill edits and
   snapshot the previous revision when one exists (a create has no prior
   state; out-of-band edits are detected by revision hash at run time,
   without a snapshot). Watching never blocks or modifies a write.
2. **Pin** - `skillcheck_generate` drafts synthetic regression cases from the
   skill's own definition: a brief, self-contained fixture data, deterministic
   checks (`contains` / `not_contains` / `regex`), and an optional LLM-judge
   rubric for what can't be checked deterministically. Cases are drafts until
   you review them (`skillcheck_cases`).
3. **Test** - `skillcheck_run` re-runs the cases against the skill's current
   on-disk revision. Each case executes in a fresh child session launched
   through Hermes's public Subagent Lifecycle API with `allowed_toolsets=()`
   (no file, shell, browser or network tools) and fixtures inlined in the
   prompt, so tests cannot touch real state.
4. **Decide** - every run reports GREEN / REGRESSION / FIRST-RUN /
   INCONCLUSIVE against the promoted baseline, with per-case evidence.
   `skillcheck_promote` accepts a run as the new baseline (refuses runs with
   inconclusive cases). `skillcheck_revert` restores the last pre-write
   snapshot - manually, and only after snapshotting the current state so the
   revert is itself reversible.

A REGRESSION verdict requires a promoted baseline: the case passed then and
fails now. The plugin never calls anything a regression on first sight, never
blocks a write, and never rewrites a skill on its own.

## Usage

In a session: `/skillcheck status`, `/skillcheck generate <name>`,
`/skillcheck cases <name>`, `/skillcheck run <name>`,
`/skillcheck promote <name>`, `/skillcheck revert <name>` - or ask the agent;
the same actions are registered as tools (`skillcheck_status`, ...).

From the shell: `hermes skillcheck status` (read-only).

## Honest limits

- Child sessions share the real environment; safety comes from narrowed
  toolsets plus inline synthetic fixtures, not a sandbox.
- Subagent launch works only inside an active agent turn, so runs are
  on-demand (slash command / tool), not a background daemon.
- Terminal results are bounded (~32k chars, no transcripts) - cases assert on
  the final deliverable, not intermediate steps.
- Hooks are fail-open by platform contract; skillcheck observes, it does not
  gate synchronously.
- Judged checks are labeled JUDGE in every report and an unavailable judge is
  INCONCLUSIVE, never a pass.
- Snapshots are taken only for writes the plugin observes (skill_manage /
  write_file / patch through the Hermes tool pipeline, or the
  on_skill_lifecycle hook). Edits made outside Hermes (an editor, sed, git)
  are still DETECTED at the next run (the revision hash changes, so a
  REGRESSION verdict still fires) but there is no pre-write snapshot for
  `skillcheck_revert` to restore; it reports that honestly instead of
  guessing.
- Generated cases are drafts for human review. Cases whose "fixtures" look
  like a filename instead of inline data are flagged at generation, in
  skillcheck_cases, and in every run report (child sessions cannot open
  files). Review/repair drafts before promoting a baseline.
- The LLM judge needs a capable model: judge no-verdict is always
  INCONCLUSIVE (never a pass), and small free-tier models may fail as
  judges while their deterministic contains/regex checks work fine.
- A wait timeout stops skillcheck waiting, then requests cancellation of the
  child session via the lifecycle API so a timed-out case does not keep
  working in the background; the cancel outcome (accepted or refused, e.g.
  when the run finished concurrently) is reported in the case detail.

## Development

```
uv run --with pytest python -m pytest tests/ -q
```

Run this from a checkout whose directory is named `skillcheck` (the default
clone name): the repo root carries the plugin's `__init__.py`, and pytest's
default import mode needs the root directory name to be a valid Python
identifier.

Admission check (same one the catalog CI runs):

```
hermes plugins validate /path/to/skillcheck
```
