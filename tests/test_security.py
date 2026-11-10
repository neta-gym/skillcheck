"""Review-hardening tests: skill-name validation in every tool and hook,
revert confined to the validated skill folder with dotfiles skipped and
bytes restored exactly, and promote/revert kept off the model-tool surface."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import skillcheck as plugin
from skillcheck.core import paths, store

BAD_NAMES = ["..", "../..", "a/b", "a\\b", ".hidden", "a..b", "", "x" * 129, "na me", "é"]


class FakeCtx:
    def __init__(self):
        self.hooks, self.tools, self.commands = {}, {}, {}

    def register_hook(self, name, cb, **kw): self.hooks[name] = cb
    def register_tool(self, name, toolset, schema, handler, **kw): self.tools[name] = handler
    def register_command(self, name, handler, **kw): self.commands[name] = handler
    def register_cli_command(self, *a, **kw): pass


@pytest.fixture()
def ctx(home, skill):
    c = FakeCtx()
    plugin.register(c)
    return c


def test_valid_skill_name_rule(home):
    assert paths.valid_skill_name("demo-skill")
    assert paths.valid_skill_name("axolotl_v2.1")
    for bad in BAD_NAMES:
        assert not paths.valid_skill_name(bad), bad


def test_resolve_skill_dir_containment(home, skill):
    assert paths.resolve_skill_dir(skill) == (home / "skills" / skill).resolve()
    for bad in BAD_NAMES:
        assert paths.resolve_skill_dir(bad) is None, bad


def test_resolve_skill_dir_refuses_symlink_escape(home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    (home / "skills" / "linkskill").symlink_to(outside)
    assert paths.resolve_skill_dir("linkskill") is None
    assert store.revision_hash("linkskill") == "absent"
    assert store.skill_files_bytes("linkskill") == {}


def test_tools_refuse_invalid_names(ctx):
    for bad in BAD_NAMES:
        if not bad:  # empty name hits the "needs a skill name" branch instead
            continue
        for tool in ("skillcheck_status", "skillcheck_generate", "skillcheck_cases",
                     "skillcheck_run"):
            out = ctx.tools[tool]({"name": bad})
            assert "refusing" in out, (tool, bad, out)
        if " " in bad:
            continue  # slash args are space-separated; tools cover the spaced case
        out = ctx.commands["skillcheck"](f"promote {bad}")
        assert "refusing" in out, ("promote", bad, out)
        out = ctx.commands["skillcheck"](f"revert {bad}")
        assert "refusing" in out, ("revert", bad, out)


def test_hooks_ignore_invalid_skill_names(ctx, home):
    ctx.hooks["pre_tool_call"](tool_name="skill_manage",
                               args={"operations": [{"action": "patch", "name": ".."}]},
                               task_id="t", session_id="s", tool_call_id="c")
    ctx.hooks["post_tool_call"](tool_name="skill_manage",
                                args={"operations": [{"action": "patch", "name": ".."}]},
                                status="ok", task_id="t", session_id="s", tool_call_id="c")
    ctx.hooks["on_skill_lifecycle"](action="edited", skill_name="a/b", session_id="s")
    assert store.tracked_skills() == []
    assert not (home / "pdata" / "skills").exists() or not any((home / "pdata" / "skills").iterdir())


def test_revert_refuses_dotdot_and_touches_nothing(ctx, home):
    (home / "important.txt").write_text("keep me", encoding="utf-8")
    (home / ".env").write_text("SECRET=1", encoding="utf-8")
    out = ctx.commands["skillcheck"]("revert ..")
    assert "refusing" in out
    assert (home / "important.txt").read_text() == "keep me"
    assert (home / ".env").read_text() == "SECRET=1"


def test_revert_confined_skips_dotfiles_restores_bytes(ctx, home, skill):
    root = home / "skills" / skill
    blob = bytes(range(256))  # non-UTF-8 content
    (root / "blob.bin").write_bytes(blob)
    sid = store.new_snapshot(skill, trigger="test")
    assert sid
    # candidate write: edit both files, add a stray file and a dotfile
    (root / "SKILL.md").write_text("broken", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01")
    (root / "stray.txt").write_text("new file", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    out = ctx.commands["skillcheck"]("revert " + skill)
    assert "reverted to snapshot" in out, out
    assert "TOTAL" in (root / "SKILL.md").read_text(encoding="utf-8")
    assert (root / "blob.bin").read_bytes() == blob  # byte-exact, no utf-8 mangling
    assert not (root / "stray.txt").exists()         # non-snapshot file removed
    assert (root / ".env").read_text() == "SECRET=1"  # dotfile left alone


def test_revert_does_not_write_through_symlinked_parent(ctx, home, skill, tmp_path):
    store.new_snapshot(skill, trigger="test")
    outside = tmp_path / "escape"
    outside.mkdir()
    (home / "skills" / skill / "refs").symlink_to(outside)
    # a snapshot entry whose parent is now a symlink out of the skill folder
    snap_root = home / "pdata" / "skills" / skill / "snapshots"
    sid = store.latest_snapshot(skill)
    (snap_root / sid / "refs").mkdir()
    (snap_root / sid / "refs" / "x.md").write_text("payload", encoding="utf-8")
    out = ctx.commands["skillcheck"]("revert " + skill)
    assert "Skipped (unsafe path)" in out
    assert not (outside / "x.md").exists()


def test_promote_revert_not_model_tools(ctx):
    assert set(ctx.tools) == {"skillcheck_status", "skillcheck_generate",
                              "skillcheck_cases", "skillcheck_run"}
    # still reachable by the user through the slash command
    out = ctx.commands["skillcheck"]("revert demo-skill")
    assert "no snapshot stored" in out
