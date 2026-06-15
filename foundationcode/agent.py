"""The agent loop: prompt the model for one structured action, execute it,
feed the observation back, and repeat until the model calls ``finish`` — or it
asks the user a question, or a self-regulation guard decides it's stuck.

The loop is deliberately defensive about a small model's failure modes:

* malformed replies switch decoding from greedy to sampling and bail out after
  a few tries (``max_invalid``);
* a ``ProgressTracker`` detects when the model is repeating itself with no new
  information and escalates nudge → sampling → graceful stop, so the agent stops
  itself long before the step cap instead of grinding;
* ``ask_user`` lets the model hand a decision back to the user instead of
  guessing or looping.
"""

from __future__ import annotations

from .context import History, build_prompt
from .fm import FM
from .parsing import extract_json, normalize_action, validate_action
from .progress import ProgressTracker
from .schema import ACTION_SCHEMA, SYSTEM_PROMPT
from .scope import check_scope
from .tools import Tools
from .ui import UI

# Exit-style codes returned by run(). Interactive mode ignores them; one-shot
# mode propagates them as the process exit code.
DONE = 0
STOPPED = 1


class Agent:
    def __init__(self, fm: FM, tools: Tools, ui: UI, max_steps: int = 25,
                 max_invalid: int = 3, stall_limit: int = 3,
                 interactive: bool = False, scope_check: bool = True):
        self.fm = fm
        self.tools = tools
        self.ui = ui
        self.max_steps = max_steps
        self.max_invalid = max_invalid
        self.stall_limit = stall_limit
        self.interactive = interactive
        self.scope_check = scope_check
        self.fm.set_schema(ACTION_SCHEMA)

    def run(self, task: str) -> int:
        """Run one task. Returns DONE or STOPPED."""
        # Pre-flight: refuse out-of-scope requests before touching anything,
        # so a vague "free up space" can't lead to deleting the project.
        if self.scope_check:
            self.ui.start_thinking("checking whether I can do this")
            can_do, reason = check_scope(self.fm, task)
            self.ui.stop_thinking()
            if not can_do:
                self.ui.decline(reason or "This isn't a coding task I can do in "
                                "this project directory.")
                return DONE

        history = History()
        tracker = ProgressTracker(stall_limit=self.stall_limit)
        consecutive_failures = 0

        for step in range(1, self.max_steps + 1):
            prompt = build_prompt(task, history, self.tools.cwd)

            # Greedy decoding repeats mistakes verbatim; after a malformed reply
            # or a detected stall, switch to sampling to break the loop.
            greedy_override = None if (consecutive_failures == 0
                                       and tracker.stall == 0) else False

            self.ui.start_thinking(f"thinking (step {step}/{self.max_steps})")
            result = self.fm.respond(prompt, instructions=SYSTEM_PROMPT,
                                     greedy=greedy_override)
            self.ui.stop_thinking()

            if not result.ok:
                err = result.error or "unknown model error"
                if _fatal_error(err):
                    self.ui.error(f"model call failed: {err}")
                    return STOPPED
                # Usually the model ran away generating a long string and blew the
                # context window. Recover: ask for a terse reply, sample next time.
                consecutive_failures += 1
                self.ui.warn("the model's reply could not be processed; asking it to be terse")
                history.add("(model error)",
                            "Your previous reply could not be processed (it may have "
                            "been too long). Reply with a SHORT JSON action: a "
                            "one-sentence thought and a single action.")
                if consecutive_failures >= self.max_invalid:
                    self.ui.error(f"giving up after repeated model errors: {err}")
                    return STOPPED
                continue

            action = extract_json(result.text)
            if action is None:
                consecutive_failures += 1
                self.ui.warn("could not parse a JSON action; retrying")
                history.add("(unparseable reply)",
                            "Your reply was not valid JSON. Reply with JSON only, "
                            "matching the schema, with a single 'action'.")
                if consecutive_failures >= self.max_invalid:
                    self.ui.error("the model could not produce a valid action; stopping.")
                    return STOPPED
                continue

            action = normalize_action(action)
            err = validate_action(action)
            if err:
                consecutive_failures += 1
                self.ui.warn(err)
                history.add(f"(invalid action: {action.get('action')})", err)
                if consecutive_failures >= self.max_invalid:
                    self.ui.error("the model could not produce a valid action; stopping.")
                    return STOPPED
                continue

            consecutive_failures = 0
            self.ui.thought(action.get("thought", ""))
            name = action["action"]

            if name == "finish":
                self.ui.final(action.get("content", "Task complete."))
                return DONE

            if name == "ask_user":
                question = action.get("content", "").strip()
                answer = self._handle_question(question)
                if answer is None:
                    return DONE  # one-shot: nothing to answer, stop cleanly
                history.add("ask_user",
                            f"You asked: {question}\nUser answered: "
                            + (answer or "(skipped — use your best judgement or finish)"))
                tracker.reset()
                continue

            # Execute a tool.
            label, observation = self.tools.dispatch(action)
            self.ui.action(label)
            self.ui.observation(observation)

            stall = tracker.assess(self.tools.signature(action), observation)
            if stall == 0:
                history.add(label, observation)
            elif stall >= self.stall_limit:
                self.ui.warn(
                    "I'm repeating myself without making progress, so I'll stop "
                    "rather than spin."
                )
                self.ui.info(
                    "Try a more specific instruction, or ask me to do one concrete "
                    "step at a time."
                )
                return STOPPED
            else:
                # Soft nudge: keep the observation but tell the model, firmly,
                # that it learned nothing. Sampling kicks in on the next call.
                history.add(label, observation + "\n\n[system] You already ran this "
                            "and saw this exact result — it gives NO new information. "
                            "Do something different, ask_user, or finish. Do not "
                            "repeat this action.")
                self.ui.info("· noticing repetition — nudging toward a new action")

        self.ui.warn(f"reached the {self.max_steps}-step safety limit; stopping.")
        return STOPPED

    def _handle_question(self, question: str) -> str | None:
        """Return the user's answer, or None when there's no one to answer."""
        if not self.interactive:
            self.ui.question_only(question)
            return None
        return self.ui.ask_user(question)


def _fatal_error(err: str) -> bool:
    """Errors no retry can fix — stop immediately rather than burn attempts."""
    e = err.lower()
    return "not available" in e or "not found" in e or "timed out" in e
