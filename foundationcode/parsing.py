"""Robust extraction and validation of the model's JSON action.

`fm --schema` almost always returns clean JSON, but a small model can still
wrap it in prose or code fences. We parse defensively and, when the action is
malformed, return a precise correction string the agent feeds back as the next
observation.
"""

from __future__ import annotations

import json

from .fm import strip_ansi
from .schema import ALLOWED_ACTIONS


def extract_json(raw: str) -> dict | None:
    """Return the first JSON object in ``raw``, or None if none parses."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Scan for the first balanced {...}, respecting strings/escapes.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    for attempt in (candidate, strip_ansi(candidate)):
                        try:
                            obj = json.loads(attempt)
                            if isinstance(obj, dict):
                                return obj
                        except json.JSONDecodeError:
                            continue
                    return None
    return None


def normalize_action(action: dict) -> dict:
    """Repair common small-model formatting slips in place, then return it.

    The most frequent slip is packing an argument into the action field, e.g.
    ``{"action": "write_file test.py"}`` instead of separate action/path. We
    split the verb out and route the remainder to the right field.
    """
    if not isinstance(action, dict):
        return action
    name = action.get("action")
    if isinstance(name, str):
        name = name.strip()
        parts = name.split(None, 1)
        if parts and parts[0] in ALLOWED_ACTIONS:
            action["action"] = parts[0]
            rest = parts[1].strip() if len(parts) > 1 else ""
            if rest:
                verb = parts[0]
                path_verbs = ("list_dir", "read_file", "write_file", "delete_file")
                if verb in path_verbs and not action.get("path"):
                    action["path"] = rest
                elif verb == "run_bash" and not action.get("command"):
                    action["command"] = rest
                elif verb == "ask_user" and not action.get("content"):
                    action["content"] = rest
        else:
            action["action"] = name
    return action


def validate_action(action: dict) -> str | None:
    """Return an error string if the action is unusable, else None."""
    name = action.get("action")
    if not name:
        return "Your reply had no 'action' field. Choose one of: " + ", ".join(
            ALLOWED_ACTIONS
        )
    if name not in ALLOWED_ACTIONS:
        return (
            f"'{name}' is not a valid action. Choose exactly one of: "
            + ", ".join(ALLOWED_ACTIONS)
        )
    if name in ("list_dir", "read_file", "write_file", "delete_file") \
            and not action.get("path"):
        return f"Action '{name}' requires a non-empty 'path' field."
    if name == "write_file" and action.get("content") is None:
        return "Action 'write_file' requires a 'content' field with the full file."
    if name == "run_bash" and not action.get("command"):
        return "Action 'run_bash' requires a non-empty 'command' field."
    if name == "ask_user" and not (action.get("content") or "").strip():
        return "Action 'ask_user' requires a 'content' field with your question."
    if name == "finish" and not (action.get("content") or "").strip():
        return "Action 'finish' requires a 'content' field summarising the result."
    return None
