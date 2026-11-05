import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import paths  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME + plugin data dir for every test."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SKILLCHECK_DATA_DIR", str(tmp_path / "pdata"))
    monkeypatch.setattr(paths, "hermes_home", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def skill(home):
    """A minimal skill on disk."""
    d = home / "skills" / "demo-skill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Sum two numbers.\n---\n"
        "# Demo\nAlways reply with the sum and the word TOTAL.\n", encoding="utf-8")
    return "demo-skill"
