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
                "true if this can be accomplished by WRITING or RUNNING a "
                "script/program/shell commands (including automating a system "
                "task such as freeing disk space). false only if it "
                "fundamentally cannot be done with code"
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
    "You decide whether a request can be accomplished by writing or running "
    "code, scripts, or shell commands. Automating a system task — for example a "
    "script that frees disk space by clearing caches and build artifacts — IS "
    "doable with code, so can_do=true. Mark can_do=false ONLY if it truly "
    "cannot be done with code at all: weather, news, booking travel, physical "
    "hardware, or personal opinions."
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
