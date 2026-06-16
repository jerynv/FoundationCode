# FoundationCode

**An autonomous coding agent that runs entirely on-device, powered by Apple's
Foundation Models.**

FoundationCode turns the small on-device model behind Apple Intelligence into a
tool-using coding agent. It reads your files, writes code, runs shell commands,
and verifies its own work — in a loop — without an API key, without a network
connection, and without a single byte leaving your Mac.

It's built on top of Apple's [`fm`](https://developer.apple.com/apple-intelligence/)
command-line tool, so the entire model runtime is whatever ships with macOS.

```
   ___                  _      _   _          ___         _
  | __|__ _  _ _ _  __| |__ _| |_(_)___ _ _ / __|___  __| |___
  | _/ _ \ || | ' \/ _` / _` |  _| / _ \ ' \ (__/ _ \/ _` / -_)
  |_|\___/\_,_|_||_\__,_\__,_|\__|_\___/_||_\___\___/\__,_\___|
```

---

## Why this exists

Every "coding agent" you can install today phones home to a frontier model. This
one doesn't. The trade-off is real — Apple's on-device model is a few-billion
parameter model with a small context window, so this is **not** a Claude/GPT
replacement. What it *is*:

- **100% local & private** — your code never leaves the machine.
- **Free & offline** — no tokens, no rate limits, works on a plane.
- **A real agent loop** — not a chat box; it plans, acts, observes, and verifies.
- **A clean reference implementation** — ~1,000 lines of dependency-free Python
  showing how to drive a small, structured-output model as an agent.

It's genuinely useful for small, well-scoped tasks (scaffold a file, write a
function and a test, run it, fix a typo, explain a module) and it's a great
starting point if you want to build on Apple's on-device model.

---

## How it works

Apple's model doesn't have native tool-calling, so FoundationCode uses
**guided generation** (`fm respond --schema`) to *force* the model to emit a
single, schema-valid JSON action every turn. There's no fragile free-text
parsing — the model is constrained to return exactly:

```json
{ "thought": "...", "action": "write_file", "path": "app.py", "content": "..." }
```

The agent executes that action, feeds the result back as the next observation,
and repeats until the model calls `finish`.

```
        ┌─────────────────────────────────────────────┐
        │  build prompt: task + working dir + history  │
        └───────────────────────┬─────────────────────┘
                                 ▼
                 fm respond --schema  (forced JSON)
                                 │
                                 ▼
        ┌─────────────────────────────────────────────┐
        │  parse → normalize → validate the action     │
        └───────────────────────┬─────────────────────┘
                                 ▼
          execute tool (read / write / bash / list)
                                 │
                                 ▼
        append capped observation to history → repeat
                                 │
                          action == finish ? ──► done
```

Three things make this robust on a small model:

1. **Aggressive context management.** The on-device context window is tiny, so
   FoundationCode manages history itself: every file read and command output is
   capped, recent steps are kept verbatim, and older steps are compressed or
   dropped to stay within a character budget (well under half the window, to
   leave room for the model's own output).
2. **Failure recovery.** Greedy decoding can make a small model repeat the same
   malformed reply forever, so after any bad action FoundationCode switches to
   sampling to break the loop, normalizes common formatting slips (e.g. an
   argument packed into the action field), and bails out after a few tries
   instead of spinning. Even a context-overflow error from the model is treated
   as a recoverable "be terse" nudge, not a crash.
3. **Self-regulation (it knows when to stop).** A small model will happily
   re-read the same file thirteen times. A `ProgressTracker` fingerprints every
   action by its *resolved* path (so `.gitignore` and `/abs/.gitignore` are the
   same action) and remembers every observation it has seen. When the model
   repeats an action and gets back content it has already seen — i.e. it learned
   nothing — FoundationCode escalates: first a firm nudge (and a switch to
   sampling), then a graceful stop. It stops *itself* long before the step cap,
   and it can call `ask_user` to hand a decision back to you rather than guess.
4. **A pre-flight scope check.** A small model can't be trusted to *refuse* a
   truly impossible request mid-loop, but it answers a single yes/no question
   reliably. So before the loop runs FoundationCode asks the model one thing:
   *could a program attempt this at all?* The bar is deliberately low — fetching
   the weather is an API call, searching flights is an API call, so those are
   fair game. Only requests that need the **physical world** (fix a faucet,
   drive you somewhere) are declined with the reason instead of being flailed
   at. The check fails **open** (errors mean "go ahead") so it never blocks real
   work, and `--no-scope-check` turns it off.
5. **Vetted recipes for codeable-but-dangerous tasks.** "Free up disk space" is
   a real coding task — the deliverable is a cleanup script — but the on-device
   model writes *unsafe* ones (it literally generated a script that spawns a
   root shell and `rm -rf`s your whole cache dir). So for disk cleanup the agent
   doesn't let the model improvise: it writes a hand-vetted, **report-first**
   `free-space.sh` that deletes nothing until you run it with `--apply`, only
   ever touches regenerable junk (caches, build artifacts, Trash, DerivedData),
   and never uses `sudo` or touches your documents or source.

> The step cap (`--max-steps`, default 25) is a safety backstop, not a target.
> With progress detection the agent almost always finishes, asks, or stops on
> its own well before reaching it — and when it does hit the cap, it says so
> instead of failing silently.

---

## Requirements

- **macOS with Apple Intelligence** and the on-device model downloaded.
- **Apple's `fm` CLI** on your `PATH` (verify with `fm available` — you should
  see `System model available`).
- **Python 3.9+** (ships with macOS / the Xcode Command Line Tools).

No third-party Python packages. None.

---

## Install

```bash
git clone https://github.com/jerynv/FoundationCode.git
cd FoundationCode
pip install -e .          # installs the `fmcode` command
```

Or run it without installing anything:

```bash
python3 -m foundationcode "explain what this project does"
```

---

## Usage

```bash
# One-shot task (asks before each file write / shell command)
fmcode "add a --version flag to cli.py and update the README"

# Auto-approve writes and commands (use in a sandbox / throwaway dir)
fmcode --auto "create utils.py with a slugify(text) function and a test, then run it"

# Read-only: let it explore and explain, but block all mutations
fmcode --readonly "how does the agent loop decide when to stop?"

# Point it at a different project
fmcode -C ~/code/myapp "write a test for the parse_date function"

# Interactive session
fmcode
```

### A real run

```
◆ task  Create greet.py with a function greet(name) returning 'Hi, '+name and
        print greet('Ada'). Run it with python3 to confirm.

  · Create greet.py with the required function and print statement.
● write_file greet.py
    ok: created greet.py (61 bytes, 4 lines)
  · Run the script to confirm the output.
● run_bash python3 greet.py
    [exit 0]
    Hi, Ada
✔ done
  Created greet.py and confirmed it prints "Hi, Ada".
```

---

## Tools

The agent has a deliberately small toolset — fewer tools means a small model
uses them more reliably.

| Action        | Arguments        | What it does                                  |
|---------------|------------------|-----------------------------------------------|
| `list_dir`    | `path`           | List a directory                              |
| `read_file`   | `path`           | Read a file (with line numbers, size-capped)  |
| `write_file`  | `path`, `content`| Create/overwrite a file (full contents)       |
| `delete_file` | `path`           | Delete a file — **opt-in** (`--allow-delete`), working dir only |
| `run_bash`    | `command`        | Run a shell command (grep, tests, git, …)     |
| `ask_user`    | `content`        | Ask you a question instead of guessing        |
| `finish`      | `content`        | Stop and return a summary to you              |

`run_bash` covers searching, running tests, and git, so the surface stays tiny.

---

## Safety

FoundationCode can modify files and run shell commands, so it ships with three
approval modes and a hard safety net:

- **`ask` (default):** prompts you to approve every file write and command. You
  can answer `y` / `n` / `a` (always, for the rest of the session).
- **`--auto`:** auto-approves mutations. Use it in a throwaway directory.
- **`--readonly`:** refuses *all* writes and commands — explore/explain only.

A **command denylist** hard-blocks catastrophic commands (`rm -rf /`, fork
bombs, `mkfs`, `dd` onto a device, `shutdown`, …) **even in `--auto` mode**, so
auto-approve can't be tricked into wiping your disk.

`delete_file` is **contained to the working directory** — it refuses to delete
anything outside it, the working directory itself, or anything inside `.git`,
even with `--auto`. Ask it to "free up disk space" and it won't improvise `rm`
commands — it writes a vetted, report-first `free-space.sh` you can read before
running (see "Vetted recipes" above).

> Still: review what it's about to do. It's a small model and it makes mistakes.

---

## Options

```
fmcode [task ...]

  -C, --cwd DIR         working directory the agent operates in (default: .)
  -m, --model {system,pcc}   on-device (default) or Private Cloud Compute
  --max-steps N         max actions before giving up (default: 25)
  --bash-timeout SECS   per-command timeout (default: 60)
  --auto                auto-approve writes and commands
  --readonly            refuse all writes and commands
  --allow-delete        permit the delete_file action (off by default)
  --no-scope-check      skip the pre-flight "is this a coding task?" check
  --no-greedy           sample instead of greedy decoding
  --no-color            disable coloured output
  --version
```

---

## Honest limitations

This is built on a small on-device model. In practice that means:

- It shines on **small, single-file, well-specified tasks** and tends to wander
  on large multi-file changes.
- With a small context window it can **forget earlier steps** on long tasks —
  keep tasks focused and use `--max-steps` to bound runs.
- It sometimes **over-verifies** (re-running a passing test) before finishing.
- Each step runs a fresh `fm` inference (~5–10s on-device), so runs are not
  instant.

These are properties of the model, not bugs in the harness. FoundationCode is
designed to fail safely (approval gates, denylist, step/loop limits) rather than
to pretend the model is bigger than it is.

---

## Prior art & design notes

FoundationCode's self-regulation is deliberately borrowed from how the better
open-source terminal agents avoid runaway loops — adapted for a model far
smaller than the ones they target:

- **[OpenHands](https://github.com/All-Hands-AI/OpenHands)** — its
  `controller/stuck.py` detects repeated identical action/observation pairs and
  alternation cycles with cheap structural checks (no extra model calls). That's
  the core idea behind our `ProgressTracker`.
- **[RA.Aid](https://github.com/ai-christianson/RA.Aid)** and
  **[Cline](https://github.com/cline/cline)** — duplicate-tool-call
  fingerprinting and a low "consecutive mistakes" ceiling (3) before escalating
  to the user. We use the same ceiling.
- **[Aider](https://github.com/Aider-AI/aider)** — caps reflective error-retries
  (`max_reflections = 3`) and feeds errors back as targeted corrections rather
  than blind retries.
- **[Gemini CLI](https://github.com/google-gemini/gemini-cli)** — a dedicated
  loop-detection service (identical-tool-call and repeated-content detection)
  and a turn cap that escalates instead of dying.
- **[OpenAI Codex CLI](https://github.com/openai/codex)** — the cautionary tale:
  native tool-calling with *no* loop bound shipped a real runaway bug. Always
  bound the loop, even with structured output.

The difference here is scale: those tools drive frontier models that mostly know
when to stop. FoundationCode drives a few-billion-parameter on-device model that
doesn't, so the harness has to be the adult in the room — hence forced JSON
actions, resolved-path fingerprinting, already-seen-content detection, and a
hard but graceful stop.

## Development

```bash
# Offline tests — no `fm` / Apple Intelligence required, run anywhere
python3 -m unittest discover -s tests -v
```

The deterministic layers (parsing, validation, safety, tools, context budgeting)
are fully unit-tested without touching the model.

Project layout:

```
foundationcode/
  fm.py        # wrapper around the `fm` CLI
  schema.py    # action schema + system prompt
  parsing.py   # JSON extraction, normalization, validation
  context.py   # context-window budgeting
  progress.py  # no-progress / loop detection
  scope.py     # pre-flight "can this be done with code?" check
  recipes.py   # vetted scripts for codeable-but-dangerous tasks
  tools.py     # tools + safety/approval layer
  agent.py     # the agent loop
  ui.py        # terminal rendering
  cli.py       # argparse entry point
```

---

## License

MIT — see [LICENSE](LICENSE).

FoundationCode is an independent project and is not affiliated with or endorsed
by Apple. "Apple" and "Apple Intelligence" are trademarks of Apple Inc.
