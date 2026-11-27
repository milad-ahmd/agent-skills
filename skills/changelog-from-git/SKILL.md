---
name: changelog-from-git
description: Generates polished, human-readable release notes and CHANGELOG entries from a git commit range, grouping changes by impact and crediting contributors when asked. A bundled script parses the Conventional Commits since the latest tag, groups breaking changes, features, fixes and performance work, attaches PR numbers, drops reverted pairs, folds bot version bumps into one line and suggests the next SemVer bump; the agent then turns each entry into plain language people understand. Use when the user asks for release notes, a changelog, "what changed since vX", or to draft a GitHub release.
---

# Changelog from Git

Release notes for humans, not a dump of commit messages. The script collects the facts; you write the notes.

## 1. Run the script

```bash
python3 scripts/release_notes.py                     # latest tag..HEAD, grouped, with a suggested bump
python3 scripts/release_notes.py --from v1.3.0 --to v1.4.0
python3 scripts/release_notes.py --md                # CHANGELOG section skeleton to rewrite
python3 scripts/release_notes.py --json              # every entry, count and the range it used
python3 scripts/release_notes.py --show-authors      # names contributors (counted only by default)
```

It only reads git. The default range starts at the latest tag reachable from `--to` (`git describe --tags --abbrev=0`). When `--to` carries a tag itself, as right after the user pushed one, it starts at the tag before that. With no tags it reads the whole history and the output says so.

Exit codes: `0` report written · `1` no commits in the range (pass an older `--from`) · `2` not a git repository, a bad ref, or a git error.

## 2. How it groups

Subjects are parsed by [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/): `type(scope)!: description`, with the type matched in any case. A commit is breaking when `!` sits right before the colon or a body line starts with the uppercase footer `BREAKING CHANGE: ` or `BREAKING-CHANGE: `. Breaking commits go first whatever their type. Then `feat`, `fix`, `perf` and `docs` get a section each, and everything else lands in Other.

The script also applies some rules of its own that the spec does not cover:

| Rule | What it does |
|---|---|
| PR numbers | from a trailing `(#123)` (squash merges) or from `Merge pull request #123 from ...`, given to the commits that merge brought in |
| Merges | never listed themselves |
| Reverts | a commit and its `Revert "..."` cancel out when both are in the range; a revert of something shipped earlier stays in Other |
| Dependency bumps | authors matching dependabot or renovate, or `chore(deps)`, `build(deps)` and `deps-dev` scopes, become one counted line, unless breaking |
| Contributors | distinct authors after `.mailmap`, bots excluded, counted but not named |
| Next version | major if anything is breaking, minor for any `feat`, else patch |

The script only sees markers. Read Other and Fixes for unmarked breaks too: removed or renamed public APIs, changed defaults, config or environment changes, dropped platform support. Move them to Breaking and raise the bump. The bump is a suggestion. Check it against what actually shipped, and under 1.0.0 ask the user, since many projects bump minor for breaking changes there.

If the output says the subjects are mostly non-conventional, everything is in Other. Group it yourself by reading the commits and, where a subject is vague, the diff (`git show --stat <sha>`). Do the same for Other in a conventional repo: a `refactor` that changes behaviour belongs under Fixes or Breaking.

When `gh` is available, enrich entries with PR titles and labels:
`gh pr list --state merged --search "merged:>=<date>" --json number,title,labels`.

## 3. Write

Start from `--md` and rewrite it:

```markdown
## v1.4.0 - 2026-10-25

**Highlights:** one or two sentences a user cares about.

### Breaking changes
- `config.timeout` is now in milliseconds. **Migration:** multiply existing values by 1000. (#142)
### Features
- Export reports as CSV (#138)
### Fixes
- Fix crash when the profile picture is missing (#140)

**Full diff:** https://github.com/OWNER/REPO/compare/v1.3.0...v1.4.0
```

## Rules

- Rewrite each entry from the user's point of view ("Fix crash when...", not "fix null check in UserCard").
- Drop what users never see. Tests, CI, refactors without visible effect, typo fixes, version bumps and "wip" can go, or be folded into one maintenance line.
- Every breaking change gets migration steps. Read the commit body and diff for them; the footer text alone is rarely enough.
- Credit people by name (`@handle`) only when the user asks; otherwise give the contributor count.
- Prepend the entry to `CHANGELOG.md` (Keep a Changelog style) only if asked. Create a GitHub release with `gh release create` only after the user confirms.
