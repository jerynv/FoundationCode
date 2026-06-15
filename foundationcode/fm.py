"""Thin wrapper around Apple's `fm` command-line tool.

FoundationCode shells out to `fm respond` for every model call. The wrapper is
deliberately dependency-free so the whole agent runs on a stock macOS Python.

Two design choices worth knowing:

* The prompt is passed as a positional argument (not stdin). We keep prompts
  well under ARG_MAX by managing the context window ourselves, so this avoids
  any ambiguity about whether `fm` should read stdin.
* We always run with ``--no-stream`` so the full JSON response arrives in one
  piece, ready to parse, and ``--greedy`` by default so a small model behaves
  as deterministically as possible.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (fm colourises help/errors, not content)."""
    return _ANSI.sub("", text)


class FMError(RuntimeError):
    """Raised when the `fm` tool is missing or a model is unavailable."""


@dataclass
class FMResult:
    text: str
    ok: bool
    error: str | None = None


class FM:
    """Stateful handle to the `fm` binary for a single agent session."""

    def __init__(self, model: str = "system", greedy: bool = True,
                 timeout: int = 180):
        self.binary = shutil.which("fm")
        self.model = model
        self.greedy = greedy
        self.timeout = timeout
        self._schema_file: str | None = None

    # -- availability -----------------------------------------------------

    def ensure_available(self) -> None:
        """Fail fast with an actionable message if the chosen model is unusable."""
        if not self.binary:
            raise FMError(
                "The `fm` command was not found on your PATH.\n"
                "FoundationCode is built on Apple's Foundation Models CLI, which "
                "ships with macOS 26 (Apple Intelligence). Install/enable it, then "
                "confirm `fm available` works."
            )
        try:
            out = subprocess.run(
                [self.binary, "available"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as exc:  # pragma: no cover - environmental
            raise FMError(f"Could not run `fm available`: {exc}") from exc

        combined = strip_ansi((out.stdout or "") + (out.stderr or ""))
        low = combined.lower()
        if self.model == "pcc" and "pcc" in low and "not available" in low:
            raise FMError(
                "Apple Private Cloud Compute (--model pcc) is not available in "
                "this context. Re-run with the on-device model: --model system."
            )
        if self.model == "system" and "system model available" not in low:
            raise FMError(
                "The on-device system model is not available:\n  "
                + combined.strip()
                + "\nEnable Apple Intelligence in System Settings and wait for the "
                "model download to finish, then retry."
            )

    # -- schema -----------------------------------------------------------

    def set_schema(self, schema: dict) -> None:
        """Persist the generation schema to a temp file reused across calls."""
        fd, path = tempfile.mkstemp(prefix="fmcode-schema-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(schema, fh)
        self._schema_file = path

    # -- inference --------------------------------------------------------

    def respond(self, prompt: str, instructions: str | None = None,
                use_schema: bool = True, greedy: bool | None = None) -> FMResult:
        if not self.binary:
            return FMResult("", False, "`fm` not found on PATH")

        use_greedy = self.greedy if greedy is None else greedy
        cmd = [self.binary, "respond", "--no-stream", "--model", self.model]
        if use_greedy:
            cmd.append("--greedy")
        if instructions:
            cmd += ["--instructions", instructions]
        if use_schema and self._schema_file:
            cmd += ["--schema", self._schema_file]
        cmd.append(prompt)

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return FMResult("", False, f"fm timed out after {self.timeout}s")
        except Exception as exc:  # pragma: no cover - environmental
            return FMResult("", False, str(exc))

        out = (proc.stdout or "").strip()
        err = strip_ansi(proc.stderr or "").strip()
        if proc.returncode != 0:
            return FMResult(out, False, err or f"fm exited with {proc.returncode}")
        if not out and err:
            return FMResult("", False, err)
        return FMResult(out, True, err or None)

    # -- cleanup ----------------------------------------------------------

    def cleanup(self) -> None:
        if self._schema_file and os.path.exists(self._schema_file):
            try:
                os.unlink(self._schema_file)
            except OSError:
                pass
        self._schema_file = None
