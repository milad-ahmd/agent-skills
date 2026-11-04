# rewindable API reference

Taken from the rewindable README and docs/how-it-works.md (v0.2). Check the installed version's README if something here doesn't match: the library is an alpha and the API may still change.

## Workflow primitives

A workflow is a plain function whose first parameter is the context: `def flow(ctx, input): ...`.

| Call | What it does |
|---|---|
| `ctx.step(name, fn, *args, retries=0, backoff=0.5, **kwargs)` | Runs `fn(*args, **kwargs)` once for this position in the workflow. Later replays return the journaled result. The result must be JSON-serializable (dataclasses and pydantic models are converted). |
| `ctx.llm(model, messages=[...], system=None, tools=None, name=None, **params)` | A model call as a step. Checks the budget first, records tokens and cost. Returns `{"content": [...blocks], "stop_reason", "usage", "model"}`; `text_of(response)` gives the text. |
| `ctx.parallel([branch, ...], name="parallel", max_workers=None)` | Runs each `branch(child_ctx)` concurrently and returns results in order. Each branch journals its own steps under its own keys (`3.0.1`, `3.1.1`, ...). |
| `ctx.approve(question, timeout=None)` | Suspends the run until a human approves or rejects. Returns an `Approval` (truthy when approved, with `.note`). On timeout it counts as a rejection. |
| `ctx.wait_for(signal, prompt=None, timeout=None, default=None)` | Suspends until a JSON payload for `signal` is delivered, then returns it; `default` after the timeout. |
| `ctx.sleep(seconds)` | Durable timer. Under a second it sleeps in-process; longer, the run is saved as `waiting` with a wake-up time and `rewindable worker` resumes it. |
| `ctx.now()` / `ctx.uuid()` | Wall-clock time and a random hex id, recorded once so replays see the same value. |
| `ctx.run_id` / `ctx.input` | The run's id and its input. |
| `current_step()` | Inside a step: `StepInfo(run_id, key, name, attempt)`. `current_step().idempotency_key` is `"{run_id}:{key}"`, stable across retries, crashes and resumes. |

Steps cannot be nested: a step's function must not open `ctx.step` or `ctx.llm` itself (`NestedStepError`). Open steps from the workflow body, a `ctx.parallel` branch, or a helper that receives the ctx.

## Agents and tools

```python
from rewindable import Agent, AnthropicModel, tool

@tool
def search(query: str, limit: int = 5) -> list[str]:
    """Search the web.

    Args:
        query: What to look for.
    """

@tool(requires_approval=True)          # every call waits for a human
def issue_refund(order_id: str, amount: float, reason: str) -> str:
    """Refund an order."""

support = Agent("support", AnthropicModel("claude-opus-5-5"),
                tools=[search, issue_refund], instructions="Verify the order before refunding.")

def handle_ticket(ctx, ticket: str) -> str:
    return support.run(ctx, ticket).text
```

Every model turn and every tool call of an `Agent` is a step, and parallel tool calls run through `ctx.parallel`. The JSON schema comes from type hints, descriptions from the docstring and its `Args:` section. A tool that raises is recorded as a completed step whose output is the error, because that error is what the model saw. A rejected approval goes back to the model as an error result with the reviewer's note. `agent.as_tool()` lets a lead agent delegate to a specialist. To call a tool's function yourself inside a workflow, wrap it: `ctx.step("check replies", check_replies.fn, contact)`.

`@tool` also takes `name=`, `description=` and `retries=`. `Agent(...)` takes `max_turns=` (default 20).

## Models

| Adapter | Install | Notes |
|---|---|---|
| `AnthropicModel("claude-opus-5-5", effort="medium")` | `rewindable[anthropic]` | Official SDK, built-in price table for cost tracking. |
| `OpenAICompatModel("llama3.2", base_url="http://localhost:11434/v1")` | `rewindable[openai]` | Any Chat Completions endpoint: OpenAI, Ollama, vLLM, LM Studio, OpenRouter. |
| `ScriptedModel(fn_or_list)` | built in | Deterministic offline model for tests and CI. Prefer a function over a list when a test resumes in another process. |

Any object with `name`, `complete(request) -> response` and `cost(response) -> float` works as a model.

## Engine

```python
engine = Engine()                       # or Engine("path/to/runs.db")
result = engine.run(flow, input, budget=Budget(max_cost_usd=2.0, max_tokens=None, max_llm_calls=None))
result.status        # "completed" | "waiting" | "failed" | ...
result.output, result.error, result.waiting_for, result.wake_at
result.executed_steps, result.replayed_steps, result.saved_usd, result.cost_usd

engine.submit(flow, input)              # queue for a worker; returns the run id
engine.resume(run_id, budget=None)      # continue a crashed, failed or waiting run
engine.rewind(run_id, "7", input=...)   # branch a new run from step 7
engine.approve(run_id, key, approved=True, note="", code=None)
engine.signal(run_id, key, payload, code=None)
engine.work(once=False)                 # the worker loop
```

Errors: `NonDeterminismError` (the code no longer matches the journal), `BudgetExceeded`, `NestedStepError`, `RunLocked` (another live process drives the run), `RunNotFound`, `WorkflowNotFound`, `ApprovalCodeError`.

## CLI

```text
rewindable ui                               local web UI: runs, steps, conversations, approve/resume/rewind
rewindable runs [--status waiting]          list recent runs, with cost
rewindable show RUN [--json]                step tree: timing, tokens, cost, results
rewindable step RUN KEY                     everything recorded for one step (full model output)
rewindable pending                          approvals and signals waiting on a human
rewindable approve RUN KEY [--reject --note "..."]
rewindable approval-code set|clear|status   require a code only you hold to approve
rewindable signal RUN KEY '{"json": "payload"}'
rewindable resume RUN [--max-cost 5]        continue a crashed, failed or waiting run
rewindable rewind RUN --to STEP [--input '...']
rewindable start app.py:flow --input '...' [--queue]
rewindable worker [--once]                  drive queued runs, due timers, answered approvals, crashed runs
```

Run ids accept unique prefixes. Approval keys look like `approve-5`; `rewindable pending` prints the exact approve command. `--input` is JSON, so a string needs quotes inside quotes: `--input '"durable execution"'`. The journal is `.rewindable/runs.db` (`--db` or `REWINDABLE_DB` to move it), one SQLite file in WAL mode, so any process on the machine can approve, resume or work on a run.

## Guarantees

- Steps are exactly-once in the journal and at-least-once in the world. If the process dies after a side effect and before the result is committed, that step runs again on resume. Pass an idempotency key to anything that must not repeat.
- `resume` calls the workflow again from the top. Completed steps return instantly; the first unfinished one runs for real. A step left `running` by a hard crash is treated as never finished.
- Replay checks each step's name and kind at its key. A mismatch raises `NonDeterminismError` rather than handing one step's result to another.
- `rewind --to K` copies every completed step that started before K into a new run (except groups that enclose K, which re-run) and leaves the original alone.
- Budgets count replayed steps too, so a crash loop cannot spend past the limit. `resume --max-cost` raises the limit for one run.
- A workflow is stored as an importable reference (`package.module:function`, or `/abs/path/script.py:function`), which is how a fresh process finds the code again. The script is imported under a module name other than `__main__`, so its `if __name__ == "__main__":` block does not run again on resume.
