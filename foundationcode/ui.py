"""Terminal rendering: colours, a thinking spinner, step output, and the
approval prompt. Colour is disabled automatically when stdout is not a TTY or
when NO_COLOR is set.
"""

from __future__ import annotations

import itertools
import os
import sys
import threading
import time


class _C:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _w(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled else text

    def dim(self, t):    return self._w("2", t)
    def bold(self, t):   return self._w("1", t)
    def green(self, t):  return self._w("38;2;55;195;160", t)
    def cyan(self, t):   return self._w("36", t)
    def yellow(self, t): return self._w("33", t)
    def red(self, t):    return self._w("31", t)
    def blue(self, t):   return self._w("34", t)


BANNER = r"""
   ___                  _      _   _          ___         _
  | __|__ _  _ _ _  __| |__ _| |_(_)___ _ _ / __|___  __| |___
  | _/ _ \ || | ' \/ _` / _` |  _| / _ \ ' \ (__/ _ \/ _` / -_)
  |_|\___/\_,_|_||_\__,_\__,_|\__|_\___/_||_\___\___/\__,_\___|
"""


class UI:
    def __init__(self, color: bool | None = None, quiet: bool = False):
        if color is None:
            color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        self.c = _C(color)
        self.quiet = quiet
        self._spinner: _Spinner | None = None

    # -- chrome -----------------------------------------------------------

    def banner(self, model: str, cwd: str) -> None:
        print(self.c.green(BANNER))
        print(self.c.dim(f"  on-device coding agent · model={model}"))
        print(self.c.dim(f"  cwd: {cwd}\n"))

    def task(self, task: str) -> None:
        print(self.c.bold("◆ task  ") + task + "\n")

    def info(self, msg: str) -> None:
        print(self.c.dim(msg))

    def warn(self, msg: str) -> None:
        print(self.c.yellow("! " + msg))

    def error(self, msg: str) -> None:
        print(self.c.red("✗ " + msg))

    # -- step rendering ---------------------------------------------------

    def thought(self, text: str) -> None:
        if text:
            print(self.c.dim("  · " + text))

    def action(self, label: str) -> None:
        print(self.c.green("● ") + self.c.bold(label))

    def observation(self, text: str) -> None:
        snippet = text.strip().splitlines()
        shown = snippet[:8]
        for line in shown:
            print(self.c.dim("    " + line[:200]))
        if len(snippet) > len(shown):
            print(self.c.dim(f"    … (+{len(snippet) - len(shown)} lines)"))
        print()

    def final(self, text: str) -> None:
        print(self.c.green("\n✔ done"))
        print(self.c.bold(text.strip()) + "\n")

    # -- approval ---------------------------------------------------------

    def confirm(self, label: str, detail: str):
        """Return True / False / 'always'."""
        self.stop_thinking()
        print(self.c.yellow("\n⚠ approval needed: ") + self.c.bold(label))
        if detail:
            for line in detail.strip().splitlines()[:12]:
                print(self.c.dim("    " + line[:200]))
        try:
            ans = input(self.c.yellow("  allow? [y]es / [n]o / [a]lways: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if ans in ("a", "always"):
            return "always"
        return ans in ("y", "yes")

    def ask_user(self, question: str) -> str:
        """Show the agent's question and read the user's answer (interactive)."""
        self.stop_thinking()
        print(self.c.cyan("\n? ") + self.c.bold(question))
        try:
            return input(self.c.cyan("  your answer (Enter to skip): ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    def question_only(self, question: str) -> None:
        """Print the agent's question when there's no one to answer (one-shot)."""
        self.stop_thinking()
        print(self.c.cyan("\n? ") + self.c.bold(question))
        print(self.c.dim("  (run interactively — `fmcode` with no task — to answer.)"))

    # -- spinner ----------------------------------------------------------

    def start_thinking(self, label: str = "thinking") -> None:
        if self.quiet or not self.c.enabled:
            return
        self._spinner = _Spinner(label)
        self._spinner.start()

    def stop_thinking(self) -> None:
        if self._spinner:
            self._spinner.stop()
            self._spinner = None


class _Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str):
        self.label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            sys.stdout.write(f"\r\x1b[2m  {frame} {self.label}…\x1b[0m")
            sys.stdout.flush()
            time.sleep(0.08)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=0.5)
        sys.stdout.write("\r\x1b[2K")
        sys.stdout.flush()
