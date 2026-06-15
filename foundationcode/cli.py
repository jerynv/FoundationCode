"""Command-line entry point for the `fmcode` agent."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .agent import Agent
from .fm import FM, FMError
from .tools import Approval, Tools
from .ui import UI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fmcode",
        description="FoundationCode — an autonomous coding agent powered by "
                    "Apple's on-device Foundation Models (via the `fm` CLI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  fmcode \"add a --json flag to cli.py and update the README\"\n"
            "  fmcode -C ./myproject --readonly \"explain how routing works\"\n"
            "  fmcode --auto \"write tests for utils.py and run them\"\n"
            "  fmcode            # interactive session\n"
        ),
    )
    p.add_argument("task", nargs="*", help="the task to perform (omit for an "
                                           "interactive session)")
    p.add_argument("-C", "--cwd", default=".", metavar="DIR",
                   help="working directory the agent operates in (default: .)")
    p.add_argument("-m", "--model", default="system", choices=("system", "pcc"),
                   help="model to use (default: system, on-device)")
    p.add_argument("--max-steps", type=int, default=25, metavar="N",
                   help="maximum actions before giving up (default: 25)")
    p.add_argument("--bash-timeout", type=int, default=60, metavar="SECS",
                   help="timeout for each run_bash command (default: 60)")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--auto", action="store_true",
                      help="auto-approve file writes and shell commands")
    mode.add_argument("--readonly", action="store_true",
                      help="refuse all writes and shell commands (explore only)")

    p.add_argument("--no-greedy", action="store_true",
                   help="use sampling instead of greedy decoding")
    p.add_argument("--no-color", action="store_true", help="disable coloured output")
    p.add_argument("--version", action="version",
                   version=f"FoundationCode {__version__}")
    return p


def _approval_mode(args) -> str:
    if args.auto:
        return Approval.AUTO
    if args.readonly:
        return Approval.READONLY
    return Approval.ASK


def _make_agent(args, ui: UI) -> Agent:
    fm = FM(model=args.model, greedy=not args.no_greedy)
    fm.ensure_available()
    cwd = os.path.abspath(os.path.expanduser(args.cwd))
    if not os.path.isdir(cwd):
        raise FMError(f"working directory does not exist: {cwd}")
    tools = Tools(cwd=cwd, ui=ui, approval=_approval_mode(args),
                  bash_timeout=args.bash_timeout)
    return Agent(fm, tools, ui, max_steps=args.max_steps)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui = UI(color=False if args.no_color else None)

    try:
        agent = _make_agent(args, ui)
    except FMError as exc:
        ui.error(str(exc))
        return 2

    ui.banner(args.model, agent.tools.cwd)

    task = " ".join(args.task).strip()
    if task:
        ui.task(task)
        return _guarded(lambda: agent.run(task), ui)

    return _interactive(agent, ui)


def _interactive(agent: Agent, ui: UI) -> int:
    ui.info("interactive session — type a task, or 'exit' to quit.\n")
    while True:
        try:
            task = input(ui.c.green("fmcode › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not task:
            continue
        if task.lower() in ("exit", "quit", ":q"):
            return 0
        ui.task(task)
        _guarded(lambda: agent.run(task), ui)
        print()


def _guarded(fn, ui: UI) -> int:
    try:
        return fn()
    except KeyboardInterrupt:
        ui.stop_thinking()
        ui.warn("\ninterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
