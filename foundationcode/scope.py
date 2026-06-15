"""Pre-flight scope check.

A small model can't be trusted to *refuse* an out-of-scope request mid-loop —
asked to "free up space on my Mac" it will cheerfully start deleting your
README. But it CAN answer a single yes/no question reliably, which is a much
easier task than multi-step agentic reasoning.

So before the action loop runs, we ask the model one binary question: is this a
coding task I can do inside this one directory? If not, we decline with the
model's own reason instead of letting it flail. The check fails OPEN — any
error classifies as "go ahead" — so a flaky classifier never blocks real work.
"""

from __future__ import annotations

from .parsing import extract_json

SCOPE_SCHEMA: dict = {
    "title": "Scope",
    "type": "object",
    "additionalProperties": False,
    "x-order": ["can_do", "reason"],
    "properties": {
        "can_do": {
            "type": "boolean",
            "description": (
                "true ONLY if this is a software/coding task achievable by "
                "reading/editing files and running commands INSIDE one project "
                "directory"
            ),
        },
        "reason": {
            "type": "string",
            "description": (
                "if can_do is false, one sentence on why and what the user "
                "should do instead"
            ),
        },
    },
    "required": ["can_do", "reason"],
}

SCOPE_INSTRUCTIONS = (
    "You judge whether a request is a coding task scoped to a single project "
    "directory. Requests about the whole computer, freeing disk space, system "
    "settings, the internet, hardware, personal files, or anything outside the "
    "project directory are NOT coding tasks and must be can_do=false. Editing "
    "code, writing files, running tests, or git inside the project are "
    "can_do=true."
)


def check_scope(fm, task: str) -> tuple[bool, str]:
    """Return (can_do, reason). Fails open: errors classify as can_do=True."""
    raw = fm.oneshot_json(task, SCOPE_INSTRUCTIONS, SCOPE_SCHEMA)
    if not raw:
        return True, ""
    obj = extract_json(raw)
    if not isinstance(obj, dict) or "can_do" not in obj:
        return True, ""
    return bool(obj.get("can_do")), str(obj.get("reason") or "")
