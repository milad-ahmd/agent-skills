# Sources

Every Docker rule and every file-format or install-command detail that `scripts/detect_stack.py` and `SKILL.md` rely on. Add a row here before adding a rule or a format to either. Anchors were checked against the `id=` attributes of the live pages in November 2026.

## Dockerfile lint rules (docs.docker.com)

| Rule | What the docs say | Source |
|---|---|---|
| `base-image-unpinned` (no tag) | "If you omit either of them, the builder assumes a `latest` tag by default." | https://docs.docker.com/reference/dockerfile/#from |
| `base-image-unpinned` (`:latest`) | Image tags are mutable: a publisher can move a tag to a new image, so builds are not guaranteed to get the same image. Pinning a version or digest fixes that. | https://docs.docker.com/build/building/best-practices/#pin-base-image-versions |
| `apt-update-alone` | Always combine `apt-get update` with `apt-get install` in the same `RUN`; `update` alone causes caching issues. | https://docs.docker.com/build/building/best-practices/#apt-get |
| `apt-lists-not-removed` | Cleaning up the apt cache by removing `/var/lib/apt/lists` reduces the image size. The same page says official Debian and Ubuntu images run `apt-get clean` automatically, so the rule checks only for the lists. | https://docs.docker.com/build/building/best-practices/#apt-get |
| `add-local-file` | Use `COPY` for files from the build context. `ADD` is for remote HTTPS and Git sources, where it has checksum support. Local tar archives are exempt because `ADD` extracts them (Dockerfile reference, ADD). | https://docs.docker.com/build/building/best-practices/#add-or-copy, https://docs.docker.com/reference/dockerfile/#add |
| `no-user` | "If a service can run without privileges, use `USER` to change to a non-root user." The docs do not say every base image runs as root, so the finding is medium confidence and asks you to check the base. | https://docs.docker.com/build/building/best-practices/#user |
| `copy-all-before-install` | "Order your layers": copy the package manifest and lockfile, install, then `COPY . .`, so code edits don't invalidate the install layer. | https://docs.docker.com/build/cache/optimize/#order-your-layers |
| `shell-form-cmd` / `shell-form-entrypoint` | Build check JSONArgsRecommended: in shell form the executable runs as a child of a shell, which doesn't pass signals. Exec form makes it PID 1. | https://docs.docker.com/reference/build-checks/json-args-recommended/ |
| `no-dockerignore` | A `.dockerignore` at the root of the context excludes files from the context. A `<Dockerfile>.dockerignore` next to the Dockerfile takes precedence. | https://docs.docker.com/build/concepts/context/#dockerignore-files |
| `dockerignore-misses-git`, `-node_modules` | Docker lists `.git` (version-control history) and vendor directories such as `node_modules` as things to put in `.dockerignore`. | https://docs.docker.com/build-cloud/optimization/#dockerignore-files |
| `dockerignore-misses-env` | Secrets must not persist in the final image ("Build arguments and environment variables are inappropriate for passing secrets"). No Docker page names `.env` files. The rule only flags a real `.env*` file that exists in the context and is not ignored; templates such as `.env.example` are exempt. | https://docs.docker.com/build/building/secrets/ |

## .dockerignore matching (docs.docker.com/build/concepts/context/#dockerignore-files)

- Patterns use Go `filepath.Match` rules after `filepath.Clean`. Leading and trailing slashes are dropped. `**` matches any number of directories, including none. A `#` in column 1 starts a comment, and the pattern `.` is ignored.
- Patterns are relative to the context root: `*.md` excludes only top-level markdown, and `node_modules` does not match `web/node_modules`.
- `!` makes an exception, and the last matching line decides. A pattern that matches a directory excludes everything under it (moby/patternmatcher `MatchesOrParentMatches`).

## Build context

Docker does not record the context in the Dockerfile. The script picks the nearest directory, from the Dockerfile's own directory up to the repo root, where every local `COPY`/`ADD` source and every `RUN --mount=type=bind,source=` exists. Compose `build.context` is not read. This is a heuristic, so the report prints the reason it used.

## Version files

| File | Format used | Source |
|---|---|---|
| `.nvmrc`, `.node-version`, `.python-version` | first non-comment line, printed as is | https://github.com/nvm-sh/nvm#nvmrc, https://github.com/shadowspawn/node-version-usage (`.node-version`, a convention shared by several version managers, with no single spec), https://docs.astral.sh/uv/concepts/python-versions/#python-version-files |
| `package.json` `engines.node`, `packageManager` | raw string | https://docs.npmjs.com/cli/configuring-npm/package-json#engines, https://nodejs.org/api/corepack.html |
| `pyproject.toml` `[project] requires-python` | raw specifier | https://packaging.python.org/en/latest/specifications/pyproject-toml/#requires-python |
| `[tool.poetry.dependencies] python` | raw constraint | https://python-poetry.org/docs/pyproject/#dependencies-and-dependency-groups |
| `Pipfile` `[requires] python_version` | raw string | https://pipenv.pypa.io/en/latest/specifiers.html |
| `go.mod` `go`, `toolchain`; `// indirect` | directive value; indirect requirements are not counted as services | https://go.dev/ref/mod#go-mod-file-go, https://go.dev/ref/mod#go-mod-file-toolchain, https://go.dev/ref/mod#go-mod-file-require |
| `.tool-versions` | `<tool> <version>` per line; tool names `nodejs`/`node`, `python`, `golang`/`go` (asdf plugin names and mise aliases) | https://asdf-vm.com/manage/configuration.html#tool-versions |

`pyproject.toml` and `Pipfile` are read with a small TOML-subset reader (tables, strings, string arrays, booleans) so Python 3.9 and 3.11 give the same output. Multi-line inline tables are not supported.

## Install commands that honour the lockfile

| Lockfile | Command | Source |
|---|---|---|
| `package-lock.json`, `npm-shrinkwrap.json` | `npm ci` (needs an existing lockfile and exits instead of updating it; a shrinkwrap file has the same format as `package-lock.json`) | https://docs.npmjs.com/cli/commands/npm-ci, https://docs.npmjs.com/cli/configuring-npm/npm-shrinkwrap-json |
| `pnpm-lock.yaml` | `pnpm install --frozen-lockfile` ("fails to install if the lockfile is out of sync") | https://pnpm.io/cli/install#--frozen-lockfile |
| `yarn.lock`, Yarn 2+ | `yarn install --immutable` (`--frozen-lockfile` is a deprecated alias) | https://yarnpkg.com/cli/install |
| `yarn.lock`, Yarn 1 | `yarn install --frozen-lockfile` | https://classic.yarnpkg.com/lang/en/docs/cli/install/ |
| `bun.lock`, `bun.lockb` (binary, before Bun 1.2) | `bun install --frozen-lockfile` | https://bun.sh/docs/cli/install |
| `uv.lock` | `uv sync --locked` (the uv Docker guide also uses `--frozen --no-install-project` for an intermediate layer) | https://docs.astral.sh/uv/guides/integration/docker/#installing-a-project |
| `poetry.lock` | `poetry install --no-root --only main` | https://python-poetry.org/docs/cli/#install |
| `Pipfile.lock` | `pipenv sync` ("for deployment scenarios, `pipenv sync` is the recommended command") | https://pipenv.pypa.io/en/latest/commands.html |
| `requirements.txt` | `pip install --no-cache-dir -r requirements.txt`; not a lockfile unless pinned or hashed | https://pip.pypa.io/en/stable/user_guide/#requirements-files |
| `go.sum` | `go mod download` | https://go.dev/ref/mod#go-mod-download |

Telling Yarn 1 from Yarn 2+: the `packageManager` field wins when present (`yarn@1.x` is classic). Otherwise the lockfile is checked: a Berry lockfile has a `__metadata:` entry, and a classic one starts with `# yarn lockfile v1`. That rule comes from the lockfiles each version writes. Yarn does not document it as a format guarantee.

## Start commands

| Signal | Source |
|---|---|
| Procfile `<process type>: <command>`; `web` is the process that receives HTTP traffic | https://devcenter.heroku.com/articles/procfile |
| `[tool.fastapi] entrypoint = "module:app"` is what `fastapi run` imports | https://fastapi.tiangolo.com/fastapi-cli/#configure-the-app-entrypoint-in-pyproject-toml |
| `[project.scripts]` console scripts | https://packaging.python.org/en/latest/specifications/pyproject-toml/#entry-points |

The FastAPI docs do not state a default port for `fastapi run`, so the script reports no port for it. Read the compose file or ask.
