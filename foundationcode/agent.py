"""The agent loop: prompt the model for one structured action, execute it,
feed the observation back, and repeat until the model calls ``finish`` (or a
guard trips).
"""

from __future__ import annotations

from .context import History, build_prompt
from .fm import FM
from .parsing import extract_json, normalize_action, validate_action
from .schema import ACTION_SCHEMA, SYSTEM_PROMPT
from .tools import Tools
from .ui import UI


class Agent:
    def __init__(self, fm: FM, tools: Tools, ui: UI, max_steps: int = 25,
                 max_repeats: int = 3, max_invalid: int = 4):
        self.fm = fm
        self.tools = tools
        self.ui = ui
        self.max_steps = max_steps
        self.max_repeats = max_repeats
        self.max_invalid = max_invalid
        self.fm.set_schema(ACTION_SCHEMA)

    def run(self, task: str) -> int:
        """Run one task to completion. Returns a process-style exit code."""
        history = History()
        recent_labels: list[str] = []
        consecutive_failures = 0

        for step in range(1, self.max_steps + 1):
            prompt = build_prompt(task, history, self.tools.cwd)

            # After a malformed reply, greedy decoding would deterministically
            # repeat the same mistake; switch to sampling to break the loop.
            greedy_override = None if consecutive_failures == 0 else False

            self.ui.start_thinking(f"thinking (step {step}/{self.max_steps})")
            result = self.fm.respond(prompt, instructions=SYSTEM_PROMPT,
                                     greedy=greedy_override)
            self.ui.stop_thinking()

            if not result.ok:
                self.ui.error(f"model call failed: {result.error}")
                return 1

            action = extract_json(result.text)
            if action is None:
                consecutive_failures += 1
                self.ui.warn("could not parse a JSON action; retrying")
                history.add("(unparseable reply)",
                            "Your reply was not valid JSON. Reply with JSON only, "
                            "matching the schema, with a single 'action'.")
                if consecutive_failures >= self.max_invalid:
                    self.ui.error("the model could not produce a valid action; stopping.")
                    return 1
                continue

            action = normalize_action(action)
            err = validate_action(action)
            if err:
                consecutive_failures += 1
                self.ui.warn(err)
                history.add(f"(invalid action: {action.get('action')})", err)
                if consecutive_failures >= self.max_invalid:
                    self.ui.error("the model could not produce a valid action; stopping.")
                    return 1
                continue

            consecutive_failures = 0
            self.ui.thought(action.get("thought", ""))

            if action["action"] == "finish":
                self.ui.final(action.get("content", "Task complete."))
                return 0

            # Loop guard: identical action repeated too many times.
            signature = _signature(action)
            recent_labels.append(signature)
            if recent_labels[-self.max_repeats:].count(signature) >= self.max_repeats:
                self.ui.error(
                    "the model repeated the same action without progress; stopping."
                )
                return 1

            label, observation = self.tools.dispatch(action)
            self.ui.action(label)
            self.ui.observation(observation)
            history.add(label, observation)

        self.ui.warn(f"reached the {self.max_steps}-step limit without finishing.")
        return 1


def _signature(action: dict) -> str:
    return "|".join(
        str(action.get(k, "")) for k in ("action", "path", "command", "content")
    )
