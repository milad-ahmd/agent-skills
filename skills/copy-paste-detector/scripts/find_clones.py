#!/usr/bin/env python3
"""Find copy-pasted code blocks and group them into clone families. Stdlib only, read-only.

Usage: python3 find_clones.py [PATH] [--min-lines 6] [--min-tokens 40] [--ext py,js,ts,...]
                              [--exclude GLOB ...] [--top 10] [--json] [--svg card.svg] [--show-paths]
                              [--fail-over PCT]
  PATH            directory (or one file) to scan (default: current directory)
  --min-lines N   a clone must span at least N code lines (default 6)
  --min-tokens N  ... and at least N tokens, so runs of tiny lines like `x = 1` don't count (default 40)
  --ext LIST      comma-separated extensions to scan (default: common source languages, see below)
  --exclude GLOB  skip paths or names matching GLOB (repeatable), e.g. 'src/generated/*'
  --top N         families shown in the text report (default 10; --json always has all of them)
  --json          machine-readable report on stdout
  --svg FILE      write a shareable 1200x630 card with the totals (no file paths unless --show-paths)
  --no-credit     leave the "made with agent-skills" line off the card
  --show-paths    put file names on the SVG card
  --fail-over PCT exit 1 only when exact duplication is over PCT percent of code lines (for CI)

How it works:
  tokens     each file is split into tokens; comments (#, //, /* */, -- by language) and whitespace go.
             Lines with no letters or digits (`}`, `});`) and import/package/include lines are left out
             of matching, so closing braces and import headers never make a clone on their own.
  exact      a hash of every window of N consecutive code lines; equal windows in two places are
             extended into the longest matching block, then overlapping matches are grouped into
             families (connected copies of the same block).
  renamed    the same pass with every identifier, string and number replaced by a placeholder
             (keywords kept), which finds copies where only names or literals were changed. Reported
             as a second tier; families already covered by exact copies are dropped from it.
  drift      two matches of the same pair of files separated by at most 3 changed or inserted lines are
             joined into one copy. Each copy is compared with the most common version in its family:
             same, renamed (only names or literals differ), drifted (lines added, removed or changed) or
             partial (shares under half of the block: it overlaps the family rather than copying it).
             Lines made only of literals (table rows) keep their literals in the renamed pass.
  skipped    hidden dirs, node_modules, vendor, third_party, dist, build, target, out, coverage, venv,
             __pycache__, migrations, test fixtures (fixtures, __fixtures__, testdata, __snapshots__),
             minified and generated files (*.min.js, *.pb.go, *_pb2.py, *.generated.*, *.g.dart),
             lockfiles (not source extensions) and files over 1 MB.

Default extensions: py js jsx ts tsx mjs cjs go java kt kts scala groovy c h cc cpp cxx hpp cs rs swift
dart php rb sh lua m mm. Others passed with --ext are read with // and /* */ comments.

Numbers: code lines are non-blank, non-comment lines. Duplicated lines are code lines inside any copy
(every copy counts, the first one too). Removable lines are what merging each family to one copy saves.

Exit codes: 0 no clone families found, or --fail-over given and exact duplication is at or under PCT
            1 clone families found, or --fail-over given and exact duplication is over PCT
            2 bad arguments, PATH missing, or no source files to scan
"""
from __future__ import annotations

import argparse
import bisect
import difflib
import fnmatch
import itertools
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from xml.sax.saxutils import escape

DEFAULT_EXT = ("py js jsx ts tsx mjs cjs go java kt kts scala groovy c h cc cpp cxx hpp cs rs swift dart php rb sh "
               "lua m mm").split()
HASH_COMMENT = {"py", "rb", "sh", "bash", "zsh", "pl", "r", "ex", "exs", "nim", "cr", "jl", "tcl"}
DASH_COMMENT = {"lua", "sql", "hs", "elm", "ada"}
BOTH_COMMENT = {"php"}
SKIP_DIRS = {"node_modules", "vendor", "third_party", "third-party", "dist", "build", "target", "out", "coverage",
             "venv", "env", "__pycache__", "bower_components", "Pods", "site-packages", "migrations",
             "fixtures", "__fixtures__", "testdata", "__snapshots__", "generated", "__generated__"}
GENERATED = ["*.min.js", "*.min.mjs", "*.pb.go", "*_pb2.py", "*_pb2_grpc.py", "*.generated.*", "*.g.dart",
             "*.pb.cc", "*.pb.h", "*_generated.go"]
MAX_BYTES = 1000000
DRIFT_GAP = 3      # changed or inserted lines allowed inside one copy before it splits in two
MAX_BUCKET = 60    # shortcut: a window seen more often than this pairs only with its first copy, not all pairs
COVERED = 0.8      # a renamed copy this much covered by exact copies adds nothing new
SAME_SPAN = 0.6   # two matches in one file are the same copy when they overlap this much of the longer one
KEYWORDS = set("""
if else elif for while do return def function func fn class struct interface enum new try catch except
finally raise throw throws switch case default break continue import from export const let var static
public private protected async await yield lambda in is not and or None null nil true false True False
this self super with as pass go defer chan select type package void int string bool float double long
char byte final override val end then begin unless until match impl trait pub mut use mod where
""".split())
IMPORT_HEADS = {"import", "package", "using", "use"}
LITERALS = {"S", "N", "None", "null", "nil", "true", "false", "True", "False"}
TEST_RX = re.compile(r"(^|/)(tests?|__tests__|specs?)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$"
                     r"|(Test|Tests|Spec)\.(java|kt|cs|scala|swift)$")

_STR = r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`'
_TAIL = r"|(?P<s>%s)|(?P<n>\d[\w.]*)|(?P<i>[^\W\d][\w$]*|\$[\w$]*)|(?P<p>\S)" % _STR
TOKEN_RX = {
    "slash": re.compile(r"(?P<c>/\*[\s\S]*?\*/|//[^\n]*)" + _TAIL),
    "hash": re.compile(r"(?P<c>#[^\n]*)" + _TAIL),
    "dash": re.compile(r"(?P<c>--[^\n]*)" + _TAIL),
    "both": re.compile(r"(?P<c>/\*[\s\S]*?\*/|//[^\n]*|#(?!\[)[^\n]*)" + _TAIL),
}


class Source:
    """One file as code lines: exact text, renamed text, token count and original line span of each."""

    def __init__(self, path, text):
        ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
        style = ("hash" if ext in HASH_COMMENT else "dash" if ext in DASH_COMMENT else
                 "both" if ext in BOTH_COMMENT else "slash")
        self.path = path
        self.raw = text.split("\n")
        by_line = defaultdict(list)          # start line -> [(exact token, renamed token, end line)]
        line = 1
        last = held = held_end = 0
        for m in TOKEN_RX[style].finditer(text):
            line += text.count("\n", last, m.start())
            last = m.start()
            kind = m.lastgroup
            if kind == "c":
                continue
            tok = m.group()
            norm = ("S" if kind == "s" else "N" if kind == "n" else
                    tok if kind == "p" or tok in KEYWORDS else "I")
            if line > held_end:      # tokens after a multi-line string stay on the line where it started
                held, held_end = line, line + tok.count("\n")
            else:
                held_end = max(held_end, line + tok.count("\n"))
            by_line[held].append((tok, norm, line + tok.count("\n")))
        self.code_lines = sorted(by_line)    # non-blank, non-comment line numbers
        self.exact, self.renamed, self.tokens, self.first, self.last = [], [], [], [], []
        in_go_imports = False
        for ln in self.code_lines:
            toks = by_line[ln]
            words = [t[0] for t in toks]
            if in_go_imports:
                in_go_imports = words != [")"]
                continue
            if words == ["import", "("]:
                in_go_imports = True
                continue
            if (words[0] in IMPORT_HEADS or words[:2] == ["#", "include"]
                    or (words[0] == "from" and "import" in words)
                    or not any(c.isalnum() for w in words for c in w)):
                continue
            norms = [t[1] for t in toks]
            self.exact.append(" ".join(words))
            # a line of only literals is table data and keeps its literals: with placeholders, rows like
            # ("a", 1), would make every table a renamed copy of every other one
            data = all(n in LITERALS or not n[0].isalnum() for n in norms)
            self.renamed.append(self.exact[-1] if data else " ".join(norms))
            self.tokens.append(len(toks))
            self.first.append(ln)
            self.last.append(max(t[2] for t in toks))
        self.tok_prefix = [0] + list(itertools.accumulate(self.tokens))

    def span(self, s, e):
        """Original (first line, last line) of code lines s..e-1."""
        return self.first[s], self.last[e - 1]

    def count_code(self, s, e):
        a, b = self.span(s, e)
        return bisect.bisect_right(self.code_lines, b) - bisect.bisect_left(self.code_lines, a)


def is_test(path):
    return bool(TEST_RX.search(path.replace(os.sep, "/")))


def collect(root, exts, excludes):
    """Return [(relative path, text)] of source files under root, skipping vendored and generated code."""
    exts = {e.lower().lstrip(".") for e in exts}

    def skipped(rel, name):
        return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g) for g in list(excludes) + GENERATED)

    paths = []
    if os.path.isfile(root):
        paths.append((os.path.basename(root), root))
    else:
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS
                             and not x.endswith(".egg-info")
                             and not skipped(os.path.relpath(os.path.join(d, x), root).replace(os.sep, "/"), x))
            for f in sorted(files):
                full = os.path.join(d, f)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                if "." in f and f.rsplit(".", 1)[1].lower() in exts and not skipped(rel, f):
                    paths.append((rel, full))
    out = []
    for rel, full in paths:
        try:
            if os.path.getsize(full) > MAX_BYTES:
                continue
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b"\0" in data[:8000]:
            continue
        text = data.decode("utf-8", "replace").replace("\r\n", "\n")
        lines = text.count("\n") + 1
        if len(text) / lines > 300:   # minified or a data blob, not hand-written code
            continue
        out.append((rel, text))
    return out


def find_runs(srcs, tier, min_lines, min_tokens):
    """Pairwise maximal matches: [fa, sa, ea, fb, sb, eb, joined] in code-line indexes (end exclusive)."""
    W = min_lines
    index = defaultdict(list)
    for f, src in enumerate(srcs):
        seq = getattr(src, tier)
        hashed = [hash(x) for x in seq]
        for i in range(len(seq) - W + 1):
            window = tuple(hashed[i:i + W])
            if len(set(window)) > W // 2:   # skip runs of one repeated line or shape (registration lists, asserts)
                index[hash(window)].append((f, i))   # 64-bit hash; collisions are not re-checked
    diag = defaultdict(list)
    for occ in index.values():
        if len(occ) < 2:
            continue
        pairs = ((occ[0], o) for o in occ[1:]) if len(occ) > MAX_BUCKET else itertools.combinations(occ, 2)
        for (fa, ia), (fb, ib) in pairs:
            if fa != fb or ib - ia >= W:
                diag[(fa, fb, ib - ia)].append(ia)
    by_pair = defaultdict(list)
    for (fa, fb, d), starts in diag.items():
        starts.sort()
        s = prev = starts[0]
        for i in starts[1:] + [None]:
            if i is not None and i == prev + 1:
                prev = i
                continue
            by_pair[(fa, fb)].append([fa, s, prev + W, fb, s + d, prev + W + d, False])
            if i is not None:
                s = prev = i
    runs = []
    for runs_of_pair in by_pair.values():
        chains = []
        for r in sorted(runs_of_pair, key=lambda r: (r[1], r[4])):
            for c in chains:
                if 0 <= r[1] - c[2] <= DRIFT_GAP and 0 <= r[4] - c[5] <= DRIFT_GAP:
                    c[2], c[5], c[6] = r[2], r[5], True
                    break
            else:
                chains.append(list(r))
        runs.extend(chains)
    keep = []
    for fa, sa, ea, fb, sb, eb, joined in runs:
        if fa == fb and sb < ea:
            continue      # a block overlapping its own copy (repetitive lines), not a copy-paste
        tp = srcs[fa].tok_prefix
        if tp[ea] - tp[sa] >= min_tokens:
            keep.append((fa, sa, ea, fb, sb, eb, joined))
    return keep


def group(runs):
    """Connected copies -> families: list of {file: [(s, e), ...]} in code-line indexes."""
    nodes, parent = [], []

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for fa, sa, ea, fb, sb, eb, _ in runs:
        for node in ((fa, sa, ea), (fb, sb, eb)):
            nodes.append(node)
            parent.append(len(parent))
        union(len(nodes) - 2, len(nodes) - 1)
    per_file = defaultdict(list)
    for k, (f, s, e) in enumerate(nodes):
        per_file[f].append((s, e, k))
    # Each match attaches to the longest span in its file that it mostly overlaps (SAME_SPAN of the larger).
    # Anchors never join each other, so a family can't chain through neighbouring blocks into one blob.
    for items in per_file.values():
        anchors = []
        for s, e, k in sorted(items, key=lambda x: (x[0] - x[1], x[0])):
            for s2, e2, k2 in anchors:
                if min(e, e2) - max(s, s2) >= SAME_SPAN * max(e - s, e2 - s2):
                    union(k, k2)
                    break
            else:
                anchors.append((s, e, k))
    comps = defaultdict(lambda: defaultdict(list))
    for k, (f, s, e) in enumerate(nodes):
        comps[find(k)][f].append((s, e))
    families = []
    for comp in comps.values():
        fam = {}
        for f, spans in comp.items():
            merged = []
            for s, e in sorted(spans):
                if merged and s < merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            fam[f] = [tuple(x) for x in merged]
        families.append(fam)
    return families


def describe(srcs, fam, tier):
    members = [(f, s, e) for f in sorted(fam, key=lambda f: srcs[f].path) for s, e in fam[f]]
    exact = [tuple(srcs[f].exact[s:e]) for f, s, e in members]
    renamed = [tuple(srcs[f].renamed[s:e]) for f, s, e in members]
    rn, ex = Counter(renamed), Counter(exact)
    ref_i = max(range(len(members)), key=lambda k: (rn[renamed[k]], ex[exact[k]], -k))   # the most common version
    ref = exact[ref_i]
    copies = []
    for k, (f, s, e) in enumerate(members):
        a, b = srcs[f].span(s, e)
        if exact[k] == ref:
            status, diff = "same", 0
        elif renamed[k] == renamed[ref_i]:
            status, diff = "renamed", 0
        else:
            ops = difflib.SequenceMatcher(None, renamed[ref_i], renamed[k], autojunk=False).get_opcodes()
            diff = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in ops if op != "equal")
            shared = sum(i2 - i1 for op, i1, i2, j1, j2 in ops if op == "equal")
            status = "partial" if shared < 0.5 * max(len(renamed[ref_i]), len(renamed[k])) else "drifted"
        copies.append({"file": srcs[f].path, "start": a, "end": b, "lines": srcs[f].count_code(s, e),
                       "status": status, "lines_differ": diff})
    sizes = [c["lines"] for c in copies]
    f0, s0, _ = members[ref_i]
    return {"tier": tier, "copies": len(copies), "lines_per_copy": copies[ref_i]["lines"],
            "duplicated_lines": sum(sizes), "removable_lines": sum(sizes) - max(sizes),
            "drifted": sum(c["status"] == "drifted" for c in copies),
            "renamed": sum(c["status"] == "renamed" for c in copies),
            "partial": sum(c["status"] == "partial" for c in copies),
            "same_file": len({c["file"] for c in copies}) == 1,
            "tests_only": all(is_test(c["file"]) for c in copies),
            "preview": srcs[f0].raw[srcs[f0].first[s0] - 1].strip()[:100],
            "members": copies, "_spans": members}


def covered_lines(srcs, families):
    cov = defaultdict(set)
    for fam in families:
        for f, s, e in fam["_spans"]:
            a, b = srcs[f].span(s, e)
            lo = bisect.bisect_left(srcs[f].code_lines, a)
            hi = bisect.bisect_right(srcs[f].code_lines, b)
            cov[f].update(srcs[f].code_lines[lo:hi])
    return cov


def analyse(root, min_lines=6, min_tokens=40, exts=DEFAULT_EXT, excludes=()):
    t0 = time.time()
    srcs = [Source(rel, text) for rel, text in collect(root, exts, excludes)]
    total = sum(len(s.code_lines) for s in srcs)
    exact = [describe(srcs, fam, "exact") for fam in group(find_runs(srcs, "exact", min_lines, min_tokens))]
    exact_cov = covered_lines(srcs, exact)
    renamed = []
    for fam in group(find_runs(srcs, "renamed", min_lines, min_tokens)):
        d = describe(srcs, fam, "renamed")
        new = False
        for f, s, e in d["_spans"]:
            n = srcs[f].count_code(s, e)
            a, b = srcs[f].span(s, e)
            inside = sum(1 for ln in exact_cov.get(f, ()) if a <= ln <= b)
            new = new or inside < COVERED * n
        if new:
            renamed.append(d)
    all_cov = covered_lines(srcs, exact + renamed)
    exact_lines = sum(len(v) for v in exact_cov.values())
    families = sorted(exact, key=lambda d: (-d["removable_lines"], -d["copies"])) + \
        sorted(renamed, key=lambda d: (-d["removable_lines"], -d["copies"]))
    for i, d in enumerate(families, 1):
        d["id"] = i
        del d["_spans"]
    pct = round(100.0 * exact_lines / total, 1) if total else 0.0
    lead = [d for d in families if d["tier"] == "exact"] or families   # headline picks prefer exact copies
    return {
        "files": len(srcs), "code_lines": total, "min_lines": min_lines, "min_tokens": min_tokens,
        "seconds": round(time.time() - t0, 2),
        "exact": {"families": len(exact), "duplicated_lines": exact_lines, "percent": pct,
                  "removable_lines": sum(d["removable_lines"] for d in exact)},
        "renamed": {"families": len(renamed),
                    "extra_lines": sum(len(v) for v in all_cov.values()) - exact_lines},
        "biggest": max(lead, key=lambda d: d["duplicated_lines"])["id"] if lead else None,
        "most_copied": max(lead, key=lambda d: (d["copies"], d["lines_per_copy"]))["id"] if lead else None,
        "families": families,
    }


def fam_line(d):
    tags = [d["tier"], "%d copies x %d lines" % (d["copies"], d["lines_per_copy"]),
            "%d removable" % d["removable_lines"]]
    if d["drifted"]:
        tags.append("%d drifted" % d["drifted"])
    if d["tier"] == "exact" and d["renamed"]:
        tags.append("%d renamed" % d["renamed"])
    if d["partial"]:
        tags.append("%d partial" % d["partial"])
    if d["same_file"]:
        tags.append("same file")
    if d["tests_only"]:
        tags.append("tests only")
    return "#%d  %s" % (d["id"], " · ".join(tags))


def headline(rep):
    if not rep["families"]:
        return "No copy-pasted blocks of %d+ lines in %s code lines" % (rep["min_lines"], format(rep["code_lines"], ","))
    return "%.1f%% of code lines are exact copies (%s of %s)" % (
        rep["exact"]["percent"], format(rep["exact"]["duplicated_lines"], ","), format(rep["code_lines"], ","))


def text_report(rep, top):
    fams = {d["id"]: d for d in rep["families"]}
    out = ["Scanned %d files, %s code lines in %.1fs (clones of %d+ lines, %d+ tokens)" % (
        rep["files"], format(rep["code_lines"], ","), rep["seconds"], rep["min_lines"], rep["min_tokens"]),
        headline(rep)]
    if rep["families"]:
        e, r = rep["exact"], rep["renamed"]
        out.append("Exact copies:   %d families, merging them would remove ~%s lines" % (
            e["families"], format(e["removable_lines"], ",")))
        out.append("Renamed copies: %d more families, %s more lines (names or literals changed)" % (
            r["families"], format(r["extra_lines"], ",")))
        b, m = fams[rep["biggest"]], fams[rep["most_copied"]]
        out.append("Biggest family: #%d, %d copies x %d lines" % (b["id"], b["copies"], b["lines_per_copy"]))
        out.append("Most copied:    #%d, this block exists %d times" % (m["id"], m["copies"]))
    for d in rep["families"][:top]:
        out.append("")
        out.append(fam_line(d))
        out.append("    first line: %s" % d["preview"])
        for c in d["members"]:
            note = {"same": "", "renamed": "  (renamed)",
                    "drifted": "  (drifted: %d line%s differ)" % (c["lines_differ"], "s"[:c["lines_differ"] != 1]),
                    "partial": "  (partial: shares under half the block)"}[c["status"]]
            out.append("    %s:%d-%d%s" % (c["file"], c["start"], c["end"], note))
    if len(rep["families"]) > top:
        out.append("\n... %d more families (--top N or --json for all)" % (len(rep["families"]) - top))
    return "\n".join(out) + "\n"


CREDIT = "made with github.com/milad-ahmd/agent-skills"


def credit(svg, on=True):
    """Sign the card in its free top-right corner so a shared card links back; --no-credit turns it off."""
    if not on:
        return svg
    return svg.replace("</svg>", '<text x="1160" y="44" font-size="16" fill="#71717a" text-anchor="end">%s</text>\n</svg>' % CREDIT)


def svg_card(rep, show_paths):
    el = ['<rect width="1200" height="630" fill="#14161c"/>', '<rect x="0" y="0" width="12" height="630" fill="#7c5cff"/>']

    def text(x, y, body, size, color="#f4f4f5", weight="400"):
        el.append('<text x="%d" y="%d" font-size="%d" font-weight="%s" fill="%s">%s</text>'
                  % (x, y, size, weight, color, escape(body)))

    e = rep["exact"]
    text(60, 80, "COPY-PASTE REPORT", 26, "#7c5cff", "700")
    text(60, 230, "%.1f%%" % e["percent"], 150, weight="800")
    text(60, 285, "of the code is an exact copy of other code", 28, "#d4d4d8")
    fams = {d["id"]: d for d in rep["families"]}
    stats = [("%d" % e["families"], "clone families"), (format(e["removable_lines"], ","), "lines removable"),
             ("%d" % rep["renamed"]["families"], "renamed-copy families")]
    if rep["most_copied"]:
        stats.append(("%dx" % fams[rep["most_copied"]]["copies"], "copies of the most-copied block"))
    for i, (big, label) in enumerate(stats):
        x = 60 + i * 280
        text(x, 370, big, 54, weight="700")
        text(x, 405, label, 20, "#a1a1aa")
    top = [d for d in rep["families"] if d["tier"] == "exact"][:5]
    most = max([d["duplicated_lines"] for d in top] or [1])
    for i, d in enumerate(top):
        y = 450 + i * 28
        w = 700.0 * d["duplicated_lines"] / most
        el.append('<rect x="60" y="%d" width="%.1f" height="20" rx="3" fill="#7c5cff"/>' % (y, max(w, 2)))
        label = "#%d  %d x %d lines" % (d["id"], d["copies"], d["lines_per_copy"])
        if show_paths:
            label += "  " + os.path.basename(d["members"][0]["file"])
        text(780, y + 16, label, 18, "#d4d4d8")
    text(60, 605, "%s files, %s code lines, clones of %d+ lines" % (
        format(rep["files"], ","), format(rep["code_lines"], ","), rep["min_lines"]), 18, "#71717a")
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" '
            'font-family="Inter, \'Helvetica Neue\', Arial, sans-serif">\n<title>%s</title>\n%s\n</svg>\n'
            % (escape(headline(rep)), "\n".join(el)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__[__doc__.index("How it works"):])
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--min-lines", type=int, default=6)
    ap.add_argument("--min-tokens", type=int, default=40)
    ap.add_argument("--ext", help="comma-separated, e.g. py,ts,go")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--svg", metavar="FILE")
    ap.add_argument("--no-credit", action="store_true", help="leave the \"made with agent-skills\" line off the card")
    ap.add_argument("--show-paths", action="store_true")
    ap.add_argument("--fail-over", type=float, metavar="PCT")
    a = ap.parse_args(argv)
    if a.min_lines < 2 or a.min_tokens < 0 or a.top < 0:
        ap.error("--min-lines must be 2 or more; --min-tokens and --top can't be negative")
    if not os.path.exists(a.path):
        print("find_clones: %s does not exist" % a.path, file=sys.stderr)
        return 2
    exts = [x.strip() for x in a.ext.split(",") if x.strip()] if a.ext else DEFAULT_EXT
    rep = analyse(a.path, a.min_lines, a.min_tokens, exts, a.exclude)
    if not rep["files"]:
        print("find_clones: no source files with extensions %s under %s" % (",".join(exts), a.path), file=sys.stderr)
        return 2
    if a.svg:
        with open(a.svg, "w", encoding="utf-8") as fh:
            fh.write(credit(svg_card(rep, a.show_paths), not a.no_credit))
    if a.json:
        json.dump(rep, sys.stdout, indent=2)
        print()
    else:
        sys.stdout.write(text_report(rep, a.top))
        if a.svg:
            print("\nCard written to %s" % a.svg)
    if a.fail_over is not None:
        over = rep["exact"]["percent"] > a.fail_over
        if over:
            print("find_clones: exact duplication %.1f%% is over the %.1f%% budget" % (rep["exact"]["percent"],
                                                                                       a.fail_over), file=sys.stderr)
        return 1 if over else 0
    return 1 if rep["families"] else 0


if __name__ == "__main__":
    sys.exit(main())
