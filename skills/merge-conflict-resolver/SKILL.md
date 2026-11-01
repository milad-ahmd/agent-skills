---
name: merge-conflict-resolver
description: Resolves git merge, rebase, cherry-pick, revert and stash pop conflicts by working out what each side meant and keeping both intents, instead of picking ours or theirs. A read-only script reports the operation in progress and what "ours" and "theirs" mean for it (they swap during a rebase), each conflicted file and its kind (content, add/add, modify/delete, rename, binary), every hunk's ours, base and theirs text, and the commits on each side that touched the file. Lockfiles such as package-lock.json, yarn.lock or Cargo.lock are regenerated with the package manager, never hand-merged, and a check mode confirms no conflict markers are left before git add and continue. Use when the user asks to resolve or fix merge conflicts, says a rebase or cherry-pick stopped with CONFLICT, sees <<<<<<< HEAD markers in a file, asks whether to keep ours or theirs, has a conflicted package-lock.json, or wants help finishing a stuck merge or rebase.
---

# Merge Conflict Resolver

A conflict means two changes touched the same lines, usually for different reasons. A good resolution keeps both reasons unless one makes the other obsolete. Picking a whole side without reading it loses someone's work.

## 1. See what is in conflict

```bash
python3 scripts/conflicts.py              # operation, sides, files, hunks, commits per side
python3 scripts/conflicts.py src/api.ts   # only these paths
python3 scripts/conflicts.py --json       # machine-readable, hunk text never cut
```

The script only reads. Start with its operation line, for example `rebasing feature onto main, commit 3/7`. During a rebase, ours is the branch you are rebasing onto and theirs is your own commit being replayed. During a revert, theirs is the code as it was before the reverted commit. A conflicted `git stash pop` leaves no state file, so the script recognises it by the `Updated upstream` and `Stashed changes` marker labels.

Exit codes: `0` nothing to resolve or clean · `1` conflicts present · `2` not a git repository or a git error.

## 2. Work out what each side meant

For each content hunk, before editing anything, compare ours and theirs against base. Base is what both sides started from, so it shows what each one changed. The script takes base from diff3/zdiff3 markers or rebuilds it from the index. To get it in the file itself next time, set `git config merge.conflictStyle zdiff3` (git 2.35 or later).

Then read the commits listed for each side with `git show <sha>`. The message, the rest of the diff and any linked issue say why the line changed: a bug fix, a rename, a new parameter, a refactor that moved the code. Look past the hunk too, at callers of a renamed function, tests either side added, and the same identifier elsewhere.

Write one line per hunk, such as "ours: X because Y; theirs: Z because W; keep: ...". If you can't say why one side made its change, find out before choosing.

## 3. Combine the intents

| Situation | Resolution |
|---|---|
| Both added entries (imports, routes, enum values, list items) | Keep both, drop exact duplicates, follow the file's ordering |
| One side renamed or moved code, the other changed its body | Apply the body change at the new name or location |
| One side refactored, the other fixed a bug in the old version | Re-apply the fix inside the refactored code |
| Both changed the same value (`timeout = 45` vs `120`) | Choose by intent from the commit messages. If it is still unclear, ask the user |
| modify/delete | Find out why it was deleted. If it moved, port the change to the new place. If the feature was removed, drop the change and say so |
| rename (rename/rename, rename/delete) or add/add | Choose one path and one content, then update references to the other |
| Binary file | Ask which version is right, then `git checkout --ours -- <path>` or `--theirs` |

Remove every marker line and keep the code consistent: one name, one signature, imports that match what is used.

## 4. Lockfiles and generated files

Never merge lockfile hunks by hand. The script flags them and prints the regenerate command.

1. Resolve the manifest first (`package.json`, `Cargo.toml`, `pyproject.toml`, `go.mod`, `Gemfile`) using step 2.
2. Take the lockfile from the long-lived branch: `git checkout --ours -- package-lock.json` when rebasing onto main, `--theirs` when merging main into your branch.
3. Run the printed command, for example `npm install --package-lock-only`, `pnpm install`, `yarn install`, `cargo update --workspace`, `poetry lock`, `uv lock`, `bundle install` or `go mod tidy`. Run it from the directory that holds the manifest. These keep versions that are already locked and resolve only what the manifest changed.
4. Check the diff of the lockfile against the long-lived branch: only the other side's dependency changes should appear.

Generated files (minified bundles, source maps, Jest snapshots, files whose header says generated or DO NOT EDIT) work the same way: resolve the sources, then re-run the generator.

## 5. Verify

```bash
python3 scripts/conflicts.py --check   # exit 1 lists file:line of marker sets left (working tree or staged) and unmerged paths
```

A Markdown or reST `=======` underline is not reported; only a full `<<<<<<<`, `=======`, `>>>>>>>` set counts. Then run the project's build, type check and the tests closest to the changed files. If something fails, fix it according to the intents from step 2.

## 6. Mark resolved and continue

`git add <file>` for each resolved file, `git rm <file>` where removing it was the right call. Then run the continue command the script printed (`git rebase --continue`, `git merge --continue`, `git cherry-pick --continue`, ...). A rebase can stop again on the next commit: go back to step 1. After a stash pop, the stash is kept; suggest `git stash drop` once the user confirms the result. If the user only asked for the files to be resolved, stop after `git add` and let them continue.

## Report

Per file and hunk: what each side meant, what you kept, and which choices were judgement calls the user should check. Then the verification commands and their results.

## Do not

- Run `git checkout --ours/--theirs`, `-X ours/theirs` or `-s ours` on whole files or the whole merge without reading every hunk.
- Delete or comment out either side's code to make the build pass.
- Run `git rebase --skip`. It drops the whole commit being replayed. Do it only if the user agrees the commit is no longer needed.
- `git add` a file that still has markers. Run `--check` first.
- Abort the operation or reset the tree on your own. `git merge --abort`, `git rebase --abort` and `git reset --hard` throw away resolution work, so the user decides.
- Commit, push or force-push unless the user asked for it.
