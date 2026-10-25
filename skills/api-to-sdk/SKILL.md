---
name: api-to-sdk
description: Generates a clean, typed, idiomatic client SDK (TypeScript, Python, Go, or Dart) from an OpenAPI/Swagger spec, Postman collection, or existing backend routes, with auth, errors, retries, pagination, docs, and tests. Use when the user asks to generate an API client, SDK, or typed fetch layer, or to wrap a REST API.
---

# API to SDK

A client SDK that developers enjoy using, not 10,000 lines of generated noise.

## 1. Find the source of truth

In priority order: an OpenAPI 3.x/Swagger file (`openapi.yaml`, `swagger.json`, a `/docs-json` endpoint) → a Postman collection → the backend route definitions in the repo (Express, NestJS, FastAPI, Django, Go handlers) → the docs the user provides.
Validate the spec and list any problems you find (missing `operationId`s, untyped responses).

## 2. Choose the approach

- **A generator exists and fits** (`openapi-typescript` + `openapi-fetch`, `orval`, `openapi-python-client`, `oapi-codegen`): use it, then add a thin hand-written ergonomic layer on top.
- **Small API (≤ 30 endpoints) or no spec**: hand-write a small client.
- Match the language, HTTP library and style already in the project (`fetch`/`axios`/`ky`, `httpx`/`requests`, `dio`).

## 3. SDK design

```ts
const client = new AcmeClient({ apiKey, baseUrl, timeoutMs: 10_000, maxRetries: 2 });
const user = await client.users.get("u_123");            // resource-grouped methods
for await (const order of client.orders.list({ status: "paid" })) { ... } // auto-pagination
```

- **Types** for every request and response model, with enums as unions or literal types, and dates documented.
- **Auth**: API key, bearer token or OAuth token provider. Never log the credentials.
- **Errors**: a typed error hierarchy (`ApiError` carrying `status`, `code`, `requestId`, `body`, with `NotFoundError`, `RateLimitError`...).
- **Retries** with exponential backoff and jitter, but only for idempotent methods and 429/5xx responses. Respect `Retry-After`.
- Timeouts, cancellation (`AbortSignal`, context) and a configurable base URL and headers.
- Pagination helpers (cursor or page) exposed as async iterators.
- Zero or minimal runtime dependencies.

## 4. Deliver

- The SDK source, a README with install, auth, examples for 3–5 core operations, error handling and pagination, plus JSDoc or docstrings taken from the spec descriptions.
- **Tests** with a mocked HTTP layer (`msw`, `respx`, `httptest`) covering success, error mapping, retries and pagination.
- If the spec changes often, a regenerate script (`npm run generate:sdk`).

## Rules

- Don't invent endpoints or fields that aren't in the spec or code. Mark any gaps.
- Keep generated code separate from the hand-written layer (for example `src/generated/`) so it can be regenerated safely.
- Run the type-check and tests before reporting that the work is done.
