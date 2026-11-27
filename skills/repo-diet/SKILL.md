---
name: repo-diet
description: Finds why a git repository is slow or heavy to clone and plans the diet. A read-only script compares .git with the checkout, lists the biggest blobs ever committed on any branch with the commit and date that added each and whether the file was since deleted, sums how much history is files that no longer exist, and flags junk committed now (.DS_Store, node_modules, virtualenvs, .pyc, dist/build/target output, logs, archives, vendored code without a lockfile) with a .gitignore check, plus large binaries that belong in Git LFS. It prints a headline like "48 MB of history; 71% is files you already deleted", a floppy-disk and clone-time comparison, JSON and an SVG card. Fixes the cheap stuff in a normal commit and only suggests git filter-repo commands, with their cost spelled out. Use when the user asks why the repo or .git folder is so big, why git clone is slow, to find large files in git history, to shrink or slim down a repository, to purge a file committed by mistake, or whether to move files to Git LFS.
---

# Repo Diet

A repo is heavy for two different reasons: junk that is committed now, and files that were deleted long ago but still ride along in every clone. The first is a normal commit. The second means rewriting history, which costs every collaborator something. Fix them in that order.

## 1. Weigh the repo

```bash
python3 scripts/repo_diet.py                  # repo in the current directory
python3 scripts/repo_diet.py -C path/to/repo --top 15
python3 scripts/repo_diet.py --svg diet.svg   # also write a 1200x630 card
python3 scripts/repo_diet.py --svg diet.svg --no-credit   # same card without the small "made with agent-skills" line
python3 scripts/repo_diet.py --json           # every number, for follow-up questions
python3 scripts/repo_diet.py --show-authors   # who added each big blob (hidden by default)
```

The script only reads. It never rewrites history, never runs filter-repo and never pushes. It works on a bare or mirror clone too, minus the .gitignore and LFS checks.

History means every object reachable from any ref (`git rev-list --objects --all`), sized by what it takes in the packs (`%(objectsize:disk)`), which is roughly what a clone downloads. A blob is `deleted` when its path is gone from HEAD and `old version` when the path still exists with other content. Paths under `fixtures/` or `testdata/` are not called junk.

Exit codes: `0` lean · `1` findings · `2` not a git repository · `3` the SVG could not be written.

## 2. Explain what you found

Lead with the headline as printed and the card path, if any. Then give the top five blobs with their status and the commit that added them, how much of history is deleted files, each junk category with its size and whether `.gitignore` covers it, and the LFS candidates. Use only the script's numbers. A 13 MB history with 7% deleted is lean, so say so and stop there.

## 3. Fix the cheap stuff first (no history rewrite)

For junk committed now, in one normal commit on a branch:

1. Add the missing lines the script printed (`node_modules/`, `.DS_Store`, `*.pyc`, `dist/` ...) to `.gitignore`.
2. Untrack what is now ignored, keeping the files on disk:
   ```bash
   git ls-files -z -c -i --exclude-standard | xargs -0 git rm --cached --
   git rm -r --cached -- web/node_modules      # or one root at a time
   ```
3. Check `git status` and confirm the list with the user before committing. A `dist/` or `build/` that is deployed from the repo (GitHub Pages, a published package) may be there on purpose: ask.

For LFS candidates: `git lfs track "*.psd"` and commit `.gitattributes`. That only affects new commits; `git lfs migrate import --no-rewrite` moves existing files in a new commit, and `git lfs migrate import --include="*.psd" --everything` rewrites history like step 4.

This makes the next commit lighter. It does not shrink the clone, because the old blobs are still in history.

## 4. History bloat: plan it, then wait for an explicit OK

Show the `git filter-repo` commands the script suggested, for example:

```bash
git filter-repo --invert-paths --path assets/video.mp4 --path old/dump.sql
git filter-repo --strip-blobs-bigger-than 10M   # cut chosen above the largest file in HEAD
```

Then spell out the cost before anyone runs them. Every commit hash from the first rewritten commit onward changes, and the tags with them, so every branch and tag needs a force push. Every collaborator has to delete their clone and re-clone; an old clone's next push brings the history back. Open pull requests point at old commits and break, so merge or close them first. The hosting service may keep the old objects alive through refs it locks (`refs/pull/*`) until its own cleanup runs.

Only with the user's explicit OK, and with git-filter-repo installed (see its INSTALL.md):

1. Make a backup with `git clone --mirror <url> backup.git`.
2. Work in a fresh clone (`git clone --no-local` for a local path); filter-repo refuses anything else. Do not add `--force` to get around that.
3. If useful, `git filter-repo --analyze` writes reports to `.git/filter-repo/analysis/`.
4. Run the agreed command, then run `repo_diet.py` again and report before and after.
5. filter-repo removes `origin` on purpose. Pushing is a separate yes: `git remote add origin <url>` then `git push --force --branches --tags --prune origin`.

Sources for every figure and flag are in `references/sources.md`.

## Do not

- Run `git filter-repo`, `git lfs migrate import` without `--no-rewrite`, or any force push without the user's explicit OK for that step.
- Use `--force` on filter-repo, or rewrite a repo that has no backup.
- Commit the `.gitignore` and `git rm --cached` changes without showing the file list.
- Name who committed a big file unless the user asks (`--show-authors`).
- Hunt for leaked secrets here. Passwords and keys in history are a security job; a diet only weighs files.
- Treat `node_modules` size on disk as repo weight. If it is not committed, it is a dependency question, not a git one.
