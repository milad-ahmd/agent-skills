---
name: durable-agent-workflows
description: Makes long-running, multi-step Python AI agent jobs crash-proof and resumable with the open-source rewindable library, so a failure at step 37 resumes there instead of re-running and re-paying for steps 1 to 36. Wraps each model call, tool call and side effect in a journaled step (ctx.step, ctx.llm), moves time, randomness and network reads behind ctx so replays match, puts emails, payments and deploys behind human approval, caps spend with a Budget, and proves it by killing a run and resuming it. Includes an AST audit script that flags calls breaking durable replay. Use when the user builds an LLM research pipeline, a batch job over many documents or an agent whose tools send email, pay or deploy, and wants it to survive crashes, resume after an exception without paying again, pause hours or days for a human approval, rewind a run to a step after fixing a bug, or asks for durable execution, checkpointing or human-in-the-loop for a Python agent.
---

# Durable Agent Workflows

A plain agent script keeps its progress in memory. When it dies at step 37, steps 1 to 36 run again, every model call is paid for twice, and an email the agent already sent goes out a second time. This skill turns that script into a durable workflow with [rewindable](https://github.com/milad-ahmd/rewindable): each model call, tool call and side effect becomes a step in a SQLite journal, and a resumed run gets completed steps back from the journal for free.

## When to use it, and when not

Use it for Python jobs that are long, costly or have consequences: research pipelines with dozens of model calls, batch processing over hundreds of documents, agents with tools that send email, move money or deploy, and anything that waits on a person or runs for hours.

Skip it for a short script that finishes in seconds and costs cents; rerunning is cheaper than converting. If the team already runs Temporal, DBOS, Restate or Inngest, use that engine's own workflow and activity model; they are the better choice at large scale, across many machines, or in languages other than Python. LangGraph checkpointers cover a graph that lives entirely inside LangGraph. rewindable fits plain Python functions on one machine (or several sharing one journal file) with no server to run.

## 0. Install

```bash
pip install rewindable                 # zero-dependency core, Python 3.10+
pip install "rewindable[anthropic]"    # Claude through the official SDK
pip install "rewindable[openai]"       # OpenAI or any compatible endpoint (Ollama, vLLM)
```

If `pip install rewindable` can't find the package, it isn't on PyPI yet; install from GitHub with `pip install git+https://github.com/milad-ahmd/rewindable`.

## 1. Audit what will break

```bash
python3 scripts/durability_audit.py path/to/agent.py     # or a folder
python3 scripts/durability_audit.py src/ --json
```

On a plain script it lists every call that will need its own step: HTTP and SDK calls (requests, httpx, urllib, aiohttp, OpenAI, Anthropic, boto3, smtplib, Stripe), subprocesses, file writes, `time.time()`, `datetime.now()`, `random`, `uuid4`, `os.urandom`, `time.sleep` and `input()`. On a rewindable workflow it reports the same calls when they run in the workflow body outside a `ctx.step` or `ctx.llm`, helpers that do I/O called straight from the body, `ctx.step("x", fetch(url))` (which runs `fetch` too early), tools that look irreversible but lack `requires_approval=True`, and payment, email or POST calls inside steps that pass no idempotency key. Each finding has `file:line`, why it breaks replay, and the `ctx` replacement. Exit codes are 0 for clean, 1 for findings and 2 for nothing to scan.

It reads code without running it. It follows imports, simple client assignments (`client = OpenAI()`) and helper functions in the scanned files, but not dynamic dispatch or objects passed in from elsewhere. A clean result means nothing known was found; the crash test in step 2.7 is the real proof. Put `# durable: ok` on a line you have checked by hand to silence it.

## 2. Convert the script, one side effect at a time

Work through the audit list in order.

1. Turn the entry point into a workflow function whose first parameter is `ctx`, and start it with `Engine().run(flow, input)`. The input must be JSON-serializable.
2. Wrap every side effect in `ctx.step(name, fn, *args)`. Pass the function and its arguments, or a lambda: `ctx.step("fetch", fetch, url)` or `ctx.step("fetch", lambda: requests.get(url).json())`. The return value must be JSON-serializable, because it is what the journal stores and replays. Use `retries=3` on flaky calls.
3. Replace raw model SDK calls with `ctx.llm(model, messages=[...])`, or with an `Agent` when the model drives tools. Both journal every turn, record tokens and cost, and check the budget first. Use `AnthropicModel("claude-opus-5-5")`, `OpenAICompatModel(...)`, or `ScriptedModel` for offline tests.
4. Move nondeterminism behind `ctx`. Use `ctx.now()` for the time, `ctx.uuid()` for ids, `ctx.step("pick", lambda: random.choice(xs))` for randomness, and `ctx.sleep(seconds)` for waits. A network read is a side effect too, so it goes in a step.
5. Put a human in front of anything irreversible. Mark the tool with `@tool(requires_approval=True)`, or call `ctx.approve("Refund €129 to dana@example.com?")` in the body before the step that does it. The run saves itself as `waiting` and the process can exit. Inside steps that pay, email or deploy, pass `current_step().idempotency_key` to the provider (a Stripe `idempotency_key=`, an `Idempotency-Key` header, a fixed Message-ID), because steps are exactly-once in the journal but at-least-once in the world.
6. Set a hard cap with `Engine().run(flow, input, budget=Budget(max_cost_usd=2.00))`. It also takes `max_tokens` and `max_llm_calls`, counts replayed steps, and is checked before every model call, so a crash loop can't spend past it.
7. Prove it. Run the workflow, kill it mid-way (Ctrl-C or `kill -9`), then run `rewindable runs` and `rewindable resume <run_id>`. The result should show replayed steps and an executed count covering only the unfinished work. `rewindable show <run_id>` prints the step tree with timings, tokens and cost. Then run the audit again.

Keep the steps coarse enough to read in `rewindable show` and fine enough that a crash loses little: one step per page fetched, per model call, per email. Steps cannot be nested; open them from the workflow body, from a `ctx.parallel` branch, or from a helper that receives the ctx.

Fan-out works with `ctx.parallel([lambda c, u=u: summarize(c, u) for u in urls])`. Each branch gets its own child ctx, journals its own steps, and replays on its own, so one failed branch never re-runs the others.

The full primitive list, the CLI and the error types are in `references/rewindable-api.md`.

## The one rule: same inputs, same steps

On resume the workflow function runs again from the top, and each `ctx` call is matched by position against the journal. The body must make the same step calls in the same order every time. Anything that touches the outside world, costs money, or can differ between runs goes through `ctx`.

Before, a plain script that loses everything on the first exception:

```python
import time, uuid, requests
from anthropic import Anthropic

client = Anthropic()

def research(topic):
    run_id = uuid.uuid4().hex
    started = time.time()
    urls = requests.get(SEARCH_API, params={"q": topic}, timeout=30).json()["urls"]
    notes = []
    for url in urls:
        page = requests.get(url, timeout=30).text
        msg = client.messages.create(model="claude-opus-5-5", max_tokens=600,
                                     messages=[{"role": "user", "content": f"Summarize:\n{page}"}])
        notes.append(msg.content[0].text)
    report = client.messages.create(model="claude-opus-5-5", max_tokens=1500,
                                    messages=[{"role": "user", "content": "\n".join(notes)}])
    requests.post(MAIL_API, json={"to": "team@example.com", "body": report.content[0].text})
```

After, the same job as a durable workflow:

```python
import requests
from rewindable import AnthropicModel, Budget, Engine, current_step, text_of

model = AnthropicModel("claude-opus-5-5")

def fetch(url: str) -> str:
    return requests.get(url, timeout=30).text

def send_report(to: str, body: str) -> str:
    key = current_step().idempotency_key  # the same on every retry and resume
    requests.post(MAIL_API, json={"to": to, "body": body}, headers={"Idempotency-Key": key}, timeout=30)
    return "sent"

def summarize(c, url: str) -> str:
    page = c.step(f"fetch {url}", fetch, url)
    reply = c.llm(model, messages=[{"role": "user", "content": f"Summarize:\n{page}"}], max_tokens=600)
    return text_of(reply)

def research(ctx, topic: str) -> str:
    started = ctx.now()
    urls = ctx.step("search", lambda: requests.get(SEARCH_API, params={"q": topic}, timeout=30).json()["urls"])
    notes = ctx.parallel([lambda c, u=u: summarize(c, u) for u in urls], name="summarize")
    reply = ctx.llm(model, messages=[{"role": "user", "content": "\n".join(notes)}], max_tokens=1500, name="report")
    report = text_of(reply)
    if not ctx.approve(f"Email the '{topic}' report to the team?"):
        return report
    ctx.step("email report", send_report, "team@example.com", report)
    return report

if __name__ == "__main__":
    print(Engine().run(research, "durable execution", budget=Budget(max_cost_usd=2.00)))
```

If the code changes so that the step at position 4 is now a different step, `resume` raises `NonDeterminismError` instead of feeding the wrong cached result to the wrong code. Branch from the last matching step with `rewind --to 4`.

## Fix a bug mid-run with rewind

A tool had a bug at step 7 and everything after it is wrong. Fix the code, then branch a new run from that step:

```bash
rewindable show <run_id>              # find the key of the first bad step, e.g. 7 or 2.1.3
rewindable rewind <run_id> --to 7     # steps before 7 replay from the journal, 7 onward runs the new code
rewindable rewind <run_id> --to 7 --input '"new topic"'
```

The original run stays untouched for comparison, and `rewindable runs` shows the new one as a branch of it. In code, use `Engine().rewind(run_id, "7")`. Rewinding to a step inside a parallel group re-runs that group, so every branch sees the fix.

## Running in production

Queue runs with `Engine().submit(flow, input)` or `rewindable start app.py:flow --input '"..."' --queue`, and keep one or more `rewindable worker` processes running under systemd or in a container. A worker drives queued runs, wakes `ctx.sleep` timers when they are due (even after a reboot), resumes runs whose approval has arrived, and takes over runs whose driver crashed. Each run has one driver at a time through a lease (30 seconds by default), so several workers can share one journal.

Approvals happen out of band: `rewindable pending` lists what is waiting, then `rewindable approve <run> <key>` (or `--reject --note "needs a receipt"`), or the buttons in `rewindable ui`. `ctx.approve(question, timeout=86400)` counts as a rejection if nobody answers in time, and `ctx.wait_for("signal", prompt, timeout=..., default=...)` waits for any JSON payload sent with `rewindable signal`.

An agent with a shell tool could run `rewindable approve` on its own request. Close that path with `rewindable approval-code set`, which works from an interactive terminal only and prints the code once. After that, approving needs the code, and an approval that reaches the journal without it is read as a rejection. Keep the code out of files, environment variables and prompts. The journal is `.rewindable/runs.db` (override with `--db` or `REWINDABLE_DB`). Back it up like any other database, and keep it out of git.

## Report

Tell the user which side effects became steps, which calls moved behind `ctx`, which tools now need approval and which steps carry an idempotency key, and the budget that was set. Include the crash test: the run id, the step it was killed at, and the replayed and executed counts from the resume. List anything left over, such as audit findings marked `# durable: ok` and why.

## What not to do

- Call `time.time()`, `datetime.now()`, `random`, `uuid4()` or an HTTP client directly in the workflow body. Replays see new values and repeat the request.
- Write `ctx.step("fetch", fetch(url))`. That calls `fetch` immediately; pass `fetch, url` or a lambda.
- Return objects that aren't JSON-serializable from a step (open files, SDK response objects, sockets). Return the text or a dict.
- Open a step inside another step, or branch the workflow on something that isn't journaled, such as a file that may change or an environment variable read mid-run.
- Edit the code and `resume` a run whose earlier steps changed. Use `rewind --to` the first changed step.
- Ship a tool that sends, pays, deletes or deploys without `requires_approval=True` or a `ctx.approve` in front of it, or without an idempotency key.
- Use `time.sleep` for waits of minutes or days. `ctx.sleep` frees the process and survives a reboot.
- Report the conversion as done without killing a real run and resuming it.
