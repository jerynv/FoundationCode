"""The action schema and system prompt that turn a general chat model into a
tool-using coding agent.

Apple's guided-generation decoder is strict and does not accept JSON-Schema
``enum``; we therefore type ``action`` as a plain string whose allowed values
live in its description, and validate the choice ourselves (see parsing.py).
The schema shape mirrors exactly what `fm schema object` emits, which we know
the decoder accepts.
"""

from __future__ import annotations

ALLOWED_ACTIONS = (
    "list_dir", "read_file", "write_file", "delete_file", "run_bash",
    "ask_user", "finish",
)

# Mutating actions require approval unless running with --auto.
MUTATING_ACTIONS = ("write_file", "delete_file", "run_bash")

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
                "exactly one of: list_dir, read_file, write_file, delete_file, "
                "run_bash, ask_user, finish"
            ),
        },
        "path": {
            "type": "string",
            "description": (
                "file or directory path for list_dir, read_file, write_file, "
                "delete_file"
            ),
        },
        "content": {
            "type": "string",
            "description": (
                "for write_file: the COMPLETE new file contents. "
                "for ask_user: the single question to ask the user. "
                "for finish: your final answer/summary (or why you stopped)."
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
    "Mac. You complete a task by taking ONE action at a time and reading its "
    "result before the next action.\n"
    "\n"
    "Reply with JSON only, matching the schema. Fields:\n"
    "- thought: one short sentence about your next move.\n"
    "- action: one of list_dir, read_file, write_file, delete_file, run_bash, "
    "ask_user, finish.\n"
    "- path: for list_dir / read_file / write_file / delete_file.\n"
    "- content: for write_file (the COMPLETE file), ask_user (your question), "
    "or finish (final answer).\n"
    "- command: for run_bash (one shell command).\n"
    "\n"
    "Scope and judgement:\n"
    "- You work ONLY inside the working directory shown below. You cannot touch "
    "the whole computer, system files, or paths outside it. If a task needs "
    "that, use finish to explain you can't do it.\n"
    "- If the task is ambiguous, risky, destructive, or needs a decision, use "
    "ask_user to ask ONE clear question instead of guessing.\n"
    "- If you have already taken an action and seen its result, do NOT repeat "
    "it — you already have that information. Use it, or move on.\n"
    "- If you cannot make progress, use ask_user or finish. Never loop.\n"
    "\n"
    "Working:\n"
    "- Explore before you edit (list_dir, read_file).\n"
    "- write_file replaces the whole file; include the entire new content. Make "
    "the SMALLEST change that satisfies the task — no extra features, type "
    "hints, tests, or refactors.\n"
    "- Use run_bash for grep/find, running tests, and git.\n"
    "- Verify your change ONCE (run it or the test). The moment it works, use "
    "finish immediately — never keep editing code that already works.\n"
    "- finish's content is a short summary of what you did, or why you stopped."
)
