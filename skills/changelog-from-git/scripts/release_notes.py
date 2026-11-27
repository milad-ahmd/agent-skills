#!/usr/bin/env python3
"""Collect the facts for release notes from a git commit range. Stdlib only, read-only, offline.

Usage: python3 release_notes.py [-C REPO] [--from REF] [--to REF] [--show-authors] [--json | --md]
  -C REPO         repository to read (default: current directory)
  --from REF      start of the range, exclusive (default: the latest tag reachable from --to, found with
                  `git describe --tags --abbrev=0`; if --to itself carries that tag, the tag before it;
                  with no tags, the whole history up to --to)
  --to REF        end of the range, inclusive (default: HEAD)
  --show-authors  list contributor names; by default they are only counted
  --json          machine-readable report on stdout
  --md            a CHANGELOG section skeleton to rewrite into user-facing language

How it groups (Conventional Commits 1.0.0, https://www.conventionalcommits.org/en/v1.0.0/):
  subject     `type(scope)!: description`; type is matched case-insensitively. Breaking if `!` sits right
              before the colon, or if a body line starts with the uppercase footer token `BREAKING CHANGE: `
              or `BREAKING-CHANGE: ` (its value runs until the next `Token: ` / `Token #` footer line).
  groups      breaking (any type), feat -> features, fix -> fixes, perf -> performance, docs -> docs,
              everything else (refactor, chore, ci, test, non-conventional subjects ...) -> other.
These rules are this script's own, not part of the spec:
  PR numbers  a trailing `(#123)` on the subject (squash merges), or `Merge pull request #123 from ...`:
              commits brought in by that merge (merge^1..merge^2) get its number.
  merges      merge commits are never listed themselves.
  reverts     a `Revert "..."` commit and the commit it reverts cancel out when both are in the range
              (matched by "This reverts commit <sha>." in the body, else by the quoted subject). A revert
              whose original is outside the range stays, in other.
  dep bumps   not listed one by one but counted on one line: commits whose author name or email contains
              "dependabot" or "renovate", or whose subject is chore(deps|deps-dev) / build(deps|deps-dev).
              A breaking bump is listed anyway.
  people      contributors = distinct authors after .mailmap, minus dependabot/renovate and "[bot]" names
  semver      suggested next bump: major if anything is breaking, minor if any feat, else patch. A
              suggestion only; under 1.0.0 SemVer makes no promises and many projects bump minor instead.

Exit codes: 0 report written
            1 no commits in the range
            2 not a git repository, a bad ref, or another git error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

GIT_ENV = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
GROUPS = [("breaking", "Breaking changes"), ("features", "Features"), ("fixes", "Fixes"),
          ("performance", "Performance"), ("docs", "Docs"), ("other", "Other")]
TYPE_GROUP = {"feat": "features", "fix": "fixes", "perf": "performance", "docs": "docs"}
SUBJECT_RE = re.compile(r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\r\n]*)\))?(?P<bang>!)?: (?P<desc>\S.*)$")
BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE: (.*)$")  # uppercase only, per spec item 15
FOOTER_RE = re.compile(r"^(?:[A-Za-z][\w-]*|BREAKING CHANGE)(?:: | #)")
PR_SQUASH_RE = re.compile(r"\s*\(#(\d+)\)\s*$")
PR_MERGE_RE = re.compile(r"^Merge pull request #(\d+) from ")
REVERT_SHA_RE = re.compile(r"This reverts commit ([0-9a-f]{7,64})")
REVERT_SUBJ_RE = re.compile(r'^Revert "(.*)"$')
DEP_BOT_RE = re.compile(r"dependabot|renovate", re.I)
DEP_SCOPES = {"deps", "deps-dev"}
SEMVER_RE = re.compile(r"^(v?)(\d+)\.(\d+)\.(\d+)$")


class GitError(Exception):
    pass


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], env=GIT_ENV, capture_output=True, stdin=subprocess.DEVNULL)
    if check and r.returncode:
        raise GitError(r.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
    return r


def out(repo, *args):
    return git(repo, *args).stdout.decode("utf-8", "replace").strip()


def commit_of(repo, ref):
    r = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
    if r.returncode:
        raise GitError("not a commit: %s" % ref)
    return r.stdout.decode().strip()


def latest_tag(repo, ref):
    r = git(repo, "describe", "--tags", "--abbrev=0", ref, check=False)
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else None


def resolve_range(repo, frm, to):
    """(from_ref or None for the root, to_ref, to_sha)."""
    to_sha = commit_of(repo, to)
    if frm:
        commit_of(repo, frm)
        return frm, to, to_sha
    tag = latest_tag(repo, to_sha)
    if tag and commit_of(repo, tag) == to_sha:  # --to is the tag just pushed: notes since the one before
        parent = git(repo, "rev-parse", "--verify", "--quiet", to_sha + "^", check=False)
        tag = latest_tag(repo, to_sha + "^") if parent.returncode == 0 else None
    return tag, to, to_sha


def parse_breaking_note(body_lines):
    """Value of the first BREAKING CHANGE / BREAKING-CHANGE footer, or None."""
    # shortcut: looks at every body line, not only the final footer paragraph; good enough for real messages
    for i, line in enumerate(body_lines):
        m = BREAKING_RE.match(line)
        if m:
            note = [m.group(1)]
            for nxt in body_lines[i + 1:]:
                if FOOTER_RE.match(nxt):
                    break
                note.append(nxt)
            return " ".join(s.strip() for s in note if s.strip()) or None
    return None


def parse_commit(sha, parents, name, email, date, message):
    lines = message.split("\n")
    subject = lines[0].strip()
    c = {"sha": sha, "short": sha[:7], "parents": parents, "name": name, "email": email, "date": date,
         "subject": subject, "type": None, "scope": None, "description": subject, "breaking": False,
         "breaking_note": None, "pr": None, "conventional": False, "body": "\n".join(lines[1:])}
    m = PR_SQUASH_RE.search(subject)
    if m:
        c["pr"] = int(m.group(1))
    sm = SUBJECT_RE.match(subject)
    if sm:
        c.update(conventional=True, type=sm.group("type").lower(), scope=sm.group("scope") or None,
                 description=sm.group("desc"), breaking=bool(sm.group("bang")))
    c["description"] = PR_SQUASH_RE.sub("", c["description"]).strip()
    note = parse_breaking_note(lines[1:])
    if note:
        c["breaking"], c["breaking_note"] = True, note
    return c


def read_commits(repo, frm, to_sha):
    rng = "%s..%s" % (frm, to_sha) if frm else to_sha
    raw = git(repo, "log", "--use-mailmap", "--format=%H%x00%P%x00%aN%x00%aE%x00%cs%x00%B%x1e", rng).stdout
    commits = []
    for rec in raw.decode("utf-8", "replace").split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, name, email, date, msg = rec.split("\x00", 5)
        commits.append(parse_commit(sha, parents.split(), name, email, date, msg.strip()))
    return commits


def is_bot(c):
    return bool(DEP_BOT_RE.search(c["name"] + " " + c["email"])) or c["name"].endswith("[bot]")


def is_dep_bump(c):
    if DEP_BOT_RE.search(c["name"] + " " + c["email"]):
        return True
    return c["type"] in ("chore", "build") and (c["scope"] or "").lower() in DEP_SCOPES


def cancel_reverts(commits):
    """Shas of revert pairs that are both in the range. commits are newest first."""
    gone = set()
    for i, c in enumerate(commits):  # newest first: a revert of a revert cancels the inner revert, not the original
        if c["sha"] in gone:
            continue
        older = [o for o in commits[i + 1:] if o["sha"] not in gone]
        m = REVERT_SHA_RE.search(c["body"])
        if m:  # the body names the commit; if it is outside the range, the revert stays
            target = next((o["sha"] for o in older if o["sha"].startswith(m.group(1))), None)
        else:
            sm = REVERT_SUBJ_RE.match(PR_SQUASH_RE.sub("", c["subject"]))
            want = sm and PR_SQUASH_RE.sub("", sm.group(1))
            target = next((o["sha"] for o in older if PR_SQUASH_RE.sub("", o["subject"]) == want), None) if sm else None
        if target:
            gone.update((c["sha"], target))
    return gone


def bump(groups, base_tag):
    level = "major" if groups["breaking"] else "minor" if groups["features"] else "patch"
    m = SEMVER_RE.match(base_tag or "")
    nxt = None
    if m:
        v, a, b, p = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        a, b, p = (a + 1, 0, 0) if level == "major" else (a, b + 1, 0) if level == "minor" else (a, b, p + 1)
        nxt = "%s%d.%d.%d" % (v, a, b, p)
    return {"level": level, "next": nxt, "note": "a suggestion from commit types; check it against what shipped"}


def build_report(repo, frm, to, show_authors):
    frm, to, to_sha = resolve_range(repo, frm, to)
    commits = read_commits(repo, frm, to_sha)
    if not commits:
        return None
    # attach merge PR numbers to the commits each merge brought in
    for c in commits:
        m = PR_MERGE_RE.match(c["subject"])
        if m and len(c["parents"]) > 1:
            brought = out(repo, "rev-list", "%s..%s" % (c["parents"][0], c["parents"][1])).split()
            for o in commits:
                if o["sha"] in brought and o["pr"] is None:
                    o["pr"] = int(m.group(1))
    merges = [c for c in commits if len(c["parents"]) > 1]
    plain = [c for c in commits if len(c["parents"]) <= 1]
    gone = cancel_reverts(plain)
    groups = {k: [] for k, _ in GROUPS}
    deps = []
    for c in plain:
        if c["sha"] in gone:
            continue
        if is_dep_bump(c) and not c["breaking"]:
            deps.append(c)
            continue
        groups["breaking" if c["breaking"] else TYPE_GROUP.get(c["type"], "other")].append(c)
    people = sorted({(c["name"], c["email"]) for c in plain if not is_bot(c)})
    to_tag = git(repo, "describe", "--tags", "--exact-match", to_sha, check=False).stdout.decode().strip()
    keys = ("short", "type", "scope", "description", "breaking_note", "pr", "date")
    rep = {
        "range": {"from": frm, "to": to, "to_sha": to_sha[:12], "from_root": frm is None,
                  "to_tag": to_tag or None},
        "counts": {"commits": len(plain), "merges_skipped": len(merges), "reverted_pairs": len(gone) // 2,
                   "dependency_bumps": len(deps), "conventional": sum(c["conventional"] for c in plain)},
        "groups": {k: [{x: c[x] for x in keys} for c in v] for k, v in groups.items()},
        "contributors": len(people),
        "suggested_bump": bump(groups, frm),
        "date": out(repo, "log", "-1", "--format=%cs", to_sha),
    }
    if show_authors:
        rep["authors"] = ["%s <%s>" % p for p in people]
    return rep


def entry(e, sha=True):
    refs = (["#%d" % e["pr"]] if e["pr"] else []) + ([e["short"]] if sha else [])
    scope = "**%s:** " % e["scope"] if e["scope"] else ""
    text = "%s%s" % (scope, e["description"])
    if e["breaking_note"]:
        text += " -- %s" % e["breaking_note"]
    return "- %s (%s)" % (text, ", ".join(refs)) if refs else "- " + text


def render_md(rep):
    title = rep["range"]["to_tag"] or rep["suggested_bump"]["next"] or "Unreleased"
    lines = ["## %s - %s" % (title, rep["date"]), "", "**Highlights:** TODO, one or two sentences a user cares about.", ""]
    for key, label in GROUPS:
        if rep["groups"][key]:
            lines.append("### " + label)
            lines += [entry(e) for e in rep["groups"][key]]
            if key == "breaking":
                lines.append("<!-- every breaking change needs a Migration: line -->")
            lines.append("")
    c = rep["counts"]
    if c["dependency_bumps"]:
        lines += ["- %d dependency updates" % c["dependency_bumps"], ""]
    lines.append("%d contributors." % rep["contributors"] if not rep.get("authors") else
                 "Contributors: " + ", ".join(a.split(" <")[0] for a in rep["authors"]))
    return "\n".join(lines)


def render_text(rep):
    r, c, b = rep["range"], rep["counts"], rep["suggested_bump"]
    lines = ["Range: %s..%s%s%s" % (r["from"] or "(root)", r["to"], " (tagged %s)" % r["to_tag"] if r["to_tag"] else "",
                                    " (no earlier tag: whole history)" if r["from_root"] else ""),
             "Commits: %d (%d conventional), merges skipped: %d, reverted pairs dropped: %d, dependency bumps: %d"
             % (c["commits"], c["conventional"], c["merges_skipped"], c["reverted_pairs"], c["dependency_bumps"]),
             "Contributors: %d" % rep["contributors"],
             "Suggested bump: %s%s (%s)" % (b["level"], " -> " + b["next"] if b["next"] else "", b["note"])]
    if c["commits"] and c["conventional"] * 2 < c["commits"]:
        lines.append("Mostly non-conventional subjects: group by reading the commits, not by this table.")
    for key, label in GROUPS:
        if rep["groups"][key]:
            lines += ["", "%s (%d)" % (label, len(rep["groups"][key]))]
            lines += ["  " + entry(e)[2:] for e in rep["groups"][key]]
    if rep.get("authors"):
        lines += ["", "Authors:"] + ["  " + a for a in rep["authors"]]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(usage=argparse.SUPPRESS, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-C", dest="repo", default=".")
    ap.add_argument("--from", dest="frm", metavar="REF")
    ap.add_argument("--to", default="HEAD", metavar="REF")
    ap.add_argument("--show-authors", action="store_true")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--md", action="store_true")
    a = ap.parse_args(argv)
    try:
        git(a.repo, "rev-parse", "--git-dir")
        rep = build_report(a.repo, a.frm, a.to, a.show_authors)
    except GitError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if rep is None:
        print("no commits in the range; pass --from with an older tag or ref", file=sys.stderr)
        return 1
    print(json.dumps(rep, indent=2) if a.json else render_md(rep) if a.md else render_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
