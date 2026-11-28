#!/usr/bin/env python3
"""Collect the facts for planning commits, or lint a commit message. Stdlib only, read-only, offline.

Usage: python3 commit_facts.py [-C REPO] [-n N] [--json]
       python3 commit_facts.py [-C REPO] [-n N] [--json] [--conventional] (--lint MESSAGE | --lint-file PATH)
  -C REPO           repository to read (default: current directory)
  -n N              how many recent non-merge commits to learn the house style from (default 100)
  --json            machine-readable output on stdout
  --lint MESSAGE    check a commit message instead of reporting the working tree
  --lint-file PATH  same, read from a file (for example .git/COMMIT_EDITMSG)
  --conventional    enforce Conventional Commits 1.0.0 even if the repo does not use them

Default mode reports staged, unstaged and untracked files with line counts, a kind per file, files whose
change is whitespace-only, files that are both staged and unstaged, conflicts, a suggested grouping, and
the repo's own message style. It never stages, commits or writes anything (git runs with
GIT_OPTIONAL_LOCKS=0 so `git status` does not even refresh the index).

  kind          from the path only: deps (dependency manifest or lockfile), ci, docs, tests, config,
                source. A package.json or pyproject.toml change is tagged deps even when it only touches
                scripts or tool settings; read the diff.
  formatting    the side (staged or unstaged) has line changes but `git diff -w` for it is empty.
                Blank-line-only changes are NOT caught (-w keeps them), binary and mode-only changes never
                count, and a file is a "formatting" group only if every side it changed on is whitespace-only.
  groups        top-level directory x kind; formatting-only files get their own group. A starting point
                for the plan, not the plan: a fix and its test usually belong in one commit.
  renames       not followed (--no-renames): a rename shows as a deletion plus an addition.

House style, from `git log --no-merges -n N` (merge, Revert "...", fixup!/squash!/amend! subjects are not
counted):
  conventional share   subjects matching the Conventional Commits 1.0.0 header shape
  common-type share    of those, subjects whose type is one of commitlint config-conventional's 11 types
                       (build chore ci docs feat fix perf refactor revert style test)
  area-prefix share    subjects like `net/http: fix ...` or `runtime: fix ...` (Go, Linux, Node style)
  style verdict        conventional if common-type share >= 50%; area-prefix if area-prefix share >= 50%;
                       mixed if common-type share >= 20%; else free-form. Confidence: low under 20 commits,
                       high when the deciding share is >= 80% (or every share is under 10%) with 50+ commits,
                       otherwise medium. A judgement call: read `git log --oneline` when it is not high.
  trailers             parsed by git itself (%(trailers:only,unfold)); counted once per commit, key case ignored
  capitalisation, trailing period, body use, median subject length: shares over the counted subjects

Lint (errors fail with exit 1; warnings never fail):
  error    empty message; subject over 72 chars (a warning when the repo's median is over 72); line 2 not blank; when Conventional Commits are enforced
           (repo verdict is conventional, or --conventional): subject not `type(scope)!: description`, or a
           `breaking change:`-like footer not written `BREAKING CHANGE: ` / `BREAKING-CHANGE: ` (uppercase)
  warning  subject over 50 chars; body line over 72 chars (URLs, indented lines and footers exempt);
           BREAKING CHANGE footer not after a blank line or another footer; when CC is enforced, a type
           not used in the last N commits; with 20+ commits of history: capitalisation or trailing period
           against a clear (80%+) house habit, and unless CC is enforced, a CC prefix in a free-form or
           area-prefix repo or a missing `area:` prefix in an area-prefix repo
  Merge, Revert "...", fixup!/squash!/amend! subjects are git-generated: no length or CC checks.
  50 comes from the git-commit docs ("no more than 50 characters"); 72 is convention (Tim Pope, 2008:
  wrap the body at about 72), not a git or spec rule. --lint-file is cleaned like an edited message
  (`--cleanup=strip`: # lines and everything from the scissors line down are dropped); --lint MESSAGE is
  taken as given, like `git commit -m` (`--cleanup=whitespace`), so a leading `#42` stays.

Cannot see: what a change means (kind is from paths, never from content), hunks that mix intents within a
file (use `git add -p`), blank-line-only formatting, renames, a custom core.commentChar, ticket-key or
gitmoji habits (the shares do not measure them; read the recent subjects the report prints), and any style
rule a repo keeps outside its history (commitlint config, CONTRIBUTING.md). Untracked files are read in
full to count lines (over 10 MB is skipped), so an unignored dependency folder makes the run slow.

Exit codes: 0 report written, or lint passed (warnings allowed)
            1 lint found errors
            2 not a git repository (default mode), a git error, or an unreadable --lint-file
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter, OrderedDict

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat",
               GIT_OPTIONAL_LOCKS="0")
# These three lines must stay byte-identical to skills/changelog-from-git/scripts/release_notes.py (CI checks it).
SUBJECT_RE = re.compile(r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\r\n]*)\))?(?P<bang>!)?: (?P<desc>\S.*)$")
BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE: (.*)$")  # uppercase only, per spec item 15
FOOTER_RE = re.compile(r"^(?:[A-Za-z][\w-]*|BREAKING CHANGE)(?:: | #)")
AREA_RE = re.compile(r"^[\w.*+{}/-]+(?:, ?[\w.*+{}/-]+)*: \S")
LOOSE_BREAKING_RE = re.compile(r"^\s*breaking[ _-]?changes?\s*:", re.I)
SKIP_SUBJECT_RE = re.compile(r'^(?:Merge |Revert "|fixup! |squash! |amend! )')
COMMON_TYPES = {"build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test"}
SOFT_SUBJECT, HARD_SUBJECT, BODY_WRAP = 50, 72, 72
SCISSORS = "# ------------------------ >8 ------------------------"
KIND_ORDER = ["source", "tests", "docs", "config", "ci", "deps", "formatting"]

DEP_FILES = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock",
    "bun.lockb", "deno.lock", "go.mod", "go.sum", "go.work", "go.work.sum", "Cargo.toml", "Cargo.lock",
    "Gemfile", "Gemfile.lock", "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock", "uv.lock",
    "pdm.lock", "composer.json", "composer.lock", "pom.xml", "build.gradle", "build.gradle.kts",
    "gradle.lockfile", "libs.versions.toml", "mix.exs", "mix.lock", "Package.swift", "Package.resolved",
    "Podfile", "Podfile.lock", "pubspec.yaml", "pubspec.lock", "packages.lock.json",
    "Directory.Packages.props", "flake.lock", "vcpkg.json", "conanfile.txt"}
DEP_RE = re.compile(r"^requirements[\w.-]*\.(?:txt|in)$")
CI_FILES = {".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", ".travis.yml", "bitbucket-pipelines.yml",
            "appveyor.yml", ".appveyor.yml", ".drone.yml", "cloudbuild.yaml", ".woodpecker.yml"}
CI_DIRS = (".github/workflows/", ".github/actions/", ".circleci/", ".buildkite/", ".woodpecker/")
DOC_EXTS = {".md", ".mdx", ".rst", ".adoc", ".asciidoc"}
DOC_DIRS = {"docs", "doc", "documentation"}
DOC_NAMES = {"README", "CHANGELOG", "CHANGES", "HISTORY", "LICENSE", "LICENCE", "COPYING", "AUTHORS",
             "NOTICE", "CONTRIBUTING"}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testdata", "testing", "fixtures",
             "__snapshots__", "e2e", "cypress"}
TEST_RE = re.compile(r"^(?:test_.*\.py|.*_test\.(?:py|go|rb|exs?)|.*\.(?:test|spec)\.\w+|.*_spec\.rb"
                     r"|.*Tests?\.(?:java|kt|cs|swift|scala)|conftest\.py)$")
CONFIG_EXTS = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json", ".jsonc", ".json5", ".properties"}
CONFIG_RE = re.compile(r"^(?:Dockerfile.*|Containerfile|docker-compose.*|compose\.ya?ml|Makefile|Procfile"
                       r"|CODEOWNERS|.*\.config\.[cm]?[jt]s)$")


class GitError(Exception):
    pass


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, stdin=subprocess.DEVNULL)
    if check and r.returncode:
        raise GitError(r.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
    return r


def kind_of(path):
    parts = path.split("/")
    name = parts[-1]
    ext = os.path.splitext(name)[1].lower()
    if name in DEP_FILES or DEP_RE.match(name):
        return "deps"
    if name in CI_FILES or path.startswith(CI_DIRS):
        return "ci"
    stem_is_doc = os.path.splitext(name)[0].upper() in DOC_NAMES and ext in DOC_EXTS | {"", ".txt"}
    if ext in DOC_EXTS or stem_is_doc or any(p.lower() in DOC_DIRS for p in parts[:-1]):
        return "docs"
    if TEST_RE.match(name) or any(p.lower() in TEST_DIRS for p in parts[:-1]):
        return "tests"
    if name.startswith(".") or ext in CONFIG_EXTS or CONFIG_RE.match(name):
        return "config"
    return "source"


def numstat(repo, cached, ignore_ws=False):
    """{path: (added, deleted)}; None counts for binary files."""
    args = ["diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv"]
    args += ["--cached"] if cached else []
    args += ["-w"] if ignore_ws else []
    res = {}
    for rec in git(repo, *args).stdout.decode("utf-8", "replace").split("\0"):
        if rec.count("\t") >= 2:
            a, d, path = rec.split("\t", 2)
            res[path] = (None, None) if a == "-" else (int(a), int(d))
    return res


def count_lines(full):
    try:
        if os.path.getsize(full) > 10 * 1024 * 1024:
            return None
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def whitespace_only(stat, ws_stat, path):
    a, d = stat.get(path, (None, None))
    if a is None or a + d == 0:  # binary or mode-only
        return False
    return ws_stat.get(path, (0, 0)) == (0, 0)


def collect_changes(repo):
    raw = git(repo, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all").stdout
    staged_stat, unstaged_stat = numstat(repo, True), numstat(repo, False)
    staged_ws, unstaged_ws = numstat(repo, True, True), numstat(repo, False, True)
    files = []
    for rec in raw.decode("utf-8", "replace").split("\0"):
        if len(rec) < 4:
            continue
        x, y, path = rec[0], rec[1], rec[3:]
        f = OrderedDict(path=path, status=x + y, kind=kind_of(path), staged=None, unstaged=None,
                        untracked=None, conflicted=False, formatting_only=False)
        if x == "U" or y == "U" or x + y in ("AA", "DD"):
            f["conflicted"] = True
        elif x + y == "??":
            f["untracked"] = {"lines": count_lines(os.path.join(repo, path))}
        else:
            if x in "MADT":
                a, d = staged_stat.get(path, (None, None))
                f["staged"] = {"added": a, "deleted": d, "whitespace_only": whitespace_only(staged_stat, staged_ws, path)}
            if y in "MDTA":
                a, d = unstaged_stat.get(path, (None, None))
                f["unstaged"] = {"added": a, "deleted": d,
                                 "whitespace_only": whitespace_only(unstaged_stat, unstaged_ws, path)}
            sides = [s for s in (f["staged"], f["unstaged"]) if s]
            f["formatting_only"] = bool(sides) and all(s["whitespace_only"] for s in sides)
        files.append(f)
    return files


def suggest_groups(files):
    groups = OrderedDict()
    for f in files:
        if f["conflicted"]:
            continue
        top = f["path"].split("/")[0] if "/" in f["path"] else "(root)"
        key = ("*", "formatting") if f["formatting_only"] else (top, f["kind"])
        groups.setdefault(key, []).append(f["path"])
    ordered = sorted(groups.items(), key=lambda kv: (KIND_ORDER.index(kv[0][1]), kv[0][0]))
    return [{"dir": k[0], "kind": k[1], "files": v} for k, v in ordered]


def share(n, total):
    return round(n / total, 2) if total else None


def read_style(repo, n):
    """House style from the last n non-merge commits; commits=0 if there is no history."""
    r = repo and git(repo, "log", "--no-merges", "-n", str(n), "--format=%x1e%B%x00%(trailers:only,unfold)", check=False)
    msgs = [] if not r or r.returncode else [m for m in r.stdout.decode("utf-8", "replace").split("\x1e") if m.strip()]
    counted, conv, common, area, caps, alpha, period, bodies = 0, 0, 0, 0, 0, 0, 0, 0
    types, scopes, trailers, lengths, trailer_names, recent = Counter(), Counter(), Counter(), [], {}, []
    for m in msgs:
        text, _, trailer_text = m.partition("\0")
        lines = text.strip("\n").split("\n")
        subject = lines[0].strip()
        if len(recent) < 8:
            recent.append(subject)
        if SKIP_SUBJECT_RE.match(subject):
            continue
        counted += 1
        lengths.append(len(subject))
        tlines = {t.strip() for t in trailer_text.split("\n") if t.strip()}
        keys = {}
        for t in tlines:  # trailer keys are case-insensitive; count each key once per commit
            keys.setdefault(t.split(":", 1)[0].strip().lower(), t.split(":", 1)[0].strip())
        for low, key in keys.items():
            trailers[trailer_names.setdefault(low, key)] += 1
        if any(ln.strip() and ln.strip() not in tlines for ln in lines[1:]):
            bodies += 1
        sm = SUBJECT_RE.match(subject)
        desc = describe(subject, sm)
        if sm:
            conv += 1
            t = sm.group("type").lower()
            types[t] += 1
            common += t in COMMON_TYPES
            if sm.group("scope"):
                scopes[sm.group("scope")] += 1
        if AREA_RE.match(subject):
            area += 1
        if desc[:1].isalpha():
            alpha += 1
            caps += desc[0].isupper()
        period += subject.endswith(".")
    common_s, area_s = share(common, counted) or 0, share(area, counted) or 0
    if not counted:
        verdict, deciding = "unknown", 0
    elif common_s >= 0.5:
        verdict, deciding = "conventional", common_s
    elif area_s >= 0.5:
        verdict, deciding = "area-prefix", area_s
    elif common_s >= 0.2:
        verdict, deciding = "mixed", common_s
    else:
        verdict, deciding = "free-form", 1 - max(common_s, area_s)
    if counted < 20:
        confidence = "low"
    elif counted >= 50 and (deciding >= 0.8 or max(common_s, area_s) < 0.1) and verdict != "mixed":
        confidence = "high"
    else:
        confidence = "medium"
    cap_s, per_s = share(caps, alpha), share(period, counted)
    return OrderedDict(
        commits=counted, verdict=verdict, confidence=confidence, conventional_share=share(conv, counted),
        common_type_share=share(common, counted), area_prefix_share=share(area, counted),
        types=OrderedDict(types.most_common()), scopes=OrderedDict(scopes.most_common(15)),
        median_subject_length=statistics.median(lengths) if lengths else None,
        capitalised_share=cap_s, capitalisation=habit(cap_s, "capitalised", "lowercase"),
        trailing_period_share=per_s, trailing_period=habit(per_s, "period", "no period"),
        body_share=share(bodies, counted), trailers=OrderedDict(trailers.most_common(8)), recent_subjects=recent)


def describe(subject, sm):
    """The subject without its `type(scope):` or `area:` prefix."""
    if sm:
        return sm.group("desc")
    return subject.split(": ", 1)[1] if AREA_RE.match(subject) else subject


def habit(s, yes, no):
    if s is None:
        return "unknown"
    return yes if s >= 0.8 else no if s <= 0.2 else "mixed"


def clean_message(text, edited):
    """Like git's cleanup: `strip` (comments and scissors dropped) for an edited file, else `whitespace`."""
    if edited:
        text = text.split(SCISSORS, 1)[0]
    lines = [ln.rstrip() for ln in text.split("\n") if not (edited and ln.startswith("#"))]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def lint(message, style, force_conventional, edited=False):
    lines = clean_message(message, edited)
    out = []

    def add(level, rule, line, msg):
        out.append(OrderedDict(level=level, rule=rule, line=line, message=msg))

    enforce = force_conventional or style["verdict"] == "conventional"
    if not lines:
        add("error", "empty", 1, "the message is empty")
        return out, enforce
    subject = lines[0]
    skip = SKIP_SUBJECT_RE.match(subject)  # git-generated subjects (merge, revert, fixup!) keep their length
    house = style.get("median_subject_length")
    if not skip and len(subject) > HARD_SUBJECT and house and house > HARD_SUBJECT:
        # A repo whose own subjects run long shouldn't fail on a convention it doesn't follow.
        add("warning", "subject-length", 1, "subject is %d chars; over %d, but this repo's median is %g"
            % (len(subject), HARD_SUBJECT, house))
    elif not skip and len(subject) > HARD_SUBJECT:
        add("error", "subject-length", 1, "subject is %d chars; keep it at %d or less (convention)"
            % (len(subject), HARD_SUBJECT))
    elif not skip and len(subject) > SOFT_SUBJECT:
        add("warning", "subject-length", 1, "subject is %d chars; git-commit docs suggest %d or less"
            % (len(subject), SOFT_SUBJECT))
    if len(lines) > 1 and lines[1].strip():
        add("error", "blank-line", 2, "line 2 must be blank: git takes everything up to the first blank line"
            " as the title")
    footer_start = len(lines)
    while footer_start > 1 and (FOOTER_RE.match(lines[footer_start - 1]) or BREAKING_RE.match(lines[footer_start - 1])):
        footer_start -= 1
    for i, ln in enumerate(lines[2:], 3):
        if len(ln) > BODY_WRAP and i - 1 < footer_start and not ln[:1].isspace() and "://" not in ln:
            add("warning", "body-wrap", i, "body line is %d chars; wrap at about %d (convention)" % (len(ln), BODY_WRAP))
    sm = SUBJECT_RE.match(subject)
    if enforce and not skip:
        if not sm:
            add("error", "conventional-subject", 1, "subject is not `type(scope)!: description` "
                "(Conventional Commits 1.0.0 items 1, 4, 5)")
        for i, ln in enumerate(lines[1:], 2):
            if LOOSE_BREAKING_RE.match(ln) and not BREAKING_RE.match(ln):
                add("error", "breaking-footer", i, "write `BREAKING CHANGE: <description>` (uppercase, colon, "
                    "space; BREAKING-CHANGE also allowed), items 12, 15, 16")
            elif BREAKING_RE.match(ln) and i > 2 and lines[i - 2].strip() and not FOOTER_RE.match(lines[i - 2]):
                add("warning", "breaking-footer", i, "footers go one blank line after the body (item 8)")
    if enforce and sm and style["types"] and sm.group("type").lower() not in style["types"] and not skip:
        add("warning", "unknown-type", 1, "type `%s` is not used in the last %d commits (used: %s)"
            % (sm.group("type"), style["commits"], ", ".join(list(style["types"])[:10])))
    if style["commits"] >= 20 and not skip:
        desc = describe(subject, sm)
        house = not enforce  # with --conventional the user chose CC; do not argue with that
        if house and style["verdict"] in ("free-form", "area-prefix") and sm and sm.group("type").lower() in COMMON_TYPES:
            add("warning", "house-prefix", 1, "the repo does not use Conventional Commits (%s); drop the `%s:` "
                "prefix" % (style["verdict"], sm.group("type")))
        elif house and style["verdict"] == "area-prefix" and not AREA_RE.match(subject):
            add("warning", "house-prefix", 1, "the repo's subjects start with the area changed, like `%s: ...`"
                % (list(style["types"])[0] if style["types"] else "pkg"))
        if desc[:1].isalpha() and style["capitalisation"] in ("capitalised", "lowercase"):
            if desc[0].isupper() != (style["capitalisation"] == "capitalised"):
                add("warning", "house-case", 1, "the repo's subjects are %s (%d%% capitalised)"
                    % (style["capitalisation"], round(100 * style["capitalised_share"])))
        if subject.endswith(".") and style["trailing_period"] == "no period":
            add("warning", "house-period", 1, "the repo's subjects do not end with a period")
        if not subject.endswith(".") and style["trailing_period"] == "period":
            add("warning", "house-period", 1, "the repo's subjects end with a period")
    return out, enforce


def fmt_counts(side):
    if side["added"] is None:
        return "binary"
    return "+%d -%d" % (side["added"], side["deleted"])


def print_facts(rep):
    files = rep["files"]
    print("Repo: %s  branch: %s" % (rep["repo_name"], rep["branch"] or "(detached or unborn)"))
    for title, pick in (("Staged", "staged"), ("Unstaged", "unstaged"), ("Untracked", "untracked")):
        sel = [f for f in files if f[pick]]
        print("\n%s (%d):" % (title, len(sel)))
        for f in sel[:200]:
            s = f[pick]
            counts = ("%s lines" % s["lines"] if s["lines"] is not None else "binary or large") \
                if pick == "untracked" else fmt_counts(s)
            ws = "  formatting only" if pick != "untracked" and s["whitespace_only"] else ""
            print("  %-8s %-14s %s%s" % (f["kind"], counts, f["path"], ws))
        if len(sel) > 200:
            print("  ... %d more (see --json)" % (len(sel) - 200))
    both = [f["path"] for f in files if f["staged"] and f["unstaged"]]
    conflicted = [f["path"] for f in files if f["conflicted"]]
    if both:
        print("\nStaged AND unstaged (commit picks the staged part only): " + ", ".join(both))
    if conflicted:
        print("\nConflicted (resolve before committing): " + ", ".join(conflicted))
    print("\nSuggested groups (top-level dir x kind; a starting point, not the plan):")
    for i, g in enumerate(rep["groups"], 1):
        shown = ", ".join(g["files"][:6]) + (" ... +%d" % (len(g["files"]) - 6) if len(g["files"]) > 6 else "")
        print("  %d. %s / %s: %s" % (i, g["dir"], g["kind"], shown))
    print_style(rep["style"])


def pct(x):
    return "-" if x is None else "%d%%" % round(100 * x)


def print_style(st):
    print("\nHouse style (last %d non-merge commits): %s, confidence %s" % (st["commits"], st["verdict"], st["confidence"]))
    if not st["commits"]:
        return
    print("  conventional shape %s, common CC types %s, area-prefix %s"
          % (pct(st["conventional_share"]), pct(st["common_type_share"]), pct(st["area_prefix_share"])))
    if st["types"]:
        label = "types" if st["verdict"] in ("conventional", "mixed") else "prefixes (CC shape, not CC types)"
        print("  %s: " % label + ", ".join("%s %d" % kv for kv in list(st["types"].items())[:12]))
    if st["scopes"]:
        print("  scopes: " + ", ".join("%s %d" % kv for kv in st["scopes"].items()))
    print("  subject: median %g chars, %s (%s capitalised), %s (%s end with .)"
          % (st["median_subject_length"], st["capitalisation"], pct(st["capitalised_share"]),
             st["trailing_period"], pct(st["trailing_period_share"])))
    print("  bodies: %s of commits" % pct(st["body_share"]))
    if st["trailers"]:
        print("  trailers (commits using each): " + ", ".join("%s %d" % kv for kv in st["trailers"].items()))
    print("  recent subjects (check for ticket keys, emoji or other habits the shares miss):")
    for subj in st["recent_subjects"]:
        print("    " + subj)


def main(argv=None):
    ap = argparse.ArgumentParser(usage=argparse.SUPPRESS, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-C", dest="repo", default=".")
    ap.add_argument("-n", type=int, default=100)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--conventional", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--lint", metavar="MESSAGE")
    mode.add_argument("--lint-file", metavar="PATH")
    a = ap.parse_args(argv)

    linting = a.lint is not None or a.lint_file is not None
    r = git(a.repo, "rev-parse", "--show-toplevel", check=False)
    root = r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else None
    if root is None and not linting:
        print("error: not a git repository: %s" % a.repo, file=sys.stderr)
        return 2
    try:
        style = read_style(root, max(a.n, 1))
        if linting:
            if a.lint_file is not None:
                try:
                    with open(a.lint_file, encoding="utf-8", errors="replace") as fh:
                        message = fh.read()
                except OSError as e:
                    print("error: cannot read %s: %s" % (a.lint_file, e.strerror), file=sys.stderr)
                    return 2
            else:
                message = a.lint
            findings, enforced = lint(message, style, a.conventional, edited=a.lint_file is not None)
            errors = sum(f["level"] == "error" for f in findings)
            if a.json:
                json.dump(OrderedDict(ok=not errors, errors=errors, warnings=len(findings) - errors,
                                      conventional_enforced=enforced, findings=findings, style=style),
                          sys.stdout, indent=2)
                print()
            else:
                why = "--conventional" if a.conventional else "repo style is %s" % style["verdict"]
                print("Lint: %s (%d errors, %d warnings); Conventional Commits %s (%s)"
                      % ("FAIL" if errors else "PASS", errors, len(findings) - errors,
                         "enforced" if enforced else "not enforced", why))
                for f in findings:
                    print("  %-7s L%-3d %-20s %s" % (f["level"], f["line"], f["rule"], f["message"]))
            return 1 if errors else 0
        files = collect_changes(root)
        b = git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False)
        rep = OrderedDict(repo_name=os.path.basename(root), branch=b.stdout.decode().strip() or None,
                          files=files, staged_and_unstaged=[f["path"] for f in files if f["staged"] and f["unstaged"]],
                          formatting_only=[f["path"] for f in files if f["formatting_only"]],
                          conflicted=[f["path"] for f in files if f["conflicted"]],
                          groups=suggest_groups(files), style=style)
    except GitError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if a.json:
        json.dump(rep, sys.stdout, indent=2)
        print()
    elif not files:
        print("Nothing to commit: no staged, unstaged or untracked changes.")
        print_style(style)
    else:
        print_facts(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
