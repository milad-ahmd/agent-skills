#!/usr/bin/env python3
"""Collect the git facts a session handoff needs, write a HANDOFF.md skeleton, or check one.

Usage:
  python3 handoff.py                      # print the facts (branch, HEAD, dirty files, branch commits, TODOs ...)
  python3 handoff.py --json               # the same as JSON
  python3 handoff.py --write HANDOFF.md   # write a skeleton (refuses to overwrite without --force)
  python3 handoff.py --check HANDOFF.md   # compare a handoff's recorded git state with the live repo

Options: -C DIR (repo to inspect, default .), --base REF (default branch override), --limit N (list cap).

Exit codes:
  report   0 ok · 2 not a git repository
  --write  0 written (outside a repo too: the skeleton says there is no git state) · 1 refused or failed
  --check  0 matches · 1 drift · 2 cannot check (not a repo, file missing, no git state recorded)

Read-only: git runs with --no-optional-locks, nothing is staged, checked out or stashed. The only file
contents printed are TODO/FIXME/XXX lines added in the uncommitted changes or on the branch.
Stdlib only, Python 3.9+.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

MARK = re.compile(r"(?<![/|])\b(TODO|FIXME|XXX)\b(?![/|])")  # skips "TODO/FIXME" mentions
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
CONFLICT = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
SECTIONS = ["Goal", "Status", "Next step", "Decisions", "Tried and failed", "Open questions",
            "Verify", "Git state", "Links"]
PLACEHOLDER = "<fill in"
SKELETON_CAP = 5
UNTRACKED_SCAN_FILES = 500
UNTRACKED_SCAN_BYTES = 512 * 1024


class NotARepo(Exception):
    pass


def git(root, *args):
    env = dict(os.environ, GIT_PAGER="cat", PAGER="cat", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    p = subprocess.run(["git", "--no-optional-locks", "-c", "core.quotepath=off", "-c", "color.ui=false", *args],
                       cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def ok(root, *args):
    rc, out = git(root, *args)
    return out.strip() if rc == 0 else None


def repo_root(start):
    try:
        rc, out = git(start, "rev-parse", "--show-toplevel")
    except (FileNotFoundError, NotADirectoryError):
        raise NotARepo(start)
    if rc != 0 or not out.strip():
        raise NotARepo(start)
    return out.strip()


def parse_status(out):
    """porcelain v1 -z → [{code, path, from}]; spaces in the XY code become '.' (like porcelain v2)."""
    parts, files, i = out.split("\0"), [], 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        xy, path, orig = e[:2], e[3:], None
        if "R" in xy or "C" in xy:
            orig = parts[i] if i < len(parts) else None
            i += 1
        files.append({"code": xy.replace(" ", "."), "path": path, "from": orig})
    return files


def working_tree(root, exclude, limit):
    rc, out = git(root, "status", "--porcelain=v1", "-z")
    files = [f for f in parse_status(out if rc == 0 else "") if f["path"] not in exclude]
    counts = {"staged": 0, "unstaged": 0, "untracked": 0, "conflicted": 0}
    for f in files:
        c = f["code"]
        if c == "??":
            counts["untracked"] += 1
        elif c in CONFLICT:
            counts["conflicted"] += 1
        else:
            counts["staged"] += c[0] not in ".?!"
            counts["unstaged"] += c[1] not in ".?!"
    return dict(counts, total=len(files), files=files[:limit], truncated=len(files) > limit)


def todos_in_diff(text, scope):
    found, path, ln, header = [], None, 0, False
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            header, path = True, None
            continue
        if header:
            if line.startswith("+++ "):
                p = line[4:].rstrip("\t").strip('"')
                path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
            elif line.startswith("@@"):
                header = False
        if not header:
            m = HUNK.match(line)
            if m:
                ln = int(m.group(1))
                continue
            if line.startswith("+") and path:
                if MARK.search(line):
                    found.append({"path": path, "line": ln, "text": line[1:].strip()[:160], "scope": scope})
                ln += 1
    return found


def todos_in_untracked(root):
    rc, out = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    found = []
    for rel in [p for p in out.split("\0") if p][:UNTRACKED_SCAN_FILES] if rc == 0 else []:
        p = os.path.join(root, rel)
        try:
            if not os.path.isfile(p) or os.path.getsize(p) > UNTRACKED_SCAN_BYTES:
                continue
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if MARK.search(line):
                found.append({"path": rel, "line": n, "text": line.strip()[:160], "scope": "untracked"})
    return found


DIFF = ["--no-color", "--no-ext-diff", "--no-textconv", "--unified=0", "--src-prefix=a/", "--dst-prefix=b/"]


def detect_base(root, override):
    if override:
        return override if ok(root, "rev-parse", "--verify", "-q", override + "^{commit}") else None
    cands = []
    origin_head = ok(root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    if origin_head:
        cands.append(origin_head)
    cands += ["origin/main", "origin/master", "main", "master"]
    for ref in cands:
        if ok(root, "rev-parse", "--verify", "-q", ref + "^{commit}"):
            return ref
    return None


def in_progress(root):
    gd = ok(root, "rev-parse", "--absolute-git-dir")
    if not gd:
        return []
    ops = []

    def read(*p):
        try:
            with open(os.path.join(gd, *p)) as fh:
                return fh.read().strip()
        except OSError:
            return ""

    if os.path.isdir(os.path.join(gd, "rebase-merge")):
        name = read("rebase-merge", "head-name").replace("refs/heads/", "")
        step, end = read("rebase-merge", "msgnum"), read("rebase-merge", "end")
        ops.append("rebase" + (" of " + name if name else "") + (" (step %s/%s)" % (step, end) if step and end else ""))
    elif os.path.isdir(os.path.join(gd, "rebase-apply")):
        if os.path.exists(os.path.join(gd, "rebase-apply", "applying")):
            ops.append("am")
        else:
            name = read("rebase-apply", "head-name").replace("refs/heads/", "")
            ops.append("rebase" + (" of " + name if name else ""))
    for f, op in (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
                  ("BISECT_LOG", "bisect")):
        if os.path.exists(os.path.join(gd, f)):
            ops.append(op)
    return ops


def gather(start=".", base=None, limit=25, exclude=()):
    root = repo_root(start)
    notes = []
    branch = ok(root, "symbolic-ref", "-q", "--short", "HEAD")
    sha = ok(root, "rev-parse", "--verify", "-q", "HEAD")
    head = None
    if sha:
        head = {"sha": sha, "short": ok(root, "rev-parse", "--short", "HEAD"),
                "subject": ok(root, "log", "-1", "--format=%s", "HEAD") or ""}
    else:
        notes.append("no commits yet")
    upstream = None
    up = ok(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") if branch else None
    if up:
        lr = (ok(root, "rev-list", "--left-right", "--count", "@{u}...HEAD") or "0 0").split()
        upstream = {"name": up, "behind": int(lr[0]), "ahead": int(lr[1])}
    wt = working_tree(root, set(exclude), limit)

    todos = []
    if sha:
        _, d = git(root, "diff", "HEAD", *DIFF)
        todos += todos_in_diff(d, "uncommitted")
    else:
        _, d = git(root, "diff", "--cached", *DIFF)
        todos += todos_in_diff(d, "uncommitted")
    todos += todos_in_untracked(root)

    base_info = None
    ref = detect_base(root, base)
    if base and not ref:
        notes.append("base %s not found" % base)
    elif not ref:
        notes.append("no default branch found (main, master, origin/HEAD); pass --base REF")
    if ref and sha:
        mb = ok(root, "merge-base", ref, "HEAD")
        if mb:
            rng = mb + "..HEAD"
            _, log = git(root, "log", "--format=%h%x1f%s", "-n", str(limit), rng)
            commits = [dict(zip(("short", "subject"), l.split("\x1f", 1))) for l in log.splitlines() if l]
            _, names = git(root, "diff", "--name-status", "--no-renames", "-z", mb, "HEAD")
            np = [p for p in names.split("\0") if p]
            changed = [{"status": np[i], "path": np[i + 1]} for i in range(0, len(np) - 1, 2)]
            _, bd = git(root, "diff", mb, "HEAD", *DIFF)
            todos += todos_in_diff(bd, "branch")
            base_info = {"ref": ref, "merge_base": mb[:12],
                         "commit_count": int(ok(root, "rev-list", "--count", rng) or 0),
                         "commits": commits, "files_changed_count": len(changed), "files_changed": changed[:limit]}
            if branch and ref.split("/")[-1] == branch and base_info["commit_count"]:
                notes.append("on the default branch; commits listed are the ones not on " + ref)
        else:
            notes.append("no common ancestor with " + ref)

    todos = [t for t in todos if t["path"] not in exclude]
    stash = len([l for l in (ok(root, "stash", "list") or "").splitlines() if l.strip()])
    return {
        "repo": root, "branch": branch, "detached": branch is None and sha is not None, "head": head,
        "upstream": upstream, "base": base_info, "in_progress": in_progress(root), "stash": stash,
        "working_tree": wt, "todos": {"total": len(todos), "items": todos[:limit]},
        "memory_file": os.path.isfile(os.path.join(root, "MEMORY.md")),
        "handoff_file": os.path.isfile(os.path.join(root, "HANDOFF.md")), "notes": notes,
    }


# ---------- rendering ----------

def render_state(f, cap=None):
    """The '## Git state' block. --check parses Branch, HEAD, In progress and the Uncommitted list.
    cap shortens the commit, changed-file and TODO lists (the skeleton uses it to stay short)."""
    wt = f["working_tree"]
    if f["head"]:
        branch = f["branch"] or "(detached at %s)" % f["head"]["short"]
        head = "%s %s" % (f["head"]["short"], f["head"]["subject"])
    else:
        branch, head = f["branch"] or "(unknown)", "(no commits)"
    up = f["upstream"]
    out = ["- Repo: " + os.path.basename(f["repo"]),
           "- Branch: " + branch,
           "- HEAD: " + head,
           "- Upstream: " + ("%s (ahead %d, behind %d)" % (up["name"], up["ahead"], up["behind"]) if up else "none")]
    b = f["base"]
    if b:
        out.append("- Base: %s (merge-base %s; %d commits on this branch, %d files changed)"
                   % (b["ref"], b["merge_base"][:7], b["commit_count"], b["files_changed_count"]))
    out.append("- In progress: " + (", ".join(f["in_progress"]) or "none"))
    out.append("- Stash: %d" % f["stash"])
    out.append("- Uncommitted: %d (%d staged, %d unstaged, %d untracked, %d conflicted)"
               % (wt["total"], wt["staged"], wt["unstaged"], wt["untracked"], wt["conflicted"]))
    for x in wt["files"]:
        out.append("  - %s %s" % (x["code"], x["path"]))
    if wt["truncated"]:
        out.append("  - ... and %d more" % (wt["total"] - len(wt["files"])))
    t = f["todos"]
    for title, items, total, fmt in (
            ("Commits on this branch (newest first):", b["commits"] if b else [], b["commit_count"] if b else 0,
             lambda c: "%s %s" % (c["short"], c.get("subject", ""))),
            ("Files changed on this branch:", b["files_changed"] if b else [], b["files_changed_count"] if b else 0,
             lambda c: "%s %s" % (c["status"], c["path"])),
            ("TODO/FIXME/XXX added:", t["items"], t["total"],
             lambda x: "%s:%d (%s) %s" % (x["path"], x["line"], x["scope"], x["text"]))):
        shown = items[:cap] if cap else items
        if shown:
            out += ["", title] + ["- " + fmt(x) for x in shown]
            if total > len(shown):
                out.append("- ... and %d more" % (total - len(shown)))
    return "\n".join(out)


def render_report(f):
    lines = ["Handoff facts for " + f["repo"], "", render_state(f), ""]
    lines.append("MEMORY.md: " + ("present" if f["memory_file"] else "none") +
                 " · HANDOFF.md: " + ("present (run --check HANDOFF.md)" if f["handoff_file"] else "none"))
    lines += ["Note: " + n for n in f["notes"]]
    return "\n".join(lines)


def skeleton(f, where):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    state = render_state(f, cap=SKELETON_CAP) if f else "- Not a git repository (%s); nothing for --check to verify." % where
    return "\n".join([
        "# Handoff: <fill in: task name>",
        "_Written %s. Narrative sections by the agent; Git state by handoff.py, regenerate rather than edit._" % now,
        "",
        "## Goal",
        "<fill in: one line, in the user's words>",
        "",
        "## Status",
        "- [x] <fill in: done>",
        "- [ ] <fill in: in progress, and how far>",
        "- [ ] <fill in: not started>",
        "",
        "## Next step",
        "<fill in: the exact command or file:line to start from, not 'continue the work'>",
        "",
        "## Decisions",
        "- <fill in: decision, because reason>",
        "",
        "## Tried and failed",
        "- <fill in: what was tried, why it failed; so the next session does not retry it>",
        "",
        "## Open questions",
        "- <fill in: questions only the user can answer, or 'none'>",
        "",
        "## Verify",
        "```bash",
        "<fill in: commands that prove the work so far is correct>",
        "```",
        "",
        "## Git state",
        state,
        "",
        "## Links",
        "- <fill in: issue, PR, docs, or 'none'>",
        "",
    ])


# ---------- --check ----------

def section(text, name):
    m = re.search(r"^##\s+" + re.escape(name) + r"\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
    return m.group(1) if m else None


def parse_state(text):
    sec = section(text, "Git state")
    body = sec if sec is not None else text
    rec = {"branch": None, "detached": False, "head": None, "dirty": None, "dirty_count": None,
           "truncated": False, "in_progress": None}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        clean = lines[i].replace("`", "").replace("*", "").strip()
        i += 1
        m = re.match(r"^-?\s*(branch|head|in progress|uncommitted)\s*:\s*(.*)$", clean, re.I)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if key == "branch":
            if val.startswith("("):
                rec["detached"] = True
            elif val:
                rec["branch"] = val.split()[0]
        elif key == "head":
            h = re.match(r"^([0-9a-f]{4,40})\b", val)
            rec["head"] = h.group(1) if h else None
        elif key == "in progress":
            rec["in_progress"] = [] if val.lower() in ("none", "") else [v.split()[0] for v in val.split(",")]
        else:
            n = re.match(r"^(\d+)", val)
            rec["dirty_count"], rec["dirty"] = (int(n.group(1)) if n else None), set()
            while i < len(lines) and re.match(r"^\s+[-*]\s", lines[i]):
                item = lines[i].replace("`", "").strip()[1:].strip()
                i += 1
                if item.startswith("..."):
                    rec["truncated"] = True
                    continue
                d = re.match(r"^([.MADRCUT?!]{2})\s+(.+?)\s*$", item)
                if d:
                    rec["dirty"].add(d.group(2))
    return rec


def check(path, start=".", base=None):
    """Return (exit_code, result dict)."""
    res = {"file": path, "status": None, "drift": [], "notes": [], "next_step": None}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        res.update(status="cannot-check", notes=["cannot read %s: %s" % (path, e.strerror)])
        return 2, res
    ns = section(text, "Next step")
    res["next_step"] = ns.strip() if ns and ns.strip() else None
    holes = text.count(PLACEHOLDER)
    if holes:
        res["notes"].append("%d unfilled '%s' placeholder(s)" % (holes, PLACEHOLDER))
    rec = parse_state(text)
    if not rec["branch"] and not rec["head"] and not rec["detached"]:
        res["status"] = "cannot-check"
        res["notes"].append("no branch or HEAD recorded; nothing to verify")
        return 2, res
    try:
        root = repo_root(start)
    except NotARepo:
        res["status"] = "cannot-check"
        res["notes"].append("not a git repository: " + os.path.abspath(start))
        return 2, res
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    exclude = () if rel.startswith("..") else (rel,)
    live = gather(root, base=base, limit=10 ** 6, exclude=exclude)
    drift = res["drift"]

    if rec["detached"] != live["detached"] or (rec["branch"] and rec["branch"] != live["branch"]):
        drift.append("branch: recorded %s, now %s" % (rec["branch"] or "detached", live["branch"] or "detached"))

    lh = live["head"]["sha"] if live["head"] else None
    if rec["head"] and not (lh and lh.startswith(rec["head"])):
        drift.append(head_drift(root, rec["head"], lh, exclude))
        if drift[-1] is None:
            drift.pop()
            res["notes"].append("HEAD moved only by committing the handoff file itself")

    if rec["dirty"] is not None:
        now = {x["path"] for x in live["working_tree"]["files"]}
        gone = sorted(rec["dirty"] - now)
        new = sorted(now - rec["dirty"])
        if rec["truncated"]:
            if gone:
                drift.append("no longer uncommitted: " + ", ".join(gone[:10]))
            if rec["dirty_count"] is not None and rec["dirty_count"] != len(now):
                drift.append("uncommitted file count: recorded %d, now %d" % (rec["dirty_count"], len(now)))
        else:
            if new:
                drift.append("uncommitted since the handoff: " + ", ".join(new[:10]) +
                             (" (+%d more)" % (len(new) - 10) if len(new) > 10 else ""))
            if gone:
                drift.append("no longer uncommitted (committed, stashed or reverted): " + ", ".join(gone[:10]) +
                             (" (+%d more)" % (len(gone) - 10) if len(gone) > 10 else ""))
    else:
        res["notes"].append("no uncommitted-file list recorded; skipped that comparison")

    if rec["in_progress"] is not None:
        live_ops = [o.split()[0] for o in live["in_progress"]]
        if sorted(rec["in_progress"]) != sorted(live_ops):
            drift.append("in-progress git operation: recorded %s, now %s"
                         % (", ".join(rec["in_progress"]) or "none", ", ".join(live_ops) or "none"))
    res["status"] = "drift" if drift else "match"
    return (1 if drift else 0), res


def head_drift(root, rec, live, exclude):
    if not live:
        return "HEAD: recorded %s, now no commits" % rec
    full = ok(root, "rev-parse", "--verify", "-q", rec + "^{commit}")
    if not full:
        return "HEAD: recorded %s is not in this clone (rebased, amended, or not fetched); now %s" % (rec, live[:7])
    if ok(root, "merge-base", "--is-ancestor", full, live) is not None:
        n = ok(root, "rev-list", "--count", full + "..HEAD")
        changed = set((ok(root, "diff", "--name-only", full, "HEAD") or "").splitlines())
        if exclude and changed and changed <= set(exclude):
            return None
        _, log = git(root, "log", "--format=%h %s", "-n", "5", full + "..HEAD")
        return "HEAD: %s new commit(s) since the handoff (%s..%s): %s" % (
            n, rec[:7], live[:7], "; ".join(log.splitlines()))
    if ok(root, "merge-base", "--is-ancestor", live, full) is not None:
        return "HEAD: now %s, which is behind the recorded %s (reset, or not pulled)" % (live[:7], rec[:7])
    return "HEAD: recorded %s and now %s have diverged (rebase or amend?)" % (rec[:7], live[:7])


def render_check(r):
    out = ["%s: %s" % (r["file"], {"match": "MATCH, git state is as recorded",
                                   "drift": "DRIFT, the repo changed since the handoff",
                                   "cannot-check": "CANNOT CHECK"}[r["status"]])]
    out += ["  - " + d for d in r["drift"]]
    out += ["Note: " + n for n in r["notes"]]
    if r["next_step"]:
        out += ["", "Next step (from the handoff):", r["next_step"]]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Collect handoff facts from git, write a HANDOFF.md skeleton, or check one.")
    ap.add_argument("-C", dest="dir", default=".", help="repository directory (default: .)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", metavar="PATH", help="write a HANDOFF.md skeleton to PATH")
    mode.add_argument("--check", metavar="PATH", help="compare PATH's recorded git state with the live repo")
    ap.add_argument("--force", action="store_true", help="let --write overwrite an existing file")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--base", help="default branch to diff against (default: origin/HEAD, main, master)")
    ap.add_argument("--limit", type=int, default=25, help="cap for each list (default 25)")
    a = ap.parse_args(argv)

    if a.check:
        code, res = check(a.check, a.dir, a.base)
        print(json.dumps(res, indent=2) if a.json else render_check(res))
        return code

    exclude = ()
    if a.write:
        try:
            root = repo_root(a.dir)
            rel = os.path.relpath(os.path.realpath(os.path.abspath(a.write)), os.path.realpath(root))
            exclude = () if rel.startswith("..") else (rel,)
        except NotARepo:
            pass
    try:
        facts = gather(a.dir, base=a.base, limit=max(1, a.limit), exclude=exclude)
    except NotARepo:
        facts = None

    if a.write:
        if os.path.exists(a.write) and not a.force:
            print("refusing to overwrite %s (pass --force)" % a.write, file=sys.stderr)
            return 1
        try:
            with open(a.write, "w", encoding="utf-8") as fh:
                fh.write(skeleton(facts, os.path.abspath(a.dir)))
        except OSError as e:
            print("cannot write %s: %s" % (a.write, e.strerror), file=sys.stderr)
            return 1
        if facts is None:
            print("note: %s is not a git repository; the skeleton has no git state to check later"
                  % os.path.abspath(a.dir), file=sys.stderr)
        if a.json:
            print(json.dumps({"written": a.write, "facts": facts}, indent=2))
        else:
            print("wrote %s; replace every '%s' placeholder, keep it under ~60 lines" % (a.write, PLACEHOLDER))
        return 0

    if facts is None:
        msg = "not a git repository: " + os.path.abspath(a.dir)
        if a.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg, file=sys.stderr)
        return 2
    print(json.dumps(facts, indent=2) if a.json else render_report(facts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
