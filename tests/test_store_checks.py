import json

from core import store
from core.checks import evaluate_case, run_deterministic


def test_revision_hash_changes_on_write(home, skill):
    h1 = store.revision_hash(skill)
    (home / "skills" / skill / "SKILL.md").write_text("changed", encoding="utf-8")
    assert store.revision_hash(skill) != h1


def test_cases_roundtrip(home, skill):
    store.save_cases(skill, [{"id": "a", "brief": "b", "checks": []}])
    assert store.load_cases(skill)[0]["id"] == "a"


def test_snapshot_and_restore_files(home, skill):
    sid = store.new_snapshot(skill, trigger="test")
    assert sid
    (home / "skills" / skill / "SKILL.md").write_text("broken", encoding="utf-8")
    files = store.snapshot_files(skill, sid)
    assert "TOTAL" in files["SKILL.md"]
    assert store.latest_snapshot(skill) == sid


def test_snapshot_missing_skill_returns_none(home):
    assert store.new_snapshot("ghost", trigger="test") is None


def test_deterministic_checks():
    assert run_deterministic("the TOTAL is 7", {"type": "contains", "value": "TOTAL"}).passed
    assert not run_deterministic("the sum is 7", {"type": "contains", "value": "TOTAL"}).passed
    assert run_deterministic("x", {"type": "not_contains", "value": "y"}).passed
    assert run_deterministic("abc123", {"type": "regex", "value": r"\d+"}).passed
    assert not run_deterministic("abc", {"type": "regex", "value": "("}).passed  # bad regex -> fail


class _Resp:
    def __init__(self, text): self.text = text

class FakeLlm:
    def __init__(self, payload):
        import json
        self.payload = payload
    def complete(self, messages, **kw):
        import json
        return _Resp(json.dumps(self.payload) if self.payload is not None else "no json here")


def test_judge_pass_and_inconclusive():
    case = {"id": "c", "brief": "b", "judge_rubric": "must mention TOTAL", "checks": []}
    out = evaluate_case("TOTAL 7", case, llm=FakeLlm({"passed": True, "reason": "ok"}))
    assert out["passed"]
    out = evaluate_case("TOTAL 7", case, llm=FakeLlm(None))
    assert not out["passed"] and out["inconclusive"]  # judge outage never reads as pass


def test_no_answer_is_inconclusive_not_fail_evidence():
    out = evaluate_case(None, {"id": "c", "checks": [{"type": "contains", "value": "x"}]})
    assert not out["passed"] and out["inconclusive"]


def test_judge_skipped_without_llm_marks_inconclusive():
    case = {"id": "c", "checks": [{"type": "contains", "value": "TOTAL"}],
            "judge_rubric": "r", "skill": "s"}
    out = evaluate_case("TOTAL", case, llm=None)
    assert not out["passed"] and out["inconclusive"]
