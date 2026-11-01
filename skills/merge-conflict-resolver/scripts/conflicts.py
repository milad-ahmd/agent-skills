#!/usr/bin/env python3
"""Show what is in conflict and why: the git operation, each conflicted file, its hunks, and the
commits on each side that touched it. Stdlib only, read-only.

Usage: python3 conflicts.py [-C REPO] [--json] [--max-lines N] [--commits N] [PATH ...]
       python3 conflicts.py --check [-C REPO] [--json] [PATH ...]
  -C REPO       repository to inspect (default: current directory)
  PATH          limit the report (or the check) to these paths
  --json        machine-readable output on stdout
  --max-lines   lines shown per side of a hunk in text output (default 40, 0 = all); JSON is never cut
  --commits     commits listed per side for each file (default 8)
  --check       verify that nothing is left to resolve: no unmerged paths and no complete conflict
                marker set (<<<<<<< then ======= then >>>>>>>) in tracked files, in the working tree
                or staged in the index. A lone ======= line (a Markdown or reST underline) is not a
                conflict. Stray <<<<<<< or >>>>>>> lines without a full set are printed as warnings.

The report covers: the operation in progress (merge, rebase, cherry-pick, revert, am, or a stash
apply/pop, read from the state files in .git), what "ours" and "theirs" mean for it, the command
that continues it; per conflicted file its kind (content, add/add, modify/delete, rename, binary,
both deleted), lockfile or generated-file detection with the command that regenerates it, every
marker hunk with its line range and the ours/base/theirs text (base comes from diff3/zdiff3 markers,
or is rebuilt with `git merge-file --zdiff3` from the index stages when the markers lack it), and
the commits on each side that changed the file. It never modifies the repository.

Exit codes: 0 nothing to resolve (report) / clean (--check)
            1 conflicts present: unmerged paths, or (--check) marker sets left in files
            2 not a git repository, or a git error
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
DEFAULT_MARKER = 7

# basename -> (tool, regenerate command, manifest to resolve first)
LOCKFILES = {
    "package-lock.json": ("npm", "npm install --package-lock-only", "package.json"),
    "npm-shrinkwrap.json": ("npm", "npm install --package-lock-only", "package.json"),
    "yarn.lock": ("yarn", "yarn install", "package.json"),
    "pnpm-lock.yaml": ("pnpm", "pnpm install", "package.json"),
    "bun.lock": ("bun", "bun install", "package.json"),
    "bun.lockb": ("bun", "bun install", "package.json"),
    "Cargo.lock": ("cargo", "cargo update --workspace", "Cargo.toml"),
    "poetry.lock": ("poetry", "poetry lock", "pyproject.toml"),
    "uv.lock": ("uv", "uv lock", "pyproject.toml"),
    "Gemfile.lock": ("bundler", "bundle install", "Gemfile"),
    "composer.lock": ("composer", "composer update --lock  (only fixes a content-hash conflict; otherwise take one "
                      "side and re-run the other side's composer require/update commands)", "composer.json"),
    "go.sum": ("go", "go mod tidy", "go.mod"),
}
GENERATED_GLOBS = [
    ("*.min.js", "minified build output"), ("*.min.css", "minified build output"),
    ("*.js.map", "source map"), ("*.css.map", "source map"),
    ("*/__snapshots__/*.snap", "Jest snapshot: resolve the code, then re-run the tests with --updateSnapshot"),
    ("__snapshots__/*.snap", "Jest snapshot: resolve the code, then re-run the tests with --updateSnapshot"),
]
GENERATED_HEADER = re.compile(rb"^// Code generated .* DO NOT EDIT\.$|@generated\b", re.M)

STATUS_KIND = {
    "UU": ("content", "both modified"),
    "AA": ("add/add", "both added, different content"),
    "UD": ("modify/delete", "modified by ours, deleted by theirs"),
    "DU": ("modify/delete", "deleted by ours, modified by theirs"),
    "AU": ("added-by-ours", "only ours has this path (no base version)"),
    "UA": ("added-by-theirs", "only theirs has this path (no base version)"),
    "DD": ("both-deleted", "deleted (or renamed away) on both sides"),
}

CONTINUE = {
    "merge": ("git merge --continue", "git merge --abort"),
    "rebase": ("git rebase --continue", "git rebase --abort"),
    "cherry-pick": ("git cherry-pick --continue", "git cherry-pick --abort"),
    "revert": ("git revert --continue", "git revert --abort"),
    "am": ("git am --continue", "git am --abort"),
    "stash": ("nothing to continue; `git stash drop` once the result is right (a conflicted pop keeps the stash)",
              "git checkout -- <files> (discards the applied stash changes; the stash itself is kept)"),
}


class GitError(Exception):
    pass


def git(repo, *args, check=True, raw=False):
    r = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, capture_output=True)
    if check and r.returncode != 0:
        raise GitError("git %s failed: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout if raw else r.stdout.decode("utf-8", "replace").rstrip("\n")


def git_ok(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, capture_output=True).returncode == 0


def read_file(path, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def rev(repo, ref):
    out = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
    return out.strip() or None


def short(repo, sha):
    return git(repo, "rev-parse", "--short", sha, check=False) or sha[:7]


def subject(repo, sha):
    return git(repo, "log", "-1", "--format=%s", sha, check=False)


def ref_name(repo, sha):
    """A branch (local first, then remote) pointing exactly at sha, else the short sha."""
    for pattern in ("refs/heads", "refs/remotes"):
        out = git(repo, "for-each-ref", "--points-at=" + sha, "--format=%(refname:short)", pattern, check=False)
        names = [n for n in out.splitlines() if n and not n.endswith("/HEAD")]
        if names:
            return names[0]
    return short(repo, sha)


def current_branch(repo):
    return git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False) or "detached HEAD"


def merge_base(repo, a, b):
    if not a or not b:
        return None
    return git(repo, "merge-base", a, b, check=False).strip() or None


# --- the operation in progress -----------------------------------------------------------
def detect_operation(repo, gd, unmerged_paths):
    """Read .git state files. Returns a dict describing the operation and both sides."""
    head = rev(repo, "HEAD")
    op = {"kind": None, "description": "", "ours": None, "theirs": None, "progress": None}

    def side(label, sha, meaning):
        return {"label": label, "sha": sha, "short": short(repo, sha) if sha else None, "meaning": meaning}

    rebase_dir = None
    for name in ("rebase-merge", "rebase-apply"):
        if os.path.isdir(os.path.join(gd, name)):
            rebase_dir = os.path.join(gd, name)
    if rebase_dir and os.path.exists(os.path.join(rebase_dir, "applying")):
        op["kind"] = "am"
        nxt, last = read_file(os.path.join(rebase_dir, "next")), read_file(os.path.join(rebase_dir, "last"))
        patch = read_file(os.path.join(rebase_dir, "final-commit"), "") or "the patch"
        patch = patch.splitlines()[0] if patch else "the patch"
        op["progress"] = {"current": int(nxt), "total": int(last)} if nxt and last else None
        op["description"] = "applying mailbox patches with git am, patch %s/%s: %s" % (nxt, last, patch)
        op["ours"] = side("HEAD", head, "your branch %s before this patch" % current_branch(repo))
        op["theirs"] = side(patch, None, "the patch being applied")
    elif rebase_dir:
        op["kind"] = "rebase"
        merge_backend = rebase_dir.endswith("rebase-merge")
        branch = read_file(os.path.join(rebase_dir, "head-name"), "") or ""
        branch = branch[len("refs/heads/"):] if branch.startswith("refs/heads/") else (branch or "detached HEAD")
        onto = read_file(os.path.join(rebase_dir, "onto"))
        if merge_backend:
            cur, total = read_file(os.path.join(rebase_dir, "msgnum")), read_file(os.path.join(rebase_dir, "end"))
            stopped = read_file(os.path.join(rebase_dir, "stopped-sha"))
        else:
            cur, total = read_file(os.path.join(rebase_dir, "next")), read_file(os.path.join(rebase_dir, "last"))
            stopped = read_file(os.path.join(rebase_dir, "original-commit"))
        theirs = rev(repo, "REBASE_HEAD") or (rev(repo, stopped) if stopped else None)
        onto_name = ref_name(repo, onto) if onto else "?"
        if cur and total and cur.isdigit() and total.isdigit():
            op["progress"] = {"current": int(cur), "total": int(total)}
        where = ", commit %s/%s" % (cur, total) if op["progress"] else ""
        what = (": %s %s" % (short(repo, theirs), subject(repo, theirs))) if theirs else ""
        op["description"] = "rebasing %s onto %s%s%s" % (branch, onto_name, where, what)
        op["ours"] = side("HEAD", head, "the branch you rebase onto, %s, plus commits already replayed" % onto_name)
        op["theirs"] = side(short(repo, theirs) if theirs else "?", theirs,
                            "your commit from %s being replayed" % branch)
    elif rev(repo, "MERGE_HEAD"):
        op["kind"] = "merge"
        mh = rev(repo, "MERGE_HEAD")
        extra = len(read_file(os.path.join(gd, "MERGE_HEAD"), "").split()) - 1
        name = ref_name(repo, mh)
        op["description"] = "merging %s into %s" % (name, current_branch(repo))
        if extra > 0:
            op["description"] += " (octopus merge with %d more heads; only the first is analysed)" % extra
        op["ours"] = side("HEAD", head, "the branch you are on, %s" % current_branch(repo))
        op["theirs"] = side(name, mh, "the branch being merged in")
    elif rev(repo, "CHERRY_PICK_HEAD"):
        op["kind"] = "cherry-pick"
        cp = rev(repo, "CHERRY_PICK_HEAD")
        op["description"] = "cherry-picking %s %s onto %s" % (short(repo, cp), subject(repo, cp), current_branch(repo))
        op["progress"] = sequencer_left(gd)
        op["ours"] = side("HEAD", head, "%s, the branch receiving the pick" % current_branch(repo))
        op["theirs"] = side(short(repo, cp), cp, "the commit being picked")
    elif rev(repo, "REVERT_HEAD"):
        op["kind"] = "revert"
        rv = rev(repo, "REVERT_HEAD")
        op["description"] = "reverting %s %s on %s" % (short(repo, rv), subject(repo, rv), current_branch(repo))
        op["progress"] = sequencer_left(gd)
        op["ours"] = side("HEAD", head, "%s, including later changes to the reverted lines" % current_branch(repo))
        op["theirs"] = side("parent of " + short(repo, rv), rv,
                            "the code as it was before %s (what the revert wants)" % short(repo, rv))
    elif unmerged_paths:
        op["kind"] = "unknown"
        op["description"] = ("unmerged paths but no merge/rebase/cherry-pick/revert/am state: probably "
                             "`git stash pop`/`apply` or `git checkout -m`")
    if op["kind"] in CONTINUE:
        op["continue"], op["abort"] = CONTINUE[op["kind"]]
    return op


def sequencer_left(gd):
    todo = read_file(os.path.join(gd, "sequencer", "todo"))
    if not todo:
        return None
    n = sum(1 for ln in todo.splitlines() if ln.strip() and not ln.startswith("#"))
    return {"remaining_including_current": n}


def promote_stash(repo, op, files):
    """A stash apply/pop leaves no state file but labels its markers 'Updated upstream'/'Stashed changes'."""
    if op["kind"] != "unknown":
        return
    labels = {(h["ours_label"], h["theirs_label"]) for f in files for h in f.get("hunks", [])}
    if ("Updated upstream", "Stashed changes") in labels:
        stash = rev(repo, "refs/stash")
        op["kind"] = "stash"
        op["description"] = "applying a stash (git stash pop/apply); the stash entry is kept until you drop it"
        op["ours"] = {"label": "Updated upstream", "sha": rev(repo, "HEAD"), "short": short(repo, "HEAD"),
                      "meaning": "the commit you are on now"}
        op["theirs"] = {"label": "Stashed changes", "sha": stash, "short": short(repo, stash) if stash else None,
                        "meaning": "your stashed work (stash@{0}, if this was the latest stash)"}
        op["continue"], op["abort"] = CONTINUE["stash"]


# --- commits on each side ---------------------------------------------------------------
def side_ranges(repo, op):
    """Returns (ours_range, theirs_range) as lists of git-log revision arguments, or None."""
    kind = op.get("kind")
    ours = (op.get("ours") or {}).get("sha")
    theirs = (op.get("theirs") or {}).get("sha")
    if kind in ("merge", "rebase", "cherry-pick") and ours and theirs:
        base = merge_base(repo, ours, theirs)
        if base:
            return [base + ".." + ours], [base + ".." + theirs]
        return [ours], [theirs]
    if kind == "revert" and ours and theirs:
        return [theirs + ".." + ours], ["-1", theirs]
    if kind == "stash" and ours and theirs:
        return [theirs + "^1.." + ours], ["-1", theirs]
    return None, None


def commits_touching(repo, rng, paths, limit):
    if not rng:
        return []
    out = git(repo, "log", "-n", str(limit), "--format=%h%x09%as%x09%s", *rng, "--", *paths, check=False)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return rows


# --- unmerged entries -------------------------------------------------------------------
def unmerged(repo, paths):
    """{path: {"stages": {1: sha, 2: sha, 3: sha}, "xy": "UU"}} from ls-files -u and status v2."""
    out = git(repo, "ls-files", "-u", "-z", "--", *paths, raw=True).decode("utf-8", "replace")
    entries = {}
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        mode, sha, stage = meta.split()
        entries.setdefault(path, {"stages": {}, "modes": {}})
        entries[path]["stages"][int(stage)] = sha
        entries[path]["modes"][int(stage)] = mode
    status = git(repo, "status", "--porcelain=v2", "-z", "--untracked-files=no", "--", *paths,
                 raw=True).decode("utf-8", "replace")
    for rec in status.split("\0"):
        if rec.startswith("u "):
            fields = rec.split(" ", 10)
            if len(fields) == 11 and fields[10] in entries:
                entries[fields[10]]["xy"] = fields[1]
    for path, e in entries.items():
        if "xy" not in e:
            s = e["stages"]
            e["xy"] = "UU" if {1, 2, 3} <= set(s) else "AA" if {2, 3} <= set(s) else \
                "UD" if {1, 2} <= set(s) else "DU" if {1, 3} <= set(s) else "AU" if 2 in s else "UA" if 3 in s else "DD"
    return entries


def blob(repo, sha):
    return git(repo, "cat-file", "blob", sha, raw=True)


def is_binary(data):
    return b"\0" in data[:8000]


def marker_size(repo, path):
    out = git(repo, "check-attr", "conflict-marker-size", "--", path, check=False)
    m = re.search(r": conflict-marker-size: (\d+)$", out)
    return int(m.group(1)) if m else DEFAULT_MARKER


def classify_generated(repo, path, sample):
    base = os.path.basename(path)
    if base in LOCKFILES:
        tool, cmd, manifest = LOCKFILES[base]
        man = os.path.join(os.path.dirname(path), manifest) if os.path.dirname(path) else manifest
        return {"type": "lockfile", "tool": tool, "regenerate": cmd, "manifest": man}
    for glob, why in GENERATED_GLOBS:
        if fnmatch.fnmatch(path, glob):
            return {"type": "generated", "why": why, "regenerate": "re-run the step that produces it"}
    attr = git(repo, "check-attr", "linguist-generated", "--", path, check=False)
    if attr.endswith(": set") or attr.endswith(": true"):
        return {"type": "generated", "why": "marked linguist-generated in .gitattributes",
                "regenerate": "re-run the generator that produces it"}
    if sample and GENERATED_HEADER.search(sample[:4000]):
        return {"type": "generated", "why": "file header says it is generated",
                "regenerate": "re-run the generator that produces it"}
    return None


# --- marker parsing ---------------------------------------------------------------------
def _marker(line, ch, size, needs_label_ok=True):
    tok = ch * size
    if line == tok:
        return ""
    if needs_label_ok and line.startswith(tok + " ") and not line.startswith(tok + ch):
        return line[size + 1:]
    return None


def parse_hunks(text, size=DEFAULT_MARKER):
    """Find conflict hunks. Returns (hunks, stray) where stray lists marker lines outside a full set.

    A hunk is <<<<<<< [label], ours, optional ||||||| [label] + base, =======, theirs, >>>>>>> [label].
    Line numbers are 1-based and inclusive of the marker lines.
    """
    lines = text.split("\n")
    hunks, stray = [], []
    state, cur = None, None
    for i, raw in enumerate(lines, 1):
        line = raw[:-1] if raw.endswith("\r") else raw
        start = _marker(line, "<", size)
        if start is not None:
            if cur is not None:
                stray.append({"line": cur["start"], "text": "<" * size + " (no closing marker set)"})
            cur = {"start": i, "ours_label": start, "ours": [], "base": None, "base_label": None, "theirs": [],
                   "theirs_label": None}
            state = "ours"
            continue
        if cur is None:
            if _marker(line, ">", size) is not None:
                stray.append({"line": i, "text": line})
            continue
        if state == "ours" and _marker(line, "|", size) is not None:
            cur["base_label"], cur["base"], state = _marker(line, "|", size), [], "base"
        elif state in ("ours", "base") and line == "=" * size:
            state = "theirs"
        elif state == "theirs" and _marker(line, ">", size) is not None:
            cur["theirs_label"], cur["end"] = _marker(line, ">", size), i
            for key in ("ours", "base", "theirs"):
                if cur[key] is not None:
                    cur[key] = "\n".join(cur[key]) + ("\n" if cur[key] else "")
            hunks.append(cur)
            cur, state = None, None
        else:
            cur[state].append(raw)
    if cur is not None:
        stray.append({"line": cur["start"], "text": "<" * size + " (no closing marker set)"})
    return hunks, stray


def rebuild_base(repo, stages, hunks, size):
    """Fill in base text for marker hunks that lack it by re-running the 3-way merge in diff3 style."""
    if not hunks or all(h["base"] is not None for h in hunks) or not {1, 2, 3} <= set(stages):
        return
    with tempfile.TemporaryDirectory() as tmp:
        names = []
        for n in (2, 1, 3):
            p = os.path.join(tmp, str(n))
            with open(p, "wb") as fh:
                fh.write(blob(repo, stages[n]))
            names.append(p)
        for style in ("--zdiff3", "--diff3"):
            r = subprocess.run(["git", "merge-file", "-p", style, "--marker-size=%d" % size,
                                "-L", "ours", "-L", "base", "-L", "theirs"] + names,
                               cwd=repo, env=GIT_ENV, capture_output=True)
            if r.returncode < 0 or r.returncode > 127 or not r.stdout:
                continue
            rebuilt, _ = parse_hunks(r.stdout.decode("utf-8", "replace"), size)
            by_sides = {(h["ours"], h["theirs"]): h["base"] for h in rebuilt}
            for h in hunks:
                if h["base"] is None and (h["ours"], h["theirs"]) in by_sides:
                    h["base"] = by_sides[(h["ours"], h["theirs"])]
                    h["base_source"] = "git merge-file " + style
            if all(h["base"] is not None for h in hunks):
                return


# --- the report -------------------------------------------------------------------------
def describe_file(repo, path, entry, op, all_entries, base_sha, commit_limit):
    xy = entry["xy"]
    stages = entry["stages"]
    kind, detail = STATUS_KIND.get(xy, ("unknown", xy))
    info = {"path": path, "status": xy, "kind": kind, "detail": detail, "binary": False,
            "stages": {str(k): v for k, v in sorted(stages.items())}, "hunks": [], "stray_markers": []}
    if "160000" in entry["modes"].values():
        info["kind"], info["detail"] = "submodule", "submodule pointer differs; pick the right commit and git add"
    # renames: a both-deleted source whose base blob reappears under a one-sided path, or a path the base lacks
    if xy in ("AU", "UA"):
        mine = stages.get(2) or stages.get(3)
        for other, e in all_entries.items():
            if other != path and e["xy"] in ("DD", "UD", "DU") and e["stages"].get(1) == mine:
                info["kind"] = "rename"
                info["detail"] = "%s was renamed to %s on %s" % (other, path, "ours" if xy == "AU" else "theirs")
                info["renamed_from"] = other
        if info["kind"] != "rename":
            info["detail"] += "; if the other side renamed it, see which path holds the same content"
    elif xy == "DD":
        info["detail"] = "deleted on both sides (usually the source of a rename/rename); git rm it unless you need it"
    elif xy in ("UD", "DU") and base_sha and not git_ok(repo, "cat-file", "-e", "%s:%s" % (base_sha, path)):
        info["kind"] = "rename"
        info["detail"] = "rename/delete: this path is a rename on %s, the original was deleted on %s" % (
            ("ours", "theirs") if xy == "UD" else ("theirs", "ours"))
    sample = b""
    for n in (2, 3, 1):
        if n in stages and not sample:
            try:
                sample = blob(repo, stages[n])
            except GitError:
                pass
    full = os.path.join(repo, path)
    worktree = None
    if os.path.isfile(full):
        with open(full, "rb") as fh:
            worktree = fh.read()
    if any(is_binary(blob(repo, s)) for s in stages.values()) or (worktree is not None and is_binary(worktree)):
        info["binary"] = True
        if info["kind"] == "content":
            info["kind"], info["detail"] = "binary", "binary file changed on both sides; no markers, pick a version"
    info["generated"] = classify_generated(repo, path, sample)
    if worktree is not None and not info["binary"]:
        size = marker_size(repo, path)
        hunks, stray = parse_hunks(worktree.decode("utf-8", "replace"), size)
        for h in hunks:
            h["base_source"] = "markers" if h["base"] is not None else None
        rebuild_base(repo, stages, hunks, size)
        info["hunks"] = [{"start": h["start"], "end": h["end"], "ours_label": h["ours_label"],
                          "theirs_label": h["theirs_label"], "ours": h["ours"], "base": h["base"],
                          "base_source": h["base_source"], "theirs": h["theirs"]} for h in hunks]
        info["stray_markers"] = stray
    info["exists_in_worktree"] = worktree is not None
    ours_rng, theirs_rng = side_ranges(repo, op)
    lookup = [path, info["renamed_from"]] if info.get("renamed_from") else [path]
    info["commits"] = {"ours": commits_touching(repo, ours_rng, lookup, commit_limit),
                       "theirs": commits_touching(repo, theirs_rng, lookup, commit_limit)}
    return info


def repo_root(start):
    if not os.path.isdir(start):
        raise GitError("%s is not a directory" % start)
    if not git_ok(start, "rev-parse", "--git-dir"):
        raise GitError("%s is not inside a git repository" % start)
    if git(start, "rev-parse", "--is-bare-repository") == "true":
        raise GitError("%s is a bare repository" % start)
    top = git(start, "rev-parse", "--show-toplevel")
    gd = git(start, "rev-parse", "--absolute-git-dir")
    return top, gd


def rel_paths(start, top, paths):
    """PATH arguments are relative to -C (as with git -C); make them relative to the top level."""
    real_top = os.path.realpath(top)
    return [os.path.relpath(os.path.realpath(os.path.join(start, p)), real_top) for p in paths]


def conflict_style(repo):
    return git(repo, "config", "--get", "merge.conflictStyle", check=False).strip() or "merge"


def build_report(start, paths, commit_limit):
    top, gd = repo_root(start)
    rel = rel_paths(start, top, paths)
    entries = unmerged(top, rel)
    op = detect_operation(top, gd, entries)
    base = None
    if op.get("kind") in ("merge", "rebase", "cherry-pick"):
        base = merge_base(top, op["ours"]["sha"], op["theirs"]["sha"])
        if op["kind"] == "cherry-pick":
            base = rev(top, op["theirs"]["sha"] + "^") or base
    elif op.get("kind") == "revert":
        base = op["theirs"]["sha"]
    files = [describe_file(top, p, e, op, entries, base, commit_limit) for p, e in sorted(entries.items())]
    promote_stash(top, op, files)
    if op["kind"] == "stash":
        ours_rng, theirs_rng = side_ranges(top, op)
        for f in files:
            f["commits"] = {"ours": commits_touching(top, ours_rng, [f["path"]], commit_limit),
                            "theirs": commits_touching(top, theirs_rng, [f["path"]], commit_limit)}
    return {"repo": top, "operation": op, "conflict_style": conflict_style(top), "files": files,
            "summary": summarize(files)}


def summarize(files):
    out = {"files": len(files), "hunks": sum(len(f["hunks"]) for f in files)}
    for f in files:
        out[f["kind"]] = out.get(f["kind"], 0) + 1
    out["lockfiles"] = sum(1 for f in files if (f.get("generated") or {}).get("type") == "lockfile")
    return out


def clip(text, max_lines):
    if text is None:
        return ["      (not available)"]
    lines = text.rstrip("\n").split("\n") if text else []
    if not lines:
        return ["      (empty)"]
    shown = lines if not max_lines else lines[:max_lines]
    out = ["      " + ln for ln in shown]
    if len(shown) < len(lines):
        out.append("      ... (%d more lines)" % (len(lines) - len(shown)))
    return out


def print_report(rep, max_lines):
    op = rep["operation"]
    if not rep["files"]:
        print("No unmerged paths." + (" Operation in progress: %s." % op["description"] if op["kind"] else ""))
        if op.get("continue"):
            print("Continue with: %s" % op["continue"])
        return
    print("Operation: %s" % op["description"])
    if op.get("ours"):
        print("  ours   = %s (%s)" % (op["ours"]["label"], op["ours"]["meaning"]))
        print("  theirs = %s (%s)" % (op["theirs"]["label"], op["theirs"]["meaning"]))
    if op.get("continue"):
        print("  continue: %s    abort: %s" % (op["continue"], op["abort"]))
    s = rep["summary"]
    print("Conflicted files: %d, marker hunks: %d, conflict style: %s" % (s["files"], s["hunks"], rep["conflict_style"]))
    for f in rep["files"]:
        print("\n== %s  [%s] %s" % (f["path"], f["kind"], f["detail"]))
        g = f.get("generated")
        if g and g["type"] == "lockfile":
            print("   lockfile (%s): do not hand-merge. Resolve %s first, take one side, then run: %s"
                  % (g["tool"], g["manifest"], g["regenerate"]))
        elif g:
            print("   generated (%s): %s" % (g["why"], g["regenerate"]))
        for side in ("ours", "theirs"):
            rows = f["commits"][side]
            if rows:
                print("   %s commits:" % side)
                for c in rows:
                    print("     %s %s %s" % (c["sha"], c["date"], c["subject"]))
        if g and g["type"] == "lockfile":
            continue
        for n, h in enumerate(f["hunks"], 1):
            print("   hunk %d, lines %d-%d" % (n, h["start"], h["end"]))
            print("     ours (%s):" % (h["ours_label"] or "ours"))
            print("\n".join(clip(h["ours"], max_lines)))
            src = (", from " + h["base_source"]) if h["base_source"] and h["base_source"] != "markers" else ""
            print("     base%s:" % src)
            print("\n".join(clip(h["base"], max_lines)))
            print("     theirs (%s):" % (h["theirs_label"] or "theirs"))
            print("\n".join(clip(h["theirs"], max_lines)))
        for st in f["stray_markers"]:
            print("   stray marker at line %d: %s" % (st["line"], st["text"]))


# --- --check ----------------------------------------------------------------------------
def candidate_files(repo, paths, cached):
    args = ["grep", "-I", "-l", "-z", "-E"] + (["--cached"] if cached else []) + ["-e", "^(<{7,}|>{7,})( |$)"]
    out = git(repo, *args, "--", *paths, check=False, raw=True).decode("utf-8", "replace")
    return [p for p in out.split("\0") if p]


def run_check(start, paths):
    top, _ = repo_root(start)
    rel = rel_paths(start, top, paths)
    entries = unmerged(top, rel)
    problems, warnings = [], []
    for p in sorted(entries):
        problems.append({"path": p, "line": None, "where": "index", "issue": "unmerged path (%s)" % entries[p]["xy"]})
    seen = set()
    for cached in (False, True):
        for p in candidate_files(top, rel, cached):
            if p in entries:
                continue
            if cached:
                data = git(top, "show", ":" + p, check=False, raw=True)
            else:
                try:
                    with open(os.path.join(top, p), "rb") as fh:
                        data = fh.read()
                except OSError:
                    continue
            hunks, stray = parse_hunks(data.decode("utf-8", "replace"), marker_size(top, p))
            where = "staged" if cached else "working tree"
            for h in hunks:
                if (p, h["start"]) not in seen:
                    seen.add((p, h["start"]))
                    problems.append({"path": p, "line": h["start"], "where": where,
                                     "issue": "conflict markers (lines %d-%d)" % (h["start"], h["end"])})
            for st in stray:
                if (p, st["line"], "w") not in seen:
                    seen.add((p, st["line"], "w"))
                    warnings.append({"path": p, "line": st["line"], "where": where, "issue": "stray marker"})
    return {"repo": top, "clean": not problems, "problems": problems, "warnings": warnings}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 usage=argparse.SUPPRESS)
    ap.add_argument("-C", dest="repo", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--max-lines", type=int, default=40)
    ap.add_argument("--commits", type=int, default=8)
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args(argv)
    try:
        if a.check:
            res = run_check(a.repo, a.paths)
            if a.json:
                print(json.dumps(res, indent=2))
            else:
                for p in res["problems"]:
                    loc = "%s:%s" % (p["path"], p["line"]) if p["line"] else p["path"]
                    print("%s: %s [%s]" % (loc, p["issue"], p["where"]))
                for w in res["warnings"]:
                    print("warning: %s:%s: %s [%s]" % (w["path"], w["line"], w["issue"], w["where"]))
                if res["clean"]:
                    print("Clean: no unmerged paths and no conflict marker sets.")
            return 0 if res["clean"] else 1
        rep = build_report(a.repo, a.paths, a.commits)
        if a.json:
            print(json.dumps(rep, indent=2))
        else:
            print_report(rep, a.max_lines)
        return 1 if rep["files"] else 0
    except GitError as e:
        if a.json:
            print(json.dumps({"error": str(e)}))
        sys.stderr.write("error: %s\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
