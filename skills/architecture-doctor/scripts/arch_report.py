#!/usr/bin/env python3
"""Rank a codebase's architecture problems by what they cost: coupling metrics from the import graph,
weighted by how often the code involved changes in git. Stdlib only.

Usage: python3 arch_report.py [path] [--days 180] [--top 10] [--json] [--layers "ui>services>db"]
                              [--exclude GLOB ...] [--depth N] [--include-tests]
                              [--god-lines 500] [--god-fan-in 5] [--hub-min 5] [--hot-min 5]
                              [--min-shared 3] [--min-ratio 0.5] [--max-commit-files 30]

What it reports
  modules        per file and per directory module: fan-in (Ca), fan-out (Ce), instability I = Ce/(Ca+Ce),
                 lines, and the import cycle it sits in. Directory modules count files outside the module.
  cycles         strongly connected components of the file and module import graphs
  god files      at least --god-lines non-blank lines and imported by at least --god-fan-in files
  hubs           files imported by at least --hub-min files, with their instability
  hotspots       commits touching the file in the last --days days x its size (needs git)
  hidden coupling  file pairs that changed together in at least --min-shared commits, with
                 shared / average(commits of each) >= --min-ratio, and no import between them.
                 Commits touching more than --max-commit-files analysed files are skipped as noise.
  layers         --layers "ui>services>db": left may import right, never the reverse. Use "," for
                 alternatives in one layer ("ui,pages>services>db,models"). A token with "/" is a path
                 prefix, one with * ? [ is a glob on the path, anything else matches a directory name.
  top problems   one ranked list: score = weight x (1 + commits in the window touching the files involved).
                 Without git history the commit factor is 1 and the ranking is structural only.

Imports come from the vendored code_map.py (Python, JS/TS with tsconfig paths, Go). Imports built at runtime,
DI containers, reflection and calls over HTTP are not visible. Test files are left out unless --include-tests.

Exit codes: 0 report written (problems found or not) - 2 error (not a directory, no source files, bad --layers)
It only reads files and runs read-only git commands (rev-parse, log).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from code_map import CodeMap, build  # noqa: E402

SCHEMA_VERSION = 1
GLOB_CHARS = set("*?[")
HOT_LINES = 150   # a file this small that changes often is rarely an architecture problem


# --- git history --------------------------------------------------------------------------
def git_history(root, days, files):
    """Commits in the window as lists of analysed files. Returns (commits, note); commits is None without git."""
    def git(*args):
        return subprocess.run(["git", "-c", "core.quotepath=off", "-C", root] + list(args),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        r = git("rev-parse", "--show-prefix")
    except OSError:
        return None, "git is not installed: churn, hotspots and hidden coupling skipped"
    if r.returncode != 0:
        return None, "not a git repository: churn, hotspots and hidden coupling skipped"
    prefix = r.stdout.strip()
    r = git("log", "--no-merges", "--since=%d days ago" % days, "--name-only", "--format=@@commit %H", "--", ".")
    if r.returncode != 0:
        msg = (r.stderr.strip().splitlines() or ["git log failed"])[-1]
        return None, "git log failed (%s): churn, hotspots and hidden coupling skipped" % msg
    commits, cur = [], None
    for line in r.stdout.splitlines():
        if line.startswith("@@commit "):
            cur = []
            commits.append(cur)
        elif line.strip() and cur is not None:
            path = line.strip()
            if prefix and path.startswith(prefix):
                path = path[len(prefix):]
            if path in files and path not in cur:
                cur.append(path)
    if not commits:
        return [], "no commits in the last %d days: churn, hotspots and hidden coupling are empty" % days
    return commits, None


def churn_of(commits):
    c = Counter()
    for files in commits:
        c.update(files)
    return c


def touching(commits, paths):
    """Commits in the window that touch any of `paths`."""
    s = set(paths)
    return sum(1 for files in commits if s.intersection(files))


def hidden_coupling(commits, churn, edge_set, mod, min_shared, min_ratio, max_files):
    pairs, skipped = Counter(), 0
    for files in commits:
        if len(files) > max_files:
            skipped += 1
            continue
        for a, b in combinations(sorted(files), 2):
            pairs[(a, b)] += 1
    out = []
    for (a, b), shared in pairs.items():
        if shared < min_shared or (a, b) in edge_set or (b, a) in edge_set:
            continue
        ratio = shared / ((churn[a] + churn[b]) / 2.0)
        if ratio < min_ratio:
            continue
        out.append({"a": a, "b": b, "shared": shared, "commits_a": churn[a], "commits_b": churn[b],
                    "ratio": round(ratio, 2), "same_module": mod[a] == mod[b]})
    out.sort(key=lambda p: (p["same_module"], -p["shared"], -p["ratio"], p["a"], p["b"]))
    return out, skipped


# --- layers -------------------------------------------------------------------------------
def parse_layers(spec):
    """'ui,pages>services>db' -> [['ui', 'pages'], ['services'], ['db']]. Raises ValueError when malformed."""
    layers = [[t.strip().strip("/") for t in part.split(",") if t.strip().strip("/")] for part in spec.split(">")]
    if len(layers) < 2 or any(not lay for lay in layers):
        raise ValueError("--layers needs at least two non-empty layers separated by '>', e.g. \"ui>services>db\"")
    seen = Counter(t for lay in layers for t in lay)
    dup = [t for t, n in seen.items() if n > 1]
    if dup:
        raise ValueError("--layers names %s in more than one layer" % ", ".join(sorted(dup)))
    return layers


def layer_of(path, layers):
    """Index of the layer a file belongs to, or None. Path prefixes and globs first, then the outermost
    directory name that names a layer."""
    for i, lay in enumerate(layers):
        for t in lay:
            if GLOB_CHARS & set(t):
                if fnmatch.fnmatch(path, t) or fnmatch.fnmatch(path, t + "/*"):
                    return i
            elif "/" in t and (path == t or path.startswith(t + "/")):
                return i
    for seg in path.split("/")[:-1]:
        for i, lay in enumerate(layers):
            if seg in lay:
                return i
    return None


def layer_violations(edges, layers, lazy=frozenset()):
    out = []
    for s, t in edges:
        ls, lt = layer_of(s, layers), layer_of(t, layers)
        if ls is not None and lt is not None and ls > lt:
            out.append({"source": s, "target": t, "from_layer": "/".join(layers[ls]),
                        "to_layer": "/".join(layers[lt]), "lazy": (s, t) in lazy})
    out.sort(key=lambda v: (v["from_layer"], v["to_layer"], v["source"], v["target"]))
    return out


# --- metrics ------------------------------------------------------------------------------
def instability(ca, ce):
    return round(ce / float(ca + ce), 2) if ca + ce else None


def module_metrics(paths, mod, edges, loc, cycle_of_module):
    ca, ce = defaultdict(set), defaultdict(set)
    for s, t in edges:
        a, b = mod[s], mod[t]
        if a != b:
            ce[a].add(t)   # files outside `a` that `a` depends on
            ca[b].add(s)   # files outside `b` that depend on `b`
    rows = defaultdict(lambda: {"files": 0, "loc": 0})
    for p in paths:
        rows[mod[p]]["files"] += 1
        rows[mod[p]]["loc"] += loc[p]
    out = []
    for m in sorted(rows):
        out.append({"id": m, "files": rows[m]["files"], "loc": rows[m]["loc"], "ca": len(ca[m]), "ce": len(ce[m]),
                    "instability": instability(len(ca[m]), len(ce[m])), "cycle": cycle_of_module.get(m)})
    return out


def analyse(root, days=180, layers=None, excludes=(), depth=None, include_tests=False, god_lines=500,
            god_fan_in=5, hub_min=5, hot_min=5, min_shared=3, min_ratio=0.5, max_commit_files=30, use_git=True):
    cm = CodeMap(root, list(excludes), skip_tests=not include_tests).analyse()
    if not cm.files:
        raise LookupError("no source files found under %s" % root)
    m = build(cm, depth=depth, hub_min=hub_min)
    paths = [f["path"] for f in m["files"]]
    loc = {f["path"]: f["loc"] for f in m["files"]}
    mod = {f["path"]: f["module"] for f in m["files"]}
    edges = [(e["source"], e["target"]) for e in m["edges"]]
    edge_set = set(edges)
    fan_in, fan_out = Counter(t for _, t in edges), Counter(s for s, _ in edges)

    cycles = []
    cycle_of = {}
    for i, c in enumerate(m["cycles"]["files"]):
        cid = "F%d" % (i + 1)
        for p in c["members"]:
            cycle_of[p] = cid
        cycles.append({"id": cid, "level": "file", "members": c["members"], "size": c["size"],
                       "eager": c["eager"], "example": c["example"],
                       "loc": sum(loc[p] for p in c["members"])})
    cycle_of_module = {}
    m_src = defaultdict(set)   # (module a, module b) -> files in a that import b
    for s, t in edges:
        if mod[s] != mod[t]:
            m_src[(mod[s], mod[t])].add(s)
    for i, c in enumerate(m["cycles"]["modules"]):
        cid = "M%d" % (i + 1)
        members = set(c["members"])
        for x in members:
            cycle_of_module[x] = cid
        inner = sorted(((a, b), sorted(fs)) for (a, b), fs in m_src.items() if a in members and b in members)
        a, b = min(inner, key=lambda e: (len(e[1]), e[0]))[0] if inner else (None, None)
        weakest = {"source": a, "target": b, "files": sorted(m_src[(a, b)])} if a else None
        cycles.append({"id": cid, "level": "module", "members": c["members"], "size": c["size"],
                       "eager": None, "example": c["example"], "weakest_edge": weakest})

    notes = []
    commits = None
    if use_git:
        commits, note = git_history(cm.root, days, set(paths))
        if note:
            notes.append(note)
    else:
        notes.append("git history not used: churn, hotspots and hidden coupling skipped")
    has_git = commits is not None
    churn = churn_of(commits) if has_git else Counter()

    files = []
    for p in paths:
        row = {"path": p, "module": mod[p], "loc": loc[p], "fan_in": fan_in[p], "fan_out": fan_out[p],
               "instability": instability(fan_in[p], fan_out[p]), "cycle": cycle_of.get(p)}
        if has_git:
            row["commits"] = churn[p]
            row["hotspot_score"] = churn[p] * loc[p]
        files.append(row)
    by_path = {f["path"]: f for f in files}

    gods = sorted((f for f in files if f["loc"] >= god_lines and f["fan_in"] >= god_fan_in),
                  key=lambda f: (-f["loc"] * f["fan_in"], f["path"]))
    hubs = sorted((f for f in files if f["fan_in"] >= hub_min), key=lambda f: (-f["fan_in"], -f["loc"], f["path"]))
    hotspots = sorted((f for f in files if has_git and f["commits"] > 0),
                      key=lambda f: (-f["hotspot_score"], -f["commits"], f["path"]))
    coupling, skipped = ([], 0)
    if has_git:
        coupling, skipped = hidden_coupling(commits, churn, edge_set, mod, min_shared, min_ratio, max_commit_files)
        if skipped:
            notes.append("%d commit(s) touching more than %d files skipped for hidden coupling"
                         % (skipped, max_commit_files))
    lazy = {(e["source"], e["target"]) for e in m["edges"] if e["lazy"]}
    violations = layer_violations(edges, layers, lazy) if layers else []
    if layers:
        unplaced = sum(1 for p in paths if layer_of(p, layers) is None)
        if unplaced == len(paths):
            notes.append("--layers matched no files: check the layer names against the folder names")
        elif unplaced:
            notes.append("%d file(s) are in no layer and were not checked" % unplaced)

    problems = rank_problems(files, by_path, cycles, gods, hubs, hotspots, coupling, violations, commits,
                             god_lines, god_fan_in, hub_min, hot_min)
    return {
        "schema_version": SCHEMA_VERSION,
        "root": m["root"],
        "depth": m["depth"],
        "days": days,
        "git": has_git,
        "stats": {"files": len(paths), "loc": sum(loc.values()), "modules": m["stats"]["modules"],
                  "edges": len(edges), "file_cycles": len(m["cycles"]["files"]),
                  "module_cycles": len(m["cycles"]["modules"]),
                  "commits": len(commits) if has_git else None,
                  "unresolved_imports": m["stats"]["unresolved"]},
        "layers": [list(lay) for lay in layers] if layers else None,
        "problems": problems,
        "modules": module_metrics(paths, mod, edges, loc, cycle_of_module),
        "files": files,
        "cycles": cycles,
        "god_files": gods,
        "hubs": hubs,
        "hotspots": hotspots,
        "hidden_coupling": coupling,
        "layer_violations": violations,
        "notes": notes,
        "not_visible": m["not_visible"],
    }


def rank_problems(files, by_path, cycles, gods, hubs, hotspots, coupling, violations, commits,
                  god_lines, god_fan_in, hub_min, hot_min):
    """One list, score = weight x heat. heat = 1 + commits in the window touching the files involved, so a
    problem in code nobody changes sinks. Weights keep the kinds on a comparable scale; read the reasons."""
    has_git = commits is not None

    def heat(paths):
        return 1 + (touching(commits, paths) if has_git else 0)

    out = []
    flagged = {}
    for f in gods:
        flagged.setdefault(f["path"], []).append("god file")
    for f in hotspots:
        if f["commits"] >= hot_min and f["loc"] >= HOT_LINES:
            flagged.setdefault(f["path"], []).append("hotspot")
    for f in hubs:
        if has_git and f["commits"] >= hot_min and f["path"] not in flagged:
            flagged.setdefault(f["path"], []).append("busy hub")
    for p, tags in flagged.items():
        f = by_path[p]
        bits = ["%d lines" % f["loc"], "imported by %d" % f["fan_in"], "imports %d" % f["fan_out"]]
        if has_git:
            bits.append(plural(f["commits"], "commit"))
        if f["cycle"]:
            bits.append("in cycle %s" % f["cycle"])
        weight = f["loc"] / 100.0 * (1 + f["fan_in"] / 10.0)
        out.append({"kind": " + ".join(tags), "files": [p], "score": round(weight * heat([p]), 1),
                    "reason": ", ".join(bits)})
    for c in cycles:
        if c["level"] != "file":
            continue
        # lazy-only cycles (imports inside functions, dynamic import(), type-only imports) are often
        # deliberate, so they weigh less
        weight = float(c["size"]) * (2.0 if c["eager"] else 0.25)
        h = heat(c["members"])
        lazy_note = "" if c["eager"] else " (only through lazy, dynamic or type-only imports)"
        reason = "%d files import each other%s: %s" % (c["size"], lazy_note, " -> ".join(c["example"]))
        if has_git:
            reason += "; %s touched it" % plural(h - 1, "commit")
        out.append({"kind": "import cycle", "files": c["members"], "score": round(weight * h, 1), "reason": reason})
    for c in cycles:
        if c["level"] != "module" or not c.get("weakest_edge"):
            continue
        w = c["weakest_edge"]
        out.append({"kind": "module cycle", "files": w["files"], "score": round(5.0 * c["size"] * heat(w["files"]), 1),
                    "reason": "%d modules depend on each other (%s); cheapest edge to cut: %s -> %s, from %d file(s)"
                              % (c["size"], " -> ".join(c["example"]), w["source"], w["target"], len(w["files"]))})
    for p in coupling:
        weight = p["ratio"] * 5 * (1.0 if p["same_module"] else 1.5)
        out.append({"kind": "hidden coupling", "files": [p["a"], p["b"]],
                    "score": round(weight * (1 + p["shared"]), 1),
                    "reason": "changed together in %d commits (of %d and %d, ratio %.2f) with no import between them%s"
                              % (p["shared"], p["commits_a"], p["commits_b"], p["ratio"],
                                 "" if p["same_module"] else ", across modules")})
    groups = defaultdict(list)
    for v in violations:
        groups[(v["from_layer"], v["to_layer"])].append(v)
    for (a, b), vs in groups.items():
        srcs = sorted({v["source"] for v in vs})
        eager_srcs = {v["source"] for v in vs if not v["lazy"]}
        # one file importing ten things the wrong way is one decision to undo, so weigh files, not edges;
        # a file whose wrong-way imports are all lazy or type-only counts a quarter (moving a type down is cheap)
        weight = 4.0 * len(eager_srcs) + 1.0 * (len(srcs) - len(eager_srcs))
        n_lazy = sum(1 for v in vs if v["lazy"])
        ex = next((v for v in vs if not v["lazy"]), vs[0])
        out.append({"kind": "layer violation", "files": srcs, "score": round(weight * heat(srcs), 1),
                    "reason": "%s imports %s %s from %s%s, e.g. %s -> %s" % (
                        a, b, plural(len(vs), "time"), plural(len(srcs), "file"),
                        " (%d lazy, dynamic or type-only)" % n_lazy if n_lazy else "",
                        ex["source"], ex["target"])})
    out.sort(key=lambda x: (-x["score"], x["kind"], x["files"]))
    for i, x in enumerate(out, 1):
        x["rank"] = i
    return out


# --- output -------------------------------------------------------------------------------
def plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def fmt_i(v):
    return "-" if v is None else "%.2f" % v


def render_text(r, top=10):
    s = r["stats"]
    head = "Architecture report for %s: %d files, %s lines, %d modules at depth %d, %d import edges" % (
        r["root"], s["files"], format(s["loc"], ","), s["modules"], r["depth"], s["edges"])
    if r["git"]:
        head += ", %d commits in the last %d days" % (s["commits"], r["days"])
    out = [head, ""]
    probs = r["problems"]
    out.append("Top problems (score = weight x (1 + commits touching them)):" if probs else
               "Top problems: none found above the thresholds.")
    for p in probs[:top]:
        out.append("%3d. [%s] %s  score %s" % (p["rank"], p["kind"], ", ".join(p["files"][:3]) +
                                               (" +%d more" % (len(p["files"]) - 3) if len(p["files"]) > 3 else ""),
                                               p["score"]))
        out.append("       %s" % p["reason"])
    if len(probs) > top:
        out.append("     ... %d more (use --top or --json)" % (len(probs) - top))

    mods = sorted(r["modules"], key=lambda x: (-(x["ca"] + x["ce"]), x["id"]))[:top]
    out += ["", "Modules (most coupled first; Ca = files outside that import it, Ce = files outside it imports):",
            "  %-50s %5s %7s %4s %4s %5s  cycle" % ("module", "files", "lines", "Ca", "Ce", "I")]
    for x in mods:
        out.append("  %-50s %5d %7d %4d %4d %5s  %s" % (x["id"][:50], x["files"], x["loc"], x["ca"], x["ce"],
                                                      fmt_i(x["instability"]), x["cycle"] or ""))

    fc = [c for c in r["cycles"] if c["level"] == "file"]
    mc = [c for c in r["cycles"] if c["level"] == "module"]
    out += ["", "Cycles: %d between files, %d between modules" % (len(fc), len(mc))]
    for c in (fc + mc)[:top]:
        tag = "" if c["level"] == "module" else ("" if c["eager"] else " (lazy imports only)")
        out.append("  %s %d %ss%s: %s" % (c["id"], c["size"], c["level"], tag, " -> ".join(c["example"])))
        w = c.get("weakest_edge")
        if w:
            out.append("     cheapest edge to cut: %s -> %s (%s)" % (w["source"], w["target"], ", ".join(w["files"][:3])
                                                                    + (" +%d" % (len(w["files"]) - 3)
                                                                       if len(w["files"]) > 3 else "")))

    out += ["", "God files (lines x fan-in): %d" % len(r["god_files"])]
    for f in r["god_files"][:top]:
        out.append("  %-60s %6d lines  fan-in %3d  fan-out %3d" % (f["path"][:60], f["loc"], f["fan_in"], f["fan_out"]))

    out += ["", "Hubs (most imported): %d" % len(r["hubs"])]
    for f in r["hubs"][:top]:
        out.append("  %-60s fan-in %3d  fan-out %3d  I %s" % (f["path"][:60], f["fan_in"], f["fan_out"],
                                                              fmt_i(f["instability"])))

    if r["git"]:
        out += ["", "Hotspots (commits in %d days x lines):" % r["days"]]
        for f in r["hotspots"][:top]:
            out.append("  %-60s %4d commits  %6d lines  score %d" % (f["path"][:60], f["commits"], f["loc"],
                                                                     f["hotspot_score"]))
        out += ["", "Hidden coupling (change together, no import): %d pair(s)" % len(r["hidden_coupling"])]
        for p in r["hidden_coupling"][:top]:
            out.append("  %s <-> %s  %d shared, ratio %.2f%s" % (p["a"], p["b"], p["shared"], p["ratio"],
                                                                 "" if p["same_module"] else ", across modules"))
    if r["layers"]:
        out += ["", "Layer violations (%s): %d" % (" > ".join(",".join(x) for x in r["layers"]),
                                                   len(r["layer_violations"]))]
        for v in r["layer_violations"][:top]:
            out.append("  %s (%s) -> %s (%s)%s" % (v["source"], v["from_layer"], v["target"], v["to_layer"],
                                                    "  lazy" if v["lazy"] else ""))
    out += ["", "Notes:"]
    out += ["  - %s" % n for n in r["notes"]]
    if s["unresolved_imports"]:
        out.append("  - %d import(s) could not be resolved to a file; their edges are missing"
                   % s["unresolved_imports"])
    out.append("  - Not visible to a static parse: %s" % r["not_visible"])
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Rank architecture problems (cycles, god files, hubs, hotspots, hidden coupling, layer "
                    "violations) by how much they cost. Exit 0 = report written, 2 = error.")
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--days", type=int, default=180, help="git window for churn and co-change (default 180)")
    ap.add_argument("--top", type=int, default=10, help="rows per list in the text report (default 10)")
    ap.add_argument("--json", action="store_true", help="print the full model as JSON")
    ap.add_argument("--layers", help='allowed import direction, e.g. "ui>services>db" (left may import right)')
    ap.add_argument("--exclude", action="append", default=[], help="glob to skip (repeatable, comma-separated ok)")
    ap.add_argument("--depth", type=int, help="directory levels per module (default: auto)")
    ap.add_argument("--include-tests", action="store_true", help="keep test files in the graph")
    ap.add_argument("--no-git", action="store_true", help="ignore git history")
    ap.add_argument("--god-lines", type=int, default=500, help="god file: minimum non-blank lines (default 500)")
    ap.add_argument("--god-fan-in", type=int, default=5, help="god file: minimum importers (default 5)")
    ap.add_argument("--hub-min", type=int, default=5, help="hub: minimum importers (default 5)")
    ap.add_argument("--hot-min", type=int, default=5, help="hotspot / busy hub: minimum commits (default 5)")
    ap.add_argument("--min-shared", type=int, default=3, help="hidden coupling: minimum shared commits (default 3)")
    ap.add_argument("--min-ratio", type=float, default=0.5,
                    help="hidden coupling: shared / average commits of the pair (default 0.5)")
    ap.add_argument("--max-commit-files", type=int, default=30,
                    help="hidden coupling: skip commits touching more files than this (default 30)")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.path):
        print("arch_report: %s is not a directory" % a.path, file=sys.stderr)
        return 2
    if a.days <= 0 or a.top <= 0:
        print("arch_report: --days and --top must be positive", file=sys.stderr)
        return 2
    try:
        layers = parse_layers(a.layers) if a.layers else None
    except ValueError as e:
        print("arch_report: %s" % e, file=sys.stderr)
        return 2
    excludes = [p.strip() for x in a.exclude for p in x.split(",") if p.strip()]
    try:
        r = analyse(a.path, a.days, layers, excludes, a.depth if a.depth and a.depth > 0 else None,
                    a.include_tests, a.god_lines, a.god_fan_in, a.hub_min, a.hot_min, a.min_shared, a.min_ratio,
                    a.max_commit_files, use_git=not a.no_git)
    except LookupError as e:
        print("arch_report: %s" % e, file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(r, indent=2) + "\n" if a.json else render_text(r, a.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
