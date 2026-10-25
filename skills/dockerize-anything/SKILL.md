---
name: dockerize-anything
description: Generates a production-ready, secure, small Dockerfile, .dockerignore and docker-compose.yml for any project by detecting its stack, then builds and verifies the image. Use when the user asks to dockerize, containerize, add Docker or compose, or reduce Docker image size.
---

# Dockerize Anything

Production-grade containers, not "FROM node, COPY . ., npm start".

## 1. Detect

- Language, version (`.nvmrc`, `engines`, `.python-version`, `go.mod`, `rust-toolchain`), and package manager plus lockfile.
- Build command, start command, port, and the env vars the app reads (search for `process.env`, `os.environ`, `os.Getenv`...).
- Services it depends on (Postgres, Redis, Mongo...) from config and dependencies.
- Whether a Dockerfile already exists. If so, **improve it** instead of replacing it.

## 2. Dockerfile rules

- **Multi-stage**: a `deps` → `build` → `runtime` chain. Only runtime artifacts end up in the final stage.
- **Pinned, slim base images**: `node:<ver>-alpine` or `-slim`, `python:<ver>-slim`, and `gcr.io/distroless/static` for Go and Rust static binaries.
- **Layer caching**: copy the lockfiles first, install dependencies, then copy the source.
- Install production dependencies only (`npm ci --omit=dev`, `pip install --no-cache-dir`, `CGO_ENABLED=0`).
- **Non-root user**, `EXPOSE <port>`, `HEALTHCHECK` when there's an HTTP endpoint, and exec-form `CMD`.
- No secrets in the image. Pass them as runtime env or BuildKit secrets.

## 3. Also generate

- `.dockerignore`: `.git`, `node_modules`, `.venv`, build output, `.env*`, logs, tests, and docs if they aren't needed.
- `docker-compose.yml`: the app plus the detected services, with named volumes, `healthcheck`, `depends_on: condition: service_healthy` and `env_file: .env`.
- `.env.example` entries for each variable the app requires.

## 4. Verify (if Docker is available)

```bash
docker build -t <app>:local .
docker images <app>:local --format '{{.Size}}'
docker compose up -d && docker compose ps && docker compose logs --tail=50 app
```

Fix any failures, then report the final image size and how to run it. Run `docker compose down` when you're done.
