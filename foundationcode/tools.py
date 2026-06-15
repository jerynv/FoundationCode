"""Tool implementations and the safety layer.

Every tool returns a short *observation* string that becomes the model's next
input, so each result is capped to keep the context window small. Mutating
tools (write_file, run_bash) go through an approval gate, and a hard denylist
blocks catastrophic shell commands regardless of approval mode.
"""

from __future__ import annotations

import os
import re
import subprocess

from .schema import MUTATING_ACTIONS

# Commands that are never run, even with --auto. Matched case-insensitively
# against the raw command string.
_DENYLIST = [
    r"rm\s+-rf?\s+(/|~|\$HOME|\*|\.\s*$)",   # rm -rf / ~ * .
    r":\(\)\s*\{.*\}\s*;",                     # fork bomb
    r"\bmkfs\b",                                # format filesystem
    r"\bdd\b[^\n]*\bof=/dev/",                  # overwrite a device
    r">\s*/dev/(sd|disk|nvme)",                 # redirect onto a raw device
    r"\bshutdown\b|\breboot\b|\bhalt\b",        # power state
    r"\bdiskutil\s+(eraseDisk|reformat|zeroDisk)",
]
_DENY_RE = [re.compile(p, re.IGNORECASE) for p in _DENYLIST]


class Approval:
    ASK = "ask"
    AUTO = "auto"
    READONLY = "readonly"


class Tools:
    def __init__(self, cwd: str, ui, approval: str = Approval.ASK,
                 bash_timeout: int = 60, max_obs_chars: int = 1800,
                 max_read_bytes: int = 12000):
        self.cwd = os.path.abspath(cwd)
        self.ui = ui
        self.approval = approval
        self.bash_timeout = bash_timeout
        self.max_obs_chars = max_obs_chars
        self.max_read_bytes = max_read_bytes

    # -- dispatch ---------------------------------------------------------

    def dispatch(self, action: dict) -> tuple[str, str]:
        """Run ``action`` and return (short_label, observation)."""
        name = action["action"]
        handler = {
            "list_dir": self._list_dir,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "delete_file": self._delete_file,
            "run_bash": self._run_bash,
        }.get(name)
        if handler is None:
            return name, f"error: '{name}' is not an executable tool"
        return handler(action)

    def signature(self, action: dict) -> str:
        """A stable identity for an action, used for loop detection.

        Paths are resolved so ``.gitignore`` and ``/abs/.gitignore`` collapse to
        the same signature. Content is excluded so repeated writes to the same
        file also register as a repeat.
        """
        name = action.get("action", "")
        path = action.get("path")
        cmd = (action.get("command") or "").strip()
        resolved = self._resolve(path) if path else ""
        return f"{name}|{resolved}|{cmd}"

    # -- helpers ----------------------------------------------------------

    def _cap(self, text: str) -> str:
        if len(text) <= self.max_obs_chars:
            return text
        head = text[: self.max_obs_chars]
        return head + f"\n… [truncated, {len(text) - self.max_obs_chars} more chars]"

    def _resolve(self, path: str) -> str:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(self.cwd, path)
        return os.path.normpath(path)

    def _within_cwd(self, resolved: str) -> bool:
        """True if ``resolved`` is the working dir or strictly inside it."""
        p = os.path.abspath(resolved)
        return p == self.cwd or p.startswith(self.cwd + os.sep)

    def _rel(self, path: str) -> str:
        try:
            return os.path.relpath(path, self.cwd)
        except ValueError:
            return path

    def _approve(self, label: str, detail: str) -> bool:
        if self.approval == Approval.AUTO:
            return True
        if self.approval == Approval.READONLY:
            return False
        decision = self.ui.confirm(label, detail)
        if decision == "always":
            self.approval = Approval.AUTO
            return True
        return decision is True

    # -- tools ------------------------------------------------------------

    def _list_dir(self, action: dict) -> tuple[str, str]:
        path = self._resolve(action["path"])
        label = f"list_dir {self._rel(path)}"
        if not os.path.exists(path):
            return label, f"error: no such directory: {path}"
        if not os.path.isdir(path):
            return label, f"error: not a directory: {path}"
        try:
            entries = sorted(os.listdir(path))
        except OSError as exc:
            return label, f"error: {exc}"
        if not entries:
            return label, "(empty directory)"
        rows = []
        for name in entries[:200]:
            full = os.path.join(path, name)
            marker = "/" if os.path.isdir(full) else ""
            rows.append(name + marker)
        more = "" if len(entries) <= 200 else f"\n… ({len(entries) - 200} more)"
        return label, self._cap("\n".join(rows) + more)

    def _read_file(self, action: dict) -> tuple[str, str]:
        path = self._resolve(action["path"])
        label = f"read_file {self._rel(path)}"
        if not os.path.exists(path):
            return label, f"error: no such file: {path}"
        if os.path.isdir(path):
            return label, f"error: {path} is a directory; use list_dir"
        try:
            with open(path, "rb") as fh:
                raw = fh.read(self.max_read_bytes + 1)
        except OSError as exc:
            return label, f"error: {exc}"
        truncated = len(raw) > self.max_read_bytes
        try:
            text = raw[: self.max_read_bytes].decode("utf-8")
        except UnicodeDecodeError:
            return label, f"error: {path} is not UTF-8 text (binary file)"
        numbered = "\n".join(
            f"{i + 1:>5}  {line}" for i, line in enumerate(text.splitlines())
        )
        if truncated:
            numbered += "\n… [file truncated; read a smaller portion with run_bash sed]"
        return label, self._cap(numbered or "(empty file)")

    def _write_file(self, action: dict) -> tuple[str, str]:
        path = self._resolve(action["path"])
        content = action.get("content") or ""
        rel = self._rel(path)
        label = f"write_file {rel}"
        existed = os.path.exists(path)
        verb = "Overwrite" if existed else "Create"
        preview = content if len(content) <= 600 else content[:600] + "\n… (more)"
        if not self._approve(
            f"{verb} {rel} ({len(content)} bytes, {content.count(chr(10)) + 1} lines)",
            preview,
        ):
            return label, "blocked: user declined (or read-only mode)"
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            return label, f"error: {exc}"
        return label, (
            f"ok: {'overwrote' if existed else 'created'} {rel} "
            f"({len(content)} bytes, {content.count(chr(10)) + 1} lines)"
        )

    def _delete_file(self, action: dict) -> tuple[str, str]:
        path = self._resolve(action["path"])
        rel = self._rel(path)
        label = f"delete_file {rel}"
        # Containment: never delete outside the working directory, the working
        # directory itself, or anything inside .git — even with --auto.
        if not self._within_cwd(path):
            return label, "blocked: refusing to delete outside the working directory"
        if os.path.abspath(path) == self.cwd:
            return label, "blocked: refusing to delete the working directory itself"
        if ".git" in os.path.relpath(path, self.cwd).split(os.sep):
            return label, "blocked: refusing to delete anything inside .git"
        if not os.path.exists(path):
            return label, f"error: no such file: {rel}"
        if os.path.isdir(path):
            return label, (
                f"error: {rel} is a directory; delete files individually or use "
                "run_bash for directories"
            )
        if not self._approve(f"Delete {rel}", "(this file will be permanently removed)"):
            return label, "blocked: user declined (or read-only mode)"
        try:
            os.remove(path)
        except OSError as exc:
            return label, f"error: {exc}"
        return label, f"ok: deleted {rel}"

    def _run_bash(self, action: dict) -> tuple[str, str]:
        command = action["command"]
        label = f"run_bash {command if len(command) < 60 else command[:57] + '...'}"

        for rx in _DENY_RE:
            if rx.search(command):
                return label, (
                    "blocked: command matched the safety denylist and was not run"
                )
        if not self._approve(f"Run shell command", command):
            return label, "blocked: user declined (or read-only mode)"

        try:
            proc = subprocess.run(
                command, shell=True, cwd=self.cwd, capture_output=True,
                text=True, timeout=self.bash_timeout,
            )
        except subprocess.TimeoutExpired:
            return label, f"error: command timed out after {self.bash_timeout}s"
        except Exception as exc:  # pragma: no cover - environmental
            return label, f"error: {exc}"

        out = (proc.stdout or "") + (proc.stderr or "")
        out = out.strip() or "(no output)"
        status = "exit 0" if proc.returncode == 0 else f"exit {proc.returncode}"
        return label, self._cap(f"[{status}]\n{out}")
