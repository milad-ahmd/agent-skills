#!/usr/bin/env python3
"""Tests for audit_workflows.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/gh-actions-auditor/scripts -p 'test_*.py'
Fixtures in fixtures/ are planted-vulnerable examples. GitHub never runs them (only the repo root's
.github/workflows/ is live), and they hold no real secrets.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit_workflows import Audit, Yaml  # noqa: E402

SCRIPT = os.path.join(HERE, "audit_workflows.py")
FX = os.path.join(HERE, "fixtures")


def run(*args):
    r = subprocess.run([sys.executable, SCRIPT, "--json", *args], capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout) if r.stdout.strip().startswith("{") else None


def audit_text(text, name="wf.yml"):
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, name)
        with open(p, "w") as f:
            f.write(text)
        a = Audit()
        a.file(p, "me")
        return a.findings


def rules(findings):
    return sorted({(f["rule"], f["severity"]) for f in findings})


class Parser(unittest.TestCase):
    def test_structure(self):
        y = Yaml("on: [push, 'pull_request']  # c\npermissions: {}\njobs:\n  a:\n    steps:\n"
                 "      - uses: x/y@v1 # pin\n        with:\n          ref: main\n      - run: |\n          one\n\n          two\n"
                 "      -\n        name: \"q: x\"\n").parse()
        self.assertEqual(list(y["on"]), ["push", "pull_request"])
        self.assertEqual(dict(y["permissions"]), {})
        steps = y["jobs"]["a"]["steps"]
        self.assertEqual((steps[0]["uses"], steps[0]["uses"].comment, steps[0]["with"]["ref"]), ("x/y@v1", "pin", "main"))
        self.assertEqual((steps[1]["run"], steps[1]["run"].line), ("one\n\ntwo", 10))
        self.assertEqual(steps[2]["name"], "q: x")

    def test_sequence_at_key_indent_and_continuation(self):
        y = Yaml("on:\n  push:\n    branches:\n    - main\njobs:\n  b:\n    if: github.event_name == 'push' &&\n      true\n").parse()
        self.assertEqual(list(y["on"]["push"]["branches"]), ["main"])
        self.assertEqual(y["jobs"]["b"]["if"], "github.event_name == 'push' && true")


class Fixtures(unittest.TestCase):
    def test_vulnerable_workflow(self):
        code, out = run(os.path.join(FX, "vulnerable.yml"))
        self.assertEqual(code, 1)
        got = rules(out["findings"])
        for want in [("pwn-request", "critical"), ("script-injection", "critical"), ("unsecure-commands", "high"),
                     ("unpinned-action", "high"), ("unpinned-action", "medium"), ("unpinned-action", "low"),
                     ("secrets-in-privileged-pr", "high"), ("write-all", "high"), ("secrets-inherit", "high"),
                     ("missing-permissions", "medium"), ("self-hosted-pr", "medium"), ("curl-pipe-shell", "medium"),
                     ("unpinned-image", "medium"), ("persist-credentials", "info")]:
            self.assertIn(want, got)
        inj = [f for f in out["findings"] if f["rule"] == "script-injection"]
        self.assertEqual(sorted(f["line"] for f in inj), [30, 35, 43])  # exact lines inside | blocks
        pwn = next(f for f in out["findings"] if f["rule"] == "pwn-request")
        self.assertEqual(pwn["line"], 18)

    def test_safe_workflow_is_clean(self):
        code, out = run(os.path.join(FX, "safe.yml"), "--fail-on", "info")
        self.assertEqual((code, out["findings"]), (0, []))

    def test_composite_action(self):
        code, out = run(os.path.join(FX, "composite", "action.yml"))
        self.assertEqual(rules(out["findings"]), [("script-injection", "high"), ("script-injection", "low"), ("unpinned-action", "medium")])

    def test_discovers_dirs_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(run(t)[0], 2)  # nothing to audit
            os.makedirs(os.path.join(t, ".github", "workflows"))
            with open(os.path.join(t, ".github", "workflows", "ci.yaml"), "w") as f:
                f.write("on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
                        "    steps:\n      - uses: actions/checkout@v4\n")
            code, out = run(t)
            self.assertEqual((code, rules(out["findings"])), (0, [("unpinned-action", "low")]))
            self.assertEqual(run(t, "--fail-on", "low")[0], 1)


class Rules(unittest.TestCase):
    def test_injection_contexts(self):
        for expr in ["github.event.issue.body", "github.event.review.body", "github.event.pages[0].page_name",
                     "github.event.commits[0].message", "github.event.head_commit.author.email",
                     "github.event.pull_request.head.label", "github.event.workflow_run.head_branch", "toJSON(github.event)"]:
            f = audit_text(f"on: issues\npermissions: {{}}\njobs:\n  a:\n    steps:\n      - run: echo \"${{{{ {expr} }}}}\"\n")
            self.assertEqual(rules(f), [("script-injection", "critical")], expr)

    def test_safe_expressions_not_flagged(self):
        f = audit_text("on: pull_request\npermissions: {}\njobs:\n  a:\n    steps:\n"
                       "      - run: echo ${{ github.sha }} ${{ github.event.pull_request.number }} ${{ inputs.name }}\n"
                       "      - env:\n          T: ${{ github.event.pull_request.title }}\n        run: echo \"$T\"\n")
        self.assertEqual(f, [])

    def test_workflow_run_pwn_and_gh_pr_checkout(self):
        f = audit_text("on:\n  workflow_run:\n    workflows: [CI]\npermissions: {}\njobs:\n  a:\n    steps:\n"
                       "      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567\n"
                       "        with:\n          ref: ${{ github.event.workflow_run.head_sha }}\n      - run: make\n")
        self.assertIn(("pwn-request", "critical"), rules(f))
        f = audit_text("on: issue_comment\npermissions: {}\njobs:\n  a:\n    steps:\n      - run: gh pr checkout 1\n      - run: ./build.sh\n")
        self.assertIn(("pwn-request", "critical"), rules(f))

    def test_permissions_variants(self):
        self.assertEqual(rules(audit_text("on: push\npermissions: write-all\njobs:\n  a:\n    steps: []\n")), [("write-all", "high")])
        self.assertEqual(rules(audit_text("on: push\njobs:\n  a:\n    permissions:\n      contents: read\n    steps: []\n")), [])
        self.assertEqual(rules(audit_text("on: push\npermissions:\n  contents: write\njobs:\n  a: {}\n  b: {}\n")),
                         [("broad-write-scope", "medium")])
        self.assertEqual(rules(audit_text("on: workflow_call\njobs:\n  a:\n    steps: []\n")), [("missing-permissions", "low")])

    def test_same_owner_inherit_is_fine(self):
        f = audit_text("on: push\npermissions: {}\njobs:\n  a:\n    uses: me/infra/.github/workflows/x.yml@0123456789abcdef0123456789abcdef01234567\n"
                       "    secrets: inherit\n")
        self.assertEqual(f, [])


if __name__ == "__main__":
    unittest.main()
