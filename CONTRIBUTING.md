# Contributing & maintenance

## Source of truth
Every skill lives in **`skills/<name>/`** in this repo. The per-skill repos (`milad-ahmd/<name>`) are **mirrors** and only exist so each skill can be found and starred on its own. Never edit a mirror directly; it gets overwritten on the next sync.

## Changing a skill
1. Edit `skills/<name>/` here.
2. Run the checks locally:
   ```bash
   python3 -m unittest discover -s skills/skills-doctor/scripts -p 'test_*.py'
   python3 skills/skills-doctor/scripts/skills_doctor.py --strict skills
   ```
3. Commit and push. CI repeats these checks, rebuilds the website and notifies search engines.
4. Update the mirrors:
   ```bash
   scripts/sync_mirrors.sh --dry-run    # see which mirrors are out of date
   scripts/sync_mirrors.sh              # copy skills/<name>/ into each mirror, commit, push
   scripts/sync_mirrors.sh skills-doctor   # or just one skill
   ```
   The sync only touches each mirror's `skills/<name>/` folder. Mirror READMEs, licenses and demo GIFs are left alone. It refuses to run if `skills/` has uncommitted changes, so mirrors always match a real commit here.

## Shared linter code
`skill_checks.py` exists in both `skills/skills-doctor/scripts/` and `skills/skill-creator-pro/scripts/`, because each skill must work when installed on its own. Edit the skills-doctor copy, then copy it over. CI fails if the two drift apart.

## Adding a skill
Create `skills/<name>/SKILL.md` (kebab-case name matching the folder, with a description that includes "Use when…"), add it to `install.sh` (`ALL` and any packs), `.claude-plugin/marketplace.json` and `scripts/catalog.tsv`, then run the checks above.
