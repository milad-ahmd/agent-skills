#!/usr/bin/env python3
"""Triage a repository's open GitHub issues: duplicate candidates, bugs missing repro details,
stale issues, label suggestions, a priority per issue and a "needs attention first" list.
Stdlib only. Read-only: it never writes to GitHub.

Usage: python3 triage.py [FILE | -] [--repo OWNER/NAME] [--limit N] [--json]
                         [--days N] [--threshold X] [--top N] [--now ISO-DATE]
  FILE / -      issue JSON saved from gh (a list of issues); "-" or a pipe reads stdin
  --repo        fetch the issues itself with
                gh issue list --repo OWNER/NAME --state open --limit N --json <FIELDS>
  --limit       issues to fetch with --repo (default 500)
  --json        machine-readable output on stdout
  --days        no activity for this many days counts as stale (default 30)
  --threshold   TF-IDF cosine score at or above which two issues are duplicate candidates
                (default 0.35)
  --top         length of the "needs attention first" list in text output (default 15)
  --max         lines per section in text output (default 40); --json is never cut
  --now         date to measure staleness from (default: current UTC time); for reproducible runs

Input fields, as gh issue list --json names them: number, title, body, labels, comments,
reactionGroups, createdAt, updatedAt, author, url. A file that uses `reactions` instead of
`reactionGroups` (a list of {content, users: {totalCount}} groups, or a REST-style
{"total_count": N} object), `user` instead of `author`, or a comment count instead of a comment
list also works.

Exit codes: 0 report printed (also when there are no open issues)
            2 bad input: unreadable or invalid JSON, or the gh command failed
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import subprocess
import sys
from collections import Counter

GH_FIELDS = "number,title,body,labels,comments,reactionGroups,createdAt,updatedAt,author,url"
MAINTAINER = {"OWNER", "MEMBER", "COLLABORATOR"}

STOPWORDS = set("""
a an and are as at be been but by can could did do does doing for from had has have having how
i if in into is it its just me my no not of on or our so some than that the their them then
there these they this to too was we were what when where which while who why will with would
you your yes also any get got use using used like want one all only very more most out up via
should still even after before about because same see seems seem other way new now
describe description bug issue problem steps step reproduce reproduction expected behavior
behaviour actual result results environment additional context screenshot screenshots version
versions please thanks thank hi hello work works working happen happens happened tried try
response clear concise information info following
""".split())

BUG_WORDS = re.compile(r"\b(bug|crash\w*|error|exception|traceback|fail\w*|broken|breaks?|"
                       r"regression|panic\w*|segfault|doesn'?t work|does not work|not working|"
                       r"wrong|incorrect|unexpected\w*|hangs?|freez\w*)\b", re.I)
SEVERE_WORDS = re.compile(r"\b(crash(es|ed|ing)?|data loss|lost (my |all )?(data|files|work)|"
                          r"los(es|ing) (data|files|work)|deletes (all|my|local|every)\w*|corrupt\w*|"
                          r"security|vulnerab\w*|leak(s|ed|ing)?|regression|outage|"
                          r"segfault|panic(s|ked)?|(token|password|secret|credential)s? (is |are |gets )?"
                          r"(printed|logged|shown|visible|stored in plain ?text))\b", re.I)
VERSION_RE = re.compile(r"\bv?\d+\.\d+(\.\d+)?\b|\bversion\W{0,3}\d")
STEPS_RE = re.compile(r"\b(steps|reproduc\w*|repro)\b|^\s*1[.)]\s", re.I | re.M)
EXPECTED_RE = re.compile(r"\bexpect\w*", re.I)
ACTUAL_RE = re.compile(r"\b(actual\w*|instead|but (it|got|the|i)|got)\b", re.I)
LOG_RE = re.compile(r"```|^ {4}\S|\btraceback\b|\w(Error|Exception)\b|\bexception\b|\berror:|\bstack ?trace\b|"
                    r"\bpanic:|\blogs?\b|\boutput\b", re.I | re.M)
EMPTY_SECTION = re.compile(r"^#{1,6}[^\n]*\n\s*_No response_\s*$", re.M)  # unanswered issue-form fields
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
LOG_BLOCK = re.compile(r"```.*?(```|$)|<details>.*?(</details>|$)", re.S | re.I)
ERROR_LINE = re.compile(r"error|exception|panic|fail|fatal", re.I)
URL_RE = re.compile(r"https?://\S+")

LABEL_RULES = [  # (suggested label, names that already cover it, pattern)
    ("feature", {"feature", "enhancement", "feature request", "type: feature", "kind/feature"},
     re.compile(r"\b(feature request|add support|support for|please add|would be (nice|great)|"
                r"option to|ability to|allow (users? )?to|proposal|it would help)\b", re.I)),
    ("question", {"question", "support", "help wanted", "type: question"},
     re.compile(r"\?\s*$|^(how|is it possible|can i|what is|why does)\b|\bhow (do|can|to)\b", re.I)),
    ("docs", {"docs", "documentation", "type: docs", "kind/documentation"},
     re.compile(r"\b(docs?|documentation|readme|typo|guide|example in the|tutorial)\b", re.I)),
]
GOOD_FIRST = re.compile(r"\b(typo|spelling|broken link|dead link|wording|rename|small fix)\b", re.I)


def fail(msg: str) -> None:
    print("triage.py: " + msg, file=sys.stderr)
    sys.exit(2)


def fetch(repo: str, limit: int) -> list:
    cmd = ["gh", "issue", "list", "--repo", repo, "--state", "open", "--limit", str(limit),
           "--json", GH_FIELDS]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        fail("cannot run gh (%s); install GitHub CLI or pass a saved JSON file" % e)
    if r.returncode:
        fail("gh exited %d: %s" % (r.returncode, r.stderr.strip()))
    return parse(r.stdout, "gh output")


def parse(text: str, where: str) -> list:
    try:
        data = json.loads(text)
    except ValueError as e:
        fail("%s is not valid JSON: %s" % (where, e))
    if not isinstance(data, list):
        fail("%s must be a JSON list of issues (the output of gh issue list --json ...)" % where)
    return data


def login(person) -> str:
    if isinstance(person, dict):
        return person.get("login") or ""
    return person or ""


def reaction_count(issue: dict) -> int:
    groups = issue.get("reactionGroups", issue.get("reactions"))
    if isinstance(groups, dict):  # REST: {"total_count": 3, "+1": 2, ...}
        return int(groups.get("total_count", 0))
    total = 0
    for g in groups or []:
        users = g.get("users") or {}
        total += int(users.get("totalCount", g.get("totalCount", 0)) if isinstance(users, dict) else 0)
    return total


def parse_time(s):
    if not s:
        return None
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def clean(text: str) -> str:
    text = HTML_COMMENT.sub("", text or "")
    return EMPTY_SECTION.sub("", text)


def without_logs(text: str) -> str:
    """Pasted logs and debug dumps share rare words that make unrelated issues look alike;
    keep only their error lines, which are the part worth matching."""
    def keep(m):
        return "\n".join([l for l in m.group(0).splitlines() if ERROR_LINE.search(l)][:5])
    return LOG_BLOCK.sub(keep, text)


def tokens(text: str) -> list:
    out = []
    for w in re.findall(r"[a-z][a-z0-9_]+", URL_RE.sub(" ", text.lower())):
        # shortcut: crude plural stemming only; enough for short issue titles
        if w.endswith(("shes", "ches", "xes", "sses")):
            w = w[:-2]
        elif len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "us")):
            w = w[:-1]
        if len(w) > 2 and w not in STOPWORDS:
            out.append(w)
    return out


def tfidf(docs: list) -> list:
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    vecs = []
    for d in docs:
        tf = Counter(d)
        v = {t: (1 + math.log(c)) * math.log((1 + n) / (1 + df[t])) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({t: x / norm for t, x in v.items() if x > 0})
    return vecs


def duplicates(issues: list, threshold: float) -> list:
    # title counted twice: titles are short and carry most of the signal
    docs = [tokens(i["title"]) * 2 + tokens(without_logs(i["text"])) for i in issues]
    vecs = tfidf(docs)
    pairs = []
    for a in range(len(issues)):
        for b in range(a + 1, len(issues)):
            va, vb = vecs[a], vecs[b]
            if len(va) > len(vb):
                va, vb = vb, va
            shared = {t: va[t] * vb[t] for t in va if t in vb}
            score = sum(shared.values())
            if score >= threshold:
                older, newer = sorted((issues[a], issues[b]), key=lambda i: i["number"])
                top = sorted(shared, key=lambda t: -shared[t])[:6]
                pairs.append({"original": older["number"], "duplicate": newer["number"],
                              "score": round(score, 2), "shared_terms": top})
    return sorted(pairs, key=lambda p: -p["score"])


def analyse(raw: dict, now: dt.datetime, days: int) -> dict:
    author = login(raw.get("author") or raw.get("user"))
    labels = [l.get("name", "") if isinstance(l, dict) else str(l) for l in raw.get("labels") or []]
    label_set = {l.lower() for l in labels}
    comments = raw.get("comments")
    comment_list = comments if isinstance(comments, list) else []
    n_comments = len(comment_list) if isinstance(comments, list) else int(comments or 0)
    title = raw.get("title") or ""
    body = clean(raw.get("body") or "")
    prose = title + "\n" + without_logs(body)  # words inside pasted logs say nothing about the issue type
    # the reporter often answers "which version?" in a comment, so their comments count as info
    reporter_text = "\n".join(clean(c.get("body", "")) for c in comment_list
                              if login(c.get("author")) == author)
    text = body + "\n" + reporter_text

    # feature requests and docs reports often mention an error or "wrong"; their title decides
    # a type label (enhancement, question, docs) or a feature/docs title beats bug words in the body
    not_a_bug = (any(label_set & covers for _, covers, _ in LABEL_RULES)
                 or LABEL_RULES[0][2].search(title) or LABEL_RULES[2][2].search(title))
    bug_like = "bug" in label_set or (bool(BUG_WORDS.search(prose) or SEVERE_WORDS.search(prose))
                                      and not not_a_bug)
    missing = []
    if bug_like:
        if not VERSION_RE.search(text):
            missing.append("version")
        if not STEPS_RE.search(text):
            missing.append("steps")
        if not (EXPECTED_RE.search(text) and ACTUAL_RE.search(text)):
            missing.append("expected/actual")
        if not LOG_RE.search(text):
            missing.append("error/logs")

    last_word = "none"
    last_by = None
    for c in reversed(comment_list):
        who = login(c.get("author"))
        assoc = (c.get("authorAssociation") or "").upper()
        if who == author:
            last_word, last_by = "reporter", who
        elif assoc in MAINTAINER or not assoc:  # no association in the data: any other person counts
            last_word, last_by = "maintainer", who
        else:
            continue  # bots and drive-by "+1" comments don't change who the issue is waiting on
        break
    maintainer_replied = any(login(c.get("author")) != author and
                             ((c.get("authorAssociation") or "").upper() in MAINTAINER
                              or not c.get("authorAssociation")) for c in comment_list)

    updated = parse_time(raw.get("updatedAt")) or parse_time(raw.get("createdAt"))
    idle = (now - updated).days if updated else 0

    suggested = []
    if bug_like and not label_set & {"bug", "type: bug", "kind/bug"}:
        suggested.append(("bug", "bug words in title/body"))
    for name, covers, pat in LABEL_RULES:
        hay = prose if name == "feature" else title  # body links to docs are common
        m = pat.search(hay)
        if m and not label_set & covers and not (name == "question" and bug_like):
            suggested.append((name, 'matched "%s"' % m.group(0).strip()))
    m = GOOD_FIRST.search(prose)
    if m and len(body) < 800 and "good first issue" not in label_set:
        suggested.append(("good first issue", 'small change: "%s"' % m.group(0)))

    commenters = {login(c.get("author")) for c in comment_list} - {author}
    return {
        "number": raw.get("number"), "title": title, "url": raw.get("url", ""), "author": author,
        "labels": labels, "reactions": reaction_count(raw), "comments": n_comments,
        "commenters": len(commenters), "bug_like": bug_like, "missing": missing,
        "idle_days": idle, "stale": idle >= days, "last_word": last_word, "last_by": last_by,
        "maintainer_replied": maintainer_replied,
        # severity only raises bugs: a feature request that mentions "security" is not urgent
        "severe": sorted({m.group(0).lower() for m in SEVERE_WORDS.finditer(prose)})
        if bug_like else [],
        "suggested_labels": [{"label": n, "why": w} for n, w in suggested],
        "text": body,
    }


def prioritise(issue: dict, dup_count: int) -> None:
    impact = (3 if issue["severe"] else 0) + (1 if issue["bug_like"] else 0)
    reach = issue["reactions"] + issue["commenters"] + 2 * dup_count
    score = impact * 2 + min(reach, 30) / 3.0
    issue["duplicates"] = dup_count
    issue["score"] = round(score, 1)
    issue["priority"] = "P1" if score >= 8 else "P2" if score >= 4 else "P3"
    if issue["priority"] == "P1" and not issue["bug_like"]:
        issue["priority"] = "P2"  # demand alone makes a feature important, not urgent
    reasons = []
    if issue["severe"]:
        reasons.append("severe: " + ", ".join(issue["severe"][:3]))
    if issue["reactions"]:
        reasons.append("%d reaction%s" % (issue["reactions"], "s" if issue["reactions"] > 1 else ""))
    if issue["commenters"]:
        reasons.append("%d other commenter%s" % (issue["commenters"], "s" if issue["commenters"] > 1 else ""))
    if dup_count:
        reasons.append("%d duplicate candidate%s" % (dup_count, "s" if dup_count > 1 else ""))
    attention = score
    if not issue["maintainer_replied"]:
        attention += 2
        reasons.append("no maintainer reply yet")
    if issue["last_word"] == "maintainer":
        attention -= 3  # the ball is in the reporter's court
        reasons.append("waiting on reporter")
    elif issue["last_word"] == "reporter" and issue["maintainer_replied"]:
        attention += 1
        reasons.append("reporter replied last")
    issue["attention"] = round(attention, 1)
    issue["reasons"] = reasons


def triage(raw: list, now: dt.datetime, days: int, threshold: float) -> dict:
    issues = [analyse(r, now, days) for r in raw]
    dups = duplicates(issues, threshold)
    count = Counter()
    for p in dups:
        count[p["original"]] += 1
        count[p["duplicate"]] += 1
    for i in issues:
        prioritise(i, count[i["number"]])
        del i["text"]
    order = sorted(issues, key=lambda i: (-i["attention"], i["number"]))
    return {"now": now.strftime("%Y-%m-%d"), "days": days, "threshold": threshold,
            "count": len(issues), "attention": [i["number"] for i in order],
            "duplicates": dups, "issues": order}


def short(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def capped(lines: list, limit: int) -> list:
    if not lines:
        return ["  none"]
    if len(lines) > limit:
        return lines[:limit] + ["  ... %d more (see --json)" % (len(lines) - limit)]
    return lines


def render(rep: dict, top: int, limit: int, source: str) -> str:
    by = {i["number"]: i for i in rep["issues"]}
    out = ["Triage of %d open issues from %s, as of %s (stale after %d days, duplicate threshold %.2f)"
           % (rep["count"], source, rep["now"], rep["days"], rep["threshold"]),
           "Read-only report: nothing was changed on GitHub. Labels and duplicates are suggestions.", ""]
    out.append("Needs attention first")
    for n, num in enumerate(rep["attention"][:top], 1):
        i = by[num]
        out.append("  %2d. #%-5s %s  %-60s  %s" % (n, num, i["priority"], short(i["title"]),
                                                  "; ".join(i["reasons"]) or "-"))
    out += ["", "Duplicate candidates (TF-IDF cosine >= %.2f; confirm by reading both)" % rep["threshold"]]
    out += capped(["  #%s may duplicate #%s  score %.2f  shared: %s" % (
        p["duplicate"], p["original"], p["score"], ", ".join(p["shared_terms"])) for p in rep["duplicates"]], limit)
    out += ["", "Bug reports missing repro details"]
    gaps = sorted((i for i in rep["issues"] if i["missing"]), key=lambda i: i["number"])
    out += capped(["  #%-5s missing: %-46s %s" % (i["number"], ", ".join(i["missing"]), short(i["title"], 50))
                   for i in gaps], limit)
    out += ["", "Stale (no activity for %d+ days)" % rep["days"]]
    stale = sorted((i for i in rep["issues"] if i["stale"]), key=lambda i: -i["idle_days"])
    waiting = {"maintainer": "waiting on reporter (last reply @%s)", "none": "never answered",
               "reporter": "waiting on maintainers (reporter replied last)"}
    out += capped(["  #%-5s %4d days  %s" % (i["number"], i["idle_days"], waiting[i["last_word"]].replace(
        "%s", i["last_by"] or "")) for i in stale], limit)
    out += ["", "Label suggestions (map to the repo's own label names: gh label list)"]
    sugg = sorted((i for i in rep["issues"] if i["suggested_labels"]), key=lambda i: i["number"])
    out += capped(["  #%-5s %s" % (i["number"], "; ".join("%s (%s)" % (x["label"], x["why"])
                                                          for x in i["suggested_labels"])) for i in sugg], limit)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Exit codes: 0 report printed, 2 bad input or gh failure. "
                                        "Never writes to GitHub.")
    ap.add_argument("file", nargs="?", help="issue JSON from gh issue list --json ...; '-' for stdin")
    ap.add_argument("--repo", help="OWNER/NAME: fetch open issues with gh (read-only)")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--now", help="ISO date or datetime to measure staleness from")
    a = ap.parse_args(argv)

    if a.repo and a.file:
        fail("give either --repo or a file, not both")
    if a.repo:
        raw, source = fetch(a.repo, a.limit), a.repo
        if len(raw) >= a.limit:
            print("triage.py: got %d issues, the --limit; older open issues were not fetched" % len(raw),
                  file=sys.stderr)
    elif a.file and a.file != "-":
        try:
            with open(a.file, encoding="utf-8") as fh:
                raw = parse(fh.read(), a.file)
        except OSError as e:
            fail("cannot read %s: %s" % (a.file, e))
        source = a.file
    elif a.file == "-" or not sys.stdin.isatty():
        raw, source = parse(sys.stdin.read(), "stdin"), "stdin"
    else:
        ap.print_usage(sys.stderr)
        fail("give --repo OWNER/NAME, a JSON file, or pipe gh issue list --json output in")

    try:
        now = parse_time(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    except ValueError:
        fail("--now must be an ISO date like 2026-11-25")
    rep = triage(raw, now, a.days, a.threshold)
    rep["source"] = source
    print(json.dumps(rep, indent=2) if a.json else render(rep, a.top, a.max, source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
