#!/usr/bin/env python3
"""Audit Agent Skills directories (SKILL.md). Stdlib only.

Usage: python3 skills_doctor.py [--strict] [--overlap 0.20] [skills_dir ...]
Defaults: ~/.claude/skills and ./.claude/skills

Exit codes: 0 clean · 1 errors · 2 warnings or overlaps only (with --strict)
"""
import argparse
import itertools
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill_checks import NAME_RX, find_secrets, jaccard, parse_frontmatter, trigger_clause  # noqa: E402


SELF_FILES = {"skill_checks.py", "test_skill_checks.py"}


def check(skill_dir):
    errs, warns = [], []
    folder = os.path.basename(skill_dir.rstrip("/"))
    text = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8", errors="replace").read()
    fm, body = parse_frontmatter(text)
    desc = ""
    if fm is None:
        errs.append("missing or unclosed YAML frontmatter (--- ... ---)")
    else:
        name, desc = fm.get("name", ""), fm.get("description", "")
        if not name:
            errs.append("frontmatter has no `name`")
        elif not NAME_RX.match(name) or len(name) > 64:
            errs.append(f"name `{name}` must be kebab-case and ≤ 64 chars")
        elif name != folder:
            warns.append(f"name `{name}` ≠ folder `{folder}`")
        if not desc:
            errs.append("frontmatter has no `description`")
        else:
            if len(desc) < 60:
                warns.append(f"description is short ({len(desc)} chars): may never trigger")
            if len(desc) > 1024:
                warns.append(f"description is long ({len(desc)} chars > 1024)")
            if not trigger_clause(desc) and str(fm.get("disable-model-invocation", "")).lower() != "true":
                warns.append("description lacks 'Use when…' trigger language")
    lines = text.count("\n") + 1
    if lines > 500:
        warns.append(f"SKILL.md is {lines} lines (> 500): move detail into references/")
    for ref in set(re.findall(r"\b(scripts/[\w./-]+\.(?:py|sh|js|ts|rb))", body)):
        if not os.path.exists(os.path.join(skill_dir, ref)):
            errs.append(f"referenced file missing: {ref}")
    for root, _, files in os.walk(skill_dir):
        for f in files:
            p = os.path.join(root, f)
            if f in SELF_FILES or os.path.getsize(p) > 2_000_000:
                continue  # the detector's own patterns/fixtures are intentional
            try:
                for kind, ln, val in find_secrets(open(p, encoding="utf-8", errors="ignore").read()):
                    errs.append(f"possible secret in {os.path.relpath(p, skill_dir)}:{ln}: {kind} {val} (remove & rotate)")
            except OSError:
                pass
    return folder, desc, errs, warns


def main():
    ap = argparse.ArgumentParser(description="Audit Agent Skills (SKILL.md).")
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--strict", action="store_true", help="exit 2 on warnings/overlaps (for CI)")
    ap.add_argument("--overlap", type=float, default=0.20, help="Jaccard threshold, whole description (default 0.20)")
    ap.add_argument("--trigger-overlap", type=float, default=0.30, help="Jaccard threshold, 'Use when…' clause only (default 0.30)")
    a = ap.parse_args()
    dirs = a.dirs or [os.path.expanduser("~/.claude/skills"), ".claude/skills"]
    skills = []
    for d in dirs:
        if os.path.isfile(os.path.join(d, "SKILL.md")):
            skills.append(d)
        elif os.path.isdir(d):
            skills += [os.path.join(d, e) for e in sorted(os.listdir(d)) if os.path.isfile(os.path.join(d, e, "SKILL.md"))]
    if not skills:
        sys.exit(f"No skills found in: {', '.join(dirs)}")
    results = [check(s) for s in skills]
    ne = nw = 0
    for name, _, errs, warns in results:
        print(f"{'❌' if errs else '⚠️ ' if warns else '✅'} {name}")
        for e in errs:
            print(f"     ❌ {e}")
        for w in warns:
            print(f"     ⚠️  {w}")
        ne, nw = ne + len(errs), nw + len(warns)
    overlaps = []
    for (x, dx, *_), (y, dy, *_) in itertools.combinations(results, 2):
        jd, jt = jaccard(dx, dy), jaccard(trigger_clause(dx), trigger_clause(dy))
        if jd >= a.overlap or jt >= a.trigger_overlap:
            overlaps.append((max(jd, jt), jd, jt, x, y))
    for _, jd, jt, x, y in sorted(overlaps, reverse=True)[:15]:
        print(f"🔀 overlap {x} ↔ {y}: description {jd:.0%}, trigger clause {jt:.0%}. Give each distinct trigger phrases, or merge")
    print(f"\n{len(results)} skills · {ne} errors · {nw} warnings · {len(overlaps)} overlaps"
          f"  (thresholds: description {a.overlap:.2f}, trigger clause {a.trigger_overlap:.2f})")
    if ne:
        sys.exit(1)
    if a.strict and (nw or overlaps):
        sys.exit(2)


if __name__ == "__main__":
    main()
