"""Path resolution for skillcheck.

Skills live under <HERMES_HOME>/skills (tools/skills_sync.py: SKILLS_DIR = HERMES_HOME / "skills").
Plugin state lives under the plugin data dir (plugins/plugin_storage.py: plugin_data_dir(name)).
Both resolve lazily per call: one process can serve several profiles, so nothing
may cache a home resolved at import time.
"""
from __future__ import annotations

import os
from pathlib import Path

PLUGIN_NAME = "skillcheck"


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # host app, when installed in Hermes
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def skills_dir() -> Path:
    return hermes_home() / "skills"


def data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir  # host helper, survives updates
        return Path(plugin_data_dir(PLUGIN_NAME))
    except Exception:
        root = Path(os.environ.get("SKILLCHECK_DATA_DIR", hermes_home() / "plugins-data" / PLUGIN_NAME))
        root.mkdir(parents=True, exist_ok=True)
        return root


def skill_dir(skill: str) -> Path:
    """On-disk directory of a skill, also one category level down (devops/axolotl)."""
    root = skills_dir()
    direct = root / skill
    if direct.is_dir():
        return direct
    if root.is_dir():
        for child in sorted(root.iterdir()):
            cand = child / skill
            if child.is_dir() and not child.name.startswith(".") and cand.is_dir():
                return cand
    return direct  # not-yet-created skill: canonical location


def is_under_skills(path: str) -> tuple[bool, str]:
    """(inside, skill-name-from-first-path-component). Pure string/Path work; no I/O."""
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p)
        rel = p.resolve(strict=False).relative_to(skills_dir().resolve(strict=False))
    except (ValueError, OSError):
        return False, ""
    parts = rel.parts
    if not parts or parts[0].startswith("."):
        return False, ""
    # category-nested skills are <category>/<skill>/...; flat skills are <skill>/...
    if len(parts) >= 2 and (skills_dir() / parts[0] / parts[1]).is_dir():
        return True, parts[1]
    return True, parts[0]
