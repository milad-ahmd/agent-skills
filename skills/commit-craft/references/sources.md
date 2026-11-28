# Sources

Every external rule `scripts/commit_facts.py` and `SKILL.md` rely on. Add a row here before adding a rule to either.

## Conventional Commits 1.0.0 (https://www.conventionalcommits.org/en/v1.0.0/)

The header, breaking-change and footer regexes are copied byte for byte from `skills/changelog-from-git/scripts/release_notes.py` so both skills parse a message the same way.

| Item | Rule | Used for |
|---|---|---|
| 1, 4, 5 | a type, an optional `(scope)`, then `: ` and the description | `SUBJECT_RE`, lint error `conventional-subject` |
| 6 | the body begins one blank line after the description | lint error `blank-line` |
| 8, 9 | footers come one blank line after the body; a token is a word (`-` for spaces, except `BREAKING CHANGE`) followed by `: ` or ` #` | `FOOTER_RE`, lint warning on footer placement |
| 12, 13 | a breaking footer is the uppercase text `BREAKING CHANGE`, a colon, a space and a description; or `!` right before the `:` | lint error `breaking-footer` |
| 14 | types other than `feat` and `fix` MAY be used | why any word matches the header shape |
| 15 | types are case-insensitive, except `BREAKING CHANGE`, which MUST be uppercase | types are lowercased before counting |
| 16 | `BREAKING-CHANGE` is a synonym in footers | accepted by `BREAKING_RE` |

The spec does not fix a list of types. The "common CC types" share uses the 11 in commitlint's `@commitlint/config-conventional` `type-enum` (build, chore, ci, docs, feat, fix, perf, refactor, revert, style, test): https://github.com/conventional-changelog/commitlint/tree/master/%40commitlint/config-conventional. That config's own limits (header and body lines up to 100) are not used here.

## git

- git-commit, DISCUSSION (https://git-scm.com/docs/git-commit): "a single short (no more than 50 characters) line summarizing the change, followed by a blank line". Source of the 50-char soft limit and the blank-line check. Same page, `--cleanup`: `default` is `strip` when the message is edited, else `whitespace`; `strip` removes `#` commentary; `scissors` drops everything from `# ------------------------ >8 ------------------------` down. So `--lint-file` (an edited message) drops comment lines and the scissors tail, and `--lint MESSAGE` (like `-m`) keeps `#` lines.
- git-interpret-trailers (https://git-scm.com/docs/git-interpret-trailers): a trailer block is the last group of `Key: value` lines after a blank line, all trailers or at least 25% with one git-generated or configured key. The script lets git apply this rule through `git log --format=%(trailers:only,unfold)` (git-log PRETTY FORMATS, https://git-scm.com/docs/git-log: "display the trailers of the body as interpreted by git-interpret-trailers"; `unfold` joins folded lines) and counts keys case-insensitively, once per commit.
- git-diff `-w` / `--ignore-all-space` (https://git-scm.com/docs/git-diff): ignores whitespace when comparing lines, so a file missing from `git diff -w --numstat` changed only whitespace. It does not ignore added or removed blank lines; that needs `--ignore-blank-lines`, which the script does not use.
- git, environment variable `GIT_OPTIONAL_LOCKS` (https://git-scm.com/docs/git, also `--no-optional-locks`): set to false, it "will prevent git status from refreshing the index as a side effect", so the script stays read-only.

## Convention, not a spec

- 72 chars (subject hard limit, body wrap): Tim Pope, "A Note About Git Commit Messages", 2008 (https://tbaggery.com/2008/04/19/a-note-about-git-commit-messages.html): about 50 for the summary ("this isn't a hard maximum"), body wrapped "to about 72 characters". Using 72 as the subject's hard limit too is this skill's choice, since a longer subject overflows the same terminal; the script calls it a convention in its messages.
- Style verdict thresholds (50% / 20% / 80%) and the `area:` prefix shape are this script's own heuristics, not from any source.
