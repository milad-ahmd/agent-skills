---
name: skills-doctor
description: Audits installed Agent Skills (SKILL.md) for problems that stop them from working, such as invalid or missing frontmatter, name and folder mismatches, vague or overlapping descriptions that never trigger or trigger wrongly, oversized files, missing referenced scripts, and possible embedded secrets, using a linter script, then proposes fixes. Use when the user asks to check, debug, clean up, or audit their skills, or asks why a skill isn't triggering.
---

# Skills Doctor 🩺

Diagnose why skills don't trigger, trigger at the wrong time or conflict with each other.

## 1. Scan

```bash
python3 scripts/skills_doctor.py                          # ~/.claude/skills + ./.claude/skills
python3 scripts/skills_doctor.py path/to/skills-dir ...   # custom locations
python3 scripts/skills_doctor.py --strict skills/         # CI: also fail on warnings/overlaps
```

Exit codes: `0` clean · `1` errors · `2` warnings or overlaps (only with `--strict`).

The checks:
- **Frontmatter:** present and valid, including folded (`>`), literal (`|`) and quoted multi-line descriptions.
- **Name:** matches its folder and is kebab-case.
- **Description:** exists, isn't too short or too long, and has "Use when…" trigger language.
- **File size,** and scripts that are referenced but missing.
- **Secrets:** both known key shapes (AKIA…, sk-…, ghp_…, private keys) and generic assignments like `*_SECRET*=`, `*PASSWORD*=`, `*TOKEN*=`, `*API_KEY*=`, plus credentials inside URLs (`scheme://user:pass@host`).
- **Overlap:** pairs of skills whose whole descriptions (Jaccard ≥ 0.20) or "Use when…" clauses (≥ 0.30) overlap. The trigger clause is where conflicts actually happen.

**Limits:** the secret scan is heuristic. It catches common leak patterns but not every secret format, so it never proves a skill is safe. For anything you'll publish, also run a dedicated scanner (`gitleaks`, `trufflehog`) and read the scripts yourself.

## 2. Interpret

- ❌ **Errors** break loading. Fix them first.
- ⚠️ **Warnings** hurt triggering or quality.
- 🔀 **Overlaps**: decide whether to merge the skills, sharpen both descriptions with distinct triggers, or uninstall one.
- 📏 **Closest pairs**: the summary always shows the highest description and trigger-clause scores, with their distance to the threshold. "0 overlaps" only means nothing crossed the line. A pair marked `close: review` (within 0.05) is one new similar skill away from conflicting, so mention it to the user.

## 3. Fix (with user approval)

Propose exact edits: rewritten descriptions (`<what it does>. Use when the user asks to …`), renames, splitting oversized skills into `references/`, and removing secrets (then **rotate** them).

Re-run with `--strict` until it exits `0`, meaning **no errors, no warnings and no overlaps**. "No errors" alone is not a clean bill of health. Read every warning, and if you believe one is a false positive, tell the user why instead of silently ignoring it.

## 4. Trigger test

For each important skill, write 3 prompts that **should** trigger it and 2 that **should not**, then check the descriptions against them.

## Rules

- Never delete a skill without confirmation. Back up before bulk edits.
- Never print the full value of a detected secret.
