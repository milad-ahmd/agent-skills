#!/usr/bin/env python3
"""Tests for run_trigger_evals (stdlib unittest). Never calls the real model: live mode runs against a fake `claude`.

Run: python3 -m unittest discover -s scripts -p 'test_run_trigger_evals.py'
"""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_trigger_evals as rte  # noqa: E402

SKILLS = {
    "commit-craft": "Turns messy working-tree changes into clean, atomic Conventional Commits. Use when the user asks to "
                    "commit, write a commit message, or split changes into commits.",
    "changelog-from-git": "Generates release notes and CHANGELOG entries from a git commit range. Use when the user asks "
                          "for release notes, a changelog, or what changed since a tag.",
    "sql-optimizer": "Explains slow SQL queries, reads EXPLAIN plans and suggests indexes for PostgreSQL and MySQL. Use "
                     "when the user shares a slow query or asks for index recommendations.",
}

# Shapes follow the documented `claude -p --output-format stream-json --verbose` events:
# system/init, assistant (message.content blocks), user (tool_result), result.
STREAM = [
    {"type": "system", "subtype": "init", "session_id": "s1", "cwd": "/tmp/x", "model": "claude-sonnet",
     "tools": ["Skill"], "skills": ["commit-craft", "changelog-from-git"], "permissionMode": "dontAsk"},
    {"type": "assistant", "session_id": "s1", "parent_tool_use_id": None,
     "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": "Loading the skill."}]}},
    {"type": "assistant", "session_id": "s1", "parent_tool_use_id": None,
     "message": {"id": "m1", "role": "assistant", "content": [
         {"type": "tool_use", "id": "toolu_1", "name": "Skill", "input": {"skill": "changelog-from-git"}}]}},
    {"type": "user", "session_id": "s1", "parent_tool_use_id": None,
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "Launching skill"}]}},
    {"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 2, "total_cost_usd": 0.0123,
     "session_id": "s1"},
]


def lines(events):
    return [json.dumps(e) + "\n" for e in events]


def make_repo(root, skills=SKILLS, evals=None):
    sd, ed = os.path.join(root, "skills"), os.path.join(root, "evals")
    for name, desc in skills.items():
        os.makedirs(os.path.join(sd, name))
        with open(os.path.join(sd, name, "SKILL.md"), "w") as f:
            f.write(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    os.makedirs(ed)
    for name, e in (evals or {}).items():
        with open(os.path.join(ed, name + ".json"), "w") as f:
            f.write(e if isinstance(e, str) else json.dumps(e))
    return sd, ed


def ev(skill, yes, no):
    return {"skill": skill, "should_trigger": yes, "should_not_trigger": no}


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = rte.main(list(argv))
        except SystemExit as x:
            code = x.code
    return code, out.getvalue(), err.getvalue()


class Tokens(unittest.TestCase):
    def test_stem_merges_inflections(self):
        self.assertEqual({rte.stem(w) for w in ("commit", "commits", "committing", "committed")}, {"commit"})
        self.assertEqual({rte.stem(w) for w in ("release", "released", "releases", "releasing")}, {"releas"})
        self.assertEqual(rte.stem("queries"), rte.stem("query"))

    def test_stopwords_and_filler_dropped(self):
        self.assertEqual(rte.tokens("Can you please help me write the PR"), ["writ", "pr"])


class Offline(unittest.TestCase):
    def setUp(self):
        self.router = rte.Router({k: {"name": k, "description": v} for k, v in SKILLS.items()})

    def top(self, prompt):
        return self.router.rank(prompt)[0][0]

    def test_rank_picks_lexical_owner(self):
        self.assertEqual(self.top("split my changes into commits with good messages"), "commit-craft")
        self.assertEqual(self.top("write release notes for v2.0"), "changelog-from-git")
        self.assertEqual(self.top("this postgres query is slow, which index?"), "sql-optimizer")

    def test_rank_is_total_and_deterministic(self):
        order, scores = self.router.rank("nothing relevant at all")
        self.assertEqual(sorted(order), sorted(SKILLS))
        self.assertEqual(order, sorted(SKILLS))  # all-zero scores fall back to name order
        self.assertTrue(all(v == 0 for v in scores.values()))

    def test_run_offline_counts_hits_and_confusion(self):
        skills = {k: {"name": k, "description": v} for k, v in SKILLS.items()}
        evals = {"commit-craft": ev("commit-craft", ["commit these changes", "nothing relevant"],
                                    ["release notes since v1", "write a commit message for these changes"])}
        [row] = rte.run_offline(skills, evals, k=1)
        self.assertEqual((row["n"], row["hits"], row["top1"]), (2, 1, 1))
        self.assertEqual(row["misses"][0]["prompt"], "nothing relevant")
        self.assertEqual((row["n_not"], row["confused"]), (2, 1))
        self.assertEqual(row["confusions"][0]["prompt"], "write a commit message for these changes")


class Files(unittest.TestCase):
    def test_coverage_reports_orphans_missing_and_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            sd, ed = make_repo(d, evals={
                "commit-craft": ev("commit-craft", ["commit this"], ["release notes"]),
                "gone-skill": ev("gone-skill", ["x"], ["y"]),
                "bad": '{"skill": "bad", "should_trigger": "not a list"}',
                "broken": "{not json",
            })
            skills = rte.load_skills(sd)
            evals, problems = rte.load_evals(ed)
            run, gone, missing = rte.coverage(skills, evals)
            self.assertEqual(list(run), ["commit-craft"])
            self.assertEqual(gone, ["gone-skill"])
            self.assertEqual(missing, ["changelog-from-git", "sql-optimizer"])
            self.assertEqual(len(problems), 2)

    def test_main_offline_markdown_json_and_fail_below(self):
        with tempfile.TemporaryDirectory() as d:
            sd, ed = make_repo(d, evals={
                "commit-craft": ev("commit-craft", ["commit these changes", "unrelated words"], ["release notes"]),
                "gone-skill": ev("gone-skill", ["x"], ["y"])})
            js, summ = os.path.join(d, "r.json"), os.path.join(d, "summary.md")
            code, out, _ = run_main("--skills-dir", sd, "--evals-dir", ed, "-k", "1", "--json", js, "--summary", summ)
            self.assertEqual(code, 0)
            self.assertIn("lexical proxy", out)
            self.assertIn("| commit-craft | 1/2 | 1/2 | 0/1 | LOW |", out)
            self.assertIn("no longer exist: gone-skill", out)
            self.assertIn("no eval file (2)", out)
            self.assertEqual(json.load(open(js))["results"][0]["hits"], 1)
            run_main("--skills-dir", sd, "--evals-dir", ed, "--summary", summ)
            self.assertEqual(open(summ).read().count("## Skill trigger evals"), 2)  # appended, like $GITHUB_STEP_SUMMARY
            code, _, err = run_main("--skills-dir", sd, "--evals-dir", ed, "-k", "1", "--fail-below", "0.9")
            self.assertEqual(code, 1)
            self.assertIn("below --fail-below", err)


class Stream(unittest.TestCase):
    def test_parse_documented_stream(self):
        r = rte.parse_stream(lines(STREAM))
        self.assertEqual(r["invoked"], ["changelog-from-git"])
        self.assertEqual(r["available"], ["commit-craft", "changelog-from-git"])
        self.assertEqual((r["result"], r["cost"], r["error"]), ("error_max_turns", 0.0123, None))

    def test_parse_no_skill_noise_and_variants(self):
        evs = [STREAM[0], STREAM[1],
               {"type": "assistant", "message": {"content": [
                   {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}},
                   {"type": "tool_use", "id": "t3", "name": "Skill", "input": {"command": "/commit-craft"}}]}},
               {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom"}]
        raw = ["not json at all\n", "\n", "{broken json\n"] + lines(evs)
        r = rte.parse_stream(raw)
        self.assertEqual(r["invoked"], ["commit-craft"])  # Bash ignored, legacy `command` key + leading slash handled
        self.assertEqual(r["error"], "boom")
        self.assertEqual(rte.parse_stream(lines([STREAM[0], STREAM[1]]))["invoked"], [])
        self.assertIsNone(rte.parse_stream(lines([STREAM[1]]))["available"])
        self.assertEqual(rte.parse_stream(["Error: unknown option '--tools'\n"])["events"], 0)

    def test_claude_cmd_keeps_prompt_out_of_argv_and_restricts_tools(self):
        cmd = rte.claude_cmd("claude", model="sonnet", max_turns=2)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Skill")
        self.assertIn("Bash", cmd[cmd.index("--disallowed-tools") + 1])
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", cmd)
        self.assertEqual(cmd[-2:], ["--model", "sonnet"])


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
skills = sorted(os.listdir(os.path.join(os.getcwd(), ".claude", "skills")))
print(json.dumps({"type": "system", "subtype": "init", "skills": skills}), flush=True)
pick = "changelog-from-git" if "release" in prompt else ("commit-craft" if "commit" in prompt else None)
if pick:
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "Skill", "input": {"skill": pick}}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01}), flush=True)
'''


class LiveWithFakeClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.sd, self.ed = make_repo(d, evals={
            "commit-craft": ev("commit-craft", ["commit this", "tidy my history"], ["release notes for v2"]),
            "changelog-from-git": ev("changelog-from-git", ["release notes please"], ["commit the release prep"])})
        self.fake = os.path.join(d, "claude")
        with open(self.fake, "w") as f:
            f.write(FAKE_CLAUDE)
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IEXEC)
        self.out = os.path.join(d, "RESULTS.md")

    def tearDown(self):
        self.tmp.cleanup()

    def live(self, *extra):
        return run_main("--live", "--skills-dir", self.sd, "--evals-dir", self.ed, "--claude", self.fake,
                        "--out", self.out, "--jobs", "2", *extra)

    def test_requires_yes_and_prints_estimate(self):
        code, out, _ = self.live()
        self.assertEqual(code, 2)
        self.assertIn("5 `claude -p` sessions", out)
        self.assertFalse(os.path.exists(self.out))

    def test_scores_hits_misses_and_false_triggers(self):
        code, out, _ = self.live("--yes")
        self.assertEqual(code, 0)
        self.assertIn("| changelog-from-git | 1/1 | 1/1 | 0 |", out)  # fake loads it for "commit the release prep"
        self.assertIn("| commit-craft | 1/2 | 0/1 | 0 |", out)
        self.assertIn('"tidy my history" → loaded no skill', out)
        self.assertIn('should_not_trigger: "commit the release prep" → loaded changelog-from-git', out)
        self.assertTrue(out.endswith(open(self.out).read()))

    def test_cli_failure_is_an_error_not_a_miss(self):
        with open(self.fake, "w") as f:
            f.write("#!/bin/sh\necho 'Invalid API key' >&2\nexit 1\n")
        code, out, _ = self.live("--yes", "--skill", "commit-craft")
        self.assertIn("| commit-craft | 0/2 | 0/1 | 3 |", out)
        self.assertIn("error: Invalid API key", out)

    def test_skill_filter(self):
        code, out, _ = self.live("--yes", "--skill", "changelog-from-git")
        self.assertIn("2 `claude -p` sessions", out)
        self.assertNotIn("| commit-craft |", out)


if __name__ == "__main__":
    unittest.main()
