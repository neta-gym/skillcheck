from core import detect


def test_operations_shape():
    args = {"operations": [
        {"action": "create", "name": "new-skill", "content": "x"},
        {"action": "view", "name": "other"},
        {"action": "patch", "name": "demo-skill", "old_string": "a", "new_string": "b"},
    ]}
    ws = detect.detect_pre("skill_manage", args)
    assert [(w.skill, w.action) for w in ws] == [("new-skill", "create"), ("demo-skill", "patch")]


def test_flat_legacy_shape():
    ws = detect.detect_pre("skill_manage", {"action": "write_file", "name": "demo-skill",
                                            "file_path": "refs/x.md"})
    assert ws[0].action == "write_file" and ws[0].file_path == "refs/x.md"


def test_file_tool_into_skills(home, skill):
    p = str(home / "skills" / skill / "SKILL.md")
    ws = detect.detect_pre("write_file", {"path": p, "content": "..."})
    assert ws and ws[0].skill == skill and ws[0].via == "write_file"


def test_file_tool_outside_skills_ignored(home):
    ws = detect.detect_pre("write_file", {"path": str(home / "elsewhere" / "f.txt")})
    assert ws == []


def test_failed_call_not_marked(home, skill):
    p = str(home / "skills" / skill / "SKILL.md")
    assert detect.detect_post("write_file", {"path": p}, status="error") == []
    assert detect.detect_post("write_file", {"path": p}, status="ok") != []


def test_lifecycle_mapping():
    assert detect.from_lifecycle("created", "s").action == "created"
    assert detect.from_lifecycle("patched", "s").action == "patched"
    assert detect.from_lifecycle("deleted", "s").action == "delete"
    assert detect.from_lifecycle("viewed", "s") is None
    assert detect.from_lifecycle("created", "") is None


def test_unrelated_tools_ignored():
    assert detect.detect_pre("terminal", {"command": "ls"}) == []
    assert detect.detect_pre("memory", {"action": "add"}) == []
