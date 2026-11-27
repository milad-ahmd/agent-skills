#!/usr/bin/env python3
"""Your year in code, from your own git history: a "wrapped" summary, a shareable SVG card and a
short markdown recap. Stdlib only, read-only.

Usage: python3 wrapped.py [--repos PATH ...] [--email ADDR ...] [--year YYYY] [--exclude GLOB]
                          [--json] [--md] [--svg card.svg] [--theme dark|light]
                          [--show-repos] [--show-details] [--github USER]
  --repos PATH   git repos, or folders searched for repos (default: current directory). Folders are
                 searched --depth levels deep (default 4); hidden dirs, node_modules, vendor, venvs
                 and build output are skipped.
  --email ADDR   your author email; repeat for each address you commit with. Default: the
                 `git config user.email` of each repo. Only commits by these emails are counted.
  --exclude GLOB leave matching paths out of line counts, e.g. 'assets/*' or '*.csv' (repeatable)
  --year YYYY    the year to wrap, by each commit's author date in its own timezone (default: this year)
  --json         all stats as JSON on stdout
  --md           a short markdown recap on stdout, for a README or a post
  --svg FILE     write a 1200x630 card (dark by default, --theme light)
  --no-credit     leave the "made with agent-skills" line off the card
  --show-repos   allow repo names on the card and in --md (off by default)
  --show-details allow commit subjects and file paths in --md (off by default)
  --github USER  add public GitHub stats (repos created, languages, stars) from the REST API. Works
                 without a token; listing stargazers (stars received this year) needs GITHUB_TOKEN,
                 otherwise all-time stars are shown. Offline or rate limited just leaves them out.

Merge commits are skipped. Lines from lockfiles, vendored, minified and generated files and binary
files are left out of line counts, languages, the biggest commit and the most-edited file. The card
and --md never contain emails; repo names, file paths and commit messages only with the flags above.

Exit codes: 0 report written
            1 git not found or an output file could not be written
            2 no repos or no commits by these emails in that year
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from xml.sax.saxutils import escape

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
SKIP_DIRS = {"node_modules", "vendor", "venv", "env", "__pycache__", "dist", "build", "target",
             "Pods", "bower_components", "site-packages"}
EXCLUDED = ["package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
            "Cargo.lock", "poetry.lock", "Pipfile.lock", "uv.lock", "Gemfile.lock", "composer.lock",
            "go.sum", "mix.lock", "flake.lock", "Podfile.lock", "packages.lock.json", "*.min.js",
            "*.min.css", "*.map", "*.pb.go", "*_pb2.py", "*.generated.*", "*.snap", "*.svg", "*.ipynb"]
EXCLUDED_DIRS = {"node_modules", "vendor", "third_party", "dist", "build", "__generated__", "generated"}
LANGS = {
    ".py": "Python", ".go": "Go", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".cs": "C#", ".swift": "Swift",
    ".m": "Objective-C", ".dart": "Dart", ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang",
    ".hs": "Haskell", ".clj": "Clojure", ".lua": "Lua", ".r": "R", ".jl": "Julia", ".zig": "Zig",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell", ".sql": "SQL",
    ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".sass": "CSS", ".less": "CSS", ".vue": "Vue",
    ".svelte": "Svelte", ".md": "Markdown", ".mdx": "Markdown", ".rst": "reStructuredText",
    ".tex": "TeX",
    ".tf": "HCL", ".hcl": "HCL", ".proto": "Protobuf", ".graphql": "GraphQL", ".gradle": "Gradle",
}
# Data formats (JSON, YAML, TOML, XML) count toward lines but not languages, as in GitHub's language bar.
NAMED = {"Dockerfile": "Dockerfile", "Makefile": "Makefile", "Jenkinsfile": "Groovy"}
VERBS = {"feat": "add", "added": "add", "adds": "add", "adding": "add", "fixed": "fix", "fixes": "fix",
         "fixing": "fix", "bugfix": "fix", "hotfix": "fix", "updated": "update", "updates": "update",
         "removed": "remove", "removes": "remove", "delete": "remove", "deleted": "remove",
         "refactored": "refactor", "refactors": "refactor", "refactoring": "refactor",
         "implemented": "implement", "implements": "implement", "improved": "improve",
         "improves": "improve", "tests": "test", "doc": "docs", "moved": "move", "renamed": "rename",
         "bumped": "bump", "bumps": "bump", "cleaned": "clean", "cleanup": "clean"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
NIGHT = set(range(22, 24)) | set(range(0, 5))   # 22:00-04:59 local
EARLY = set(range(5, 9))                         # 05:00-08:59 local


# ---------------------------------------------------------------- finding repos and commits

def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def find_repos(paths, depth=4):
    found = []
    for root in paths:
        root = os.path.abspath(os.path.expanduser(root))
        base = root.rstrip(os.sep).count(os.sep)
        for cur, dirs, files in os.walk(root):
            if ".git" in dirs or ".git" in files:  # a .git file is a worktree or submodule
                found.append(cur)
                dirs[:] = []
                continue
            if cur.count(os.sep) - base >= depth:
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
    out, seen = [], set()
    for r in found:
        top = git(r, "rev-parse", "--show-toplevel").strip()
        key = os.path.realpath(top or r)
        if top and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def excluded(path, extra=()):
    parts = path.split("/")
    if any(fnmatch.fnmatch(path, pat) for pat in extra):
        return True
    if any(p in EXCLUDED_DIRS for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], pat) for pat in EXCLUDED)


def language(path):
    name = path.rsplit("/", 1)[-1]
    if name in NAMED:
        return NAMED[name]
    return LANGS.get(os.path.splitext(name)[1].lower())


def verb(subject):
    m = re.match(r"\s*([A-Za-z]+)(\([^)]*\))?!?:", subject)  # conventional commit type
    word = m.group(1) if m else (re.match(r"\s*\W*([A-Za-z]{2,})\b", subject) or [None, None])[1]
    if not word:
        return None
    word = word.lower()
    return VERBS.get(word, word)


def read_commits(repo, emails, year, extra_excludes=()):
    """Own non-merge commits whose author date, in its own offset, falls in `year`."""
    fmt = "%x1e%H%x1f%ae%x1f%aI%x1f%B%x1d"
    # --since is only a cheap pre-filter on committer date; the exact year check is below.
    raw = git(repo, "-c", "core.quotePath=false", "log", "--branches", "--tags", "--remotes", "--no-merges", "--no-renames",
              "--numstat", "--since=%d-12-30" % (year - 1), "--format=" + fmt)
    name = os.path.basename(repo)
    commits = []
    for chunk in raw.split("\x1e")[1:]:
        head, _, stat = chunk.partition("\x1d")
        sha, email, date, message = head.split("\x1f", 3)
        when = dt.datetime.fromisoformat(date.replace("Z", "+00:00"))  # py3.9 rejects "Z"
        if email.lower() not in emails or when.year != year:
            continue
        files = []
        for line in stat.strip().splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3 or parts[0] == "-":  # "-" is a binary file
                continue
            path = parts[2].strip('"')
            if not excluded(path, extra_excludes):
                files.append((path, int(parts[0]), int(parts[1])))
        commits.append({"sha": sha, "repo": name, "when": when, "message": message.strip(),
                        "files": files})
    return commits


# ---------------------------------------------------------------- stats

def longest_streak(days):
    best, start, run_start, prev = 0, None, None, None
    for d in sorted(days):
        run_start = d if prev is None or (d - prev).days != 1 else run_start
        prev = d
        length = (d - run_start).days + 1
        if length > best:
            best, start = length, run_start
    end = start + dt.timedelta(days=best - 1) if start else None
    return best, start, end


def pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def compute(commits, year):
    commits = sorted(commits, key=lambda c: c["when"])
    n = len(commits)
    hours, weekdays, months = [0] * 24, [0] * 7, [0] * 12
    per_day, repos, langs, files, verbs = Counter(), Counter(), Counter(), Counter(), Counter()
    added = removed = 0
    biggest, longest = None, None
    for c in commits:
        w = c["when"]
        hours[w.hour] += 1
        weekdays[w.weekday()] += 1
        months[w.month - 1] += 1
        per_day[w.date()] += 1
        repos[c["repo"]] += 1
        v = verb(c["message"].splitlines()[0] if c["message"] else "")
        if v:
            verbs[v] += 1
        size = 0
        for path, a, r in c["files"]:
            added += a
            removed += r
            size += a + r
            files[(c["repo"], path)] += 1
            lang = language(path)
            if lang:
                langs[lang] += a + r
        brief = {"sha": c["sha"][:10], "repo": c["repo"], "date": w.date().isoformat(),
                 "subject": c["message"].splitlines()[0] if c["message"] else ""}
        if size and (biggest is None or size > biggest["lines"]):
            biggest = dict(brief, lines=size)
        if longest is None or len(c["message"]) > longest["chars"]:
            longest = dict(brief, chars=len(c["message"]))
    streak, s_start, s_end = longest_streak(per_day)
    lang_total = sum(langs.values())
    busy_day = max(per_day.items(), key=lambda kv: (kv[1], -kv[0].toordinal())) if per_day else None
    busy_month = max(range(12), key=lambda i: (months[i], -i))
    ends = []
    for c in (commits[0], commits[-1]) if commits else ():
        ends.append({"sha": c["sha"][:10], "repo": c["repo"], "when": c["when"].isoformat(),
                     "subject": c["message"].splitlines()[0] if c["message"] else ""})
    top_file = files.most_common(1)[0] if files else None
    stats = {
        "year": year, "commits": n, "active_days": len(per_day),
        "longest_streak": {"days": streak, "start": s_start.isoformat() if s_start else None,
                           "end": s_end.isoformat() if s_end else None},
        "busiest_day": {"date": busy_day[0].isoformat(), "commits": busy_day[1]} if busy_day else None,
        "busiest_month": {"month": MONTHS[busy_month], "commits": months[busy_month]},
        "months": months, "hours": hours, "weekdays": weekdays,
        "night_owl_pct": pct(sum(hours[h] for h in NIGHT), n),
        "early_bird_pct": pct(sum(hours[h] for h in EARLY), n),
        "weekend_pct": pct(weekdays[5] + weekdays[6], n),
        "lines_added": added, "lines_removed": removed,
        "languages": [{"language": k, "lines": v, "pct": pct(v, lang_total)} for k, v in langs.most_common(8)],
        "repos": [{"repo": k, "commits": v} for k, v in repos.most_common(5)],
        "repo_count": len(repos),
        "verbs": [{"verb": k, "commits": v} for k, v in verbs.most_common(5)],
        "biggest_commit": biggest, "longest_message": longest,
        "most_edited_file": {"repo": top_file[0][0], "path": top_file[0][1], "commits": top_file[1]} if top_file else None,
        "first_commit": ends[0] if ends else None, "last_commit": ends[-1] if ends else None,
    }
    stats["archetype"] = archetype(stats, verbs)
    return stats


def archetype(s, verbs):
    """First matching rule wins; the rule text is shown next to the title."""
    n = s["commits"]
    if n < 10:
        return {"name": "The Fresh Start", "rule": "fewer than 10 commits this year, every one counts"}
    month_top = max(s["months"])
    langs = [l for l in s["languages"] if l["pct"] >= 10]
    rules = [
        (s["night_owl_pct"] >= 30, "The Night Owl", "%.0f%% of commits between 22:00 and 05:00" % s["night_owl_pct"]),
        (s["early_bird_pct"] >= 25, "The Early Bird", "%.0f%% of commits between 05:00 and 09:00" % s["early_bird_pct"]),
        (s["weekend_pct"] >= 35, "The Weekend Warrior", "%.0f%% of commits on Saturday or Sunday" % s["weekend_pct"]),
        (s["longest_streak"]["days"] >= 14, "The Streak Machine", "%d days in a row with a commit" % s["longest_streak"]["days"]),
        (verbs["refactor"] >= 0.15 * n, "The Refactorer", "%.0f%% of commits are refactors" % pct(verbs["refactor"], n)),
        (s["lines_removed"] > s["lines_added"] > 0, "The Gardener", "deleted more lines (%d) than added (%d)" % (s["lines_removed"], s["lines_added"])),
        (verbs["fix"] >= 0.30 * n, "The Bug Squasher", "%.0f%% of commits are fixes" % pct(verbs["fix"], n)),
        (len(langs) >= 4, "The Polyglot", "%d languages each above 10%% of changed lines" % len(langs)),
        (month_top >= 0.40 * n, "The Sprinter", "%.0f%% of the year's commits landed in %s" % (pct(month_top, n), s["busiest_month"]["month"])),
    ]
    for hit, name, rule in rules:
        if hit:
            return {"name": name, "rule": rule}
    return {"name": "The Steady Builder", "rule": "no single habit dominated: consistent all year"}


# ---------------------------------------------------------------- GitHub (public, optional)

API = "https://api.github.com"


def http_get(url, accept="application/vnd.github+json", token=True):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "dev-wrapped",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    tok = token and (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r), r.headers.get("X-RateLimit-Remaining")
    except urllib.error.HTTPError as e:
        if e.code == 401 and tok:  # stale token in the env: public data needs none
            return http_get(url, accept, token=False)
        raise


def github_stats(user, year, budget=40):
    """Public repos, languages and stars received in `year`. Never raises: errors go into "error"."""
    out = {"user": user, "partial": False}
    calls = 0
    try:
        repos = []
        for page in range(1, 4):
            batch, left = http_get("%s/users/%s/repos?type=owner&per_page=100&page=%d" % (API, user, page))
            calls += 1
            repos += batch
            if len(batch) < 100:
                break
        own = [r for r in repos if not r.get("fork")]
        y = str(year)
        out["public_repos"] = len(own)
        out["repos_created"] = sum(1 for r in own if (r.get("created_at") or "").startswith(y))
        active = [r for r in own if (r.get("pushed_at") or "") >= y]  # last push only, so "since Jan 1"
        out["repos_pushed_since_jan1"] = len(active)
        out["languages"] = [k for k, _ in Counter(r["language"] for r in active if r.get("language")).most_common(5)]
        out["total_stars"] = sum(r.get("stargazers_count", 0) for r in own)
        stars = 0
        # The docs give no stargazer order, so every page is read until the call budget runs out.
        try:
            for r in sorted(own, key=lambda r: -r.get("stargazers_count", 0)):
                count = r.get("stargazers_count", 0)
                for page in range(1, (count + 99) // 100 + 1):
                    if calls >= budget or (left is not None and int(left) <= 1):
                        out["partial"] = True
                        break
                    batch, left = http_get("%s/repos/%s/stargazers?per_page=100&page=%d" % (API, r["full_name"], page),
                                           accept="application/vnd.github.star+json")
                    calls += 1
                    stars += sum(1 for s in batch if (s.get("starred_at") or "").startswith(y))
            out["stars_this_year"] = stars
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            out["stars_note"] = "listing stargazers needs a token; set GITHUB_TOKEN for stars received this year"
    except urllib.error.HTTPError as e:
        out["partial"] = True
        out["error"] = {404: "user not found", 403: "rate limited", 429: "rate limited"}.get(e.code, "HTTP %d" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        out["partial"] = True
        out["error"] = "offline or unreachable (%s)" % getattr(e, "reason", e)
    return out


# ---------------------------------------------------------------- output

def short(n):
    for div, suffix in ((1e6, "M"), (1e3, "k")):
        if n >= div:
            v = n / div
            return (("%.1f" % v).rstrip("0").rstrip(".") if v < 100 else "%d" % v) + suffix
    return str(n)


def fit(text, chars):
    return text if len(text) <= chars else text[:chars - 1] + "…"


def text_report(s):
    a = s["archetype"]
    lines = ["Your %d in code: %s (%s)" % (s["year"], a["name"], a["rule"]), "",
             "commits        %d across %d repos" % (s["commits"], s["repo_count"]),
             "active days    %d" % s["active_days"],
             "longest streak %d days (%s to %s)" % (s["longest_streak"]["days"], s["longest_streak"]["start"], s["longest_streak"]["end"]),
             "busiest day    %s (%d commits)" % (s["busiest_day"]["date"], s["busiest_day"]["commits"]),
             "busiest month  %s (%d commits)" % (s["busiest_month"]["month"], s["busiest_month"]["commits"]),
             "lines          +%d / -%d" % (s["lines_added"], s["lines_removed"]),
             "night owl %.0f%%  early bird %.0f%%  weekends %.0f%%" % (s["night_owl_pct"], s["early_bird_pct"], s["weekend_pct"]),
             "languages      " + ", ".join("%s %.0f%%" % (l["language"], l["pct"]) for l in s["languages"][:5]),
             "repos          " + ", ".join("%s (%d)" % (r["repo"], r["commits"]) for r in s["repos"]),
             "verbs          " + ", ".join("%s (%d)" % (v["verb"], v["commits"]) for v in s["verbs"]),
             "weekdays       " + "  ".join("%s %d" % (d, c) for d, c in zip(DAYS, s["weekdays"])),
             "hours          " + " ".join(str(h) for h in s["hours"])]
    b, m, f = s["biggest_commit"], s["longest_message"], s["most_edited_file"]
    if b:
        lines.append("biggest commit %s %s, %d lines: %s" % (b["repo"], b["sha"], b["lines"], b["subject"]))
    if m:
        lines.append("longest msg    %s %s, %d chars" % (m["repo"], m["sha"], m["chars"]))
    if f:
        lines.append("most edited    %s:%s (%d commits)" % (f["repo"], f["path"], f["commits"]))
    for label, c in (("first commit", s["first_commit"]), ("last commit", s["last_commit"])):
        lines.append("%-14s %s %s: %s" % (label, c["when"], c["repo"], c["subject"]))
    g = s.get("github")
    if g:
        lines.append(github_line(g) or "github         " + g.get("error", "no data"))
        if g.get("stars_note"):
            lines.append("github note    " + g["stars_note"])
    return "\n".join(lines)


def github_stars(g):
    if "stars_this_year" in g:
        return "%d GitHub stars received%s" % (g["stars_this_year"], " (partial)" if g["partial"] else "")
    return "%d GitHub stars (all time)" % g["total_stars"] if "total_stars" in g else ""


def github_line(g):
    if "repos_created" not in g:
        return ""
    return "%s, %d public repos created this year" % (github_stars(g), g["repos_created"])


def markdown(s, show_repos=False, show_details=False):
    a = s["archetype"]
    langs = ", ".join("%s (%.0f%%)" % (l["language"], l["pct"]) for l in s["languages"][:3])
    out = ["## My %d in code: %s" % (s["year"], a["name"]), "",
           "- %d commits on %d days, longest streak %d days" % (s["commits"], s["active_days"], s["longest_streak"]["days"]),
           "- +%s / -%s lines" % (short(s["lines_added"]), short(s["lines_removed"])),
           "- Busiest month: %s. %.0f%% of commits on weekends, %.0f%% between 22:00 and 05:00" % (
               s["busiest_month"]["month"], s["weekend_pct"], s["night_owl_pct"])]
    if langs:
        out.append("- Top languages: " + langs)
    if s["verbs"]:
        out.append("- Favourite commit verb: %s" % s["verbs"][0]["verb"])
    if show_repos:
        out.append("- Top repos: " + ", ".join(r["repo"] for r in s["repos"][:3]))
    if show_details:
        b, f = s["biggest_commit"], s["most_edited_file"]
        if b:
            out.append("- Biggest commit: \"%s\" (%d lines)" % (b["subject"], b["lines"]))
        if f:
            out.append("- Most edited file: `%s` (%d commits)" % (f["path"], f["commits"]))
    g = s.get("github")
    if g and github_line(g):
        out.append("- " + github_line(g))
    out += ["", "_%s: %s._" % (a["name"], a["rule"])]
    return "\n".join(out)


THEMES = {
    "dark": {"bg": "#111318", "panel": "#1c1f27", "fg": "#f4f4f5", "muted": "#a1a1aa", "accent": "#ff7a45"},
    "light": {"bg": "#faf7f2", "panel": "#efe9df", "fg": "#18181b", "muted": "#52525b", "accent": "#c2410c"},
}
FONT = "Inter, 'Helvetica Neue', Arial, sans-serif"


CREDIT = "made with github.com/milad-ahmd/agent-skills"


def credit(svg, on=True):
    """Sign the card in its free top-right corner so a shared card links back; --no-credit turns it off."""
    if not on:
        return svg
    return svg.replace("</svg>", '<text x="1160" y="44" font-size="16" fill="#71717a" text-anchor="end">%s</text>\n</svg>' % CREDIT)


def svg_card(s, theme="dark", show_repos=False):
    t = THEMES[theme]
    a = s["archetype"]
    el = []

    def text(x, y, body, size, color, weight="400", anchor="start"):
        el.append('<text x="%d" y="%d" font-size="%d" font-weight="%s" fill="%s" text-anchor="%s">%s</text>'
                  % (x, y, size, weight, color, anchor, escape(body)))

    el.append('<rect width="1200" height="630" fill="%s"/>' % t["bg"])
    el.append('<rect x="0" y="0" width="12" height="630" fill="%s"/>' % t["accent"])
    text(60, 72, "MY %d IN CODE" % s["year"], 24, t["accent"], "700")
    text(60, 148, fit(a["name"], 28), 68, t["fg"], "800")
    text(60, 192, fit(a["rule"], 80), 24, t["muted"])
    tiles = [(short(s["commits"]), "commits"), (str(s["active_days"]), "active days"),
             (str(s["longest_streak"]["days"]), "day streak"),
             ("+%s/-%s" % (short(s["lines_added"]), short(s["lines_removed"])), "lines")]
    for i, (big, label) in enumerate(tiles):
        x = 60 + i * 275
        el.append('<rect x="%d" y="225" width="255" height="130" rx="14" fill="%s"/>' % (x, t["panel"]))
        text(x + 24, 298, big, 48 if len(big) <= 7 else 36 if len(big) <= 9 else 30, t["fg"], "800")
        text(x + 24, 334, label, 22, t["muted"])
    text(60, 410, "Top languages", 24, t["fg"], "700")
    for i, l in enumerate(s["languages"][:3]):
        y = 440 + i * 46
        w = max(4, int(300 * l["pct"] / 100.0))
        el.append('<rect x="60" y="%d" width="%d" height="26" rx="6" fill="%s"/>' % (y, w, t["accent"]))
        text(72 + w, y + 21, "%s %.0f%%" % (l["language"], l["pct"]), 22, t["fg"])
    if not s["languages"]:
        text(60, 460, "no source files counted", 22, t["muted"])
    text(640, 410, "When I commit", 24, t["fg"], "700")
    peak = max(s["hours"]) or 1
    for h, c in enumerate(s["hours"]):
        bh = max(2, int(110 * c / peak))
        el.append('<rect x="%d" y="%d" width="16" height="%d" rx="3" fill="%s"/>'
                  % (640 + h * 21, 552 - bh, bh, t["accent"] if c == peak else t["muted"]))
    for h in (0, 6, 12, 18):
        text(640 + h * 21, 580, "%02d" % h, 18, t["muted"])
    foot = ["busiest month %s" % s["busiest_month"]["month"], "%.0f%% weekends" % s["weekend_pct"]]
    if s["verbs"]:
        foot.append("favourite verb: %s" % s["verbs"][0]["verb"])
    g = s.get("github")
    if g and github_stars(g):
        foot.append(github_stars(g))
    if show_repos and s["repos"]:
        foot.append("top repos: " + ", ".join(r["repo"] for r in s["repos"][:3]))
    text(60, 612, fit("  ·  ".join(foot), 95), 20, t["muted"])
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" '
            'font-family="%s">\n<title>%s</title>\n%s\n</svg>\n'
            % (escape(FONT, {'"': "&quot;"}), escape("My %d in code: %s" % (s["year"], a["name"])), "\n".join(el)))


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Exit codes: 0 report written, 1 git missing or write error, "
                                       "2 no repos or no commits found")
    p.add_argument("--repos", nargs="+", default=["."])
    p.add_argument("--email", action="append", default=[])
    p.add_argument("--year", type=int, default=dt.date.today().year)
    p.add_argument("--depth", type=int, default=4)
    p.add_argument("--exclude", action="append", default=[])
    p.add_argument("--json", action="store_true")
    p.add_argument("--md", action="store_true")
    p.add_argument("--svg")
    p.add_argument("--no-credit", action="store_true", help="leave the \"made with agent-skills\" line off the card")
    p.add_argument("--theme", choices=sorted(THEMES), default="dark")
    p.add_argument("--show-repos", action="store_true")
    p.add_argument("--show-details", action="store_true")
    p.add_argument("--github")
    args = p.parse_args(argv)

    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        print("git not found", file=sys.stderr)
        return 1
    repos = find_repos(args.repos, args.depth)
    if not repos:
        print("No git repositories found under: " + ", ".join(args.repos), file=sys.stderr)
        return 2
    given = {e.lower() for e in args.email}
    commits, seen = [], set()
    for repo in repos:
        emails = given or {git(repo, "config", "user.email").strip().lower()} - {""}
        if not emails:
            print("skipped %s: no user.email set, pass --email" % repo, file=sys.stderr)
            continue
        for c in read_commits(repo, emails, args.year, args.exclude):
            if c["sha"] not in seen:  # the same project cloned twice counts once
                seen.add(c["sha"])
                commits.append(c)
    if not commits:
        print("No commits by %s in %d across %d repos." % (", ".join(sorted(given)) or "the configured user.email",
                                                         args.year, len(repos)), file=sys.stderr)
        return 2
    stats = compute(commits, args.year)
    if args.github:
        stats["github"] = github_stats(args.github, args.year)
    if args.svg:
        try:
            with open(args.svg, "w", encoding="utf-8") as fh:
                fh.write(credit(svg_card(stats, args.theme, args.show_repos), not args.no_credit))
        except OSError as e:
            print("could not write %s: %s" % (args.svg, e), file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(stats, indent=2))
    elif args.md:
        print(markdown(stats, args.show_repos, args.show_details))
    else:
        print(text_report(stats))
        if args.svg:
            print("\ncard written to " + args.svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
