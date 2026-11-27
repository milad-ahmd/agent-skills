#!/usr/bin/env python3
"""Find why a git repository is heavy to clone: the size of .git against the checkout, the biggest
blobs ever committed (and whether they are already deleted), junk committed right now (.DS_Store,
node_modules, venvs, build output, logs, archives), and large binaries that are Git LFS candidates.
Stdlib only and read-only: it never rewrites history, never runs git filter-repo and never pushes.

Usage: python3 repo_diet.py [-C REPO] [--top N] [--json] [--svg card.svg] [--show-authors]
  -C REPO         repository to inspect (default: current directory); a bare clone works too
  --top N         rows per list (default 10)
  --json          everything as JSON on stdout
  --svg FILE      also write a 1200x630 diet card
  --show-authors  include the author of the commit that added each big blob (hidden by default)

"History" is every object reachable from any ref (git rev-list --objects --all), sized with
%(objectsize:disk), the compressed size in the packs, which is what a clone downloads. A blob is
"deleted" when its path no longer exists in HEAD and "old version" when the path exists with other
content. MB = 1,000,000 bytes.

Exit codes: 0 lean, nothing worth fixing
            1 findings (junk committed now, LFS candidates, or deleted files weighing down history)
            2 not a git repository
            3 the SVG file could not be written
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import shlex
import subprocess
import sys
from xml.sax.saxutils import escape

MB = 1000 * 1000
LFS_MIN = 1 * MB          # binaries at or above this are LFS candidates
HISTORY_MIN = 1 * MB      # deleted files must weigh at least this much ...
HISTORY_SHARE = 0.10      # ... and be at least this share of history to count as a finding
BANDWIDTH_MBIT = 100      # stated assumption for the clone-time comparison, not a measurement

# Sources for every figure: references/sources.md
REFERENCES = [  # (bytes, plural)
    (1474560, "floppy disks"),
    (4196020, "copies of shareware Doom"),
    (737280000, "CD-ROMs"),
]

DIR_JUNK = [  # (category, directory name, .gitignore line)
    ("dependencies", "node_modules", "node_modules/"),
    ("dependencies", "bower_components", "bower_components/"),
    ("virtualenv", ".venv", ".venv/"),
    ("virtualenv", "venv", "venv/"),
    ("python cache", "__pycache__", "__pycache__/"),
    ("build output", "dist", "dist/"),
    ("build output", "build", "build/"),
    ("build output", "target", "target/"),
]
FILE_JUNK = [  # (category, suffix or exact name, .gitignore line)
    ("OS junk", ".DS_Store", ".DS_Store"),
    ("OS junk", "Thumbs.db", "Thumbs.db"),
    ("python cache", ".pyc", "*.pyc"),
    ("python cache", ".pyo", "*.pyo"),
    ("build output", ".class", "*.class"),
    ("build output", ".o", "*.o"),
    ("logs", ".log", "*.log"),
    ("archives", ".zip", None),
    ("archives", ".tar", None),
    ("archives", ".tar.gz", None),
    ("archives", ".tgz", None),
    ("archives", ".7z", None),
    ("archives", ".rar", None),
]
FIXTURE_DIRS = {"fixtures", "__fixtures__", "testdata", "test_data"}
LOCKFILES = {"go.sum", "composer.lock", "Gemfile.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
             "Cargo.lock", "poetry.lock", "uv.lock", "Pipfile.lock"}
BINARY_EXT = {".psd", ".ai", ".sketch", ".fig", ".blend", ".fbx", ".obj", ".max", ".tif", ".tiff", ".png",
              ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wav",
              ".mp3", ".flac", ".ogg", ".pdf", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".jar", ".war",
              ".dll", ".exe", ".so", ".dylib", ".bin", ".iso", ".dmg", ".apk", ".ipa", ".onnx", ".pt", ".pth",
              ".h5", ".ckpt", ".safetensors", ".parquet", ".sqlite", ".db"}


class GitError(Exception):
    pass


def git(repo, *args, stdin=None, check=True):
    p = subprocess.run(["git", "-C", repo] + list(args), input=stdin, capture_output=True)
    if check and p.returncode != 0:
        raise GitError(p.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
    return p.stdout.decode("utf-8", "surrogateescape")


# ---------------------------------------------------------------- reading the repo

def pack_size(repo):
    """Bytes of the object database: loose + packs + garbage, from `git count-objects -v` (KiB)."""
    kib = {}
    for line in git(repo, "count-objects", "-v").splitlines():
        key, _, val = line.partition(":")
        if key in ("size", "size-pack", "size-garbage"):
            kib[key] = int(val.strip() or 0)
    return {"loose": kib.get("size", 0) * 1024, "packed": kib.get("size-pack", 0) * 1024,
            "garbage": kib.get("size-garbage", 0) * 1024,
            "total": sum(kib.values()) * 1024}


def head_files(repo):
    """{path: (blob sha, size)} for every file in HEAD; empty for a repo without commits."""
    if git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False).strip() == "":
        return {}
    out = {}
    for rec in git(repo, "ls-tree", "-r", "-l", "-z", "HEAD").split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        mode, typ, sha, size = meta.split()
        if typ == "blob":
            out[path] = (sha, int(size))
    return out


def history_objects(repo):
    """All reachable objects with type, size and on-disk size; blobs carry the path rev-list saw first."""
    # shortcut: rev-list --objects prints paths unquoted, so a path containing a newline is misread;
    # switch to --objects with -z once git 2.47+ can be assumed.
    rl = subprocess.Popen(["git", "-C", repo, "rev-list", "--objects", "--all"], stdout=subprocess.PIPE)
    fmt = "--batch-check=%(objecttype) %(objectname) %(objectsize) %(objectsize:disk) %(rest)"
    cf = subprocess.run(["git", "-C", repo, "cat-file", fmt], stdin=rl.stdout, capture_output=True)
    rl.stdout.close()
    rl.wait()
    if rl.returncode != 0 or cf.returncode != 0:
        raise GitError(cf.stderr.decode("utf-8", "replace").strip() or "rev-list/cat-file failed")
    total, blobs = 0, []
    for line in cf.stdout.decode("utf-8", "surrogateescape").splitlines():
        parts = line.split(" ", 4)
        if len(parts) < 4 or parts[1] == "missing":
            continue
        disk = int(parts[3])
        total += disk
        if parts[0] == "blob":
            blobs.append({"sha": parts[1], "bytes": int(parts[2]), "disk": disk,
                          "path": parts[4] if len(parts) > 4 else ""})
    return total, blobs


def added_by(repo, sha, show_authors):
    fmt = "%H%x00%ad%x00%an" if show_authors else "%H%x00%ad"
    out = git(repo, "log", "--all", "--reverse", "--date=short", "--format=" + fmt, "--find-object=" + sha,
              check=False).splitlines()
    if not out:
        return {}
    f = out[0].split("\0")
    info = {"commit": f[0][:10], "date": f[1]}
    if show_authors and len(f) > 2:
        info["author"] = f[2]
    return info


def worktree(repo):
    return git(repo, "rev-parse", "--is-inside-work-tree", check=False).strip() == "true"


def ignore_status(repo, paths):
    """{path: True} for paths the repo's own ignore files cover (global excludes do not count)."""
    if not paths:
        return {}
    out = git(repo, "check-ignore", "--no-index", "-v", "-z", "--stdin",
              stdin="\0".join(paths).encode("utf-8", "surrogateescape"), check=False).split("\0")
    covered = {}
    for i in range(0, len(out) - 3, 4):
        source, pattern, path = out[i], out[i + 2], out[i + 3]
        if source and not os.path.isabs(source) and not source.startswith(".git/") and not pattern.startswith("!"):
            covered[path] = True
    return covered


def lfs_tracked(repo, paths):
    if not paths:
        return set()
    out = git(repo, "check-attr", "--cached", "-z", "--stdin", "filter",
              stdin="\0".join(paths).encode("utf-8", "surrogateescape"), check=False).split("\0")
    return {out[i] for i in range(0, len(out) - 2, 3) if out[i + 2] == "lfs"}


# ---------------------------------------------------------------- analysis

def classify(path):
    """(category, gitignore line, root) for committed junk, or None."""
    parts = path.split("/")
    if FIXTURE_DIRS & set(parts[:-1]):
        return None  # committed on purpose as test data
    for cat, name, line in DIR_JUNK:
        if name in parts[:-1]:
            return cat, line, "/".join(parts[:parts.index(name) + 1])
    base = parts[-1]
    for cat, suffix, line in FILE_JUNK:
        if base == suffix or (suffix.startswith(".") and base.endswith(suffix) and base != suffix):
            return cat, line, path
    return None


def find_junk(files, ignored):
    groups = {}
    for path, (_, size) in files.items():
        hit = classify(path)
        if not hit:
            continue
        cat, line, root = hit
        g = groups.setdefault(cat, {"category": cat, "files": 0, "bytes": 0, "roots": {}, "gitignore": set(),
                                    "ignored": 0})
        g["files"] += 1
        g["bytes"] += size
        g["roots"][root] = g["roots"].get(root, 0) + size
        if line:
            g["gitignore"].add(line)
        if ignored is not None and path in ignored:
            g["ignored"] += 1
    vend = sorted({"/".join(p.split("/")[:p.split("/").index("vendor") + 1]) for p in files
                   if "vendor" in p.split("/")[:-1]})
    if vend and not any(posixpath.basename(p) in LOCKFILES or p.endswith("vendor/modules.txt") for p in files):
        inside = [(p, s) for p, (_, s) in files.items() if any(p.startswith(v + "/") for v in vend)]
        groups["vendored, no lockfile"] = {"category": "vendored, no lockfile", "files": len(inside),
                                           "bytes": sum(s for _, s in inside), "roots": {v: 0 for v in vend},
                                           "gitignore": set(), "ignored": 0}
    out = []
    for g in groups.values():
        g["roots"] = sorted(g["roots"], key=lambda r: -g["roots"][r])
        g["gitignore"] = sorted(g["gitignore"])
        g["ignore_rule"] = None if ignored is None else ("all" if g["ignored"] == g["files"]
                                                         else "some" if g["ignored"] else "none")
        out.append(g)
    return sorted(out, key=lambda g: -g["bytes"])


def lfs_candidates(repo, files, has_worktree):
    cands = [p for p, (_, s) in files.items()
             if s >= LFS_MIN and posixpath.splitext(p.lower())[1] in BINARY_EXT]
    tracked = lfs_tracked(repo, cands) if has_worktree else set()
    return sorted(({"path": p, "bytes": files[p][1]} for p in cands if p not in tracked), key=lambda r: -r["bytes"])


def compare(total):
    return [{"reference": name, "bytes": b, "count": total / float(b)} for b, name in REFERENCES]


def clone_seconds(total):
    return total * 8 / (BANDWIDTH_MBIT * 1e6)


def human(n):
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return "%.1f %s" % (n / div, unit)
    return "%d B" % n


def count(x):
    return "{:,}".format(int(round(x))) if x >= 10 else "%.1f" % x


def headline(total, deleted_share):
    floppies = total / float(REFERENCES[0][0])
    secs = clone_seconds(total)
    t = "%.1f seconds" % secs if secs < 120 else "%.1f minutes" % (secs / 60)
    return ("%s of history; %d%% is files you already deleted. That is %s floppy disks, or %s to clone at "
            "%d Mbit/s." % (human(total), round(deleted_share * 100), count(floppies), t, BANDWIDTH_MBIT))


def filter_repo_commands(deleted_paths, deleted_sizes, head_max):
    """Suggested (never run) git filter-repo commands. The size cut stays above every file in HEAD."""
    cmds = []
    if deleted_paths:
        cmds.append("git filter-repo --invert-paths " + " ".join("--path " + shlex.quote(p) for p in deleted_paths))
    cut = int(head_max // MB) + 1  # in M; above head_max whether filter-repo's M is 10^6 or 2^20
    if any(s > cut * 1024 * 1024 for s in deleted_sizes):
        cmds.append("git filter-repo --strip-blobs-bigger-than %dM" % cut)
    return cmds


def diet(repo, top=10, show_authors=False):
    odb = pack_size(repo)
    files = head_files(repo)
    head_shas = {sha for sha, _ in files.values()}
    history_total, blobs = history_objects(repo)
    for b in blobs:
        b["status"] = "in HEAD" if b["sha"] in head_shas else ("old version" if b["path"] in files else "deleted")
    deleted = sum(b["disk"] for b in blobs if b["status"] == "deleted")
    share = deleted / float(history_total) if history_total else 0.0
    biggest = sorted(blobs, key=lambda b: (-b["disk"], b["path"]))[:top]
    for b in biggest:
        b.update(added_by(repo, b["sha"], show_authors))
    paths = {}
    for b in blobs:
        p = paths.setdefault(b["path"], {"path": b["path"], "disk": 0, "versions": 0,
                                         "status": "deleted" if b["path"] not in files else "in HEAD"})
        p["disk"] += b["disk"]
        p["versions"] += 1
    heavy_paths = sorted(paths.values(), key=lambda p: (-p["disk"], p["path"]))[:top]
    wt = worktree(repo)
    ignored = ignore_status(repo, [p for p in files if classify(p)]) if wt else None
    junk = find_junk(files, ignored)
    lfs = lfs_candidates(repo, files, wt)
    bloat = deleted >= HISTORY_MIN and share >= HISTORY_SHARE
    notes = []
    if not wt:
        notes.append("no working tree (bare clone): .gitignore and LFS attribute checks skipped")
    if not files:
        notes.append("HEAD has no commits; everything in history counts as deleted")
    notes.append("on-disk sizes of delta-compressed objects depend on how git packed them (git cat-file docs)")
    return {
        "repo": os.path.basename(os.path.abspath(repo)) or repo,
        "git_dir_bytes": odb["total"], "object_db": odb,
        "history_bytes": history_total, "checkout_bytes": sum(s for _, s in files.values()),
        "checkout_files": len(files), "blobs_in_history": len(blobs),
        "deleted_bytes": deleted, "deleted_share": share,
        "headline": headline(history_total, share),
        "comparisons": compare(history_total), "clone_seconds": clone_seconds(history_total),
        "bandwidth_mbit": BANDWIDTH_MBIT,
        "biggest_blobs": biggest, "heaviest_paths": heavy_paths,
        "junk": junk, "lfs_candidates": lfs[:top], "lfs_candidate_count": len(lfs),
        "history_bloat": bloat,
        "filter_repo_suggestions": filter_repo_commands(
            [p["path"] for p in heavy_paths if p["status"] == "deleted"],
            [b["bytes"] for b in biggest if b["status"] == "deleted"],
            max([s for _, s in files.values()] or [0])) if bloat else [],
        "findings": bool(junk or lfs or bloat), "show_authors": show_authors, "notes": notes,
    }


# ---------------------------------------------------------------- output

def report(d):
    out = ["REPO DIET  %s" % d["repo"], d["headline"],
           ".git: %s on disk · checkout: %s in %s files · %s blobs in history"
           % (human(d["git_dir_bytes"]), human(d["checkout_bytes"]), "{:,}".format(d["checkout_files"]),
              "{:,}".format(d["blobs_in_history"])), ""]
    out.append("Biggest blobs ever committed (size / on disk):")
    for i, b in enumerate(d["biggest_blobs"], 1):
        when = " added %s %s" % (b.get("commit", "?"), b.get("date", "")) if b.get("commit") else ""
        who = " by %s" % b["author"] if b.get("author") else ""
        out.append("  %2d. %-46s %9s / %9s  %-11s%s%s" % (i, b["path"][-46:] or "(no path)", human(b["bytes"]),
                                                         human(b["disk"]), b["status"], when, who))
    out.append("Heaviest paths, all versions:")
    for p in d["heaviest_paths"]:
        out.append("  %-50s %9s  %3d version%s  %s" % (p["path"][-50:], human(p["disk"]), p["versions"],
                                                       "" if p["versions"] == 1 else "s", p["status"]))
    out.append("")
    if d["junk"]:
        out.append("Junk committed now (fix with .gitignore + git rm --cached, no history rewrite):")
        for g in d["junk"]:
            rule = {"all": "already ignored", "some": "partly ignored", "none": "NOT in .gitignore",
                    None: "ignore not checked"}[g["ignore_rule"]]
            out.append("  %-26s %5d file%s %9s  %s%s" % (g["category"], g["files"], "" if g["files"] == 1 else "s",
                                                           human(g["bytes"]), rule,
                                                         ("  add: " + ", ".join(g["gitignore"])) if g["gitignore"]
                                                         and g["ignore_rule"] != "all" else ""))
            for r in g["roots"][:3]:
                out.append("      " + r)
    else:
        out.append("No junk committed in HEAD.")
    if d["lfs_candidates"]:
        out.append("Git LFS candidates (large binaries not tracked by LFS), %d total:" % d["lfs_candidate_count"])
        for r in d["lfs_candidates"]:
            out.append("  %-56s %9s" % (r["path"][-56:], human(r["bytes"])))
    if d["filter_repo_suggestions"]:
        out.append("")
        out.append("History bloat: %s in deleted files. Rewriting history fixes it but changes every commit "
                   "hash, needs a force push and a re-clone by everyone. NOT run, suggestions only:"
                   % human(d["deleted_bytes"]))
        for c in d["filter_repo_suggestions"]:
            out.append("  " + c)
    out.append("")
    for n in d["notes"]:
        out.append("Note: " + n)
    out.append("Verdict: %s" % ("findings, see above" if d["findings"] else "lean"))
    return "\n".join(out) + "\n"


def svg_card(d):
    bg, panel, fg, muted, accent, red = "#10151a", "#1d252c", "#f4f4f5", "#a1a1aa", "#4ade80", "#f87171"
    el = ['<rect width="1200" height="630" fill="%s"/>' % bg,
          '<rect x="0" y="0" width="12" height="630" fill="%s"/>' % accent]

    def text(x, y, body, size, color, weight="400"):
        el.append('<text x="%d" y="%d" font-size="%d" font-weight="%s" fill="%s">%s</text>'
                  % (x, y, size, weight, color, escape(body)))

    text(60, 72, "REPO DIET", 24, accent, "700")
    text(60, 160, human(d["history_bytes"]), 96, fg, "800")
    text(60, 210, "%d%% is files you already deleted" % round(d["deleted_share"] * 100), 32, red, "700")
    text(60, 256, ("= %s floppy disks, %.0f s to clone at %d Mbit/s"
                   % (count(d["comparisons"][0]["count"]), d["clone_seconds"], d["bandwidth_mbit"]))[:80], 26, muted)
    rows = d["biggest_blobs"][:5]
    peak = float(rows[0]["disk"]) if rows and rows[0]["disk"] else 1.0
    text(60, 320, "Biggest blobs in history", 24, fg, "700")
    for i, b in enumerate(rows):
        y = 344 + i * 46
        w = max(4, int(520 * b["disk"] / peak))
        el.append('<rect x="60" y="%d" width="520" height="30" rx="6" fill="%s"/>' % (y, panel))
        el.append('<rect x="60" y="%d" width="%d" height="30" rx="6" fill="%s" opacity="0.85"/>'
                  % (y, w, red if b["status"] == "deleted" else accent))
        text(600, y + 23, "%s  %s%s" % (posixpath.basename(b["path"])[:28] or "(no path)", human(b["disk"]),
                                        "  deleted" if b["status"] == "deleted" else ""), 22, fg)
    foot = [".git %s" % human(d["git_dir_bytes"]), "checkout %s" % human(d["checkout_bytes"])]
    if d["junk"]:
        foot.append("%d junk files committed" % sum(g["files"] for g in d["junk"]))
    if d["lfs_candidate_count"]:
        foot.append("%d LFS candidates" % d["lfs_candidate_count"])
    text(60, 600, "  ·  ".join(foot), 22, muted)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" '
            'font-family="Inter, \'Helvetica Neue\', Arial, sans-serif">\n<title>%s</title>\n%s\n</svg>\n'
            % (escape("Repo diet: %s of history" % human(d["history_bytes"])), "\n".join(el)))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Exit codes: 0 lean, 1 findings, 2 not a git repository, "
                                       "3 SVG could not be written")
    p.add_argument("-C", dest="repo", default=".", help="repository to inspect (default: current directory)")
    p.add_argument("--top", type=int, default=10, help="rows per list (default 10)")
    p.add_argument("--json", action="store_true", help="everything as JSON on stdout")
    p.add_argument("--svg", metavar="FILE", help="also write a 1200x630 SVG card")
    p.add_argument("--show-authors", action="store_true", help="name who added each big blob (hidden by default)")
    args = p.parse_args(argv)
    if not os.path.isdir(args.repo) or not git(args.repo, "rev-parse", "--git-dir", check=False).strip():
        print("not a git repository: %s" % args.repo, file=sys.stderr)
        return 2
    try:
        d = diet(args.repo, max(1, args.top), args.show_authors)
    except GitError as e:
        print("git error: %s" % e, file=sys.stderr)
        return 2
    if args.svg:
        try:
            with open(args.svg, "w", encoding="utf-8") as fh:
                fh.write(svg_card(d))
        except OSError as e:
            print("could not write %s: %s" % (args.svg, e), file=sys.stderr)
            return 3
    if args.json:
        print(json.dumps(d, indent=2))
    else:
        sys.stdout.write(report(d))
        if args.svg:
            print("card written to " + args.svg)
    return 1 if d["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
