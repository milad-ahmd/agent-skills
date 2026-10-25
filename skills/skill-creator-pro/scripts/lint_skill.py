#!/usr/bin/env python3
"""Lint a single Agent Skill folder (SKILL.md). Stdlib only.

Usage: python3 lint_skill.py path/to/skill-name
Exit code 1 on errors.
"""
import os
import re
import sys

NAME_RX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SECRET = re.compile(r"(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    d = sys.argv[1].rstrip("/")
    p = os.path.join(d, "SKILL.md")
    if not os.path.isfile(p):
        sys.exit(f"❌ {p} not found")
    text = open(p, encoding="utf-8").read()
    errs, warns, ok = [], [], []
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        errs.append("frontmatter must start the file: ---\\nname: ...\\ndescription: ...\\n---")
        fm = {}
    else:
        fm = dict(l.split(":", 1) for l in m.group(1).splitlines() if ":" in l and not l.startswith(" "))
        fm = {k.strip(): v.strip() for k, v in fm.items()}
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
        if not re.search(r"\b(use (when|for|if|before)|when the user)\b", desc, re.I): warns.append("add 'Use when the user asks to …' trigger phrases")
        if re.match(r"(?i)\s*(i|you|we)\b", desc): warns.append("write the description in third person")
    body = text[m.end():] if m else text
    n = body.count("\n")
    (warns.append(f"body is {n} lines (> 500): move details to references/") if n > 500 else ok.append(f"body {n} lines"))
    if not re.search(r"^#{1,3} ", body, re.M): warns.append("no headings: structure as workflow / output / rules")
    if not re.search(r"(?im)^#+.*\b(rules|guardrails|constraints)\b", body): warns.append("no Rules section: add safety/quality guardrails")
    for ref in set(re.findall(r"\b((?:scripts|references)/[\w./-]+)", body)):
        if not os.path.exists(os.path.join(d, ref)): errs.append(f"referenced file missing: {ref}")
    if SECRET.search(text): errs.append("possible secret in SKILL.md")
    for o in ok: print(f"✅ {o}")
    for w in warns: print(f"⚠️  {w}")
    for e in errs: print(f"❌ {e}")
    print(f"\n{'FAIL' if errs else 'PASS'}: {len(errs)} errors, {len(warns)} warnings")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
