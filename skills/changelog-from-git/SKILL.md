---
name: changelog-from-git
description: Generates polished, human-readable release notes and CHANGELOG entries from a git commit range, grouping changes by impact and crediting contributors. Use when the user asks for release notes, a changelog, "what changed since vX", or to draft a GitHub release.
---

# Changelog from Git

Release notes for humans, not a dump of commit messages.

## 1. Determine the range

```bash
git describe --tags --abbrev=0                 # latest tag
git tag --sort=-creatordate | head -5
git --no-pager log <from>..<to> --no-merges --pretty='%h|%an|%s'
git --no-pager log <from>..<to> --merges --pretty='%h|%s'   # PR merges (#123)
```

Default range: from the latest tag to `HEAD`. If there are no tags, use the whole history and say so.
When `gh` is available, enrich entries with PR titles, labels and authors:
`gh pr list --state merged --search "merged:>=<date>" --json number,title,author,labels`.

## 2. Classify

| Section | Sources |
|---|---|
| ⚠️ Breaking changes | `!`, `BREAKING CHANGE`, removed or renamed public APIs, config changes |
| ✨ Features | `feat` |
| 🐛 Fixes | `fix` |
| ⚡ Performance | `perf` |
| 📚 Docs / 🧹 Maintenance | `docs`, `refactor`, `chore`, `build`, `ci`, `deps` (collapse these) |

If the commits don't follow Conventional Commits, classify by reading the diff summary, not just the message.

## 3. Write

```markdown
## v1.4.0 — 2026-10-25

**Highlights:** one or two sentences a user cares about.

### ⚠️ Breaking changes
- `config.timeout` is now in milliseconds. **Migration:** multiply existing values by 1000. (#142)
### ✨ Features
- Export reports as CSV (#138, @alice)
### 🐛 Fixes
- Fix crash when the profile picture is missing (#140)

**Full diff:** https://github.com/OWNER/REPO/compare/v1.3.0...v1.4.0
```

## Rules

- Rewrite each entry from the user's point of view ("Fix crash when...", not "fix null check in UserCard").
- Drop noise: merge commits, typo fixes, version bumps, "wip".
- Every breaking change gets migration steps.
- Suggest the next version number using SemVer (major for breaking changes, minor for features, patch for fixes only).
- Prepend the entry to `CHANGELOG.md` (Keep a Changelog style) only if asked. Create a GitHub release with `gh release create` only after the user confirms.
