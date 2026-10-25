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
```

The checks: frontmatter present and valid, `name` matches its folder and is kebab-case, the description exists, isn't too short or too long, and contains "when to use" trigger language; the file size; scripts that are referenced but missing; likely secrets; and pairs of skills whose descriptions overlap heavily (conflict risk).

## 2. Interpret

- ❌ **Errors** break loading. Fix them first.
- ⚠️ **Warnings** hurt triggering or quality.
- 🔀 **Overlaps**: decide whether to merge the skills, sharpen both descriptions with distinct triggers, or uninstall one.

## 3. Fix (with user approval)

Propose exact edits: rewritten descriptions (`<what it does>. Use when the user asks to …`), renames, splitting oversized skills into `references/`, and removing secrets (then **rotate** them). Re-run the doctor until it reports no errors.

## 4. Trigger test

For each important skill, write 3 prompts that **should** trigger it and 2 that **should not**, then check the descriptions against them.

## Rules

- Never delete a skill without confirmation. Back up before bulk edits.
- Never print the full value of a detected secret.
