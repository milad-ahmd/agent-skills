# Sources

Every comparison size and every suggested alternative in `scripts/weigh_in.py`, with where it comes from. Add a row here before adding one to the script.

## Reference sizes

| Reference | Bytes in the script | Source |
|---|---|---|
| Super Mario Bros. (NES) cartridge | 40,960 | Wikipedia, "Super Mario Bros.": 256 kilobits (32 KiB) of program code and data plus 64 kilobits (8 KiB) of graphics |
| Apollo Guidance Computer memory | 77,824 | Wikipedia, "Apollo Guidance Computer": Block II had 2,048 words of erasable core memory and 36,864 words of core rope memory, 16-bit words (15 data bits plus parity). (2,048 + 36,864) x 2 bytes |
| 3.5-inch HD floppy disk | 1,474,560 | The "1.44 MB" format: 80 tracks x 2 sides x 18 sectors x 512 bytes = 1,440 KiB (Wikipedia, "Floppy disk") |
| Shareware Doom | 4,196,020 | Doom Wiki, "DOOM1.WAD": size of the v1.9 shareware IWAD (the v1.8 file is the same size) |
| 80-minute CD-ROM | 737,280,000 | 75 sectors per second x 4,800 seconds = 360,000 sectors x 2,048 user bytes (Mode 1, ECMA-130). Sold as "700 MB" |
| Single-layer DVD | 4,700,000,000 | The 4.7 GB printed on the disc (ECMA-267). A DVD-R holds slightly more |

The weight classes (strawweight to black hole) are a joke scale, not a measurement, and need no source.

## Alternatives

| Package | Suggested instead | Source |
|---|---|---|
| moment | dayjs, date-fns or Luxon | momentjs.com/docs, "Project Status": "Moment.js is a legacy project in maintenance mode". The 2020 recommendations listed Luxon, Day.js, date-fns and js-Joda |
| request | global `fetch` (Node 18+) or undici | github.com/request/request/issues/3142, the deprecation notice npm prints on install. Node docs: `fetch` is enabled by default from Node 18 |
| aws-sdk (v2) | `@aws-sdk/client-*` (v3) | AWS Developer Tools Blog, "Announcing end-of-support for AWS SDK for JavaScript v2": end of support on 8 September 2025, migrate to v3 |
| node-sass | sass (Dart Sass) | npmjs.com/package/node-sass ("no longer supported, use sass or sass-embedded") and sass-lang.com/blog/node-sass-is-end-of-life |
| tslint | eslint with typescript-eslint | typescript-eslint.io, "What about TSLint?": TSLint was deprecated in 2019 in favour of typescript-eslint |
| lodash | lodash-es, per-method imports or native methods | The saving is in browser bundles (tree shaking), not on disk |
| nvidia-* (Python, with torch) | CPU-only torch wheels | pytorch.org/get-started/locally: choose "CPU" as the compute platform, which installs from `https://download.pytorch.org/whl/cpu`. Only when the machine has no GPU |
| opencv-python | opencv-python-headless | opencv-python README (github.com/opencv/opencv-python): headless packages are for server environments such as Docker and have no GUI dependencies; keep the full package if the code calls `cv2.imshow` |

## Package manager facts the script relies on

- pnpm layout: packages live at `node_modules/.pnpm/<name>@<version>/node_modules/<name>`, files are hard links into the content-addressable store, dependencies and top-level entries are symlinks (pnpm.io, "Symlinked node_modules structure"). With the default `packageImportMethod: auto`, pnpm tries hard links first on Linux and Windows but copy-on-write clones first on macOS (pnpm.io, "Settings: node_modules"); clones share disk blocks without showing up as hard links, so the script cannot see that sharing.
- package-lock.json v2 and v3: the `packages` map is keyed by install path (`node_modules/a/node_modules/b`), the root is `""`, and `dev: true` marks packages that are only in the devDependencies tree (docs.npmjs.com, "package-lock.json").
- Python: `RECORD` in each `*.dist-info` is a CSV of path, hash, size; paths are relative to the directory holding the `.dist-info` (site-packages) and may point outside it, for example to `bin/`. Hash and size may be empty (packaging.python.org, "Recording installed projects").
- Go: module source is extracted to `$GOMODCACHE/<module>@<version>`; uppercase letters in paths and versions are written as `!` plus the lowercase letter; `go.sum` lines ending in `/go.mod` hash only the go.mod file (go.dev/ref/mod, "Module cache" and "go.sum files").
