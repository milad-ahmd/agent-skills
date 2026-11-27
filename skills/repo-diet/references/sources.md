# Sources

Every comparison size and every git or git-filter-repo behaviour `scripts/repo_diet.py` and `SKILL.md` rely on. Add a row here before adding a claim to either.

## Reference sizes

The same figures as `skills/dependency-weigh-in/references/sources.md`, repeated so this skill works when installed on its own.

| Reference | Bytes in the script | Source |
|---|---|---|
| 3.5-inch HD floppy disk | 1,474,560 | The "1.44 MB" format: 80 tracks x 2 sides x 18 sectors x 512 bytes = 1,440 KiB (Wikipedia, "Floppy disk") |
| Shareware Doom | 4,196,020 | Doom Wiki, "DOOM1.WAD": size of the v1.9 shareware IWAD |
| 80-minute CD-ROM | 737,280,000 | 75 sectors per second x 4,800 seconds x 2,048 user bytes (Mode 1, ECMA-130) |

Clone time is plain arithmetic: history bytes x 8 / 100,000,000 bits per second. The 100 Mbit/s link is a stated assumption, not a measurement, and the time ignores server-side packing and checkout.

## git (git-scm.com/docs)

- `git count-objects -v`: `size`, `size-pack` and `size-garbage` are in KiB unless `-H` is given.
- `git rev-list --objects --all`: every object reachable from any ref, blobs and trees with the path they were first seen at.
- `git cat-file --batch-check`: atoms `%(objecttype)`, `%(objectname)`, `%(objectsize)`, `%(objectsize:disk)`, `%(rest)`. CAVEATS: on-disk sizes are accurate, but which object is stored as a delta of which is arbitrary and can change on repack, so per-object disk sizes of delta-compressed files should not be over-read.
- `git log --find-object=<id>`: commits that change the number of occurrences of that object; with `--all --reverse` the first line is the commit that introduced it.
- `git ls-tree -r -l -z HEAD`: mode, type, object, size, then a tab and the path.
- `git check-ignore --no-index -v -z --stdin`: checks ignore rules for paths that are already tracked; verbose output gives the source file and pattern, and a source that is an absolute path is `core.excludesFile` (a personal global ignore, not the repo's).
- `git check-attr --cached -z --stdin filter`: reads `.gitattributes` from the index; output is `<path> NUL <attribute> NUL <info> NUL`.
- `git ls-files -c -i --exclude-standard`: tracked files that match an ignore pattern (`-i` needs `-c` or `-o` and an exclude option).
- `git push --all` and `--branches` are the same option.

## git-filter-repo (github.com/newren/git-filter-repo, Documentation/git-filter-repo.txt)

- `--path <path>` selects exact files or directories; `--invert-paths` keeps everything except them. Several `--path` options make a union.
- `--strip-blobs-bigger-than <size>`: K, M and G suffixes. It strips every blob over the size, including ones still in HEAD, which is why the script picks a cut above the largest file in HEAD.
- `--analyze` writes reports under `.git/filter-repo/analysis/` and changes nothing.
- It refuses to run outside a fresh clone; `--force` skips that check and the docs warn against using it casually. For a clone of a local repository pass `--no-local`; `--bare` or `--mirror` clones are fine.
- "All commit IDs (starting with the first one where changes are made) are modified"; old and new histories are incompatible, so pushing looks like a rebase of every branch and tag.
- It removes the `origin` remote on purpose so nobody pushes by accident, and moves `refs/remotes/origin/*` to `refs/heads/*`. To push back deliberately the docs give `git push --force --branches --tags --prune` after re-adding origin.
- Other clones: "make everyone delete their existing clones and reclone".
- Forges lock some refs (`refs/pull/*`, `refs/merge-requests/*`); clearing those and triggering a server-side garbage collection follows each forge's own directions.

## Git LFS (github.com/git-lfs/git-lfs, docs/man)

- `git lfs track "<pattern>"` writes the pattern to `.gitattributes`; the script detects LFS with the `filter` attribute set to `lfs`.
- `git lfs migrate import --include="<pattern>" --everything` rewrites history (new commit SHAs, force push needed). `--everything` covers all local and remote refs but only updates local refs.
- `git lfs migrate import --no-rewrite` moves files to LFS in a new commit on the current branch without rewriting history.
