#!/usr/bin/env python3
"""Print a repository's tech debt as a shop receipt: every TODO, FIXME, HACK, XXX, "temporary",
deprecation and lint/type-check suppression, aged with git blame and priced, plus a ranked list of
what to pay first. Stdlib only, read-only.

Usage: python3 debt_receipt.py [-C REPO] [--json] [--svg FILE] [--top N] [--items N]
                               [--currency usd|points] [--show-authors] [--max-blame N]
                               [--now YYYY-MM-DD] [--fail-over N] [PATH ...]
  -C REPO          repository to scan (default: current directory)
  PATH             only scan tracked files under these paths
  --json           machine-readable report on stdout instead of the receipt
  --svg FILE       also write the receipt as an SVG image
  --top N          items in the "pay these first" list (default 5)
  --items N        line items printed on the receipt, most expensive first (default 12, 0 = all)
  --currency       usd ("$12.40", default) or points ("12.4 pts")
  --show-authors   show who last touched each marker line and a per-author tally. Off by default:
                   the receipt only says how many people contributed
  --max-blame N    blame at most N markers per file (default 300); the rest are priced unaged
  --now DATE       date to age against (default today); makes receipts reproducible
  --fail-over N    exit 1 when TOTAL DEBT is over N (for CI budgets)

What it scans: files from `git ls-files` (so .gitignore is respected). Skipped: binaries, files over
2 MB, vendored directories (vendor/, node_modules/, third_party/ ...), lockfiles, minified files,
source maps and generated files (protobuf output, "@generated" / "DO NOT EDIT" headers).
Comments are found per language: # (Python, shell, Ruby, YAML ...), // and /* */ (C family, JS/TS,
Go, Rust, Java, Kotlin, Swift ...), -- (SQL, Lua, Haskell), <!-- --> (HTML, XML, Markdown, Vue).
"temporary" counts only next to fix, workaround, hack, solution, patch, measure, disabled,
removed, until or "for now". A marker inside a string literal is not counted. Not handled: Python docstrings (treated as
strings, so TODOs in them are missed), shell heredocs, regex literals, and a stray apostrophe in
prose (Vue templates, YAML) can hide a comment on the rest of that line.

Price per item = base x (1 + age in years) x (1 + commits to the file in the last 180 days / 10,
capped at 3) x 2 for a blanket suppression (no rule named); items that link a ticket (#123,
ABC-45, gh-7 or a URL) get 50% off. Age comes from `git blame -w` of the marker line, so it is the
date the line last changed, not always the date the marker was first written.

Exit codes: 0 receipt printed (debt or not)
            1 --fail-over N given and TOTAL DEBT is over N
            2 not a git repository, a git error, or no scannable files
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import subprocess
import sys
from xml.sax.saxutils import escape

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
WIDTH = 42
MAX_BYTES = 2 * 1024 * 1024
CHURN_DAYS = 180

BASE = {"FIXME": 8, "HACK": 8, "XXX": 6, "TEMPORARY": 5, "DEPRECATED": 4, "TODO": 3, "SUPPRESS": 4}
DEBT_ORDER = ["FIXME", "HACK", "XXX", "TEMPORARY", "TODO"]  # one debt item per line, highest first
SUPPRESS_BASE = {"@ts-ignore": 6, "@ts-nocheck": 6, "@ts-expect-error": 3, "pragma: no cover": 2}

# --- languages -------------------------------------------------------------------------------
# family -> (line comment starts, block comment pairs, string delimiters)
C_STR = ['"""', "`", '"', "'"]
FAMILIES = {
    "hash": (["#"], [], ['"""', "'''", '"', "'"]),
    "c": (["//"], [("/*", "*/")], C_STR),
    "rust": (["//"], [("/*", "*/")], ['"']),  # 'a lifetimes would look like strings
    "php": (["//", "#"], [("/*", "*/")], ['"', "'"]),
    "sql": (["--"], [("/*", "*/")], ["'", '"']),
    "lua": (["--"], [("--[[", "]]")], ['"', "'"]),
    "haskell": (["--"], [("{-", "-}")], ['"']),
    "html": ([], [("<!--", "-->")], []),
    "markdown": ([], [("<!--", "-->")], ["`"]),  # code spans and fences are examples, not debt
    "vue": (["//"], [("<!--", "-->"), ("/*", "*/")], ["`", '"']),  # no ' : prose apostrophes
}
MULTILINE_STRINGS = {'"""', "'''", "`"}
EXT = {}
for _fam, _exts in {
    "hash": "py pyi pyx rb rake sh bash zsh fish ksh pl pm r yaml yml toml cfg conf tf tfvars hcl nim ex exs "
            "jl cmake ps1 psm1 nix coffee mk properties bzl star gemspec dockerfile",
    "c": "c h cc cpp cxx hpp hh hxx m mm cs java kt kts scala sc go swift js jsx mjs cjs ts tsx mts cts "
         "dart groovy gradle proto sol zig css scss sass less fs fsx v d",
    "rust": "rs", "php": "php", "sql": "sql", "lua": "lua", "haskell": "hs elm",
    "html": "html htm xml xhtml", "markdown": "md markdown mdx", "vue": "vue svelte astro",
}.items():
    for _e in _exts.split():
        EXT[_e] = _fam
NAMES = {"makefile": "hash", "gnumakefile": "hash", "dockerfile": "hash", "containerfile": "hash",
         "rakefile": "hash", "gemfile": "hash", "cmakelists.txt": "hash", "build": "hash",
         "workspace": "hash", "jenkinsfile": "c", "vagrantfile": "hash", "procfile": "hash"}
ANNOTATED_DEPRECATION = {"java", "kt", "kts", "scala", "groovy"}  # count @Deprecated, not the doc tag

VENDOR_DIRS = {"vendor", "vendored", "node_modules", "third_party", "third-party", "thirdparty",
               "bower_components", "Pods", "Carthage", ".yarn", "site-packages", "dist", ".next"}
LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock",
             "Cargo.lock", "poetry.lock", "uv.lock", "Pipfile.lock", "Gemfile.lock", "composer.lock",
             "go.sum", "flake.lock", "mix.lock", "pubspec.lock", "Podfile.lock", "packages.lock.json"}
GENERATED_NAME = re.compile(r"(\.min\.(js|css)|\.map|_pb2(_grpc)?\.py|\.pb\.go|\.pb\.(cc|h)|\.g\.dart|"
                            r"\.generated\.\w+|\.designer\.cs|\.lock)$", re.I)
GENERATED_HEADER = re.compile(r"@generated|DO NOT EDIT|<auto-generated|^\W*(this file (is|was) )?"
                              r"(auto-?generated|generated (by|from))\b", re.I)

# --- markers ---------------------------------------------------------------------------------
# A marker wrapped in quotes or slashes ("TODO", `FIXME`, TODO/FIXME) is a mention, not debt.
DEBT_RE = re.compile(r"(?<![\"'`/])(?:\b(TODO|FIXME|HACK|XXX)\b|\b(todo|fixme|hack|Todo|Fixme|Hack)(?=\s*[:(]))"
                     r"(?![\"'`/])")
TEMP_RE = re.compile(r"\btemporar(?:y|ily)(?:\s+\w+)?\s+(?:fix|workaround|hack|solution|patch|measure|"
                     r"disabled?|removed?|until|for now)\b", re.I)
DEPRECATED_TAG = re.compile(r"@deprecated\b", re.I)
COMMENT_SUPPRESS = [  # (label, regex, function(match) -> blanket?)
    ("eslint-disable", re.compile(r"\beslint-disable(?:-next-line|-line)?\b(.*)"),
     lambda m: not m.group(1).split("--")[0].strip(" *")),
    ("@ts-ignore", re.compile(r"@ts-ignore\b"), lambda m: False),
    ("@ts-nocheck", re.compile(r"@ts-nocheck\b"), lambda m: True),
    ("@ts-expect-error", re.compile(r"@ts-expect-error\b"), lambda m: False),
    ("noqa", re.compile(r"\bnoqa\b(:\s*[A-Z]+[0-9]+)?", re.I), lambda m: not m.group(1)),
    ("type: ignore", re.compile(r"\btype:\s*ignore\b(\[[^\]]+\])?"), lambda m: not m.group(1)),
    ("nolint", re.compile(r"^nolint\b(:(\S+))?"), lambda m: not m.group(2) or m.group(2) == "all"),
    ("pylint: disable", re.compile(r"\bpylint:\s*disable\s*=\s*([\w,-]+)"), lambda m: m.group(1) == "all"),
    ("rubocop:disable", re.compile(r"\brubocop:disable\s+(\S+)"), lambda m: m.group(1) == "all"),
    ("pragma: no cover", re.compile(r"\bpragma:\s*no\s*cover\b"), lambda m: False),
]
CODE_SUPPRESS = [
    ("@SuppressWarnings", re.compile(r"@SuppressWarnings\b"), lambda m: False),
    ("@Suppress", re.compile(r"@Suppress\("), lambda m: False),
    ("#[allow]", re.compile(r"#!?\[allow\(([^)]*)\)"), lambda m: "warnings" in m.group(1)),
]
CODE_DEPRECATED = re.compile(r"@[Dd]eprecated\b|#\[deprecated\b")
TICKET_RE = re.compile(r"https?://\S+|(?<![\w&])#\d+\b|\bgh-\d+\b|"
                       r"\b(?!(?:UTF|ISO|SHA|MD|RFC|AES|RSA|TLS|SSL|HTTP|IPV|PEP|ES|CP|WIN|XEP|ECMA|IEEE)-)[A-Z][A-Z0-9]{1,9}-\d+\b")

ACTIONS = {
    "FIXME": "fix it, or file a ticket and link it in the comment",
    "HACK": "replace the workaround, or ticket it and write down why it is still needed",
    "XXX": "decide what the XXX is warning about; fix it or turn it into a ticket",
    "TEMPORARY": "check whether the temporary code became permanent; make it real or remove it",
    "TODO": "do it, ticket it, or delete it once the owner confirms it is obsolete",
    "DEPRECATED": "find the remaining callers; remove the deprecated code when there are none",
}


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, text=True,
                       errors="replace")
    if check and r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args[:2]), r.stderr.strip()))
    return r.stdout


# --- scanning --------------------------------------------------------------------------------
def family_for(path):
    base = os.path.basename(path).lower()
    if base in NAMES:
        return NAMES[base]
    if base.startswith("dockerfile") or base.startswith("containerfile"):
        return "hash"
    return EXT.get(base.rsplit(".", 1)[-1]) if "." in base else None


def skip_reason(path):
    parts = path.split("/")
    if any(p in VENDOR_DIRS for p in parts[:-1]):
        return "vendored"
    if parts[-1] in LOCKFILES:
        return "lockfile"
    if GENERATED_NAME.search(parts[-1]):
        return "generated"
    if family_for(path) is None:
        return "no comment syntax"
    return None


def split_line(line, fam, state):
    """Split one line into comment text and code text. state carries an open block comment or a
    multi-line string into the next line. Returns (comments, code, state)."""
    line_starts, blocks, strings = FAMILIES[fam]
    comments, code, i, n = [], [], 0, len(line)
    while i < n:
        if state:
            kind, end = state
            if kind == "block":
                j = line.find(end, i)
                if j < 0:
                    comments.append(line[i:])
                    return comments, "".join(code), state
                comments.append(line[i:j])
                i, state = j + len(end), None
                continue
            j = i
            while j < n and not line.startswith(end, j):
                j += 2 if line[j] == "\\" else 1
            if j >= n:
                return comments, "".join(code), state if end in MULTILINE_STRINGS else None
            i, state = j + len(end), None
            continue
        hit = next((b for b in blocks if line.startswith(b[0], i)), None)
        if hit:
            state, i = ("block", hit[1]), i + len(hit[0])
            continue
        lc = next((s for s in line_starts if line.startswith(s, i)), None)
        if lc:
            comments.append(line[i + len(lc):])
            return comments, "".join(code), None
        q = next((s for s in strings if line.startswith(s, i)), None)
        if q:
            state, i = ("str", q), i + len(q)
            continue
        code.append(line[i])
        i += 1
    if state and state[0] == "str" and state[1] not in MULTILINE_STRINGS:
        state = None
    return comments, "".join(code), state


def find_markers(text, path):
    """Yield (kind, label, blanket, comment_text) for each marker in a file, with 1-based line."""
    fam = family_for(path)
    ext = os.path.basename(path).rsplit(".", 1)[-1].lower()
    state, out = None, []
    for no, line in enumerate(text.split("\n"), 1):  # not splitlines(): \f or U+2028 would shift line numbers
        line = line.rstrip("\r")
        comments, code, state = split_line(line, fam, state)
        ctext = " ".join(c.strip() for c in comments).strip()
        found = []
        if ctext:
            kinds = {(m.group(1) or m.group(2)).upper() for m in DEBT_RE.finditer(ctext)}
            if TEMP_RE.search(ctext):
                kinds.add("TEMPORARY")
            if kinds:
                kind = next(k for k in DEBT_ORDER if k in kinds)
                found.append((kind, kind, False))
            if ext not in ANNOTATED_DEPRECATION and DEPRECATED_TAG.search(ctext):
                found.append(("DEPRECATED", "@deprecated", False))
            for c in comments:
                c = c.rstrip()  # keep the leading space: "// nolint" is prose, "//nolint" a directive
                for label, rx, blanket in COMMENT_SUPPRESS:
                    m = rx.search(c)
                    if m:
                        found.append(("SUPPRESS", label, blanket(m)))
        if code.strip():
            for label, rx, blanket in CODE_SUPPRESS:
                m = rx.search(code)
                if m:
                    found.append(("SUPPRESS", label, blanket(m)))
            if CODE_DEPRECATED.search(code) and not any(k == "DEPRECATED" for k, _, _ in found):
                found.append(("DEPRECATED", "@deprecated", False))
        for kind, label, blanket in found:
            out.append({"kind": kind, "label": label, "blanket": blanket, "line": no,
                        "text": (ctext or code.strip())[:120]})
    return out


def read_text(repo, path):
    """Return (text, skip_reason). Binary, huge, minified and generated files are skipped."""
    full = os.path.join(repo, path)
    try:
        if not os.path.isfile(full):
            return None, "missing"
        if os.path.getsize(full) > MAX_BYTES:
            return None, "too large"
        with open(full, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None, "unreadable"
    if b"\0" in raw[:8192]:
        return None, "binary"
    text = raw.decode("utf-8", errors="replace")
    head = text.splitlines()[:20]
    if any(GENERATED_HEADER.search(h) for h in head[:5]):
        return None, "generated"
    if head and sum(len(h) for h in head) / len(head) > 300:
        return None, "minified"
    return text, None


# --- git facts -------------------------------------------------------------------------------
def blame(repo, path, lines, ignore_revs):
    """Map line -> (sha, author, unix time) for the given lines, in one git blame call."""
    args = ["blame", "--porcelain", "-w"]
    if ignore_revs:
        args += ["--ignore-revs-file", ignore_revs]
    for ln in lines:
        args += ["-L", "%d,%d" % (ln, ln)]
    try:
        out = git(repo, *args, "--", path)
    except RuntimeError:
        return {}  # e.g. a file staged but never committed
    res, info, cur = {}, {}, None
    for row in out.splitlines():
        if row.startswith("\t"):
            continue
        parts = row.split(" ")
        if len(parts) in (3, 4) and len(parts[0]) in (40, 64) and all(c in "0123456789abcdef" for c in parts[0]):
            cur = parts[0]
            info.setdefault(cur, {})
            res[int(parts[2])] = cur
        elif cur and parts[0] in ("author", "author-time"):
            info[cur][parts[0]] = row.split(" ", 1)[1] if len(parts) > 1 else ""
    return {ln: (sha, info[sha].get("author", ""), int(info[sha].get("author-time", 0) or 0))
            for ln, sha in res.items()}


def churn(repo, now):
    since = (now - dt.timedelta(days=CHURN_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    out = git(repo, "log", "--since=" + since, "--until=" + now.strftime("%Y-%m-%dT%H:%M:%S"),
              "--format=", "--name-only", "--no-renames", check=False)
    counts = {}
    for name in out.splitlines():
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


# --- pricing ---------------------------------------------------------------------------------
def price(item):
    base = SUPPRESS_BASE.get(item["label"], BASE[item["kind"]])
    years = (item["age_days"] or 0) / 365.25
    p = base * (1 + years) * (1 + min(item["churn_180d"], 20) / 10.0)
    if item["blanket"]:
        p *= 2
    return round(p, 2)


def age_text(days):
    if days is None:
        return "unaged"
    if days < 1:
        return "today"
    y, rest = divmod(int(days), 365)
    m = rest // 30
    if y:
        return "%dy %dm" % (y, m)
    return "%dm %dd" % (m, rest % 30) if m else "%dd" % rest


def money(v, currency):
    return ("-" if v < 0 else "") + ("$%.2f" % abs(v) if currency == "usd" else "%.1f pts" % abs(v))


def reasons(it):
    r = []
    if it["age_days"] is not None and it["age_days"] >= 365:
        r.append("%s old" % age_text(it["age_days"]))
    if it["churn_180d"]:
        n = it["churn_180d"]
        r.append("file changed %d time%s in %d days" % (n, "" if n == 1 else "s", CHURN_DAYS))
    if it["kind"] in ("FIXME", "HACK", "XXX") and not it["ticket"]:
        r.append("no ticket")
    if it["blanket"]:
        r.append("blanket suppression, hides every rule")
    return r or ["highest price left"]


def action(it):
    if it["kind"] != "SUPPRESS":
        return ACTIONS[it["kind"]]
    if it["label"] == "@ts-ignore":
        return "fix the type error, or use @ts-expect-error so it fails once the error is gone"
    if it["blanket"]:
        return "name the one rule it is for, or fix the warning and delete the comment"
    return "delete it, run the linter, and fix the warning if it still fires"


# --- report ----------------------------------------------------------------------------------
def build(repo, paths, now, max_blame, show_authors, top):
    files = [f for f in git(repo, "ls-files", "-z", "--", *paths).split("\0") if f]
    skipped, items, scanned = {}, [], 0
    for path in files:
        why = skip_reason(path)
        text = None
        if not why:
            text, why = read_text(repo, path)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        scanned += 1
        for it in find_markers(text, path):
            it["path"] = path
            items.append(it)
    ignore = os.path.join(repo, ".git-blame-ignore-revs")
    ignore = ignore if os.path.isfile(ignore) else None
    by_file, capped = {}, []
    for it in items:
        by_file.setdefault(it["path"], []).append(it)
    jobs = {}
    for path, its in by_file.items():
        lines = sorted({it["line"] for it in its})
        if max_blame and len(lines) > max_blame:
            capped.append(path)
            lines = lines[:max_blame]
        jobs[path] = lines
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        blamed = dict(zip(jobs, pool.map(lambda p: blame(repo, p, jobs[p], ignore), jobs)))
    hot = churn(repo, now)
    now_ts = now.timestamp()
    authors = {}
    for it in items:
        b = blamed.get(it["path"], {}).get(it["line"])
        committed = b and not set(b[0]) <= {"0"}
        if committed:
            it["age_days"] = max(0, int((now_ts - b[2]) // 86400))
            it["date"] = dt.datetime.fromtimestamp(b[2], dt.timezone.utc).strftime("%Y-%m-%d")
            it["commit"] = b[0][:10]
            authors[b[1]] = authors.get(b[1], 0) + 1
            if show_authors:
                it["author"] = b[1]
        else:  # uncommitted line (age 0) or beyond --max-blame (unaged, priced as new)
            it["age_days"] = 0 if b or it["line"] in jobs.get(it["path"], []) else None
            it["date"] = None
            it["commit"] = None
        it["churn_180d"] = hot.get(it["path"], 0)
        t = TICKET_RE.search(it["text"])
        it["ticket"] = t.group(0) if t else None
        it["full_price"] = price(it)
        it["price"] = round(it["full_price"] / 2, 2) if it["ticket"] else it["full_price"]
    items.sort(key=lambda i: (-i["price"], -(i["age_days"] or 0), i["path"], i["line"]))
    kinds = {}
    for it in items:
        k = kinds.setdefault(it["kind"], {"count": 0, "price": 0.0})
        k["count"] += 1
        k["price"] = round(k["price"] + it["full_price"], 2)
    subtotal = round(sum(i["full_price"] for i in items), 2)
    total = round(sum(i["price"] for i in items), 2)
    head = git(repo, "rev-parse", "--short", "HEAD", check=False).strip() or "no commits"
    pay = [dict(path=i["path"], line=i["line"], kind=i["kind"], label=i["label"], price=i["price"],
                text=i["text"], reasons=reasons(i), action=action(i)) for i in items[:top]]
    report = {
        "repo": os.path.basename(os.path.abspath(repo)), "head": head, "now": now.strftime("%Y-%m-%d"),
        "files_scanned": scanned, "files_skipped": skipped, "items": items, "by_kind": kinds,
        "subtotal": subtotal, "ticket_discount": round(subtotal - total, 2), "total": total,
        "tracked": sum(1 for i in items if i["ticket"]), "contributors": len(authors),
        "blame_capped_files": capped, "pay_first": pay,
        "formula": "base x (1 + age in years) x (1 + commits in %dd / 10, max 3) x 2 if blanket "
                   "suppression; 50%% off with a ticket link" % CHURN_DAYS,
        "base_points": dict(BASE, **SUPPRESS_BASE),
    }
    if show_authors:
        report["authors"] = authors
    return report


def two_col(left, right):
    room = WIDTH - len(right) - 1
    return left[:room].ljust(room) + " " + right


def center(s):
    return s[:WIDTH].center(WIDTH).rstrip()


def receipt(rep, currency, max_items, show_authors):
    eq, dash = "=" * WIDTH, "-" * WIDTH
    out = [eq, center("TECH DEBT RECEIPT"), center("%s @ %s" % (rep["repo"], rep["head"])),
           center("%s  %d files scanned" % (rep["now"], rep["files_scanned"])), dash]
    items = rep["items"] if not max_items else rep["items"][:max_items]
    for it in items:
        name = "%s %s" % (it["kind"], it["label"]) if it["kind"] == "SUPPRESS" else it["kind"]
        if it["blanket"]:
            name += " (all)"
        out.append(two_col("1x " + name, money(it["full_price"], currency)))
        tail = "  " + ("aged " + age_text(it["age_days"]) if it["age_days"] is not None else "unaged")
        loc = "%s:%d" % (it["path"], it["line"])
        room = WIDTH - 3 - len(tail)
        if len(loc) > room:
            loc = "..." + loc[-(room - 3):]
        out.append("   " + loc + tail)
        if it["ticket"]:
            out.append("   ticket " + it["ticket"][:WIDTH - 10])
        if show_authors and it.get("author"):
            out.append(("   by " + it["author"])[:WIDTH])
    rest = len(rep["items"]) - len(items)
    if rest:
        out.append(center("... and %d more item%s" % (rest, "" if rest == 1 else "s")))
    if not rep["items"]:
        out.append(center("nothing owed. suspicious."))
    out.append(dash)
    for kind in sorted(rep["by_kind"], key=lambda k: -rep["by_kind"][k]["price"]):
        k = rep["by_kind"][kind]
        out.append(two_col("%-10s x%d" % (kind, k["count"]), money(k["price"], currency)))
    out.append(dash)
    out.append(two_col("SUBTOTAL", money(rep["subtotal"], currency)))
    if rep["ticket_discount"]:
        out.append(two_col("TICKET DISCOUNT (%d tracked)" % rep["tracked"],
                           money(-rep["ticket_discount"], currency)))
    out.append(two_col("TOTAL DEBT", money(rep["total"], currency)))
    out.append(dash)
    aged = [i for i in rep["items"] if i["age_days"]]
    if aged:
        old = max(aged, key=lambda i: i["age_days"])
        out.append(("oldest: %s (%s)" % (os.path.basename(old["path"]), age_text(old["age_days"])))[:WIDTH])
    if show_authors and rep.get("authors"):
        out.append("cashiers:")
        for name, n in sorted(rep["authors"].items(), key=lambda a: -a[1]):
            out.append(two_col("  " + name, "x%d" % n))
    else:
        out.append("contributors: %d (names hidden)" % rep["contributors"])
    if rep["blame_capped_files"]:
        out.append(("aging capped in %d file(s), --max-blame" % len(rep["blame_capped_files"]))[:WIDTH])
    out += ["price = base x (1 + age in years)",
            "      x (1 + commits in 180d / 10, max 3)",
            "      x 2 if a blanket suppression",
            "      x 0.5 if it links a ticket",
            "base: FIXME/HACK 8, XXX 6, TEMPORARY 5,",
            "      DEPRECATED 4, TODO 3, SUPPRESS 2-6",
            dash, center("THANK YOU FOR SHOPPING AT GIT"),
            center("NO REFUNDS. INTEREST ACCRUES DAILY."), "", center(barcode(rep["head"])), eq]
    return out


def barcode(seed):
    bars = "".join("|" * (1 + int(c, 16) % 3) + " " for c in seed if c in "0123456789abcdef")
    return (bars or "| || |||")[:WIDTH - 6]


def plan_text(rep, currency):
    if not rep["pay_first"]:
        return []
    out = ["", "PAY THESE FIRST"]
    for n, p in enumerate(rep["pay_first"], 1):
        out.append("%2d. %s:%d  %s  %s" % (n, p["path"], p["line"], p["label"], money(p["price"], currency)))
        out.append("    why: " + "; ".join(p["reasons"]))
        out.append("    do:  " + p["action"])
        out.append("    > " + p["text"][:100])
    return out


def svg(lines):
    lh, cw, pad = 17, 7.8, 22
    w = int(WIDTH * cw + 2 * pad)
    h = int(len(lines) * lh + 2 * pad + 10)
    teeth = " ".join("%d,%d" % (x, h - (6 if (x // 10) % 2 else 0)) for x in range(w, -1, -10))
    paper = "0,0 %d,0 %s" % (w, teeth)
    # no-break spaces: some renderers ignore xml:space and would collapse the receipt's columns
    rows = "\n".join('<text x="%d" y="%d">%s</text>' % (pad, pad + 12 + n * lh, escape(line).replace(" ", "\u00a0"))
                     for n, line in enumerate(lines))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'xml:space="preserve">\n<title>Tech debt receipt</title>\n'
            '<polygon points="%s" fill="#fbfaf5" stroke="#d6d3c4"/>\n'
            '<g font-family="ui-monospace, Menlo, Consolas, monospace" font-size="13" fill="#222">\n'
            '%s\n</g>\n</svg>\n') % (w, h + 4, w, h + 4, paper, rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n\n".join(__doc__.split("\n\n")[1:]))
    ap.add_argument("-C", dest="repo", default=".")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--receipt", action="store_true", help="print the receipt (default)")
    ap.add_argument("--svg")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--items", type=int, default=12)
    ap.add_argument("--currency", choices=["usd", "points"], default="usd")
    ap.add_argument("--show-authors", action="store_true")
    ap.add_argument("--max-blame", type=int, default=300)
    ap.add_argument("--now")
    ap.add_argument("--fail-over", type=float)
    a = ap.parse_args(argv)
    now = (dt.datetime.strptime(a.now, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc) if a.now
           else dt.datetime.now().astimezone())
    try:
        top = git(a.repo, "rev-parse", "--show-toplevel").strip()
        rel = [os.path.relpath(os.path.realpath(os.path.join(a.repo, p)), os.path.realpath(top))
               for p in a.paths]
        rep = build(top, rel, now, a.max_blame, a.show_authors, a.top)
    except (RuntimeError, OSError) as e:
        print("debt_receipt: not a git repository or git failed: %s" % e, file=sys.stderr)
        return 2
    if not rep["files_scanned"]:
        print("debt_receipt: no scannable files (skipped: %s)" % (rep["files_skipped"] or "none tracked"),
              file=sys.stderr)
        return 2
    lines = receipt(rep, a.currency, a.items, a.show_authors)
    if a.svg:
        with open(a.svg, "w", encoding="utf-8") as fh:
            fh.write(svg(lines))
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print("\n".join(lines + plan_text(rep, a.currency)))
    if a.fail_over is not None and rep["total"] > a.fail_over:
        print("debt_receipt: TOTAL DEBT %s is over the budget of %s" % (
            money(rep["total"], a.currency), money(a.fail_over, a.currency)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
