#!/usr/bin/env python3
"""Tests for durability_audit.py (stdlib unittest, no network, rewindable not needed).

Run: python3 -m unittest discover -s skills/durable-agent-workflows/scripts -p 'test_*.py'
Fixtures are written into temp dirs: a plain agent script, a correct rewindable workflow,
a broken one, tools with and without approval, and a project split across files.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from durability_audit import audit, main  # noqa: E402

SCRIPT = os.path.join(HERE, "durability_audit.py")

PLAIN_AGENT = '''\
import json
import time
import uuid

import anthropic
import requests

client = anthropic.Anthropic()


def research(topic):
    run_id = uuid.uuid4().hex
    started = time.time()
    pages = [requests.get(u, timeout=10).text for u in ["https://a.example", "https://b.example"]]
    notes = client.messages.create(model="claude-opus-5-5", max_tokens=800,
                                   messages=[{"role": "user", "content": "\\n".join(pages)}])
    with open(f"report-{run_id}.md", "w") as f:
        f.write(notes.content[0].text)
    requests.post("https://hooks.example/notify", json={"topic": topic, "took": time.time() - started})
    return notes


if __name__ == "__main__":
    print(json.dumps(research("durable execution").model_dump()))
'''

CORRECT_WORKFLOW = '''\
from datetime import datetime, timezone

import requests

from rewindable import Agent, AnthropicModel, Budget, Engine, current_step, tool

model = AnthropicModel("claude-opus-5-5")


def fetch(url):
    return requests.get(url, timeout=10).text


def save(path, text):
    with open(path, "w") as f:
        f.write(text)
    return path


@tool
def search(query: str) -> list[str]:
    """Search the web."""
    return requests.get("https://search.example", params={"q": query}, timeout=10).json()


@tool(requires_approval=True)
def send_report(to: str, body: str) -> str:
    """Email the report."""
    key = current_step().idempotency_key
    requests.post("https://mail.example/send", json={"to": to, "body": body},
                  headers={"Idempotency-Key": key}, timeout=10)
    return "sent"


researcher = Agent("researcher", model, tools=[search])
mailer = Agent("mailer", model, tools=[send_report])


def research(ctx, topic: str) -> str:
    started = ctx.now()
    stamp = datetime.fromtimestamp(started, tz=timezone.utc).isoformat()
    run_tag = ctx.uuid()
    urls = ctx.step("search", lambda: requests.get("https://search.example", params={"q": topic}).json())
    pages = ctx.parallel([lambda c, u=u: c.step(f"fetch {i}", fetch, u) for i, u in enumerate(urls)])
    summary = ctx.llm(model, messages=[{"role": "user", "content": "\\n".join(pages)}], max_tokens=800)
    text = summary["content"][0]["text"]
    notes = researcher.run(ctx, f"Check these claims: {text}").text
    ctx.step("save", save, f"report-{run_tag}.md", notes)
    ctx.sleep(3600)
    if not ctx.approve(f"Email the {stamp} report?"):
        return "cancelled"
    return mailer.run(ctx, f"Email this to the team: {notes}").text


if __name__ == "__main__":
    print(Engine().run(research, "durable execution", budget=Budget(max_cost_usd=2.0)))
'''

BROKEN_WORKFLOW = '''\
import random
import time

import requests
from openai import OpenAI

from rewindable import Engine

llm = OpenAI()


def load(url):
    return requests.get(url, timeout=10).json()


def flow(ctx, url):
    started = time.time()
    data = requests.get(url).json()
    ok = ctx.step("fetch wrapped", lambda: requests.get(url).json())
    also_ok = ctx.step("load wrapped", load, url)
    page = load(url)
    early = ctx.step("called too early", load(url))
    pick = random.choice(data)
    answer = llm.chat.completions.create(model="gpt-5", messages=[{"role": "user", "content": str(pick)}])
    time.sleep(5)
    checked = time.time()  # durable: ok
    return {"started": started, "answer": answer.choices[0].message.content}


Engine().run(flow, "https://api.example/items")
'''

TOOLS_NO_APPROVAL = '''\
import stripe
import requests

from rewindable import Agent, tool


@tool
def refund_order(order_id: str, amount: float) -> str:
    """Refund an order."""
    stripe.Refund.create(payment_intent=order_id, amount=int(amount * 100))
    return "refunded"


@tool(requires_approval=True)
def post_comment(issue: int, body: str) -> str:
    """Comment on an issue."""
    requests.post(f"https://tracker.example/issues/{issue}/comments", json={"body": body})
    return "ok"


@tool
def lookup(order_id: str) -> dict:
    """Read an order."""
    return requests.get(f"https://shop.example/orders/{order_id}").json()


def handle(ctx, ticket):
    total = ctx.step("charge", lambda: requests.post("https://pay.example/charge", json={"t": ticket}))
    return Agent("support", None, tools=[refund_order, post_comment, lookup]).run(ctx, ticket).text
'''

HELPERS = '''\
import requests


def fetch_page(url):
    return requests.get(url, timeout=10).text


def tidy(text):
    return " ".join(text.split())
'''

FLOW_USING_HELPERS = '''\
from helpers import fetch_page, tidy

from rewindable import Engine


def crawl(ctx, url):
    raw = fetch_page(url)
    clean = tidy(raw)
    safe = ctx.step("fetch", fetch_page, url)
    return clean + safe
'''


def write(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p) or root, exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


def run_main(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(args)
    return code, out.getvalue()


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def scan(self, files, target=None):
        write(self.tmp, files)
        return audit([os.path.join(self.tmp, target) if target else self.tmp])

    def calls(self, report, kind=None):
        return [(f["line"], f["call"]) for f in report["findings"] if kind is None or f["kind"] == kind]


class PlainScriptTest(AuditTest):
    def test_lists_every_side_effect_that_needs_a_step(self):
        r = self.scan({"agent.py": PLAIN_AGENT})
        self.assertEqual(r["modes"][os.path.join(self.tmp, "agent.py")], "plain")
        cats = sorted(f["category"] for f in r["findings"])
        self.assertEqual(cats, ["file", "llm", "network", "network", "time", "time", "uuid"])
        self.assertTrue(all(f["kind"] == "needs-step" for f in r["findings"]))

    def test_model_call_through_client_variable(self):
        r = self.scan({"agent.py": PLAIN_AGENT})
        llm = [f for f in r["findings"] if f["category"] == "llm"]
        self.assertEqual(llm[0]["call"], "client.messages.create")
        self.assertIn("ctx.llm", llm[0]["fix"])

    def test_fixes_name_the_ctx_replacement(self):
        r = self.scan({"agent.py": PLAIN_AGENT})
        fixes = {f["call"]: f["fix"] for f in r["findings"]}
        self.assertEqual(fixes["time.time"], "ctx.now()")
        self.assertEqual(fixes["uuid.uuid4"], "ctx.uuid()")
        self.assertEqual(fixes["requests.get"], 'ctx.step("network call", lambda: requests.get(u, timeout=10))')

    def test_post_is_called_out_as_irreversible(self):
        r = self.scan({"agent.py": PLAIN_AGENT})
        post = [f for f in r["findings"] if f["call"] == "requests.post"][0]
        self.assertIn("irreversible", post["why"])
        get = [f for f in r["findings"] if f["call"] == "requests.get"][0]
        self.assertNotIn("irreversible", get["why"])

    def test_constructing_a_client_is_not_an_effect(self):
        r = self.scan({"agent.py": PLAIN_AGENT})
        self.assertNotIn("anthropic.Anthropic", [f["call"] for f in r["findings"]])


class CorrectWorkflowTest(AuditTest):
    def test_no_findings(self):
        r = self.scan({"flow.py": CORRECT_WORKFLOW})
        self.assertEqual(r["findings"], [], json.dumps(r["findings"], indent=1))

    def test_recognises_the_workflow(self):
        r = self.scan({"flow.py": CORRECT_WORKFLOW})
        self.assertEqual([w["function"] for w in r["workflows"]], ["research"])
        self.assertEqual(r["modes"][os.path.join(self.tmp, "flow.py")], "rewindable")

    def test_exit_zero(self):
        write(self.tmp, {"flow.py": CORRECT_WORKFLOW})
        code, out = run_main([os.path.join(self.tmp, "flow.py")])
        self.assertEqual(code, 0)
        self.assertIn("no findings", out)


class BrokenWorkflowTest(AuditTest):
    def test_flags_time_and_requests_in_the_body(self):
        r = self.scan({"flow.py": BROKEN_WORKFLOW})
        calls = self.calls(r, "replay")
        self.assertIn((17, "time.time"), calls)
        self.assertIn((18, "requests.get"), calls)

    def test_lambda_and_function_reference_in_ctx_step_are_fine(self):
        r = self.scan({"flow.py": BROKEN_WORKFLOW})
        lines = {f["line"] for f in r["findings"]}
        self.assertNotIn(19, lines)
        self.assertNotIn(20, lines)

    def test_helper_called_directly_is_followed(self):
        r = self.scan({"flow.py": BROKEN_WORKFLOW})
        helper = [f for f in r["findings"] if f["line"] == 21]
        self.assertEqual(helper[0]["call"], "load()")
        self.assertIn("requests.get", helper[0]["why"])
        self.assertIn('ctx.step("load", load', helper[0]["fix"])

    def test_calling_the_step_function_too_early(self):
        r = self.scan({"flow.py": BROKEN_WORKFLOW})
        early = [f for f in r["findings"] if f["line"] == 22]
        self.assertEqual([(f["call"], f["category"]) for f in early], [("load()", "early-call")])
        self.assertIn('ctx.step("...", load, url)', early[0]["fix"])

    def test_random_model_call_and_sleep(self):
        r = self.scan({"flow.py": BROKEN_WORKFLOW})
        by_line = {f["line"]: f for f in r["findings"]}
        self.assertEqual(by_line[23]["category"], "random")
        self.assertEqual(by_line[24]["category"], "llm")
        self.assertEqual(by_line[24]["call"], "llm.chat.completions.create")
        self.assertEqual(by_line[25]["category"], "sleep")
        self.assertIn("ctx.sleep", by_line[25]["fix"])

    def test_suppression_comment(self):
        r = self.scan({"flow.py": BROKEN_WORKFLOW})
        self.assertNotIn(26, {f["line"] for f in r["findings"]})

    def test_module_level_engine_run_is_not_a_finding(self):
        r = self.scan({"flow.py": BROKEN_WORKFLOW})
        self.assertNotIn(30, {f["line"] for f in r["findings"]})

    def test_exit_one_and_json(self):
        write(self.tmp, {"flow.py": BROKEN_WORKFLOW})
        code, out = run_main([os.path.join(self.tmp, "flow.py"), "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(data["workflows"][0]["function"], "flow")
        f = data["findings"][0]
        for key in ("file", "line", "kind", "mode", "function", "call", "category", "why", "fix"):
            self.assertIn(key, f)

    def test_parallel_lambda_branch_body_is_checked(self):
        src = (
            "import requests\nfrom rewindable import Engine\n\n\n"
            "def flow(ctx, urls):\n"
            "    good = ctx.parallel([lambda c, u=u: c.step('get', lambda: requests.get(u).text) for u in urls])\n"
            "    bad = ctx.parallel([lambda c, u=u: requests.get(u).text for u in urls])\n"
            "    return good + bad\n"
        )
        r = self.scan({"flow.py": src})
        self.assertEqual(self.calls(r), [(7, "requests.get")])

    def test_helper_that_receives_ctx_is_a_workflow_too(self):
        src = (
            "import time\nfrom rewindable import Engine\n\n\n"
            "def sub(c, x):\n"
            "    return time.time() + x\n\n\n"
            "def flow(ctx):\n"
            "    return sub(ctx, 1)\n"
        )
        r = self.scan({"flow.py": src})
        self.assertEqual(self.calls(r), [(6, "time.time")])
        self.assertIn("sub", [w["function"] for w in r["workflows"]])


class ToolTest(AuditTest):
    def test_irreversible_tool_without_approval(self):
        r = self.scan({"tools.py": TOOLS_NO_APPROVAL})
        approval = [f for f in r["findings"] if f["kind"] == "approval"]
        self.assertEqual([f["function"] for f in approval], ["refund_order"])
        self.assertIn("stripe.Refund.create", approval[0]["why"])

    def test_read_only_tool_is_left_alone(self):
        r = self.scan({"tools.py": TOOLS_NO_APPROVAL})
        self.assertNotIn("lookup", [f["function"] for f in r["findings"]])

    def test_missing_idempotency_key(self):
        r = self.scan({"tools.py": TOOLS_NO_APPROVAL})
        idem = sorted((f["function"], f["call"]) for f in r["findings"] if f["kind"] == "idempotency")
        self.assertEqual(idem, [("handle", "requests.post"), ("post_comment", "requests.post"),
                                ("refund_order", "stripe.Refund.create")])

    def test_tool_called_directly_in_a_workflow(self):
        src = TOOLS_NO_APPROVAL + "\n\ndef direct(ctx, oid):\n    return refund_order.fn(oid, 10.0)\n"
        r = self.scan({"tools.py": src})
        direct = [f for f in r["findings"] if f["function"] == "direct"]
        self.assertEqual(direct[0]["call"], "refund_order()")
        self.assertIn("refund_order.fn", direct[0]["fix"])


class FolderTest(AuditTest):
    def test_helpers_in_another_file_are_followed(self):
        r = self.scan({"helpers.py": HELPERS, "flow.py": FLOW_USING_HELPERS})
        self.assertEqual(r["modes"][os.path.join(self.tmp, "helpers.py")], "helper")
        self.assertEqual([(f["line"], f["call"]) for f in r["findings"]], [(7, "fetch_page()")])
        self.assertIn("helpers.py:5", r["findings"][0]["why"])

    def test_folder_mixes_modes_and_skips_noise(self):
        write(self.tmp, {
            "app/flow.py": CORRECT_WORKFLOW,
            "scripts/agent.py": PLAIN_AGENT,
            "tests/test_flow.py": "import time\ntime.time()\n",
            ".venv/lib/x.py": "import requests\nrequests.get('x')\n",
            "notes.txt": "not python",
        })
        r = audit([self.tmp])
        self.assertEqual(r["files_scanned"], 2)
        self.assertEqual({f["mode"] for f in r["findings"]}, {"plain"})
        r2 = audit([self.tmp], include_tests=True)
        self.assertEqual(r2["files_scanned"], 3)

    def test_syntax_error_is_skipped_not_fatal(self):
        write(self.tmp, {"ok.py": CORRECT_WORKFLOW, "bad.py": "def (:\n"})
        r = audit([self.tmp])
        self.assertEqual(r["files_scanned"], 1)
        self.assertEqual(len(r["skipped"]), 1)


class ExitCodeTest(AuditTest):
    def test_empty_folder_is_exit_two(self):
        code, _ = run_main([self.tmp])
        self.assertEqual(code, 2)

    def test_missing_path_is_exit_two(self):
        code, _ = run_main([os.path.join(self.tmp, "nope.py")])
        self.assertEqual(code, 2)

    def test_only_unparsable_files_is_exit_two(self):
        write(self.tmp, {"bad.py": "def (:\n"})
        code, _ = run_main([self.tmp])
        self.assertEqual(code, 2)

    def test_plain_script_with_effects_is_exit_one(self):
        write(self.tmp, {"agent.py": PLAIN_AGENT})
        code, out = run_main([self.tmp])
        self.assertEqual(code, 1)
        self.assertIn("needs a step", out)

    def test_cli_subprocess_and_help(self):
        write(self.tmp, {"flow.py": CORRECT_WORKFLOW})
        p = subprocess.run([sys.executable, SCRIPT, self.tmp], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        h = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True)
        self.assertIn("static analysis limits", h.stdout)
        self.assertIn("durable: ok", h.stdout)


if __name__ == "__main__":
    unittest.main()
