#!/usr/bin/env python3
"""Audit Agent Skills directories (SKILL.md). Stdlib only.

Usage: python3 skills_doctor.py [skills_dir ...]
Defaults: ~/.claude/skills and ./.claude/skills
"""
import itertools
import os
import re
import sys

STOP = set("a an the and or to of for in on with when user asks use is are it this that by from as at be your you their into its can".split())
SECRET = re.compile(r"(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|github_pat_\w{50,}|xox[baprs]-[\w-]{10,}|AIza[\w-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
NAME_RX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def frontmatter(text):
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def words(s):
    return {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", s.lower()) if w not in STOP}


def check(skill_dir):
    errs, warns = [], []
    path = os.path.join(skill_dir, "SKILL.md")
    folder = os.path.basename(skill_dir.rstrip("/"))
    text = open(path, encoding="utf-8", errors="replace").read()
    fm = frontmatter(text)
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
            if not re.search(r"\b(use when|use for|when the user|use if|trigger)", desc, re.I):
                warns.append("description lacks 'Use when…' trigger language")
    lines = text.count("\n") + 1
    if lines > 500:
        warns.append(f"SKILL.md is {lines} lines (> 500): move detail into references/")
    for ref in set(re.findall(r"\b(scripts/[\w./-]+\.(?:py|sh|js|ts|rb))", text)):
        if not os.path.exists(os.path.join(skill_dir, ref)):
            errs.append(f"referenced file missing: {ref}")
    for root, _, files in os.walk(skill_dir):
        for f in files:
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) < 2_000_000 and SECRET.search(open(p, encoding="utf-8", errors="ignore").read()):
                    errs.append(f"possible secret in {os.path.relpath(p, skill_dir)}: remove & rotate")
            except OSError:
                pass
    return folder, desc, errs, warns


def main():
    dirs = sys.argv[1:] or [os.path.expanduser("~/.claude/skills"), ".claude/skills"]
    skills = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for entry in sorted(os.listdir(d)):
            sd = os.path.join(d, entry)
            if os.path.isfile(os.path.join(sd, "SKILL.md")):
                skills.append(sd)
    if not skills:
        sys.exit(f"No skills found in: {', '.join(dirs)}")
    results = [check(s) for s in skills]
    ne = nw = 0
    for name, _, errs, warns in results:
        icon = "❌" if errs else "⚠️ " if warns else "✅"
        print(f"{icon} {name}")
        for e in errs:
            print(f"     ❌ {e}")
        for w in warns:
            print(f"     ⚠️  {w}")
        ne += len(errs)
        nw += len(warns)
    overlaps = []
    for (a, da, *_), (b, db, *_) in itertools.combinations(results, 2):
        wa, wb = words(da), words(db)
        if wa and wb:
            j = len(wa & wb) / len(wa | wb)
            if j >= 0.35:
                overlaps.append((j, a, b))
    for j, a, b in sorted(overlaps, reverse=True)[:10]:
        print(f"🔀 overlap {j:.0%}: {a} ↔ {b}: sharpen descriptions or merge")
    print(f"\n{len(results)} skills · {ne} errors · {nw} warnings · {len(overlaps)} overlaps")
    sys.exit(1 if ne else 0)


if __name__ == "__main__":
    main()
