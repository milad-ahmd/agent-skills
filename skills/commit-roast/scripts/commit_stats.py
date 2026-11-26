#!/usr/bin/env python3
"""Measure a repository's commit-message habits so they can be roasted with real numbers.

Read-only: runs `git log` and nothing else. Merge commits are counted separately and left out of
every message statistic. Times use each commit's author date in its own timezone.

Examples:
  python3 commit_stats.py                         # last 1000 commits of HEAD
  python3 commit_stats.py -C path/to/repo --since "6 months ago"
  python3 commit_stats.py main~500..main --author alice --json
  python3 commit_stats.py --show-authors          # name authors in evidence (anonymous by default)

Exit codes: 0 report printed; 2 not a git repository, git error, or no commits in the range.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
MSG_WIDTH = 60
BIG_COMMIT_LINES = 200
FIX_CHAIN_SECONDS = 3600

LAZY = {"wip", "fix", "fixes", "fixed", "update", "updates", "updated", "asdf", "asd", ".", "..", "...",
        "changes", "change", "stuff", "misc", "test", "tests", "testing", "tmp", "temp", "minor", "commit",
        "save", "work", "done", "oops", "typo", "cleanup", "refactor", "more", "again", "x", "a", "-",
        "small fix", "minor fix", "minor changes", "some changes", "fix bug", "bug fix", "bugfix",
        "fix stuff", "update code", "changes made", "initial", "final", "final fix", "it works", "lol"}

SWEARS = re.compile(r"\b(fuck\w*|shit\w*|bullshit|damn\w*|crap\w*|wtf|ffs|goddamn\w*|bloody|piss\w*)\b", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⭕]|:[a-z0-9_+-]+:")
CONVENTIONAL = re.compile(r"^([A-Za-z]+)(\([^()]*\))?(!)?: \S")
ISSUE_REF = re.compile(r"(?<![\w&])#\d+\b|\b[A-Z][A-Z0-9]+-\d+\b|\bgh-\d+\b")
TRAILER = re.compile(r"^(signed-off-by|co-authored-by|change-id|reviewed-by|acked-by|tested-by|cc):", re.I)
TYPO = re.compile(r"\btypos?\b", re.I)
FIXWORD = re.compile(r"\b(fix|fixe[sd]|fixing|hotfix|bugfix|oops)\b", re.I)

# Base verbs counted as imperative. Their -s/-ed/-ing forms count as not imperative.
VERBS = """add adjust allow apply avoid bind bump build cache call catch change check clarify clean
cleanup configure convert correct create deprecate define delete disable document downgrade drop enable
ensure exclude expose export extract fetch filter fix format generate guard handle hide implement import
improve include increase init initialize introduce keep limit lint load log make mark merge migrate move
optimize parse pass patch polish port prevent raise read reduce refactor refine register release remove
rename render replace require reset restore return revert revise rework run save send set show simplify
skip sort split start stop store support switch sync tidy track trim tweak unify update upgrade use
validate wire wrap write""".split()
IRREGULAR = {"made": "make", "wrote": "write", "written": "write", "built": "build", "ran": "run",
             "kept": "keep", "sent": "send", "began": "begin", "brought": "bring", "found": "find"}


def _verb_forms():
    forms = {}
    for v in VERBS:
        cands = {v + "s", v + "es", v + "ed", v + "d", v + "ing", v + v[-1] + "ed", v + v[-1] + "ing"}
        if v.endswith("e"):
            cands.add(v[:-1] + "ing")
        if v.endswith("y"):
            cands |= {v[:-1] + "ies", v[:-1] + "ied"}
        for c in cands:
            if c not in VERBS:
                forms[c] = v
    forms.update(IRREGULAR)
    return forms


VERB_FORMS = _verb_forms()


def git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError as e:
        return 127, "", str(e)
    return r.returncode, r.stdout, r.stderr.strip()


def censor(text):
    return SWEARS.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def short(text, width=MSG_WIDTH):
    text = censor(" ".join(text.split()))
    return text if len(text) <= width else text[:width - 3] + "..."


def strip_type(subject):
    m = CONVENTIONAL.match(subject)
    return subject[m.end() - 1:].strip() if m else subject.strip()


def mood(subject):
    """'imperative', 'not' (added/fixes/fixing), or None when the first word isn't a known verb."""
    words = re.findall(r"[A-Za-z']+", strip_type(subject))
    if not words:
        return None, ""
    w = words[0].lower()
    if w in VERBS:
        return "imperative", w
    if w in VERB_FORMS:
        return "not", w
    return None, w


def is_fix(c):
    return c["type"] == "fix" or bool(FIXWORD.search(c["subject"]))


def norm(subject):
    return re.sub(r"[\s!.?]+$", "", subject.strip().lower()) or subject.strip()


def read_log(repo, rev, since, max_count, author):
    args = ["log", "--no-color", "--no-renames", "--numstat", "--format=%x1e%H%x1f%an%x1f%aI%x1f%P%x1f%B%x1f"]
    if max_count:
        args.append("--max-count=%d" % max_count)
    if since:
        args.append("--since=" + since)
    if author:
        args.append("--author=" + author)
    args += [rev, "--"]
    code, out, err = git(repo, *args)
    if code:
        return None, err or "git log failed"
    commits = []
    for rec in out.split("\x1e")[1:]:
        parts = rec.split("\x1f", 5)
        if len(parts) < 6:
            continue
        sha, name, date, parents, body, numstat = parts
        lines_changed, files = 0, set()
        for line in numstat.strip().splitlines():
            cols = line.split("\t", 2)
            if len(cols) == 3:
                if cols[0].isdigit() and cols[1].isdigit():
                    lines_changed += int(cols[0]) + int(cols[1])
                files.add(cols[2])
        msg_lines = body.strip("\n").splitlines()
        subject = msg_lines[0].strip() if msg_lines else ""
        rest = [l for l in msg_lines[1:] if l.strip() and not TRAILER.match(l.strip())]
        when = dt.datetime.fromisoformat(date[:-1] + "+00:00" if date.endswith("Z") else date)  # newer git prints Z for UTC
        m = CONVENTIONAL.match(subject)
        commits.append({
            "sha": sha, "author": name, "when": when, "merge": len(parents.split()) > 1,
            "subject": subject, "body": "\n".join(rest).strip(), "lines": lines_changed, "files": files,
            "type": m.group(1).lower() if m else None, "scope": bool(m and m.group(2)),
        })
    return commits, None


def pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def analyse(commits, show_authors=False):
    merges = [c for c in commits if c["merge"]]
    cs = [c for c in commits if not c["merge"]]
    n = len(cs)

    def ev(c, **extra):
        e = {"sha": c["sha"][:8], "message": short(c["subject"])}
        if show_authors:
            e["author"] = c["author"]
        e.update(extra)
        return e

    lengths = sorted(len(c["subject"]) for c in cs)
    buckets = Counter()
    for L in lengths:
        buckets["1-10" if L <= 10 else "11-30" if L <= 30 else "31-50" if L <= 50 else "51-72" if L <= 72 else "73+"] += 1
    one_word = [c for c in cs if len(c["subject"].split()) == 1]
    lazy = [c for c in cs if norm(c["subject"]) in LAZY]
    repeats = Counter(norm(c["subject"]) for c in cs)
    shame = [{"message": short(m), "count": k} for m, k in repeats.most_common() if k >= 2][:10]
    caps = [c for c in cs if sum(ch.isalpha() for ch in c["subject"]) >= 4 and c["subject"].isupper()]
    swears = [c for c in cs if SWEARS.search(c["subject"] + "\n" + c["body"])]
    emoji = [c for c in cs if EMOJI.search(c["subject"])]
    conv = [c for c in cs if c["type"]]
    moods = Counter()
    past = Counter()
    for c in cs:
        kind, word = mood(c["subject"])
        moods[kind] += 1
        if kind == "not":
            past[word] += 1
    classified = moods["imperative"] + moods["not"]
    long_subj = [c for c in cs if len(c["subject"]) > 72]
    big = [c for c in cs if c["lines"] > BIG_COMMIT_LINES]
    big_nobody = [c for c in big if not c["body"]]
    reverts = [c for c in cs if c["subject"].startswith('Revert "') or c["type"] == "revert"]
    typos = [c for c in cs if TYPO.search(c["subject"]) and c["lines"] <= 10]
    issues = [c for c in cs if ISSUE_REF.search(c["subject"] + "\n" + c["body"])]
    late = [c for c in cs if c["when"].hour < 5]
    friday = [c for c in cs if c["when"].weekday() == 4 and c["when"].hour >= 17]

    # Fix-the-fix chains: consecutive fix commits (oldest first) sharing a file, each within an hour of the last.
    chrono = sorted(cs, key=lambda c: c["when"])
    chains, run = [], []
    for c in chrono:
        prev = run[-1] if run else None
        if is_fix(c) and prev and (c["when"] - prev["when"]).total_seconds() <= FIX_CHAIN_SECONDS \
                and c["files"] & prev["files"]:
            run.append(c)
            continue
        if len(run) >= 2:
            chains.append(run)
        run = [c] if is_fix(c) else []
    if len(run) >= 2:
        chains.append(run)
    chain_commits = sum(len(ch) for ch in chains)
    giants = sorted(cs, key=lambda c: -c["lines"])[:3]

    def r(x, d):
        return x / d if d else 0.0

    descriptive = [c for c in cs if len(c["subject"].split()) > 1 and norm(c["subject"]) not in LAZY
                   and len(c["subject"]) >= 10]
    parts = {
        "descriptive subjects": (30, r(len(descriptive), n)),
        "imperative mood": (15, r(moods["imperative"], classified)),
        "subject <= 72 chars": (15, 1 - r(len(long_subj), n)),
        "big commits with a body": (15, 1 - r(len(big_nobody), len(big)) if big else 1.0),
        "conventional commits": (10, r(len(conv), n)),
        "no fix-the-fix chains": (10, 1 - r(chain_commits, n)),
        "no shouting or swearing": (5, 1 - r(len({c["sha"] for c in caps + swears}), n)),
    }
    score = round(sum(w * v for w, v in parts.values()))

    stats = {
        "commits": n, "merges": len(merges), "authors": len({c["author"] for c in cs}),
        "first": chrono[0]["when"].date().isoformat() if cs else None,
        "last": chrono[-1]["when"].date().isoformat() if cs else None,
        "subject_length": {
            "median": statistics.median(lengths) if lengths else 0,
            "p90": lengths[int(0.9 * (len(lengths) - 1))] if lengths else 0,
            "min": lengths[0] if lengths else 0, "max": lengths[-1] if lengths else 0,
            "buckets": {b: buckets[b] for b in ("1-10", "11-30", "31-50", "51-72", "73+")},
        },
        "one_word": {"count": len(one_word), "pct": pct(len(one_word), n)},
        "lazy": {"count": len(lazy), "pct": pct(len(lazy), n)},
        "hall_of_shame": shame,
        "all_caps": {"count": len(caps), "examples": [ev(c) for c in caps[:3]]},
        "swearing": {"count": len(swears), "examples": [ev(c) for c in swears[:3]]},
        "emoji": {"count": len(emoji), "pct": pct(len(emoji), n)},
        "conventional": {"count": len(conv), "pct": pct(len(conv), n),
                         "with_scope": sum(c["scope"] for c in conv),
                         "types": dict(Counter(c["type"] for c in conv).most_common())},
        "imperative": {"imperative": moods["imperative"], "not_imperative": moods["not"],
                       "unclassified": moods[None], "pct": pct(moods["imperative"], classified),
                       "top_not_imperative": dict(past.most_common(5))},
        "subject_over_72": {"count": len(long_subj), "pct": pct(len(long_subj), n),
                            "longest": ev(max(long_subj, key=lambda c: len(c["subject"])),
                                          length=max(len(c["subject"]) for c in long_subj)) if long_subj else None},
        "big_without_body": {"big_commits": len(big), "without_body": len(big_nobody),
                             "examples": [ev(c, lines=c["lines"]) for c in
                                          sorted(big_nobody, key=lambda c: -c["lines"])[:3]]},
        "fix_chains": {"chains": len(chains), "commits": chain_commits,
                       "longest": [ev(c, when=c["when"].strftime("%Y-%m-%d %H:%M")) for c in
                                   max(chains, key=len)] if chains else []},
        "late_night": {"count": len(late), "pct": pct(len(late), n), "window": "00:00-04:59 local"},
        "friday_evening": {"count": len(friday), "pct": pct(len(friday), n), "window": "Friday 17:00-23:59 local"},
        "reverts": {"count": len(reverts), "examples": [ev(c) for c in reverts[:3]]},
        "giants": [ev(c, lines=c["lines"], files=len(c["files"]), has_body=bool(c["body"])) for c in giants],
        "typo_commits": {"count": len(typos)},
        "issue_refs": {"count": len(issues), "pct": pct(len(issues), n)},
        "score": score,
        "score_parts": {k: {"weight": w, "rate": round(v, 3), "points": round(w * v, 1)} for k, (w, v) in parts.items()},
        "score_formula": "score = sum(weight x rate): " + " + ".join("%d x %s" % (w, k) for k, (w, _) in parts.items()),
    }
    stats["roastable"] = roastable(stats, cs, lazy, ev)
    if show_authors:
        per = Counter(c["author"] for c in cs)
        lazy_by = Counter(c["author"] for c in lazy)
        stats["by_author"] = [{"author": a, "commits": k, "lazy": lazy_by[a]} for a, k in per.most_common(10)]
    return stats


def roastable(s, cs, lazy, ev):
    """The five most roastable facts, each with sha + message evidence. Weight = how bad, roughly 0-100."""
    n = s["commits"] or 1
    facts = []
    if s["hall_of_shame"]:
        top = s["hall_of_shame"][0]
        hits = [c for c in cs if short(norm(c["subject"])) == top["message"]][:3]
        facts.append((top["count"] * 100.0 / n * 3, '"%s" was the whole message %d times' % (top["message"], top["count"]),
                      [ev(c) for c in hits]))
    if s["one_word"]["count"]:
        ex = [c for c in cs if len(c["subject"].split()) == 1][:3]
        facts.append((s["one_word"]["pct"] * 1.5, "%d of %d commits (%s%%) have a one-word message"
                      % (s["one_word"]["count"], n, s["one_word"]["pct"]), [ev(c) for c in ex]))
    fc = s["fix_chains"]
    if fc["chains"]:
        L = len(fc["longest"])
        facts.append((20 + 10 * L, "%d fix-the-fix chains; the longest is %d fixes in a row on the same files"
                      % (fc["chains"], L), fc["longest"][:4]))
    bb = s["big_without_body"]
    if bb["without_body"]:
        top = bb["examples"][0]
        facts.append((25 + 50.0 * bb["without_body"] / max(1, bb["big_commits"]),
                      "%d of %d commits over %d changed lines have no body; the biggest changed %d lines"
                      % (bb["without_body"], bb["big_commits"], BIG_COMMIT_LINES, top["lines"]), bb["examples"][:2]))
    if s["late_night"]["pct"] >= 5:
        ex = [c for c in cs if c["when"].hour < 5][:2]
        facts.append((s["late_night"]["pct"] * 2, "%s%% of commits were made between midnight and 5am"
                      % s["late_night"]["pct"], [ev(c, when=c["when"].strftime("%a %H:%M")) for c in ex]))
    if s["friday_evening"]["count"]:
        ex = [c for c in cs if c["when"].weekday() == 4 and c["when"].hour >= 17][:2]
        facts.append((10 + s["friday_evening"]["pct"] * 2, "%d commits landed on a Friday evening"
                      % s["friday_evening"]["count"], [ev(c, when=c["when"].strftime("%a %H:%M")) for c in ex]))
    if s["swearing"]["count"]:
        facts.append((30 + 5 * s["swearing"]["count"], "%d commit messages swear" % s["swearing"]["count"],
                      s["swearing"]["examples"][:2]))
    if s["all_caps"]["count"]:
        facts.append((25 + 5 * s["all_caps"]["count"], "%d commit subjects are in ALL CAPS" % s["all_caps"]["count"],
                      s["all_caps"]["examples"][:2]))
    if s["subject_over_72"]["longest"]:
        lg = s["subject_over_72"]["longest"]
        facts.append((10 + s["subject_over_72"]["pct"], "%d subjects run past 72 characters; the longest is %d"
                      % (s["subject_over_72"]["count"], lg["length"]), [lg]))
    if s["reverts"]["count"]:
        facts.append((15 + 5 * s["reverts"]["count"], "%d reverts" % s["reverts"]["count"], s["reverts"]["examples"][:2]))
    if s["typo_commits"]["count"] >= 2:
        ex = [c for c in cs if TYPO.search(c["subject"]) and c["lines"] <= 10][:2]
        facts.append((8 + 3 * s["typo_commits"]["count"], "%d tiny typo-fix commits" % s["typo_commits"]["count"],
                      [ev(c, lines=c["lines"]) for c in ex]))
    if s["imperative"]["not_imperative"] and s["imperative"]["pct"] < 60:
        word, k = next(iter(s["imperative"]["top_not_imperative"].items()))
        facts.append((60 - s["imperative"]["pct"], "only %s%% of verb-led subjects are imperative; \"%s\" opens %d of them"
                      % (s["imperative"]["pct"], word, k), []))
    facts.sort(key=lambda f: -f[0])
    return [{"fact": f, "evidence": e} for _, f, e in facts[:5]]


def render(s, label):
    out = []
    w = out.append
    w("COMMIT ROAST: %s" % label)
    w("%d commits (+%d merges, not scored) by %d author(s), %s to %s"
      % (s["commits"], s["merges"], s["authors"], s["first"], s["last"]))
    w("")
    w("HYGIENE SCORE: %d/100" % s["score"])
    for k, p in s["score_parts"].items():
        w("  %-26s %5.1f / %-3d (rate %.2f)" % (k, p["points"], p["weight"], p["rate"]))
    w("  " + s["score_formula"])
    w("")
    sl = s["subject_length"]
    w("Subject length: median %s, p90 %s, min %s, max %s   buckets %s" % (
        sl["median"], sl["p90"], sl["min"], sl["max"], " ".join("%s:%d" % kv for kv in sl["buckets"].items())))
    w("One-word messages: %d (%s%%)   lazy messages: %d (%s%%)" % (
        s["one_word"]["count"], s["one_word"]["pct"], s["lazy"]["count"], s["lazy"]["pct"]))
    cv = s["conventional"]
    w("Conventional Commits: %d (%s%%), %d with scope   types: %s" % (
        cv["count"], cv["pct"], cv["with_scope"], ", ".join("%s %d" % kv for kv in cv["types"].items()) or "-"))
    im = s["imperative"]
    w("Imperative mood: %s%% (%d imperative, %d not, %d unclassified)   top non-imperative: %s" % (
        im["pct"], im["imperative"], im["not_imperative"], im["unclassified"],
        ", ".join("%s %d" % kv for kv in im["top_not_imperative"].items()) or "-"))
    w("Subjects over 72 chars: %d (%s%%)" % (s["subject_over_72"]["count"], s["subject_over_72"]["pct"]))
    bb = s["big_without_body"]
    w("Commits over %d lines: %d, %d of them without a body" % (BIG_COMMIT_LINES, bb["big_commits"], bb["without_body"]))
    w("Fix-the-fix chains: %d (%d commits)" % (s["fix_chains"]["chains"], s["fix_chains"]["commits"]))
    w("Late night (%s): %d (%s%%)   Friday evening: %d (%s%%)" % (
        s["late_night"]["window"], s["late_night"]["count"], s["late_night"]["pct"],
        s["friday_evening"]["count"], s["friday_evening"]["pct"]))
    w("Reverts: %d   tiny typo commits: %d   ALL CAPS: %d   swearing: %d   emoji: %d   issue refs: %d (%s%%)" % (
        s["reverts"]["count"], s["typo_commits"]["count"], s["all_caps"]["count"], s["swearing"]["count"],
        s["emoji"]["count"], s["issue_refs"]["count"], s["issue_refs"]["pct"]))
    if s["hall_of_shame"]:
        w("")
        w("HALL OF SHAME (most repeated messages)")
        for h in s["hall_of_shame"]:
            w("  %4dx  %s" % (h["count"], h["message"]))
    w("")
    w("GIANT COMMITS")
    for g in s["giants"]:
        w("  %s  %6d lines  %3d files  %s  %s" % (g["sha"], g["lines"], g["files"],
                                                   "body" if g["has_body"] else "no body", g["message"]))
    w("")
    w("MOST ROASTABLE")
    for i, f in enumerate(s["roastable"], 1):
        w("  %d. %s" % (i, f["fact"]))
        for e in f["evidence"]:
            extra = "  [%s]" % ", ".join("%s=%s" % (k, v) for k, v in e.items() if k not in ("sha", "message"))
            w("       %s  %s%s" % (e["sha"], e["message"], extra if len(e) > 2 else ""))
    if "by_author" in s:
        w("")
        w("BY AUTHOR")
        for a in s["by_author"]:
            w("  %-24s %5d commits  %4d lazy" % (a["author"][:24], a["commits"], a["lazy"]))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                epilog="Exit codes: 0 report printed; 2 not a git repository, git error, or no commits.",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("range", nargs="?", default="HEAD", help="revision or range for git log (default HEAD)")
    p.add_argument("-C", dest="repo", default=".", help="repository path (default .)")
    p.add_argument("--since", help='passed to git log --since, e.g. "6 months ago" or 2026-01-01')
    p.add_argument("--max-count", type=int, default=1000, help="newest N commits, merges included (default 1000, 0 = all)")
    p.add_argument("--author", help="passed to git log --author (regex on name or email)")
    p.add_argument("--show-authors", action="store_true", help="name authors in evidence (anonymous by default)")
    p.add_argument("--json", action="store_true", help="print JSON instead of the text report")
    a = p.parse_args(argv)

    code, _, err = git(a.repo, "rev-parse", "--git-dir")
    if code:
        print("Not a git repository: %s (%s)" % (a.repo, err or "git not found"), file=sys.stderr)
        return 2
    commits, err = read_log(a.repo, a.range, a.since, a.max_count, a.author)
    if commits is None:
        print("git log failed: %s" % err, file=sys.stderr)
        return 2
    if not [c for c in commits if not c["merge"]]:
        print("No non-merge commits in that range.", file=sys.stderr)
        return 2
    s = analyse(commits, a.show_authors)
    if a.json:
        print(json.dumps(s, indent=2, ensure_ascii=False))
    else:
        print(render(s, a.range))
    return 0


if __name__ == "__main__":
    sys.exit(main())
