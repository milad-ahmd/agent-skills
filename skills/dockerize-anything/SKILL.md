---
name: dockerize-anything
description: Generates a production-ready, secure, small Dockerfile, .dockerignore and docker-compose.yml for any project by detecting its stack, then builds and verifies the image. A read-only script first reads the stack from the project's files (runtime version, lockfile and install command, start command, port, env var names, monorepo layout) and reviews an existing Dockerfile against Docker's documented practices for a smaller, safer image. Use when the user asks to dockerize, containerize, add Docker or compose, or reduce Docker image size.
---

# Dockerize Anything

Production-grade containers, not "FROM node, COPY . ., npm start".

## 1. Detect: run the script, don't guess

```bash
python3 scripts/detect_stack.py [PATH]          # text report
python3 scripts/detect_stack.py [PATH] --json   # same facts as JSON
```

It only reads files: no network, no installs, and nothing from the project is run. Exit codes: `0` report written (lint findings included) · `2` PATH is not a directory, or there is no app manifest and no Dockerfile.

For each app directory (one with `package.json`, `pyproject.toml`, `requirements.txt`, `Pipfile`, `setup.py`, `manage.py` or `go.mod`) it reports the following.

| Fact | Where it comes from |
|---|---|
| runtime | versions exactly as written, with the file each came from (`.nvmrc`, `.node-version`, `engines`, `.python-version`, `requires-python`, Pipfile, go.mod `go`/`toolchain`, `.tool-versions`) |
| packages | the manager from the lockfile and the install that honours it: `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable` (Yarn 1: `--frozen-lockfile`), `bun install --frozen-lockfile`, `uv sync --locked`, `poetry install --no-root --only main`, `pipenv sync`, `go mod download` |
| commands | Procfile `web` and package.json scripts (high confidence); FastAPI/Flask app objects, Django `wsgi.py`, `main` (medium); pyproject console scripts (low, often CLIs) |
| port | the listen call or PORT default with `file:line`, or `reads $PORT, no default found` |
| env vars | names only, from `process.env`, `import.meta.env`, `os.environ`, `os.getenv`, `os.Getenv` and `.env.example`-style templates |
| services | Postgres, MySQL, Redis, MongoDB, RabbitMQ or Kafka from the declared dependencies |

Ranges such as `22.x || 24.x` are left for you to resolve. If nothing is pinned, ask, or pick the current LTS and say so. When the port is only `$PORT`, the image has to set `PORT`. Real `.env` files are listed by path and never opened. A pydantic `BaseSettings` class comes back as a hint, so read its fields for the env names.

Repo-wide it reports monorepo signals (several app dirs, workspaces, `go.work`, uv workspaces), compose files, and a lint of every existing Dockerfile. See section 2.

In a monorepo, ask which app to containerize before writing anything, unless the user already said.

What it cannot see: values computed at runtime, env names built from strings, libraries that read env themselves (dj-database-url reads `DATABASE_URL`, for example), unknown frameworks, and the real build context. The context is inferred from COPY sources, and compose files are not parsed. Code is read with regular expressions, not parsers. Test files, `node_modules`, `vendor`, virtualenvs and build output are skipped. Check medium and low items in the code before you rely on them.

## 2. An existing Dockerfile: improve it, don't replace it

Each finding gives a line, a confidence and the Docker docs URL it rests on (all in `references/sources.md`):

| Rule | What it means |
|---|---|
| `base-image-unpinned` | `FROM` with no tag (the builder uses `latest`) or with `:latest` |
| `apt-update-alone` | `apt-get update` in its own `RUN`, which leaves a stale cached index |
| `apt-lists-not-removed` | `apt-get install` in a shipped stage without `rm -rf /var/lib/apt/lists/*` |
| `add-local-file` | `ADD` of local files where `COPY` fits (URLs, Git and tar archives are fine) |
| `no-user` | no `USER` in the final stage, or a switch back to root. Medium: the base image may set a user, so check it |
| `copy-all-before-install` | `COPY . .` before the first dependency install, so every code change re-runs the install |
| `shell-form-cmd` / `-entrypoint` | shell form does not pass SIGTERM to the app |
| `no-dockerignore`, `dockerignore-misses-*` | `.git`, a real `.env` file or `node_modules` would be sent to the builder |

The report names the build context it assumed. When it had to guess, the `.dockerignore` findings drop to medium. Fix the high-confidence findings and confirm each medium one in the file first. Keep what the author did on purpose, such as cache mounts, build args and the stage names CI targets.

## 3. Dockerfile rules (new or improved)

Build a multi-stage chain (`deps`, then `build`, then `runtime`) so only runtime artifacts reach the final stage. Base it on a pinned, slim image that matches the detected runtime: `node:<ver>-alpine` or `-slim`, `python:<ver>-slim`, or `gcr.io/distroless/static` for Go and Rust static binaries.

For layer caching, copy the lockfiles first, run the install command from the report, then copy the source. Install production dependencies only (`npm ci --omit=dev`, `pip install --no-cache-dir`, `CGO_ENABLED=0`).

The final stage runs as a non-root user, has `EXPOSE <port>` from the report, a `HEALTHCHECK` when there's an HTTP endpoint, and an exec-form `CMD`. Keep secrets out of the image; pass them as runtime env or BuildKit secrets.

## 4. Also generate

- `.dockerignore`: `.git`, `node_modules`, `.venv`, build output, `.env*`, logs, tests, and docs if they aren't needed. Patterns are anchored at the context root, so use `**/node_modules` in a monorepo.
- `docker-compose.yml`: the app plus the detected services, with named volumes, `healthcheck`, `depends_on: condition: service_healthy` and `env_file: .env`.
- `.env.example` with every env var name from the report and placeholder values only, never values copied from a real `.env`.

## 5. Verify (if Docker is available)

```bash
docker build -t <app>:local .
docker images <app>:local --format '{{.Size}}'
docker compose up -d && docker compose ps && docker compose logs --tail=50 app
```

Fix any failures. Re-run `python3 scripts/detect_stack.py` and make sure the new Dockerfile has no high-confidence findings. Then report the final image size and how to run it. Run `docker compose down` when you're done.
