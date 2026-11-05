"""Turn hook payloads into skill-write events.

Two write paths exist in Hermes:
- skill_manage tool (tools/skill_manager_tool.py): advertised shape is
  {"operations": [{action, name, ...}, ...]}; a legacy flat shape
  {action, name, ...} is still accepted. Actions that mutate: create, edit,
  patch, write_file, remove_file, delete.
- plain write_file / patch tools aimed straight at a path under the skills dir.

Detection never raises and never blocks; hooks are fail-open by contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .paths import is_under_skills

SKILL_WRITE_ACTIONS = frozenset({"create", "edit", "patch", "write_file", "remove_file", "delete"})
FILE_WRITE_TOOLS = frozenset({"write_file", "patch", "edit_file"})
SKILL_TOOL = "skill_manage"


@dataclass
class SkillWrite:
    skill: str
    action: str                     # create|edit|patch|write_file|remove_file|delete|file_write
    via: str                        # tool name that carried the write
    file_path: Optional[str] = None


def from_skill_manage(args: dict[str, Any]) -> list[SkillWrite]:
    out: list[SkillWrite] = []
    ops = args.get("operations")
    if isinstance(ops, list):
        for op in ops:
            if not isinstance(op, dict):
                continue
            action, name = str(op.get("action", "")), str(op.get("name", ""))
            if action in SKILL_WRITE_ACTIONS and name:
                out.append(SkillWrite(skill=name, action=action, via=SKILL_TOOL,
                                      file_path=op.get("file_path") or None))
        return out
    action, name = str(args.get("action", "")), str(args.get("name", ""))
    if action in SKILL_WRITE_ACTIONS and name:
        out.append(SkillWrite(skill=name, action=action, via=SKILL_TOOL,
                              file_path=args.get("file_path") or None))
    return out


def from_file_tool(tool_name: str, args: dict[str, Any]) -> list[SkillWrite]:
    path = args.get("path") or args.get("file_path") or ""
    if not isinstance(path, str) or not path:
        return []
    inside, skill = is_under_skills(path)
    if not inside or not skill:
        return []
    return [SkillWrite(skill=skill, action="file_write", via=tool_name, file_path=path)]


def detect_pre(tool_name: str, args: Optional[dict[str, Any]]) -> list[SkillWrite]:
    """Writes a tool call is ABOUT to attempt (snapshot source)."""
    if not isinstance(args, dict):
        return []
    if tool_name == SKILL_TOOL:
        return from_skill_manage(args)
    if tool_name in FILE_WRITE_TOOLS:
        return from_file_tool(tool_name, args)
    return []


def detect_post(tool_name: str, args: Optional[dict[str, Any]], status: str) -> list[SkillWrite]:
    """Writes a tool call DID land. Anything not ok is discarded."""
    if status and status != "ok":
        return []
    return detect_pre(tool_name, args)


def from_lifecycle(action: str, skill_name: str) -> Optional[SkillWrite]:
    """on_skill_lifecycle hook payload -> event. Fires only on landed mutations."""
    if not skill_name:
        return None
    a = str(action or "")
    if a in ("created", "patched", "edited"):
        return SkillWrite(skill=skill_name, action=a, via="on_skill_lifecycle")
    if a == "deleted":
        return SkillWrite(skill=skill_name, action="delete", via="on_skill_lifecycle")
    return None
