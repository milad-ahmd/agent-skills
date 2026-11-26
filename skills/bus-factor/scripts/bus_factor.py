#!/usr/bin/env python3
"""Find the code only one person knows and estimate a repository's bus factor. Stdlib only, read-only.

Usage: python3 bus_factor.py [-C REPO] [PATH ...] [--days 365] [--now YYYY-MM-DD] [--max-files 2000]
                             [--top 10] [--include-docs] [--alias FROM=TO ...] [--show-authors]
                             [--json] [--svg card.svg]
  -C REPO         repository to scan (default: current directory)
  PATH            limit the scan to these paths (pathspecs relative to the repo root)
  --days N        recency window: who touched a file in the last N days (default 365)
  --now DATE      end of the window (default: today); makes reports reproducible
  --max-files N   blame at most N files, the largest first (default 2000, 0 = all); the report says when capped
  --top N         rows in each text table (default 10)
  --include-docs  also scan docs (*.md, *.rst, *.txt, docs/, LICENSE, CHANGELOG ...)
  --alias FROM=TO count the identity whose name or email is FROM as the person named TO (repeatable);
                  .mailmap is applied first, by git itself
  --show-authors  print real names and emails; by default people are Author A, B, C ... (A owns the most
                  lines in this run; labels are stable within one run, not across runs)
  --json          machine-readable report on stdout
  --svg FILE      write a shareable 1200x630 card (no names unless --show-authors)

How it measures (files at HEAD; lockfiles, vendored, minified, generated and binary files skipped):
  ownership   `git blame --line-porcelain -w HEAD` per file, lines per person (mailmap applied by git)
  recency     `git log --use-mailmap --name-only` over the window: who touched each file, and who is
              active in the repo at all
  knowers     a person knows a file if they own >= 20% of its lines and are active in the repo in the
              window, or if they made >= 2 commits to the file in the window
  at risk     one person owns >= 80% of the file and at most one other person touched it in the window;
              knowledge at risk = lines x owner share. "orphaned" = nobody active knows the file
  bus factor  the smallest number of people whose departure leaves more than 50% of the scanned lines
              with no knower. Exact search over combinations of people while that stays cheap, then a
              greedy fallback that removes people in order of lines known (Algorithm 1 of Avelino et al.,
              "A novel approach for estimating truck factors", ICPC 2016, which does the same with files
              authored). The report says which method produced the number.

Exit codes: 0 report written
            2 not a git repository, no commits, a git error, or nothing to scan
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import itertools
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
OWNER_RISK = 0.8      # owner share that makes a file single-owner
MAX_OTHERS = 1        # at most this many other recent authors and the file still counts as at risk
KNOWER_SHARE = 0.2    # an active person owning this share of a file knows it
KNOWER_COMMITS = 2    # ... and so does anyone with this many commits to it in the window (one typo fix is not enough)
EXACT_BUDGET = 2000000  # knower-set checks allowed in the exact search before switching to greedy

LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
             "Cargo.lock", "poetry.lock", "Pipfile.lock", "uv.lock", "Gemfile.lock", "composer.lock", "go.sum",
             "mix.lock", "flake.lock", "Podfile.lock", "packages.lock.json", "pubspec.lock", "gradle.lockfile"}
GENERATED = ["*.min.js", "*.min.css", "*.map", "*.pb.go", "*_pb2.py", "*_pb2_grpc.py", "*.generated.*",
             "*.g.dart", "*.snap", "*.svg", "*.ipynb", "*.csv", "*.tsv", "*.lock"]
VENDOR_DIRS = {"node_modules", "vendor", "third_party", "third-party", "dist", "build", "__generated__",
               "generated", "bower_components", "Pods", ".yarn"}
GIT_META = {".mailmap", ".gitignore", ".gitattributes", ".gitmodules", ".git-blame-ignore-revs"}
DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc", ".asciidoc"}
DOC_DIRS = {"docs", "doc"}
DOC_NAMES = ("LICENSE", "LICENCE", "COPYING", "CHANGELOG", "CHANGES", "AUTHORS", "CONTRIBUTORS", "NOTICE")


class GitError(Exception):
    pass


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, stdin=subprocess.DEVNULL)
    if check and r.returncode:
        raise GitError(r.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
    return r


def skip_reason(path, include_docs=False):
    """Why a path is not scanned, or None to scan it."""
    parts = path.split("/")
    name = parts[-1]
    if name in LOCKFILES:
        return "lockfile"
    if name in GIT_META:
        return "git metadata"
    if any(p in VENDOR_DIRS for p in parts[:-1]):
        return "vendored"
    if any(fnmatch.fnmatch(name, g) for g in GENERATED):
        return "generated"
    if not include_docs and (os.path.splitext(name)[1].lower() in DOC_EXT or name.upper().startswith(DOC_NAMES)
                             or any(p.lower() in DOC_DIRS for p in parts[:-1])):
        return "docs"
    return None


def list_files(repo, paths, include_docs):
    """(path, lines) for text files at HEAD, plus Counter of skip reasons."""
    empty = git(repo, "hash-object", "-t", "tree", "--stdin").stdout.decode().strip()  # works for sha1 and sha256
    out = git(repo, "diff", "--numstat", "-z", "--no-renames", empty, "HEAD", "--", *paths).stdout
    files, skipped = [], Counter()
    for rec in out.decode("utf-8", "replace").split("\0"):
        if not rec:
            continue
        added, _deleted, path = rec.split("\t", 2)
        if added == "-":
            skipped["binary"] += 1
            continue
        reason = skip_reason(path, include_docs)
        if reason:
            skipped[reason] += 1
        elif int(added):
            files.append((path, int(added)))
    return files, skipped


def blame(repo, path):
    """Counter of (name, email) -> lines at HEAD, or None if git could not blame the file."""
    r = git(repo, "blame", "--line-porcelain", "-w", "HEAD", "--", path, check=False)
    if r.returncode:
        return None
    counts, name = Counter(), ""
    for line in r.stdout.split(b"\n"):
        if line.startswith(b"author "):
            name = line[7:].decode("utf-8", "replace")
        elif line.startswith(b"author-mail "):
            counts[(name, line[12:].decode("utf-8", "replace").strip("<>"))] += 1
    return counts


def recent_activity(repo, since, until, paths):
    """(file -> Counter(raw identity -> commits), Counter(raw identity -> commits in repo)) in the window."""
    fmt = "--format=%x01%aN%x00%aE%x00%ct"
    out = git(repo, "log", "--use-mailmap", "--no-merges", "--name-only", "-z", fmt,
              "--since=%s" % since.strftime("%Y-%m-%dT%H:%M:%SZ"), "HEAD", "--", *paths).stdout
    per_file, active = defaultdict(Counter), Counter()
    for rec in out.decode("utf-8", "replace").split("\x01")[1:]:
        fields = rec.split("\0")  # name, email, time, "\nfirst/path", "path", ...
        if int(fields[2]) > until.timestamp():
            continue
        who = (fields[0], fields[1])
        active[who] += 1
        for f in fields[3:]:
            f = f.lstrip("\n")
            if f:
                per_file[f][who] += 1
    return per_file, active


def make_resolver(aliases):
    table = {}
    for a in aliases:
        if "=" not in a:
            raise SystemExit("--alias expects FROM=TO, got %r" % a)
        src, dst = a.split("=", 1)
        table[src.strip().lower()] = dst.strip()

    def resolve(ident):
        name, email = ident
        target = table.get(email.lower()) or table.get(name.lower()) or name
        return " ".join(target.split()).lower(), target, email
    return resolve


def letters(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def bus_factor(groups, total):
    """groups: {frozenset(knowers): lines}. Returns (factor, removed people, method)."""
    half = total / 2.0

    def orphaned(removed):
        return sum(n for ks, n in groups.items() if ks <= removed)

    if orphaned(frozenset()) > half:
        return 0, [], "exact"
    known = Counter()
    for ks, n in groups.items():
        for p in ks:
            known[p] += n
    people = sorted(known, key=lambda p: (-known[p], p))
    spent, k_floor = 0, 1
    for k in range(1, len(people) + 1):
        n_combos = 1
        for i in range(k):
            n_combos = n_combos * (len(people) - i) // (i + 1)
        if spent + n_combos * len(groups) > EXACT_BUDGET:
            break
        spent += n_combos * len(groups)
        for combo in itertools.combinations(people, k):
            if orphaned(frozenset(combo)) > half:
                return k, list(combo), "exact"
        k_floor = k + 1
    removed = []
    for p in people:  # Avelino et al.'s Algorithm 1: drop the top author until coverage falls below 50%
        removed.append(p)
        if orphaned(frozenset(removed)) > half:
            break
    method = "greedy" if k_floor == 1 else "greedy (exact search ruled out fewer than %d)" % k_floor
    return len(removed), removed, method


def analyse(repo, paths=(), days=365, now=None, max_files=2000, include_docs=False, aliases=(), workers=None):
    if git(repo, "rev-parse", "--is-inside-work-tree", check=False).returncode:
        raise GitError("not a git repository: %s" % repo)
    if git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode:
        raise GitError("the repository has no commits")
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=days)
    files, skipped = list_files(repo, paths, include_docs)
    all_count, all_lines = len(files), sum(n for _, n in files)
    files.sort(key=lambda f: (-f[1], f[0]))
    capped = bool(max_files) and len(files) > max_files
    if capped:
        files = files[:max_files]
    with ThreadPoolExecutor(max_workers=workers or min(8, (os.cpu_count() or 2) * 2)) as pool:
        blames = list(pool.map(lambda f: blame(repo, f[0]), files))
    per_file_raw, active_raw = recent_activity(repo, since, now, paths)

    resolve = make_resolver(aliases)
    names, emails = {}, defaultdict(set)

    def key(ident):
        k, shown, email = resolve(ident)
        names.setdefault(k, shown)
        emails[k].add(email)
        return k

    active = {key(i) for i in active_raw}
    owned_total, rows, failed = Counter(), [], 0
    for (path, _), counts in zip(files, blames):
        if not counts:
            failed += 1
            continue
        owned = Counter()
        for ident, n in counts.items():
            owned[key(ident)] += n
        recent = Counter()
        for ident, n in per_file_raw.get(path, {}).items():
            recent[key(ident)] += n
        lines = sum(owned.values())
        owner, top = min(owned.items(), key=lambda kv: (-kv[1], kv[0]))
        share = top / float(lines)
        knowers = frozenset([p for p, n in owned.items() if p in active and n >= KNOWER_SHARE * lines]
                            + [p for p, n in recent.items() if n >= KNOWER_COMMITS])
        others = [p for p in recent if p != owner]
        at_risk = share >= OWNER_RISK and len(others) <= MAX_OTHERS
        owned_total.update(owned)
        rows.append({"path": path, "lines": lines, "owner": owner, "owner_share": round(share, 3),
                     "owner_active": owner in active, "recent_authors": len(recent), "churn": sum(recent.values()),
                     "knowers": knowers, "at_risk": at_risk,
                     "status": "orphaned" if not knowers else "single owner" if at_risk else "ok",
                     "risk": round(lines * share) if at_risk else 0})
    if not rows:
        raise GitError("nothing to scan: no text source files at HEAD%s" % (" under the given paths" if paths else ""))

    order = sorted(names, key=lambda p: (-owned_total[p], p))  # includes active people who own no lines
    label = {p: "Author %s" % letters(i) for i, p in enumerate(order)}
    total = sum(r["lines"] for r in rows)
    groups = Counter()
    for r in rows:
        groups[r["knowers"]] += r["lines"]
    factor, removed, method = bus_factor(groups, total)
    orphaned = sum(n for ks, n in groups.items() if not ks)
    after = sum(n for ks, n in groups.items() if ks <= frozenset(removed))

    dirs = {}
    for r in rows:
        d = os.path.dirname(r["path"]) or "."
        e = dirs.setdefault(d, {"dir": d, "files": 0, "lines": 0, "risk": 0, "risky_files": 0, "churn": 0,
                                "_owned": Counter(), "_knowers": set()})
        e["files"] += 1
        e["lines"] += r["lines"]
        e["risk"] += r["risk"]
        e["risky_files"] += r["at_risk"]
        e["churn"] += r["churn"]
        e["_owned"][r["owner"]] += r["lines"]
        e["_knowers"] |= r["knowers"]
    for e in dirs.values():
        owner, n = min(e.pop("_owned").items(), key=lambda kv: (-kv[1], kv[0]))
        e.update(owner=owner, owner_share=round(n / float(e["lines"]), 3), knowers=len(e.pop("_knowers")),
                 risk_pct=round(100.0 * e["risk"] / e["lines"], 1))

    sole = defaultdict(list)
    for r in rows:
        if len(r["knowers"]) == 1:
            sole[next(iter(r["knowers"]))].append(r)
    people = []
    for p in order:
        mine = sorted(sole.get(p, []), key=lambda r: (-r["lines"], r["path"]))
        people.append({"person": p, "lines_owned": owned_total[p], "active": p in active,
                       "only_knower_files": len(mine), "only_knower_lines": sum(r["lines"] for r in mine),
                       "only_knower_paths": [r["path"] for r in mine]})

    return {"rows": rows, "dirs": list(dirs.values()), "people": people, "label": label, "names": names,
            "emails": emails, "factor": factor, "removed": removed, "method": method, "total_lines": total,
            "orphaned_lines": orphaned, "orphaned_after": after, "files_scanned": len(rows),
            "files_total": all_count, "lines_total": all_lines, "capped": capped, "blame_failed": failed,
            "skipped": dict(skipped), "days": days, "since": since.date().isoformat(), "now": now.date().isoformat(),
            "active_people": len(active)}


def who(rep, p, show):
    if not show:
        return rep["label"][p]
    return "%s <%s>" % (rep["names"][p], ", ".join(sorted(rep["emails"][p])))


def pct(a, b):
    return 100.0 * a / b if b else 0.0


def headline(rep):
    if rep["factor"] == 0:
        return "Bus factor: 0 (%.0f%% of the code already has no active person who knows it)" % pct(
            rep["orphaned_lines"], rep["total_lines"])
    return "Bus factor: %d" % rep["factor"]


def to_json(rep, show):
    def person(p):
        return who(rep, p, show)
    files = sorted(rep["rows"], key=lambda r: (-r["risk"], -r["lines"], r["path"]))
    return {
        "bus_factor": rep["factor"], "method": rep["method"],
        "removed": [person(p) for p in rep["removed"]],
        "orphaned_after_removal_pct": round(pct(rep["orphaned_after"], rep["total_lines"]), 1),
        "orphaned_now_pct": round(pct(rep["orphaned_lines"], rep["total_lines"]), 1),
        "lines_scanned": rep["total_lines"], "files_scanned": rep["files_scanned"],
        "files_candidate": rep["files_total"], "capped": rep["capped"], "blame_failed": rep["blame_failed"],
        "skipped": rep["skipped"], "window": {"days": rep["days"], "since": rep["since"], "until": rep["now"]},
        "active_people": rep["active_people"],
        "rules": {"owner_risk_share": OWNER_RISK, "max_other_recent_authors": MAX_OTHERS,
                  "knower_min_share": KNOWER_SHARE, "knower_min_recent_commits": KNOWER_COMMITS},
        "files": [dict(r, owner=person(r["owner"]), knowers=sorted(person(p) for p in r["knowers"])) for r in files],
        "dirs": [dict(d, owner=person(d["owner"])) for d in sorted(rep["dirs"], key=lambda d: (-d["risk"], d["dir"]))],
        "people": [dict(p, person=person(p["person"])) for p in rep["people"]],
    }


def text_report(rep, show, top):
    out = [headline(rep)]
    T = rep["total_lines"]
    if rep["factor"]:
        gone = [who(rep, p, show) for p in rep["removed"]]
        out.append("  Losing %s would leave %.0f%% of the scanned code with nobody active who knows it."
                   % (", ".join(gone[:8]) + (" and %d more" % (len(gone) - 8) if len(gone) > 8 else ""),
                      pct(rep["orphaned_after"], T)))
    out.append("  Method: %s. Already orphaned today: %.0f%% of lines." % (rep["method"], pct(rep["orphaned_lines"], T)))
    scope = "Scanned %d files, %s lines; %d people active in the last %d days (%s to %s)." % (
        rep["files_scanned"], format(T, ","), rep["active_people"], rep["days"], rep["since"], rep["now"])
    out.append("  " + scope)
    if rep["capped"]:
        out.append("  Capped: blamed the %d largest of %d files (--max-files)." % (rep["files_scanned"], rep["files_total"]))
    if rep["blame_failed"]:
        out.append("  %d files could not be blamed and were left out." % rep["blame_failed"])
    if rep["skipped"]:
        out.append("  Skipped: " + ", ".join("%d %s" % (n, k) for k, n in sorted(rep["skipped"].items())))

    risky = sorted((r for r in rep["rows"] if r["at_risk"]), key=lambda r: (-r["risk"], r["path"]))
    out.append("")
    out.append("Riskiest files (one person owns >= %d%%, at most %d other recent author)" % (OWNER_RISK * 100, MAX_OTHERS))
    if not risky:
        out.append("  none")
    for r in risky[:top]:
        out.append("  %6s lines  %3.0f%% %s  recent %d  churn %d  %s  %s" % (
            format(r["lines"], ","), r["owner_share"] * 100, who(rep, r["owner"], show),
            r["recent_authors"], r["churn"], r["status"], r["path"]))
    if len(risky) > top:
        out.append("  ... %d more (--top or --json)" % (len(risky) - top))

    dirs = sorted((d for d in rep["dirs"] if d["risk"]), key=lambda d: (-d["risk"], d["dir"]))
    out.append("")
    out.append("Riskiest directories (knowledge at risk = lines x owner share in single-owner files)")
    if not dirs:
        out.append("  none")
    for d in dirs[:top]:
        out.append("  %6s at risk  %3.0f%% of %-7s  %d/%d files  knowers %d  top owner %s %.0f%%  %s" % (
            format(d["risk"], ","), d["risk_pct"], format(d["lines"], ","), d["risky_files"], d["files"],
            d["knowers"], who(rep, d["owner"], show), d["owner_share"] * 100, d["dir"] + "/"))

    out.append("")
    out.append("Only you know (files where one person is the only active knower)")
    sole = sorted((p for p in rep["people"] if p["only_knower_files"]), key=lambda p: -p["only_knower_lines"])
    for p in sole[:top]:
        dir_counts = Counter((os.path.dirname(x) or ".") + "/" for x in p["only_knower_paths"])
        where = ", ".join("%s (%d)" % kv for kv in sorted(dir_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:4])
        out.append("  %s: %d files, %s lines%s  %s" % (
            who(rep, p["person"], show), p["only_knower_files"], format(p["only_knower_lines"], ","),
            "" if p["active"] else " (inactive in the window)", where))
    if not sole:
        out.append("  none")
    elif len(sole) > top:
        out.append("  ... %d more people" % (len(sole) - top))
    gone = [p for p in rep["people"] if not p["active"]]
    if gone:
        out.append("")
        out.append("Owners with no commits in the window: %s, holding %s lines of blame."
                   % (", ".join(who(rep, p["person"], show) for p in gone[:6]) + (" ..." if len(gone) > 6 else ""),
                      format(sum(p["lines_owned"] for p in gone), ",")))
    return "\n".join(out) + "\n"


COLORS = ["#e4572e", "#f3a712", "#29335c", "#669bbc", "#a8c686", "#8e6c8a", "#b0b0b0"]


def svg_card(rep, show):
    T = rep["total_lines"]
    el = ['<rect width="1200" height="630" fill="#14161c"/>',
          '<rect x="0" y="0" width="12" height="630" fill="#e4572e"/>']

    def text(x, y, body, size, color="#f4f4f5", weight="400"):
        el.append('<text x="%d" y="%d" font-size="%d" font-weight="%s" fill="%s">%s</text>'
                  % (x, y, size, weight, color, escape(body)))

    text(60, 80, "BUS FACTOR", 26, "#e4572e", "700")
    text(60, 230, str(rep["factor"]), 170, weight="800")
    sub = ("more than half the code already has no active owner" if rep["factor"] == 0 else
           "%s leaving would orphan %.0f%% of the code" % (
               "1 person" if rep["factor"] == 1 else "%d people" % rep["factor"], pct(rep["orphaned_after"], T)))
    text(300, 170, sub, 30, "#d4d4d8")
    single = sum(r["lines"] for r in rep["rows"] if r["at_risk"])
    text(300, 215, "%.0f%% of lines live in files one person owns" % pct(single, T), 26, "#a1a1aa")
    text(60, 330, "Who owns the code (share of lines, git blame)", 24, weight="700")
    slices = [(rep["label"][p["person"]] if not show else rep["names"][p["person"]], p["lines_owned"])
              for p in rep["people"][:6]]
    rest = T - sum(n for _, n in slices)
    if rest > 0:
        slices.append(("everyone else", rest))
    x = 60
    for i, (name, n) in enumerate(slices):
        w = 1080.0 * n / T
        el.append('<rect x="%.1f" y="355" width="%.1f" height="56" fill="%s"/>' % (x, max(w, 1), COLORS[i % 7]))
        x += w
    for i, (name, n) in enumerate(slices):
        cx, cy = 60 + (i % 4) * 280, 455 + (i // 4) * 44
        el.append('<rect x="%d" y="%d" width="22" height="22" rx="4" fill="%s"/>' % (cx, cy - 18, COLORS[i % 7]))
        text(cx + 32, cy, "%s %.0f%%" % (name[:16], pct(n, T)), 22, "#d4d4d8")
    text(60, 600, "%s files, %s lines, %d active in the last %d days" % (
        format(rep["files_scanned"], ","), format(T, ","), rep["active_people"], rep["days"]), 20, "#71717a")
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" '
            'font-family="Inter, \'Helvetica Neue\', Arial, sans-serif">\n<title>%s</title>\n%s\n</svg>\n'
            % (escape(headline(rep)), "\n".join(el)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__[__doc__.index("How it measures"):])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("-C", dest="repo", default=".")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--now", help="YYYY-MM-DD, end of the recency window (default today)")
    ap.add_argument("--max-files", type=int, default=2000)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--include-docs", action="store_true")
    ap.add_argument("--alias", action="append", default=[], metavar="FROM=TO")
    ap.add_argument("--show-authors", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--svg", metavar="FILE")
    a = ap.parse_args(argv)
    now = None
    if a.now:
        now = dt.datetime.strptime(a.now, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=dt.timezone.utc)
    try:
        rep = analyse(a.repo, a.paths, a.days, now, a.max_files, a.include_docs, a.alias)
    except GitError as e:
        print("bus_factor: %s" % e, file=sys.stderr)
        return 2
    if a.svg:
        with open(a.svg, "w", encoding="utf-8") as fh:
            fh.write(svg_card(rep, a.show_authors))
    if a.json:
        json.dump(to_json(rep, a.show_authors), sys.stdout, indent=2)
        print()
    else:
        sys.stdout.write(text_report(rep, a.show_authors, a.top))
        if a.svg:
            print("\nCard written to %s" % a.svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
