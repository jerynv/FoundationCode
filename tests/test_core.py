"""Offline unit tests for FoundationCode's deterministic layers.

These exercise parsing, validation, the safety/approval layer, the tools, and
context-window management. None of them call the model, so they run in CI on any
machine (no `fm`, no Apple Intelligence required).

    python3 -m unittest discover -s tests -v
"""

import os
import tempfile
import unittest

from foundationcode.context import History, build_prompt, PROMPT_CHAR_BUDGET
from foundationcode.parsing import extract_json, normalize_action, validate_action
from foundationcode.tools import Approval, Tools, _DENY_RE


class _FakeUI:
    """Stands in for the terminal UI; would approve everything if asked."""

    def __init__(self):
        self.confirm_called = False

    def confirm(self, label, detail):
        self.confirm_called = True
        return True


class ParsingTests(unittest.TestCase):
    def test_plain_json(self):
        got = extract_json('{"thought":"t","action":"finish","content":"done"}')
        self.assertEqual(got["action"], "finish")

    def test_code_fenced_with_prose(self):
        raw = 'Sure!\n```json\n{"thought":"x","action":"list_dir","path":"."}\n```\nthanks'
        got = extract_json(raw)
        self.assertEqual(got["action"], "list_dir")
        self.assertEqual(got["path"], ".")

    def test_braces_inside_strings(self):
        got = extract_json('{"action":"write_file","content":"def f(): return {1:2}"}')
        self.assertIn("{1:2}", got["content"])

    def test_garbage_returns_none(self):
        self.assertIsNone(extract_json("no json here"))
        self.assertIsNone(extract_json(""))

    def test_normalize_packs_arg_into_action(self):
        a = normalize_action({"action": "write_file test.py", "content": "x"})
        self.assertEqual(a["action"], "write_file")
        self.assertEqual(a["path"], "test.py")

    def test_normalize_run_bash_arg(self):
        a = normalize_action({"action": "run_bash ls -la"})
        self.assertEqual(a["action"], "run_bash")
        self.assertEqual(a["command"], "ls -la")

    def test_normalize_leaves_clean_action(self):
        a = normalize_action({"action": "read_file", "path": "x"})
        self.assertEqual(a["action"], "read_file")
        self.assertEqual(a["path"], "x")


class ValidationTests(unittest.TestCase):
    def test_unknown_action(self):
        self.assertIsNotNone(validate_action({"action": "delete_everything"}))

    def test_missing_path(self):
        self.assertIsNotNone(validate_action({"action": "read_file"}))

    def test_write_requires_content(self):
        self.assertIsNotNone(validate_action({"action": "write_file", "path": "x"}))

    def test_run_bash_requires_command(self):
        self.assertIsNotNone(validate_action({"action": "run_bash"}))

    def test_finish_requires_content(self):
        self.assertIsNotNone(validate_action({"action": "finish", "content": "  "}))

    def test_valid_actions_pass(self):
        self.assertIsNone(validate_action({"action": "list_dir", "path": "."}))
        self.assertIsNone(validate_action(
            {"action": "write_file", "path": "x", "content": "y"}))
        self.assertIsNone(validate_action({"action": "finish", "content": "done"}))


class SafetyTests(unittest.TestCase):
    DANGEROUS = [
        "rm -rf /", "rm -rf ~", "sudo rm -rf /*", ":(){ :|:& };:",
        "dd if=/dev/zero of=/dev/disk0", "mkfs.ext4 /dev/sda", "shutdown -h now",
    ]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fmc-test-")
        self.ui = _FakeUI()

    def test_denylist_blocks_even_in_auto(self):
        t = Tools(cwd=self.tmp, ui=self.ui, approval=Approval.AUTO)
        for cmd in self.DANGEROUS:
            _, obs = t._run_bash({"action": "run_bash", "command": cmd})
            self.assertIn("denylist", obs, f"should block: {cmd}")
        # The denylist must short-circuit before approval is consulted.
        self.assertFalse(self.ui.confirm_called)

    def test_safe_command_runs_in_auto(self):
        t = Tools(cwd=self.tmp, ui=self.ui, approval=Approval.AUTO)
        _, obs = t._run_bash({"action": "run_bash", "command": "echo hello"})
        self.assertIn("hello", obs)
        self.assertIn("exit 0", obs)

    def test_readonly_blocks_write(self):
        t = Tools(cwd=self.tmp, ui=self.ui, approval=Approval.READONLY)
        target = os.path.join(self.tmp, "nope.txt")
        _, obs = t._write_file({"action": "write_file", "path": target, "content": "x"})
        self.assertIn("blocked", obs)
        self.assertFalse(os.path.exists(target))

    def test_readonly_blocks_bash(self):
        t = Tools(cwd=self.tmp, ui=self.ui, approval=Approval.READONLY)
        _, obs = t._run_bash({"action": "run_bash", "command": "echo x"})
        self.assertIn("blocked", obs)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fmc-test-")
        self.t = Tools(cwd=self.tmp, ui=_FakeUI(), approval=Approval.AUTO)

    def test_write_then_read_roundtrip(self):
        _, w = self.t._write_file(
            {"action": "write_file", "path": "a.py", "content": "print(1)\n"})
        self.assertIn("created", w)
        _, r = self.t._read_file({"action": "read_file", "path": "a.py"})
        self.assertIn("print(1)", r)
        self.assertIn("1  ", r)  # line numbering present

    def test_read_missing_file(self):
        _, r = self.t._read_file({"action": "read_file", "path": "ghost.py"})
        self.assertIn("no such file", r)

    def test_list_dir(self):
        open(os.path.join(self.tmp, "x.txt"), "w").close()
        _, out = self.t._list_dir({"action": "list_dir", "path": "."})
        self.assertIn("x.txt", out)

    def test_observation_is_capped(self):
        t = Tools(cwd=self.tmp, ui=_FakeUI(), approval=Approval.AUTO, max_obs_chars=50)
        big = "x" * 5000
        capped = t._cap(big)
        self.assertLess(len(capped), 200)
        self.assertIn("truncated", capped)


class ContextTests(unittest.TestCase):
    def test_empty_history(self):
        self.assertIn("no steps", History().render())

    def test_recent_steps_full_old_compressed(self):
        h = History()
        for i in range(8):
            h.add(f"read_file f{i}.py", f"contents number {i} " + "y" * 400)
        rendered = h.render()
        self.assertIn("read_file f7.py", rendered)            # newest present
        self.assertIn("y" * 400, rendered)                    # a recent step in full
        self.assertLessEqual(rendered.count("y" * 400), 3)    # only recent shown full
        self.assertIn("…", rendered)                          # older steps compressed

    def test_old_steps_omitted_when_over_budget(self):
        h = History()
        for i in range(30):
            h.add(f"read_file f{i}.py", "z" * 400)
        rendered = h.render(budget=3000)
        self.assertIn("omitted", rendered)                    # dropped under budget
        self.assertIn("read_file f29.py", rendered)           # newest always kept

    def test_budget_enforced(self):
        h = History()
        for i in range(60):
            h.add(f"step{i}", "z" * 500)
        self.assertLessEqual(len(h.render(budget=4000)), 4000 + 200)

    def test_build_prompt_contains_task(self):
        p = build_prompt("DO THE THING", History(), "/tmp/proj")
        self.assertIn("DO THE THING", p)
        self.assertIn("/tmp/proj", p)


if __name__ == "__main__":
    unittest.main()
