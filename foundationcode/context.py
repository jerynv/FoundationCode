"""Context-window management.

Apple's on-device model has a small context window (a few thousand tokens), so
the single most important job here is to keep the prompt small. We do that by:

* capping every stored observation,
* showing the most recent steps in full and compressing older ones to a single
  line, and
* enforcing an overall character budget, dropping the oldest steps first.

Roughly four characters per token, so the default 9000-char budget targets
~2200 tokens of history, leaving headroom for the system prompt and the
model's reply.
"""

from __future__ import annotations

from dataclasses import dataclass

# Characters, not tokens — a deliberately conservative heuristic (~4 chars/token).
PROMPT_CHAR_BUDGET = 9000
FULL_DETAIL_STEPS = 3            # most recent steps shown verbatim
COMPRESSED_OBS_CHARS = 160       # older steps collapse to this much observation


@dataclass
class Step:
    label: str          # e.g. 'read_file src/app.py'
    observation: str     # capped tool result


class History:
    def __init__(self) -> None:
        self._steps: list[Step] = []

    def add(self, label: str, observation: str) -> None:
        self._steps.append(Step(label, observation))

    def __len__(self) -> int:
        return len(self._steps)

    def last_label(self) -> str | None:
        return self._steps[-1].label if self._steps else None

    def render(self, budget: int = PROMPT_CHAR_BUDGET) -> str:
        if not self._steps:
            return "(no steps taken yet)"

        total = len(self._steps)
        lines: list[str] = []
        for idx, step in enumerate(self._steps):
            n = idx + 1
            recent = idx >= total - FULL_DETAIL_STEPS
            if recent:
                obs = step.observation
            else:
                obs = step.observation.strip().replace("\n", " ")
                if len(obs) > COMPRESSED_OBS_CHARS:
                    obs = obs[:COMPRESSED_OBS_CHARS] + " …"
            lines.append(f"[{n}] {step.label}\n      -> {obs}")

        # Enforce the char budget by dropping the OLDEST steps first, always
        # keeping at least the most recent one. A single counter tracks how many
        # were dropped (no fragile parsing of marker strings).
        omitted = 0

        def total_size() -> int:
            marker = len(f"[… {omitted} earlier step(s) omitted …]\n") if omitted else 0
            return marker + sum(len(s) + 1 for s in lines)

        while len(lines) > 1 and total_size() > budget:
            lines.pop(0)
            omitted += 1

        if omitted:
            lines.insert(0, f"[… {omitted} earlier step(s) omitted …]")
        return "\n".join(lines)


def build_prompt(task: str, history: History, cwd: str) -> str:
    """Assemble the per-step user prompt fed to the model."""
    return (
        f"TASK:\n{task}\n\n"
        f"WORKING DIRECTORY: {cwd}\n\n"
        f"STEPS SO FAR:\n{history.render()}\n\n"
        "Decide the single next action that makes progress on the TASK. "
        "If the task is already complete, use action finish. "
        "Reply with JSON only."
    )
