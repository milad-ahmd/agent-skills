#!/usr/bin/env python3
"""Weigh a project's installed dependencies: node_modules (npm, pnpm, yarn, bun), a Python venv's
site-packages, and Go modules in the module cache. Compares the total with real-world sizes (floppy
disks, shareware Doom, the Apollo Guidance Computer) and lists what to slim. Stdlib only, read-only:
it never installs, removes or changes anything.

Usage: python3 weigh_in.py [PROJECT] [--venv DIR] [--top N] [--json] [--svg card.svg]
  PROJECT     project directory (default: current directory)
  --venv DIR  virtualenv to weigh (default: .venv or venv in PROJECT, when present)
  --top N     rows per list (default 10)
  --json      everything as JSON on stdout
  --svg FILE  also write a 1200x630 weigh-in card
  --no-credit     leave the "made with agent-skills" line off the card

Sizes are apparent sizes (the sum of st_size of regular files), shown with MB = 1,000,000 bytes.
The on-disk figure counts allocated blocks once per inode. Symlinks are never followed, so pnpm's
top-level links and npm workspace links are not counted twice.

Exit codes: 0 weighed
            1 the SVG file could not be written
            2 nothing installed to weigh (no node_modules, venv or downloaded Go modules)
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import defaultdict, deque
from xml.sax.saxutils import escape

# Sources for every figure: references/sources.md
REFERENCES = [  # (bytes, singular, plural)
    (40960, "Super Mario Bros. cartridge", "Super Mario Bros. cartridges"),
    (77824, "Apollo Guidance Computer memory", "Apollo Guidance Computers' worth of memory"),
    (1474560, "3.5-inch HD floppy disk", "floppy disks"),
    (4196020, "copy of shareware Doom", "copies of shareware Doom"),
    (737280000, "80-minute CD-ROM", "CD-ROMs"),
    (4700000000, "single-layer DVD", "single-layer DVDs"),
]
# A joke scale, not a measurement.
WEIGHT_CLASSES = [(1e6, "strawweight"), (10e6, "flyweight"), (50e6, "featherweight"), (100e6, "lightweight"),
                  (250e6, "middleweight"), (500e6, "heavyweight"), (1e9, "super heavyweight")]
BLACK_HOLE = "black hole"

# Known heavy or retired packages and a maintained alternative. Sources: references/sources.md
ALTERNATIVES = {
    "npm": [
        ("moment", "dayjs, date-fns or Luxon", "Moment calls itself a legacy project in maintenance mode"),
        ("request", "global fetch (Node 18+) or undici", "deprecated since 2020"),
        ("aws-sdk", "modular @aws-sdk/client-* packages (SDK v3)", "SDK v2 reached end of support on 8 Sep 2025"),
        ("node-sass", "sass (Dart Sass)", "node-sass is deprecated and builds a native binary"),
        ("tslint", "eslint with typescript-eslint", "TSLint is deprecated"),
        ("lodash", "lodash-es or per-method imports, or native methods",
         "smaller browser bundles; the saving is in the bundle more than on disk"),
    ],
    "python": [
        ("nvidia-*", "CPU-only torch wheels from https://download.pytorch.org/whl/cpu, if this machine has no GPU",
         "CUDA builds of torch pull in the nvidia-* libraries"),
        ("opencv-python", "opencv-python-headless on servers and in containers",
         "the headless build has no GUI dependencies; keep opencv-python if you use cv2.imshow"),
    ],
}
DEV_TOOLS = {"typescript", "eslint", "prettier", "jest", "vitest", "mocha", "nodemon", "ts-node", "tslint",
             "webpack", "vite", "husky", "lint-staged", "rollup", "esbuild"}
DEV_PREFIXES = ("@types/", "eslint-plugin-", "eslint-config-", "@typescript-eslint/", "@testing-library/")


# ---------------------------------------------------------------- measuring

def measure(top, skip_child=None):
    """Apparent bytes, file count, on-disk bytes (each inode once) and bytes in hard-linked files."""
    apparent = files = ondisk = shared = 0
    seen = set()
    for dirpath, dirnames, filenames in os.walk(top):
        if skip_child and dirpath == top and skip_child in dirnames:
            dirnames.remove(skip_child)
        for f in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, f))
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            apparent += st.st_size
            files += 1
            if st.st_nlink > 1:
                shared += st.st_size
            if (st.st_dev, st.st_ino) not in seen:
                seen.add((st.st_dev, st.st_ino))
                blocks = getattr(st, "st_blocks", None)
                ondisk += blocks * 512 if blocks is not None else st.st_size
    return {"bytes": apparent, "files": files, "on_disk": ondisk, "hard_linked": shared}


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def package_dirs(nm):
    """Real (non-symlink) package directories directly inside a node_modules folder."""
    try:
        entries = sorted(os.listdir(nm))
    except OSError:
        return
    for e in entries:
        if e.startswith("."):
            continue
        p = os.path.join(nm, e)
        if e.startswith("@") and os.path.isdir(p) and not os.path.islink(p):
            for sub in sorted(os.listdir(p)):
                q = os.path.join(p, sub)
                if os.path.isdir(q) and not os.path.islink(q):
                    yield q
        elif os.path.isdir(p) and not os.path.islink(p):
            yield p


def instance(path, root, nested):
    pj = read_json(os.path.join(path, "package.json"))
    deps = set()
    for key in ("dependencies", "optionalDependencies", "peerDependencies"):
        if isinstance(pj.get(key), dict):
            deps.update(pj[key])
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    m = measure(path, "node_modules" if nested else None)
    return {"name": pj.get("name") or rel.split("node_modules/")[-1], "version": str(pj.get("version", "?")),
            "path": rel, "bytes": m["bytes"], "files": m["files"], "deps": deps, "dev": None}


def find_npm(nm, root):
    out = []
    stack = [nm]
    while stack:
        cur = stack.pop()
        for p in package_dirs(cur):
            if os.path.isfile(os.path.join(p, "package.json")):
                out.append(instance(p, root, nested=True))
            if os.path.isdir(os.path.join(p, "node_modules")):
                stack.append(os.path.join(p, "node_modules"))
    return out


def find_pnpm(nm, root):
    """pnpm keeps each package at node_modules/.pnpm/<name>@<version>/node_modules/<name>; the other entries
    in that inner node_modules are symlinks to its dependencies and are skipped."""
    out = []
    store = os.path.join(nm, ".pnpm")
    for d in sorted(os.listdir(store)):
        for p in package_dirs(os.path.join(store, d, "node_modules")):
            if os.path.isfile(os.path.join(p, "package.json")):
                out.append(instance(p, root, nested=False))
    return out


def resolve(start, name, root, index):
    """Node's lookup: <dir>/node_modules/<name> in start and each parent up to root. Inside pnpm's store this
    finds the sibling symlink in .pnpm/<pkg>/node_modules, so one rule covers both layouts."""
    d = start
    while True:
        if os.path.basename(d) != "node_modules":
            cand = os.path.join(d, "node_modules", name)
            if os.path.isdir(cand):
                return index.get(os.path.realpath(cand))
        if d == root or os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def closure(starts, edges):
    seen, queue = set(), deque(starts)
    while queue:
        n = queue.popleft()
        if n not in seen:
            seen.add(n)
            queue.extend(edges[n] - seen)
    return seen


def chain_to(target, directs, parents, inst):
    """Shortest path direct dependency -> ... -> target over the resolved install graph."""
    prev, queue = {target: None}, deque([target])
    while queue:
        n = queue.popleft()
        if n in directs and n != target:
            path = [n]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return [inst[k]["name"] for k in path]
        for p in sorted(parents[n]):
            if p not in prev:
                prev[p] = n
                queue.append(p)
    return []


def weigh_node(root, top):
    nm = os.path.join(root, "node_modules")
    pnpm = os.path.isdir(os.path.join(nm, ".pnpm"))
    has = lambda f: os.path.isfile(os.path.join(root, f))  # noqa: E731
    manager = "pnpm" if pnpm else "npm" if has("package-lock.json") else "yarn" if has("yarn.lock") else \
        "bun" if glob.glob(os.path.join(root, "bun.lock*")) else "npm"
    inst = find_pnpm(nm, root) if pnpm else find_npm(nm, root)
    total = measure(nm)
    pj = read_json(os.path.join(root, "package.json"))
    kinds = {}
    for key, kind in (("devDependencies", "dev"), ("optionalDependencies", "optional"), ("dependencies", "prod")):
        for n in (pj.get(key) or {}):
            kinds[n] = kind

    root_real = os.path.realpath(root)
    index = {os.path.realpath(os.path.join(root, i["path"])): k for k, i in enumerate(inst)}
    edges, parents, by_name = defaultdict(set), defaultdict(set), defaultdict(list)
    for k, i in enumerate(inst):
        by_name[i["name"]].append(i)
        here = os.path.realpath(os.path.join(root, i["path"]))
        for dep in i["deps"]:
            t = resolve(here, dep, root_real, index)
            if t is not None and t != k:
                edges[k].add(t)
                parents[t].add(k)
    direct = {}  # instance index -> (name, kind)
    for n, kind in kinds.items():
        t = resolve(root_real, n, root_real, index)
        if t is not None:
            direct[t] = (n, kind)

    notes = []
    lock = read_json(os.path.join(root, "package-lock.json"))
    packages = lock.get("packages") if isinstance(lock.get("packages"), dict) else None
    if packages and not pnpm:
        dev_source = "package-lock.json v%s dev flags" % lock.get("lockfileVersion", "?")
        for i in inst:
            i["dev"] = bool((packages.get(i["path"]) or {}).get("dev"))
    else:
        dev_source = "dependency graph from installed package.json files"
        prod = closure([k for k, (_, kind) in direct.items() if kind != "dev"], edges)
        for k, i in enumerate(inst):
            i["dev"] = k not in prod
        if lock and not packages:
            notes.append("package-lock.json has no \"packages\" map (lockfile v1), so dev-only weight comes "
                         "from the dependency graph")
    dev_bytes = sum(i["bytes"] for i in inst if i["dev"])

    heaviest = []
    for k in sorted(range(len(inst)), key=lambda x: -inst[x]["bytes"])[:top]:
        row = {f: inst[k][f] for f in ("name", "version", "path", "bytes", "files", "dev")}
        row["direct"] = k in direct
        row["pulled_in_by"] = [] if row["direct"] else chain_to(k, direct, parents, inst)
        heaviest.append(row)

    directs = []
    for k, (n, kind) in direct.items():
        members = closure([k], edges)
        directs.append({"name": n, "kind": kind, "bytes": sum(inst[m]["bytes"] for m in members),
                        "own_bytes": inst[k]["bytes"], "packages": len(members)})
    directs.sort(key=lambda d: (-d["bytes"], d["name"]))

    dupes = []
    for n, xs in by_name.items():
        if len(xs) > 1:
            sizes = [x["bytes"] for x in xs]
            dupes.append({"name": n, "copies": len(xs), "versions": sorted({x["version"] for x in xs}),
                          "extra_bytes": sum(sizes) - max(sizes)})
    dupes.sort(key=lambda d: (-d["extra_bytes"], d["name"]))

    dev_in_prod = sorted(n for n, k in kinds.items() if k == "prod" and (n in DEV_TOOLS or n.startswith(DEV_PREFIXES)))
    other = max(0, total["bytes"] - sum(i["bytes"] for i in inst))
    for d in sorted(os.listdir(nm)):  # .cache, .vite and similar build caches live here too
        full = os.path.join(nm, d)
        if d.startswith(".") and d not in (".bin", ".pnpm") and os.path.isdir(full) and not os.path.islink(full):
            size = measure(full)["bytes"]
            if size >= 1e6:
                notes.append("node_modules/%s is a tool cache, not a package: %s" % (d, human(size)))
    if total["hard_linked"]:
        notes.append("%s is in files with more than one hard link (pnpm, for one, links packages from "
                     "a shared store), so deleting this node_modules frees less than its size" % human(total["hard_linked"]))
    return dict(total, ecosystem="node", manager=manager, label="node_modules", packages=len(inst),
                unattributed_bytes=other, dev_only_bytes=dev_bytes, dev_source=dev_source, heaviest=heaviest,
                heaviest_direct=directs[:top], duplicates=dupes[:top], duplicated_packages=len(dupes), dev_tools_in_dependencies=dev_in_prod,
                suggestions=suggest("npm", by_name), notes=notes,
                dedupe_command={"npm": "npm dedupe", "pnpm": "pnpm dedupe"}.get(manager))


# ---------------------------------------------------------------- python

def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def site_packages(venv):
    hits = glob.glob(os.path.join(venv, "lib", "python*", "site-packages")) + \
        glob.glob(os.path.join(venv, "Lib", "site-packages"))
    return sorted(hits)


def read_dist(info, sp):
    name, version, requires = None, "?", set()
    try:
        with open(os.path.join(info, "METADATA"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    break  # headers end at the first blank line
                key, _, val = line.partition(":")
                val = val.strip()
                if key == "Name":
                    name = val
                elif key == "Version":
                    version = val
                elif key == "Requires-Dist" and not re.search(r"\bextra\s*==", val):
                    m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", val)
                    if m:
                        requires.add(norm(m.group(0)))
    except OSError:
        pass
    if not name:  # {name}-{version}.dist-info, both normalized with exactly one dash
        name, _, version = os.path.basename(info)[:-len(".dist-info")].partition("-")
    size = files = outside = outside_files = 0
    seen = set()
    prefix = os.path.normpath(sp) + os.sep
    try:
        with open(os.path.join(info, "RECORD"), encoding="utf-8", newline="") as fh:
            for row in csv.reader(fh):
                if not row or not row[0]:
                    continue
                p = os.path.normpath(os.path.join(sp, row[0]))  # relative to site-packages unless absolute
                if p in seen:
                    continue
                seen.add(p)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    size += st.st_size
                    files += 1
                    if not p.startswith(prefix):  # console scripts and binaries in the venv's bin/
                        outside += st.st_size
                        outside_files += 1
    except OSError:
        return None
    return {"name": name, "version": version, "bytes": size, "files": files, "requires": requires,
            "outside": outside, "outside_files": outside_files}


def weigh_python(venv, top):
    sps = site_packages(venv)
    if not sps:
        return None
    dists, total = [], {"bytes": 0, "files": 0, "on_disk": 0, "hard_linked": 0}
    for sp in sps:
        for k, v in measure(sp).items():
            total[k] += v
        for info in sorted(glob.glob(os.path.join(sp, "*.dist-info"))):
            d = read_dist(info, sp)
            if d:
                dists.append(d)
                total["bytes"] += d["outside"]
                total["on_disk"] += d["outside"]
                total["files"] += d["outside_files"]
    required_by = defaultdict(set)
    for d in dists:
        for r in d["requires"]:
            required_by[r].add(d["name"])
    rows = []
    for d in sorted(dists, key=lambda x: -x["bytes"])[:top]:
        rows.append({"name": d["name"], "version": d["version"], "bytes": d["bytes"], "files": d["files"],
                     "required_by": sorted(required_by.get(norm(d["name"]), ()))})
    by_name = {norm(d["name"]): [d] for d in dists}
    notes = ["bytecode caches written after install and .egg-info installs are not in any RECORD, so they "
             "count as unattributed"]
    return dict(total, ecosystem="python", manager="pip", label=os.path.basename(os.path.abspath(venv)),
                packages=len(dists), unattributed_bytes=max(0, total["bytes"] - sum(d["bytes"] for d in dists)),
                heaviest=rows, suggestions=suggest("python", by_name), notes=notes)


# ---------------------------------------------------------------- go

def gomodcache():
    if os.environ.get("GOMODCACHE"):
        return os.environ["GOMODCACHE"], None
    if not shutil.which("go"):
        return None, "go is not on PATH and GOMODCACHE is not set, so Go modules were skipped"
    try:
        out = subprocess.run(["go", "env", "GOMODCACHE"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return (out, None) if out else (None, "`go env GOMODCACHE` returned nothing, so Go modules were skipped")


def escape_path(s):
    """Module cache case-encoding: every uppercase letter becomes '!' plus the lowercase letter."""
    return re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), s)


def go_modules(root):
    """Modules whose source is needed: go.sum lines without /go.mod (go.mod requires if there is no go.sum)."""
    required, direct, summed = {}, set(), set()
    try:
        with open(os.path.join(root, "go.mod"), encoding="utf-8") as fh:
            block = False
            for line in fh:
                s = line.strip()
                if s.startswith("require ("):
                    block = True
                elif block and s == ")":
                    block = False
                else:
                    parts = s[len("require "):].split() if s.startswith("require ") else s.split() if block else []
                    if len(parts) >= 2:
                        required[parts[0]] = parts[1]
                        if "// indirect" not in s:
                            direct.add(parts[0])
    except OSError:
        pass
    try:
        with open(os.path.join(root, "go.sum"), encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 3 and not parts[1].endswith("/go.mod"):
                    summed.add((parts[0], parts[1]))
    except OSError:
        pass
    return sorted(summed or required.items()), direct


def weigh_go(root, top):
    cache, note = gomodcache()
    if not cache:
        return None, note
    mods, direct = go_modules(root)
    rows, missing = [], 0
    total = {"bytes": 0, "files": 0, "on_disk": 0, "hard_linked": 0}
    for path, version in mods:
        d = os.path.join(cache, escape_path(path) + "@" + escape_path(version))
        if not os.path.isdir(d):
            missing += 1
            continue
        m = measure(d)
        for k, v in m.items():
            total[k] += v
        rows.append({"name": path, "version": version, "bytes": m["bytes"], "files": m["files"],
                     "direct": path in direct})
    if not rows:
        return None, "no module from go.mod/go.sum is in the module cache yet (run `go mod download`)"
    notes = ["the module cache is shared by every Go project on this machine"]
    if missing:
        notes.append("%d modules listed in go.sum are not in the cache (only their go.mod may be needed)" % missing)
    rows.sort(key=lambda r: -r["bytes"])
    return dict(total, ecosystem="go", manager="go", label="Go modules", packages=len(rows), unattributed_bytes=0,
                heaviest=rows[:top], suggestions=[], notes=notes), None


# ---------------------------------------------------------------- comparing and suggesting

def suggest(eco, by_name):
    out = []
    for pattern, instead, why in ALTERNATIVES[eco]:
        if pattern.endswith("*"):
            hits = [n for n in by_name if n.startswith(pattern[:-1])]
        else:
            hits = [pattern] if pattern in by_name else []
        if hits:
            size = sum(i["bytes"] for n in hits for i in by_name[n])
            out.append({"package": pattern, "matched": sorted(hits), "bytes": size, "instead": instead, "why": why})
    return sorted(out, key=lambda s: -s["bytes"])


def compare(total):
    rows = [{"reference": plural, "bytes": b, "count": total / float(b)} for b, _, plural in REFERENCES]
    fits = [r for r in rows if r["count"] >= 1]
    head = fits[-1] if fits else None
    return rows, head


def weight_class(total):
    for limit, name in WEIGHT_CLASSES:
        if total < limit:
            return name
    return BLACK_HOLE


def human(n):
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return "%.1f %s" % (n / div, unit)
    return "%d B" % n


def count(x):
    return "{:,}".format(int(round(x))) if x >= 10 else "%.1f" % x if x >= 1 else "%.2f" % x


def headline(total):
    rows, head = compare(total)
    floppies = rows[2]["count"]
    if head is None:
        return "lighter than one %s" % REFERENCES[0][1]
    s = "about %s %s" % (count(head["count"]), head["reference"])
    if head["reference"] != "floppy disks":
        s += " (or %s floppy disks)" % count(floppies)
    return s


# ---------------------------------------------------------------- output

def report(data):
    out = ["DEPENDENCY WEIGH-IN  %s" % data["project"],
           "Total: %s in %s files, weight class: %s" % (human(data["bytes"]), "{:,}".format(data["files"]),
                                                       data["weight_class"].upper()),
           "That is %s." % data["headline"], ""]
    for e in data["ecosystems"]:
        out.append("== %s (%s): %s, %s files, %d packages, %s on disk"
                   % (e["label"], e["manager"], human(e["bytes"]), "{:,}".format(e["files"]), e["packages"],
                      human(e["on_disk"])))
        out.append("Heaviest packages (own files):")
        for i, r in enumerate(e["heaviest"], 1):
            tag = "  dev" if r.get("dev") else "  indirect" if e["ecosystem"] == "go" and not r["direct"] else ""
            out.append("  %2d. %-44s %10s%s" % (i, (r["name"] + " " + r["version"])[:44], human(r["bytes"]), tag))
            if r.get("pulled_in_by"):
                out.append("      pulled in by " + " > ".join(r["pulled_in_by"]))
            if r.get("required_by"):
                out.append("      required by " + ", ".join(r["required_by"][:4]))
        if e.get("heaviest_direct"):
            out.append("Heaviest direct dependencies, with what they pull in (shared deps count in each):")
            for d in e["heaviest_direct"]:
                out.append("  %-34s %10s  %4d packages  (%s)" % (d["name"][:34], human(d["bytes"]), d["packages"],
                                                                 d["kind"]))
        if e.get("dev_only_bytes") is not None and e["ecosystem"] == "node":
            out.append("Dev-only weight: %s (from %s)." % (human(e["dev_only_bytes"]), e["dev_source"]))
        if e.get("duplicates"):
            cmd = e.get("dedupe_command")
            out.append("Duplicate copies you could dedupe (suggestion%s):" % (", try `%s`" % cmd if cmd else ""))
            for d in e["duplicates"]:
                out.append("  %-30s %d copies of %s, %s in extra copies"
                           % (d["name"][:30], d["copies"], ", ".join(d["versions"]), human(d["extra_bytes"])))
        if e.get("dev_tools_in_dependencies"):
            out.append("Usually dev-only but listed under dependencies (suggestion): "
                       + ", ".join(e["dev_tools_in_dependencies"]))
        if e.get("suggestions"):
            out.append("Lighter or maintained alternatives (suggestions, check before switching):")
            for s in e["suggestions"]:
                out.append("  %s (%s now) -> %s: %s" % (s["package"], human(s["bytes"]), s["instead"], s["why"]))
        if e.get("unattributed_bytes"):
            out.append("Not in any package: %s" % human(e["unattributed_bytes"]))
        for n in e["notes"]:
            out.append("Note: " + n)
        out.append("")
    for n in data["notes"]:
        out.append("Note: " + n)
    out.append("Compared with:")
    for r in data["comparisons"]:
        out.append("  %s %s" % (count(r["count"]), r["reference"]))
    return "\n".join(out).rstrip() + "\n"


CREDIT = "made with github.com/milad-ahmd/agent-skills"


def credit(svg, on=True):
    """Sign the card in its free top-right corner so a shared card links back; --no-credit turns it off."""
    if not on:
        return svg
    return svg.replace("</svg>", '<text x="1160" y="44" font-size="16" fill="#71717a" text-anchor="end">%s</text>\n</svg>' % CREDIT)


def svg_card(data):
    bg, panel, fg, muted, accent = "#111318", "#1c1f27", "#f4f4f5", "#a1a1aa", "#ff7a45"
    el = ['<rect width="1200" height="630" fill="%s"/>' % bg,
          '<rect x="0" y="0" width="12" height="630" fill="%s"/>' % accent]

    def text(x, y, body, size, color, weight="400", anchor="start"):
        el.append('<text x="%d" y="%d" font-size="%d" font-weight="%s" fill="%s" text-anchor="%s">%s</text>'
                  % (x, y, size, weight, color, anchor, escape(body)))

    text(60, 72, "DEPENDENCY WEIGH-IN", 24, accent, "700")
    text(60, 160, human(data["bytes"]), 96, fg, "800")
    text(60, 210, data["weight_class"].upper(), 32, accent, "700")
    text(60, 256, ("= " + data["headline"])[:80], 26, muted)
    rows = sorted((r for e in data["ecosystems"] for r in e["heaviest"]), key=lambda r: -r["bytes"])[:5]
    peak = float(rows[0]["bytes"]) if rows and rows[0]["bytes"] else 1.0
    text(60, 320, "Heaviest packages", 24, fg, "700")
    for i, r in enumerate(rows):
        y = 344 + i * 46
        w = max(4, int(560 * r["bytes"] / peak))
        el.append('<rect x="60" y="%d" width="%d" height="30" rx="6" fill="%s"/>' % (y, w, panel))
        el.append('<rect x="60" y="%d" width="%d" height="30" rx="6" fill="%s" opacity="0.85"/>' % (y, w, accent))
        text(640, y + 23, "%s  %s" % (r["name"][:30], human(r["bytes"])), 22, fg)
    dupes = sum(e.get("duplicated_packages", 0) for e in data["ecosystems"])
    foot = ["{:,} files".format(data["files"]), "%d packages" % sum(e["packages"] for e in data["ecosystems"])]
    if dupes:
        foot.append("%d duplicated packages" % dupes)
    dev = sum(e.get("dev_only_bytes") or 0 for e in data["ecosystems"])
    if dev:
        foot.append("%s dev-only" % human(dev))
    text(60, 600, "  ·  ".join(foot), 22, muted)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" '
            'font-family="Inter, \'Helvetica Neue\', Arial, sans-serif">\n<title>%s</title>\n%s\n</svg>\n'
            % (escape("Dependency weigh-in: %s, %s" % (human(data["bytes"]), data["weight_class"])), "\n".join(el)))


def weigh(root, venv=None, top=10):
    root = os.path.abspath(root)
    ecos, notes = [], []
    nm = os.path.join(root, "node_modules")
    if os.path.isdir(nm):
        ecos.append(weigh_node(root, top))
    elif os.path.exists(os.path.join(root, ".pnp.cjs")):
        notes.append("Yarn Plug'n'Play keeps packages as zip files in .yarn/cache; only node_modules is weighed")
    if venv is None:
        venv = next((os.path.join(root, v) for v in (".venv", "venv")
                     if os.path.isfile(os.path.join(root, v, "pyvenv.cfg"))), None)
    if venv:
        py = weigh_python(venv, top)
        if py:
            ecos.append(py)
        else:
            notes.append("no site-packages found under %s" % venv)
    if os.path.isfile(os.path.join(root, "go.mod")):
        go, note = weigh_go(root, top)
        if go:
            ecos.append(go)
        if note:
            notes.append(note)
    ecos = [e for e in ecos if e["bytes"] or e["packages"]]
    total = sum(e["bytes"] for e in ecos)
    rows, _ = compare(total)
    return {"project": os.path.basename(root), "bytes": total, "files": sum(e["files"] for e in ecos),
            "weight_class": weight_class(total), "headline": headline(total), "comparisons": rows,
            "ecosystems": ecos, "notes": notes}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Exit codes: 0 weighed, 1 SVG could not be written, 2 nothing installed to weigh")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--venv")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.add_argument("--svg")
    p.add_argument("--no-credit", action="store_true", help="leave the \"made with agent-skills\" line off the card")
    args = p.parse_args(argv)
    data = weigh(args.project, args.venv, max(1, args.top))
    if not data["ecosystems"]:
        print("Nothing installed to weigh in %s. Install the dependencies first (for example `npm ci`, "
              "`pnpm install`, `pip install -r requirements.txt` in a venv, or `go mod download`), then run "
              "this again. This script never installs anything." % os.path.abspath(args.project), file=sys.stderr)
        for n in data["notes"]:
            print("Note: " + n, file=sys.stderr)
        return 2
    if args.svg:
        try:
            with open(args.svg, "w", encoding="utf-8") as fh:
                fh.write(credit(svg_card(data), not args.no_credit))
        except OSError as e:
            print("could not write %s: %s" % (args.svg, e), file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        sys.stdout.write(report(data))
        if args.svg:
            print("\ncard written to " + args.svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
