"""End-to-end plugin flow with a fake host ctx: write detection, candidate
marking, case run through fake child sessions, verdicts, promote, revert."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import skillcheck as plugin  # the plugin package
from skillcheck.core import store


class FakeHandle:
    def to_dict(self): return {}


class FakeWait:
    timed_out = False


class FakeResult:
    def __init__(self, summary):
        self.ready = True
        self.summary = summary


class FakeSubagents:
    """Stands in for ctx.subagent_lifecycle; answer chosen per launch."""
    answers = []
    def launch(self, request):
        self.last_request = request
        return FakeHandle()
    def wait(self, handle, timeout_seconds=None):
        return FakeWait()
    def result(self, handle):
        return FakeResult(self.answers.pop(0))


class FakeLlm:
    def complete_structured(self, messages, schema):
        return {"parsed": {"passed": True, "reason": "fake judge ok"}}


class FakeCtx:
    def __init__(self):
        self.hooks = {}
        self.tools = {}
        self.commands = {}
        self.subagent_lifecycle = FakeSubagents()
        self.llm = FakeLlm()
    def register_hook(self, name, cb, **kw): self.hooks[name] = cb
    def register_tool(self, name, toolset, schema, handler, **kw): self.tools[name] = handler
    def register_command(self, name, handler, **kw): self.commands[name] = handler
    def register_cli_command(self, *a, **kw): pass


@pytest.fixture()
def ctx(home, skill, monkeypatch):
    # runner imports the host module at call time; give it a fake
    import types
    fake_mod = types.ModuleType("agent.subagent_lifecycle")
    class SubagentLaunchRequest:
        def __init__(self, **kw): self.kw = kw
    fake_mod.SubagentLaunchRequest = SubagentLaunchRequest
    agent_pkg = types.ModuleType("agent")
    agent_pkg.subagent_lifecycle = fake_mod
    monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.subagent_lifecycle", fake_mod)
    c = FakeCtx()
    plugin.register(c)
    return c


def test_register_wires_everything(ctx):
    assert set(ctx.hooks) == {"pre_tool_call", "post_tool_call", "on_skill_lifecycle"}
    assert set(ctx.tools) == {"skillcheck_status", "skillcheck_generate", "skillcheck_cases",
                              "skillcheck_run"}
    # promote/revert are user-invoked only: slash command, never model tools
    assert "skillcheck_promote" not in ctx.tools and "skillcheck_revert" not in ctx.tools
    assert "skillcheck" in ctx.commands


def test_write_marks_candidate_and_snapshots(ctx, home, skill):
    before = store.revision_hash(skill)
    ctx.hooks["pre_tool_call"](tool_name="skill_manage",
                               args={"operations": [{"action": "patch", "name": skill,
                                                     "old_string": "TOTAL", "new_string": "SUM"}]},
                               task_id="t1", session_id="s1", tool_call_id="c1")
    (home / "skills" / skill / "SKILL.md").write_text(
        (home / "skills" / skill / "SKILL.md").read_text().replace("TOTAL", "SUM"), encoding="utf-8")
    ctx.hooks["post_tool_call"](tool_name="skill_manage",
                                args={"operations": [{"action": "patch", "name": skill}]},
                                status="ok", task_id="t1", session_id="s1", tool_call_id="c1")
    state = store.load_state(skill)
    assert state["pending_candidate"]["action"] == "patch"
    assert state["pending_candidate"]["revision_hash"] != before
    snap = store.latest_snapshot(skill)
    assert snap and b"TOTAL" in store.snapshot_files(skill, snap)["SKILL.md"]


def test_lifecycle_hook_marks_candidate(ctx, skill):
    ctx.hooks["on_skill_lifecycle"](action="edited", skill_name=skill, session_id="s9")
    assert store.load_state(skill)["pending_candidate"]["action"] == "edited"


def test_run_first_run_then_promote_then_regression(ctx, skill):
    store.save_cases(skill, [{
        "id": "main-flow", "skill": skill, "title": "main", "brief": "sum 3 and 4",
        "fixtures": "", "checks": [{"type": "contains", "value": "TOTAL"}],
        "judge_rubric": "", "status": "draft"}])
    ctx.subagent_lifecycle.answers = ["The TOTAL is 7"]
    out = ctx.tools["skillcheck_run"]({"name": skill})
    assert "FIRST-RUN" in out and "1/1 passed" in out
    out = ctx.commands["skillcheck"]("promote " + skill)
    assert "baseline set" in out
    # candidate now fails the same case -> REGRESSION against the baseline
    ctx.subagent_lifecycle.answers = ["The answer is 7"]  # missing TOTAL
    out = ctx.tools["skillcheck_run"]({"name": skill})
    assert "REGRESSION" in out and "main-flow" in out


def test_child_gets_no_tools(ctx, skill):
    store.save_cases(skill, [{"id": "c", "skill": skill, "title": "t", "brief": "b",
                              "fixtures": "", "checks": [{"type": "contains", "value": "x"}],
                              "judge_rubric": "", "status": "draft"}])
    ctx.subagent_lifecycle.answers = ["x"]
    ctx.tools["skillcheck_run"]({"name": skill})
    assert ctx.subagent_lifecycle.last_request.kw["allowed_toolsets"] == ()


def test_revert_restores_snapshot_and_is_reversible(ctx, home, skill):
    original = (home / "skills" / skill / "SKILL.md").read_text()
    ctx.hooks["pre_tool_call"](tool_name="skill_manage",
                               args={"operations": [{"action": "edit", "name": skill}]},
                               task_id="t", session_id="s", tool_call_id="c")
    (home / "skills" / skill / "SKILL.md").write_text("broken skill", encoding="utf-8")
    ctx.hooks["post_tool_call"](tool_name="skill_manage",
                                args={"operations": [{"action": "edit", "name": skill}]},
                                status="ok", task_id="t", session_id="s", tool_call_id="c")
    out = ctx.commands["skillcheck"]("revert " + skill)
    assert "reverted to snapshot" in out
    assert (home / "skills" / skill / "SKILL.md").read_text() == original
    assert "Pre-revert state kept" in out  # the broken state is itself recoverable
    assert "pending_candidate" not in store.load_state(skill)


def test_promote_refuses_inconclusive(ctx, skill):
    store.save_cases(skill, [{"id": "c", "skill": skill, "title": "t", "brief": "b",
                              "fixtures": "", "checks": [{"type": "contains", "value": "x"}],
                              "judge_rubric": "", "status": "draft"}])
    ctx.subagent_lifecycle.answers = [None]  # child produced nothing
    ctx.tools["skillcheck_run"]({"name": skill})
    out = ctx.commands["skillcheck"]("promote " + skill)
    assert "inconclusive" in out.lower() and store.load_baseline(skill) is None


def test_hooks_never_raise(ctx, skill):
    # malformed payloads must not propagate into the agent loop
    ctx.hooks["pre_tool_call"](tool_name="skill_manage", args={"operations": [{"bad": 1}]})
    ctx.hooks["post_tool_call"](tool_name="skill_manage", args=None, status="ok")
    ctx.hooks["on_skill_lifecycle"](action=None, skill_name=None)


def test_filename_fixtures_flagged():
    from skillcheck.core.cases import fixture_looks_like_filename
    assert fixture_looks_like_filename("data.csv")
    assert fixture_looks_like_filename(" spacing.txt ")
    assert not fixture_looks_like_filename("3, 5, 7")
    assert not fixture_looks_like_filename("")
    assert not fixture_looks_like_filename("line one\nline two.csv")
