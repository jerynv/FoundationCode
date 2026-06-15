"""No-progress / loop detection — the agent's self-regulation.

A small model will happily re-read the same file forever. We treat a step as
*no progress* when the model repeats an action it has already taken **and** gets
back content it has already seen — i.e. it learned nothing. Consecutive
no-progress steps trip an escalation handled by the agent: first a hard nudge
(and a switch to sampling to break greedy determinism), then a graceful stop.

The key fix over a naive guard: signatures are computed from the *resolved*
path, so ``.gitignore`` and ``/abs/path/.gitignore`` count as the same action.
"""

from __future__ import annotations

import collections


class ProgressTracker:
    def __init__(self, stall_limit: int = 3, hard_repeat: int = 4):
        # stall_limit: consecutive no-progress steps before we stop.
        # hard_repeat: total times one exact action may occur before it counts
        #              as a stall even if the output keeps changing slightly.
        self.stall_limit = stall_limit
        self.hard_repeat = hard_repeat
        self._sigs: collections.Counter = collections.Counter()
        self._seen_obs: set = set()
        self.stall = 0

    def assess(self, signature: str, observation: str) -> int:
        """Record a step; return the consecutive-stall count (0 == progress)."""
        self._sigs[signature] += 1
        obs_key = hash(observation.strip())
        duplicate_action = self._sigs[signature] >= 2
        duplicate_obs = obs_key in self._seen_obs
        self._seen_obs.add(obs_key)

        learned_nothing = duplicate_action and duplicate_obs
        too_many = self._sigs[signature] >= self.hard_repeat
        if learned_nothing or too_many:
            self.stall += 1
        else:
            self.stall = 0
        return self.stall

    def reset(self) -> None:
        """Clear the stall streak (e.g. after the user answers a question)."""
        self.stall = 0
