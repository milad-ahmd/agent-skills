#!/usr/bin/env python3
"""Trigger evals: does each skill load for the prompts it should, and stay quiet for a neighbour's? Stdlib only.

Usage:
  python3 scripts/run_trigger_evals.py [--offline] [-k 3] [--threshold 0.6] [--skill NAME ...]
                                       [--json FILE|-] [--summary FILE] [--fail-below 0.7]
  python3 scripts/run_trigger_evals.py --live [--skill NAME ...] [--model sonnet] [--jobs 4]
                                       [--max-turns 2] [--out evals/RESULTS.md] --yes

Eval files: evals/<skill>.json = {"skill": "<name>", "should_trigger": [...], "should_not_trigger": [...]}

--offline (default, free, CI-safe) is a LEXICAL PROXY, not the model. Every prompt is ranked against every skill
  present in skills/ by TF-IDF cosine over name ×2 + description + its "Use when…" clause (skill_checks stopwords,
  2+ char tokens, crude suffix stemming). Reports hit@k for should_trigger prompts, and confusion = should_not_trigger
  prompts where the skill still ranks #1. It measures how distinctive the descriptions are, nothing more.
--live (spends model usage, never run it in CI) starts one `claude -p` session per prompt in a temp project that
  holds every skill under .claude/skills/, with only the Skill tool enabled, and reads the stream-json output for
  the Skill tool_use. Without --yes it only prints the number of sessions it would start.

Exit codes: 0 · 1 overall hit rate below --fail-below · 2 --live without --yes, or `claude` not found
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "skills-doctor", "scripts"))
try:
    from skill_checks import STOP, parse_frontmatter, trigger_clause  # noqa: E402
except Exception:  # skills-doctor missing or mid-edit: fall back to a minimal parser
    STOP = set("a an the and or to of for in on with when user users asks ask use used is are it this that by from "
               "as at be your you their into its can any all via also not no do does".split())

    def parse_frontmatter(text):
        m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
        if not m:
            return None, text
        return dict(re.findall(r"(?m)^([A-Za-z0-9_-]+):\s*['\"]?(.*?)['\"]?\s*$", m.group(1))), text[m.end():]

    def trigger_clause(desc):
        m = re.search(r"(?is)\b(use (?:it |this )?(?:when|for|if|before)|when the user)\b(.*)$", desc or "")
        return m.group(2) if m else ""

PROXY_NOTE = ("Offline mode is a lexical proxy: it ranks prompts by TF-IDF similarity to each skill's description. "
              "It shows how distinctive the descriptions are, not what the model actually loads. Use --live for that.")
# Chat filler that skill_checks.STOP doesn't need (it ignores tokens under 3 chars) but user prompts are full of.
FILLER = set("my me we us our ours im ive he she they them what how why who which where did so if than then there here "
             "just get got make made want need please help could would should will up out about more most some like".split())
DENY = "Bash,Edit,Write,MultiEdit,NotebookEdit,Read,Glob,Grep,WebFetch,WebSearch,Task,Agent,TodoWrite"


# --- loading --------------------------------------------------------------------------
def load_skills(skills_dir):
    """{folder: {"name", "description"}} for every skills/<folder>/SKILL.md that exists right now."""
    out = {}
    for d in sorted(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else []:
        p = os.path.join(skills_dir, d, "SKILL.md")
        try:
            fm, _ = parse_frontmatter(open(p, encoding="utf-8", errors="replace").read())
        except OSError:
            continue
        fm = fm or {}
        out[d] = {"name": fm.get("name") or d, "description": fm.get("description", "")}
    return out


def load_evals(evals_dir):
    """Returns (evals {skill: {...}}, problems [str]). Malformed files are reported, never fatal."""
    evals, problems = {}, []
    for f in sorted(os.listdir(evals_dir)) if os.path.isdir(evals_dir) else []:
        if not f.endswith(".json"):
            continue
        try:
            e = json.load(open(os.path.join(evals_dir, f), encoding="utf-8"))
            ok = (isinstance(e, dict) and isinstance(e.get("skill"), str)
                  and all(isinstance(e.get(k), list) and all(isinstance(p, str) and p.strip() for p in e[k])
                          for k in ("should_trigger", "should_not_trigger")))
        except (OSError, ValueError) as x:
            problems.append(f"{f}: unreadable ({x})")
            continue
        if not ok:
            problems.append(f"{f}: needs \"skill\" plus non-empty string lists \"should_trigger\" and \"should_not_trigger\"")
            continue
        if f[:-5] != e["skill"]:
            problems.append(f"{f}: file name doesn't match skill `{e['skill']}`")
        evals[e["skill"]] = e
    return evals, problems


def coverage(skills, evals, only=None):
    """(evals to run, eval files whose skill is gone, skills without an eval file)."""
    run = {s: e for s, e in evals.items() if s in skills and (not only or s in only)}
    return run, sorted(s for s in evals if s not in skills), sorted(s for s in skills if s not in evals)


# --- offline: TF-IDF router -----------------------------------------------------------
def stem(w):
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            if len(w) >= 5 and w[-1] == w[-2] and w[-1] not in "aeiouls":
                w = w[:-1]  # committ -> commit, shipp -> ship
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def tokens(s):
    return [stem(w) for w in re.findall(r"[a-z][a-z0-9]+", (s or "").lower()) if w not in STOP and w not in FILLER]


class Router:
    """Ranks skills for a prompt by cosine similarity of sublinear-TF × IDF vectors."""

    def __init__(self, skills):
        docs = {k: tokens(k.replace("-", " ")) * 2 + tokens(v["description"]) + tokens(trigger_clause(v["description"]))
                for k, v in skills.items()}
        df = Counter(t for d in docs.values() for t in set(d))
        n = len(docs)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self.vecs = {k: self.vec(d) for k, d in docs.items()}

    def vec(self, toks):
        v = {t: (1 + math.log(c)) * self.idf[t] for t, c in Counter(toks).items() if t in self.idf}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def rank(self, prompt):
        q = self.vec(tokens(prompt))
        scores = {k: sum(q[t] * d.get(t, 0.0) for t in q) for k, d in self.vecs.items()}
        return sorted(scores, key=lambda k: (-scores[k], k)), scores


def run_offline(skills, evals, k=3):
    router, rows = Router(skills), []
    for s, e in sorted(evals.items()):
        miss, conf, hits, top1 = [], [], 0, 0
        for p in e["should_trigger"]:
            order, _ = router.rank(p)
            r = order.index(s) + 1
            hits, top1 = hits + (r <= k), top1 + (r == 1)
            if r > k:
                miss.append({"prompt": p, "rank": r, "top": order[:k]})
        for p in e["should_not_trigger"]:
            order, _ = router.rank(p)
            if order[0] == s:
                conf.append({"prompt": p, "runner_up": order[1] if len(order) > 1 else None})
        rows.append({"skill": s, "n": len(e["should_trigger"]), "hits": hits, "top1": top1,
                     "n_not": len(e["should_not_trigger"]), "confused": len(conf), "misses": miss, "confusions": conf})
    return rows


# --- live: claude -p ------------------------------------------------------------------
def parse_stream(lines):
    """Parse `claude -p --output-format stream-json --verbose` lines.

    Returns {"invoked": [skill names in call order], "available": [skills from the init event] or None,
             "result": result subtype or None, "cost": total_cost_usd or None, "error": str or None, "events": int}.
    """
    out = {"invoked": [], "available": None, "result": None, "cost": None, "error": None, "events": 0}
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        out["events"] += 1
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            if isinstance(ev.get("skills"), list):
                out["available"] = [s if isinstance(s, str) else s.get("name", "") for s in ev["skills"]]
        elif t == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                    inp = b.get("input") or {}
                    name = inp.get("skill") or inp.get("command") or inp.get("name") or ""
                    if name:
                        out["invoked"].append(name.strip().lstrip("/"))
        elif t == "result":
            out["result"], out["cost"] = ev.get("subtype"), ev.get("total_cost_usd")
            if ev.get("is_error") and ev.get("subtype") not in ("error_max_turns",):
                out["error"] = str(ev.get("result") or ev.get("subtype"))
    return out


def claude_cmd(claude, model=None, max_turns=2, budget=None):
    """argv for one headless session. The prompt goes in on stdin, so the variadic tool flags can't swallow it."""
    cmd = [claude, "-p", "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns),
           "--tools", "Skill", "--allowed-tools", "Skill", "--disallowed-tools", DENY,
           "--permission-mode", "dontAsk", "--setting-sources", "project", "--strict-mcp-config",
           "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    if budget:
        cmd += ["--max-budget-usd", str(budget)]
    return cmd


def make_project(skills_dir, skills):
    d = tempfile.mkdtemp(prefix="trigger-eval-")
    for s in skills:
        shutil.copytree(os.path.join(skills_dir, s), os.path.join(d, ".claude", "skills", s),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return d


def run_one(cmd, prompt, skills_dir, skills, timeout=180):
    """One prompt in a fresh project. Stops the session as soon as a Skill tool_use shows up."""
    proj, lines, errf = make_project(skills_dir, skills), [], tempfile.TemporaryFile("w+")
    try:  # stderr goes to a file so a chatty CLI can't fill the pipe and deadlock the stdout loop
        p = subprocess.Popen(cmd, cwd=proj, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf,
                             text=True, env={**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        timer = threading.Timer(timeout, p.kill)
        timer.start()
        try:
            try:
                p.stdin.write(prompt)
                p.stdin.close()
            except BrokenPipeError:
                pass  # the CLI exited before reading the prompt; its stderr says why
            for line in p.stdout:
                lines.append(line)
                if '"Skill"' in line and parse_stream([line])["invoked"]:
                    p.terminate()
                    break
            p.wait()
            errf.seek(0)
            err = errf.read().strip()
        finally:
            timer.cancel()
        res = parse_stream(lines)
        if not res["events"]:  # no stream at all: bad flag, not logged in, crash...
            res["error"] = (err or "".join(lines).strip() or f"exit {p.returncode}")[:300]
        return res
    finally:
        errf.close()
        shutil.rmtree(proj, ignore_errors=True)


def run_live(a, skills, evals):
    claude = a.claude or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    jobs = [(s, kind, p) for s, e in sorted(evals.items()) for kind in ("should_trigger", "should_not_trigger") for p in e[kind]]
    print(f"Live mode: {len(jobs)} `claude -p` sessions ({len(evals)} skills × their prompts), each at least one model "
          f"request and at most --max-turns {a.max_turns}. {len(skills)} skills installed per session.")
    if not a.yes:
        print("Nothing run. Re-run with --yes to spend that usage.")
        sys.exit(2)
    if not os.path.isfile(claude):
        sys.exit(f"claude CLI not found ({claude}); pass --claude PATH")
    cmd, names = claude_cmd(claude, a.model, a.max_turns, a.max_budget_usd), sorted(skills)
    done = []

    def go(job):
        r = run_one(cmd, job[2], a.skills_dir, names, a.timeout)
        done.append(job)
        mark = "!" if r["error"] else ("+" if (job[0] in r["invoked"]) == (job[1] == "should_trigger") else "-")
        print(f"  [{len(done)}/{len(jobs)}] {mark} {job[0]} {job[1]}: {', '.join(r['invoked']) or 'no skill'}", file=sys.stderr)
        return job, r

    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        results = list(ex.map(go, jobs))
    rows = {s: {"skill": s, "n": 0, "hits": 0, "n_not": 0, "false": 0, "errors": 0, "cost": 0.0, "details": []} for s in evals}
    for (s, kind, p), r in results:
        row, hit = rows[s], s in r["invoked"]
        row["errors"] += bool(r["error"])
        row["cost"] += r["cost"] or 0.0
        if kind == "should_trigger":
            row["n"], row["hits"] = row["n"] + 1, row["hits"] + hit
        else:
            row["n_not"], row["false"] = row["n_not"] + 1, row["false"] + hit
        if r["error"] or hit != (kind == "should_trigger"):
            row["details"].append({"kind": kind, "prompt": p, "invoked": r["invoked"], "error": r["error"],
                                   "loaded": None if r["available"] is None else s in r["available"]})
    return [rows[s] for s in sorted(rows)]


# --- reporting ------------------------------------------------------------------------
def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "n/a"


def coverage_md(gone, missing, problems):
    out = []
    if gone:
        out.append(f"- Eval files for skills that no longer exist: {', '.join(gone)}")
    if missing:
        out.append(f"- Skills with no eval file ({len(missing)}): {', '.join(missing)}")
    out += [f"- Malformed: {p}" for p in problems]
    return ["", "### Coverage", *out] if out else []


def offline_md(rows, k, threshold, ctx):
    n, h = sum(r["n"] for r in rows), sum(r["hits"] for r in rows)
    t1, nn, c = sum(r["top1"] for r in rows), sum(r["n_not"] for r in rows), sum(r["confused"] for r in rows)
    out = ["## Skill trigger evals (offline lexical proxy)", "", f"> {PROXY_NOTE}", "",
           f"{ctx['skills']} skills installed · {len(rows)} evaluated · hit@{k} {pct(h, n)} · top-1 {pct(t1, n)} · "
           f"confusion {pct(c, nn)} of should_not prompts ranked #1", "",
           f"| Skill | hit@{k} | top-1 | confused | flag |", "|---|---|---|---|---|"]
    for r in rows:
        flag = " ".join(x for x, on in (("LOW", r["n"] and r["hits"] / r["n"] < threshold), ("CONFUSED", r["confused"])) if on)
        out.append(f"| {r['skill']} | {r['hits']}/{r['n']} | {r['top1']}/{r['n']} | {r['confused']}/{r['n_not']} | {flag} |")
    miss = [(r["skill"], m) for r in rows for m in r["misses"]]
    if miss:
        out += ["", f"### Misses (should_trigger prompt ranked below #{k})"]
        out += [f"- **{s}** #{m['rank']}: \"{m['prompt']}\" (top: {', '.join(m['top'])})" for s, m in miss]
    conf = [(r["skill"], x) for r in rows for x in r["confusions"]]
    if conf:
        out += ["", "### Confusions (should_not prompt where the skill ranked #1)"]
        out += [f"- **{s}**: \"{x['prompt']}\" (runner-up: {x['runner_up']})" for s, x in conf]
    return "\n".join(out + coverage_md(ctx["gone"], ctx["missing"], ctx["problems"])) + "\n"


def live_md(rows, ctx):
    n, h = sum(r["n"] for r in rows), sum(r["hits"] for r in rows)
    nn, f = sum(r["n_not"] for r in rows), sum(r["false"] for r in rows)
    cost = sum(r["cost"] for r in rows)
    out = ["## Skill trigger evals (live: `claude -p`)", "",
           f"Model: {ctx['model'] or 'CLI default'} · {ctx['skills']} skills installed · {len(rows)} evaluated · "
           f"hit rate {pct(h, n)} · false triggers {pct(f, nn)} · errors {sum(r['errors'] for r in rows)} · "
           f"reported cost ${cost:.2f}", "",
           "| Skill | hit | false trigger | errors |", "|---|---|---|---|"]
    out += [f"| {r['skill']} | {r['hits']}/{r['n']} | {r['false']}/{r['n_not']} | {r['errors']} |" for r in rows]
    det = [(r["skill"], d) for r in rows for d in r["details"]]
    if det:
        out += ["", "### Misses, false triggers and errors"]
        for s, d in det:
            what = d["error"] and f"error: {d['error']}" or f"loaded {', '.join(d['invoked']) or 'no skill'}"
            warn = " (skill missing from the session's init list)" if d["loaded"] is False else ""
            out.append(f"- **{s}** {d['kind']}: \"{d['prompt']}\" → {what}{warn}")
    return "\n".join(out + coverage_md(ctx["gone"], ctx["missing"], ctx["problems"])) + "\n"


def write(path, text, append=False):
    if path == "-":
        sys.stdout.write(text)
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a" if append else "w", encoding="utf-8") as f:
        f.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Skill trigger evals (offline lexical proxy, or live `claude -p`).")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="lexical router baseline (default; free, CI-safe)")
    mode.add_argument("--live", action="store_true", help="real `claude -p` sessions (costs usage; needs --yes)")
    ap.add_argument("--skills-dir", default=os.path.join(ROOT, "skills"))
    ap.add_argument("--evals-dir", default=os.path.join(ROOT, "evals"))
    ap.add_argument("--skill", action="append", help="only evaluate this skill (repeatable)")
    ap.add_argument("-k", type=int, default=3, help="offline: a should_trigger prompt hits if the skill ranks in the top k")
    ap.add_argument("--threshold", type=float, default=0.6, help="offline: flag skills with hit@k below this (default 0.6)")
    ap.add_argument("--fail-below", type=float, help="exit 1 if the overall hit rate is below this (0-1)")
    ap.add_argument("--json", help="also write raw results as JSON (- for stdout)")
    ap.add_argument("--summary", help="also write the markdown here, e.g. $GITHUB_STEP_SUMMARY (appended to)")
    ap.add_argument("--model", help="live: model for claude -p")
    ap.add_argument("--jobs", type=int, default=4, help="live: parallel sessions (default 4)")
    ap.add_argument("--max-turns", type=int, default=2, help="live: --max-turns per session (default 2)")
    ap.add_argument("--max-budget-usd", type=float, help="live: per-session spend cap passed to claude")
    ap.add_argument("--timeout", type=int, default=180, help="live: seconds per session (default 180)")
    ap.add_argument("--claude", help="live: path to the claude CLI")
    ap.add_argument("--out", default=os.path.join(ROOT, "evals", "RESULTS.md"), help="live: markdown report path")
    ap.add_argument("--yes", action="store_true", help="live: actually start the sessions")
    a = ap.parse_args(argv)

    skills = load_skills(a.skills_dir)
    evals, problems = load_evals(a.evals_dir)
    run, gone, missing = coverage(skills, evals, set(a.skill or []))
    for s in sorted(set(a.skill or []) - set(run)):
        problems.append(f"--skill {s}: no eval file or no such skill")
    ctx = {"skills": len(skills), "gone": gone, "missing": missing, "problems": problems, "model": a.model}
    if a.live:
        rows = run_live(a, skills, run)
        md, rate = live_md(rows, ctx), (sum(r["hits"] for r in rows), sum(r["n"] for r in rows))
        write(a.out, md)
    else:
        rows = run_offline(skills, run, a.k)
        md, rate = offline_md(rows, a.k, a.threshold, ctx), (sum(r["hits"] for r in rows), sum(r["n"] for r in rows))
    sys.stdout.write(md)
    if a.summary:
        write(a.summary, md, append=True)
    if a.json:
        write(a.json, json.dumps({"mode": "live" if a.live else "offline", "k": a.k, **ctx, "results": rows}, indent=2) + "\n")
    if a.fail_below is not None and rate[1] and rate[0] / rate[1] < a.fail_below:
        print(f"\nOverall hit rate {rate[0] / rate[1]:.2f} is below --fail-below {a.fail_below}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
