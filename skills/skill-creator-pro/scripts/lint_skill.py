#!/usr/bin/env python3
"""Lint a single Agent Skill folder (SKILL.md). Stdlib only.

Usage: python3 lint_skill.py path/to/skill-name
Exit code 1 on errors.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill_checks import NAME_RX, find_secrets, parse_frontmatter, trigger_clause  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    d = sys.argv[1].rstrip("/")
    p = os.path.join(d, "SKILL.md")
    if not os.path.isfile(p):
        sys.exit(f"❌ {p} not found")
    text = open(p, encoding="utf-8").read()
    errs, warns, ok = [], [], []
    fm, body = parse_frontmatter(text)
    if fm is None:
        errs.append("frontmatter must start the file: ---\\nname: ...\\ndescription: ...\\n---")
        fm = {}
    name, desc = fm.get("name", ""), fm.get("description", "")
    if not name: errs.append("missing `name`")
    elif not NAME_RX.match(name) or len(name) > 64: errs.append(f"`name` must be kebab-case ≤ 64 chars (got `{name}`)")
    elif name != os.path.basename(d): warns.append(f"`name` ({name}) should match folder ({os.path.basename(d)})")
    else: ok.append(f"name `{name}`")
    if not desc: errs.append("missing `description`")
    else:
        if len(desc) > 1024: errs.append(f"description too long ({len(desc)} > 1024)")
        elif len(desc) < 80: warns.append(f"description short ({len(desc)} chars): add concrete triggers")
        else: ok.append(f"description {len(desc)} chars")
        if not trigger_clause(desc): warns.append("add 'Use when the user asks to …' trigger phrases")
        if re.match(r"(?i)\s*(i|you|we)\b", desc): warns.append("write the description in third person")
    n = body.count("\n")
    (warns.append(f"body is {n} lines (> 500): move details to references/") if n > 500 else ok.append(f"body {n} lines"))
    if not re.search(r"^#{1,3} ", body, re.M): warns.append("no headings: structure as workflow / output / rules")
    if not re.search(r"(?im)^#+.*\b(rules|guardrails|constraints)\b", body): warns.append("no Rules section: add safety/quality guardrails")
    for ref in set(re.findall(r"\b((?:scripts|references)/[\w./-]+)", body)):
        if not os.path.exists(os.path.join(d, ref)): errs.append(f"referenced file missing: {ref}")
    for kind, ln, val in find_secrets(text):
        errs.append(f"possible secret in SKILL.md:{ln}: {kind} {val}")
    for o in ok: print(f"✅ {o}")
    for w in warns: print(f"⚠️  {w}")
    for e in errs: print(f"❌ {e}")
    print(f"\n{'FAIL' if errs else 'PASS'}: {len(errs)} errors, {len(warns)} warnings")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
