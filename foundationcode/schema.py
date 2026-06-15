"""The action schema and system prompt that turn a general chat model into a
tool-using coding agent.

Apple's guided-generation decoder is strict and does not accept JSON-Schema
``enum``; we therefore type ``action`` as a plain string whose allowed values
live in its description, and validate the choice ourselves (see parsing.py).
The schema shape mirrors exactly what `fm schema object` emits, which we know
the decoder accepts.
"""

from __future__ import annotations

ALLOWED_ACTIONS = ("list_dir", "read_file", "write_file", "run_bash", "finish")

# Mutating actions require approval unless running with --auto.
MUTATING_ACTIONS = ("write_file", "run_bash")

ACTION_SCHEMA: dict = {
    # Apple's guided-generation decoder requires the root type's `title`.
    "title": "Step",
    "type": "object",
    "additionalProperties": False,
    "x-order": ["thought", "action", "path", "content", "command"],
    "properties": {
        "thought": {
            "type": "string",
            "description": "one short sentence: what you will do next and why",
        },
        "action": {
            "type": "string",
            "description": (
                "exactly one of: list_dir, read_file, write_file, run_bash, finish"
            ),
        },
        "path": {
            "type": "string",
            "description": "file or directory path for list_dir, read_file, write_file",
        },
        "content": {
            "type": "string",
            "description": (
                "for write_file: the COMPLETE new file contents. "
                "for finish: your final answer/summary for the user."
            ),
        },
        "command": {
            "type": "string",
            "description": "for run_bash: a single shell command to execute",
        },
    },
    "required": ["thought", "action"],
}

SYSTEM_PROMPT = (
    "You are FoundationCode, an autonomous coding agent running on the user's "
    "Mac. You finish the user's task by taking ONE action at a time and reading "
    "the result before the next action.\n"
    "\n"
    "Reply with JSON only, matching the schema. Fields:\n"
    "- thought: one short sentence about your next move.\n"
    "- action: exactly one of list_dir, read_file, write_file, run_bash, finish.\n"
    "- path: for list_dir / read_file / write_file.\n"
    "- content: for write_file (the COMPLETE file) or finish (final answer).\n"
    "- command: for run_bash (one shell command).\n"
    "\n"
    "Rules:\n"
    "- Explore before you edit: list_dir and read_file to understand the code.\n"
    "- write_file replaces the whole file, so include the entire new content.\n"
    "- Use run_bash for grep/find, running tests, git, and shell edits.\n"
    "- Make the SMALLEST change that satisfies the task. Do not add features, "
    "type hints, extra tests, or refactors that were not requested.\n"
    "- Do exactly what was asked, then verify it ONCE (run it or the test).\n"
    "- The moment the task is verified working, use action finish immediately. "
    "Never keep editing code that already works.\n"
    "- Never repeat an action that already succeeded or already failed; if you "
    "are unsure whether you are done, you are done — call finish.\n"
    "- finish's content is a short summary of what you did, for the user."
)
