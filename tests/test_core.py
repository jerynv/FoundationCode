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
from foundationcode.progress import ProgressTracker
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


class ProgressTrackerTests(unittest.TestCase):
    def test_distinct_actions_make_progress(self):
        t = ProgressTracker(stall_limit=3)
        self.assertEqual(t.assess("read|a||", "contents A"), 0)
        self.assertEqual(t.assess("read|b||", "contents B"), 0)
        self.assertEqual(t.assess("list|.||", "x y z"), 0)

    def test_repeat_with_same_output_stalls(self):
        t = ProgressTracker(stall_limit=3)
        t.assess("read|a||", "same")          # first time: progress
        self.assertEqual(t.stall, 0)
        s2 = t.assess("read|a||", "same")     # repeat action + repeat output
        s3 = t.assess("read|a||", "same")
        self.assertEqual(s2, 1)
        self.assertEqual(s3, 2)
        self.assertGreaterEqual(t.assess("read|a||", "same"), 3)  # hits stop

    def test_new_output_resets_stall(self):
        t = ProgressTracker(stall_limit=3)
        t.assess("read|a||", "same")
        t.assess("read|a||", "same")          # stall = 1
        self.assertEqual(t.assess("write|b||", "ok created"), 0)  # progress resets

    def test_reset(self):
        t = ProgressTracker(stall_limit=3)
        t.assess("read|a||", "same")
        t.assess("read|a||", "same")
        t.reset()
        self.assertEqual(t.stall, 0)


class SignatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fmc-test-")
        self.t = Tools(cwd=self.tmp, ui=_FakeUI(), approval=Approval.AUTO)

    def test_relative_and_absolute_path_collapse(self):
        rel = self.t.signature({"action": "read_file", "path": ".gitignore"})
        ab = self.t.signature(
            {"action": "read_file", "path": os.path.join(self.tmp, ".gitignore")})
        self.assertEqual(rel, ab)  # the exact bug that defeated the old guard

    def test_different_files_differ(self):
        a = self.t.signature({"action": "read_file", "path": "a.py"})
        b = self.t.signature({"action": "read_file", "path": "b.py"})
        self.assertNotEqual(a, b)


class NewActionTests(unittest.TestCase):
    def test_validate_delete_and_ask(self):
        self.assertIsNotNone(validate_action({"action": "delete_file"}))      # needs path
        self.assertIsNone(validate_action({"action": "delete_file", "path": "x"}))
        self.assertIsNotNone(validate_action({"action": "ask_user"}))          # needs content
        self.assertIsNone(
            validate_action({"action": "ask_user", "content": "which file?"}))

    def test_normalize_ask_user_packs_question(self):
        a = normalize_action({"action": "ask_user which file should I edit?"})
        self.assertEqual(a["action"], "ask_user")
        self.assertEqual(a["content"], "which file should I edit?")

    def test_normalize_delete_packs_path(self):
        a = normalize_action({"action": "delete_file old.txt"})
        self.assertEqual(a["action"], "delete_file")
        self.assertEqual(a["path"], "old.txt")


class DeleteGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fmc-test-")
        self.t = Tools(cwd=self.tmp, ui=_FakeUI(), approval=Approval.AUTO,
                       allow_delete=True)

    def test_disabled_by_default(self):
        t = Tools(cwd=self.tmp, ui=_FakeUI(), approval=Approval.AUTO)  # no allow_delete
        p = os.path.join(self.tmp, "keep.txt")
        with open(p, "w") as fh:
            fh.write("x")
        _, obs = t._delete_file({"action": "delete_file", "path": "keep.txt"})
        self.assertIn("disabled", obs)
        self.assertTrue(os.path.exists(p))  # untouched

    def _write(self, rel, body="x"):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) else None
        with open(p, "w") as fh:
            fh.write(body)
        return p

    def test_deletes_file_in_cwd(self):
        p = self._write("junk.txt")
        _, obs = self.t._delete_file({"action": "delete_file", "path": "junk.txt"})
        self.assertIn("deleted", obs)
        self.assertFalse(os.path.exists(p))

    def test_refuses_outside_cwd(self):
        other = tempfile.NamedTemporaryFile(delete=False)
        other.write(b"x"); other.close()
        _, obs = self.t._delete_file({"action": "delete_file", "path": other.name})
        self.assertIn("outside the working directory", obs)
        self.assertTrue(os.path.exists(other.name))  # untouched
        os.unlink(other.name)

    def test_refuses_git_internals(self):
        self._write(".git/config", "[core]")
        _, obs = self.t._delete_file({"action": "delete_file", "path": ".git/config"})
        self.assertIn(".git", obs)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ".git/config")))

    def test_refuses_directory(self):
        os.makedirs(os.path.join(self.tmp, "subdir"))
        _, obs = self.t._delete_file({"action": "delete_file", "path": "subdir"})
        self.assertIn("directory", obs)

    def test_readonly_blocks_delete(self):
        p = self._write("keep.txt")
        ro = Tools(cwd=self.tmp, ui=_FakeUI(), approval=Approval.READONLY)
        _, obs = ro._delete_file({"action": "delete_file", "path": "keep.txt"})
        self.assertIn("blocked", obs)
        self.assertTrue(os.path.exists(p))


class _ScriptedFM:
    """A fake FM that replays canned replies, for deterministic loop tests."""

    def __init__(self, replies, scope=None):
        self.replies = list(replies)
        self.scope = scope  # raw JSON string returned by oneshot_json, or None
        self.calls = 0

    def set_schema(self, schema):
        pass

    def oneshot_json(self, prompt, instructions, schema, greedy=True):
        return self.scope

    def respond(self, prompt, instructions=None, use_schema=True, greedy=None):
        from foundationcode.fm import FMResult
        self.calls += 1
        if not self.replies:
            return FMResult('{"thought":"d","action":"finish","content":"end"}', True)
        item = self.replies.pop(0)
        if isinstance(item, FMResult):
            return item
        import json as _json
        return FMResult(_json.dumps(item), True)


class _SilentUI:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.asked = []

    def __getattr__(self, _name):       # swallow all render calls
        return lambda *a, **k: None

    def confirm(self, *a):
        return True

    def question_only(self, q):
        self.asked.append(q)

    def ask_user(self, q):
        self.asked.append(q)
        return self.answers.pop(0) if self.answers else ""


class AgentControlFlowTests(unittest.TestCase):
    def _agent(self, fm, ui, interactive=False, **kw):
        from foundationcode.agent import Agent
        tmp = tempfile.mkdtemp(prefix="fmc-test-")
        tools = Tools(cwd=tmp, ui=ui, approval=Approval.AUTO)
        # scope_check off here so these tests isolate the action loop.
        return Agent(fm, tools, ui, interactive=interactive, scope_check=False, **kw)

    def test_finish_returns_done(self):
        from foundationcode.agent import DONE
        fm = _ScriptedFM([{"thought": "t", "action": "finish", "content": "done"}])
        self.assertEqual(self._agent(fm, _SilentUI()).run("x"), DONE)

    def test_ask_user_oneshot_stops_after_asking(self):
        from foundationcode.agent import DONE
        fm = _ScriptedFM([{"thought": "t", "action": "ask_user",
                           "content": "which file?"}])
        ui = _SilentUI()
        self.assertEqual(self._agent(fm, ui, interactive=False).run("x"), DONE)
        self.assertEqual(ui.asked, ["which file?"])
        self.assertEqual(fm.calls, 1)  # stopped right after asking

    def test_ask_user_interactive_feeds_answer_back(self):
        from foundationcode.agent import DONE
        fm = _ScriptedFM([
            {"thought": "t", "action": "ask_user", "content": "which file?"},
            {"thought": "t", "action": "finish", "content": "did it"},
        ])
        ui = _SilentUI(answers=["app.py"])
        self.assertEqual(self._agent(fm, ui, interactive=True).run("x"), DONE)
        self.assertEqual(ui.asked, ["which file?"])
        self.assertEqual(fm.calls, 2)

    def test_stall_stops_well_before_step_cap(self):
        from foundationcode.agent import STOPPED
        fm = _ScriptedFM([{"thought": "t", "action": "list_dir", "path": "."}] * 20)
        agent = self._agent(fm, _SilentUI(), max_steps=20, stall_limit=3)
        self.assertEqual(agent.run("x"), STOPPED)
        self.assertLess(fm.calls, 8)  # self-stopped, didn't grind to 20

    def test_model_errors_recover_then_give_up(self):
        from foundationcode.agent import STOPPED
        from foundationcode.fm import FMResult
        fm = _ScriptedFM([FMResult("", False, "context exceeded")] * 6)
        agent = self._agent(fm, _SilentUI(), max_invalid=4)
        self.assertEqual(agent.run("x"), STOPPED)
        self.assertEqual(fm.calls, 4)  # retried up to max_invalid, then stopped

    def test_fatal_error_stops_immediately(self):
        from foundationcode.agent import STOPPED
        from foundationcode.fm import FMResult
        fm = _ScriptedFM([FMResult("", False, "system model is not available")] * 6)
        agent = self._agent(fm, _SilentUI(), max_invalid=4)
        self.assertEqual(agent.run("x"), STOPPED)
        self.assertEqual(fm.calls, 1)  # no point retrying an unavailable model


class ScopeGateTests(unittest.TestCase):
    def _agent(self, fm, ui=None):
        from foundationcode.agent import Agent
        tmp = tempfile.mkdtemp(prefix="fmc-test-")
        tools = Tools(cwd=tmp, ui=ui or _SilentUI(), approval=Approval.AUTO)
        return Agent(fm, tools, ui or _SilentUI(), scope_check=True)

    def test_out_of_scope_declines_without_running_loop(self):
        from foundationcode.agent import DONE
        fm = _ScriptedFM(
            replies=[{"thought": "t", "action": "write_file", "path": "x",
                      "content": "y"}],
            scope='{"can_do": false, "reason": "not a coding task"}')
        self.assertEqual(self._agent(fm).run("free up space on my mac"), DONE)
        self.assertEqual(fm.calls, 0)  # the action loop never ran

    def test_in_scope_runs_loop(self):
        from foundationcode.agent import DONE
        fm = _ScriptedFM(
            replies=[{"thought": "t", "action": "finish", "content": "done"}],
            scope='{"can_do": true, "reason": ""}')
        self.assertEqual(self._agent(fm).run("edit utils.py"), DONE)
        self.assertEqual(fm.calls, 1)

    def test_fails_open_when_classifier_returns_nothing(self):
        from foundationcode.agent import DONE
        fm = _ScriptedFM(
            replies=[{"thought": "t", "action": "finish", "content": "done"}],
            scope=None)  # classifier failed -> proceed rather than block real work
        self.assertEqual(self._agent(fm).run("do a thing"), DONE)
        self.assertEqual(fm.calls, 1)


if __name__ == "__main__":
    unittest.main()
