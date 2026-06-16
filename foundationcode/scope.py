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
                "true if a computer PROGRAM could attempt it: writing/running "
                "code, calling an API, automating a browser, fetching or "
                "processing data. false ONLY if it requires acting in the "
                "physical world (hardware, driving, cooking) or is not a task a "
                "computer can perform at all"
            ),
        },
        "reason": {
            "type": "string",
            "description": "if false, one sentence on why it needs the physical world",
        },
    },
    "required": ["can_do", "reason"],
}

SCOPE_INSTRUCTIONS = (
    "Decide if a computer program could ATTEMPT the request. Almost everything "
    "qualifies: fetching the weather is an API call (true), searching/booking "
    "flights is an API call (true), freeing disk space is a script (true), "
    "math, file edits, web scraping, automation — all true. Set can_do=false "
    "ONLY when it physically cannot be done by a program: repairing hardware, "
    "driving a car, cooking food, or things that are not computer tasks at all."
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
