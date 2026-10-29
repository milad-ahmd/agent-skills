---
name: e2e-flow-tester
description: Finds a web app's critical user flows (sign up, log in, checkout, password reset, creating and editing the core object, search, settings) and writes Playwright end-to-end tests that drive a real browser through them, using the project's existing Playwright or Cypress setup when there is one, then de-flakes the tests and adds a CI job. Use when the user asks to add e2e or end-to-end tests, set up Playwright, test a signup, login or checkout flow in the browser, write smoke tests for user journeys, or catch UI regressions before deploys.
---

# E2E Flow Tester

Cover the few journeys that would cost money or users if they broke, with browser tests that pass every time.

This skill writes browser-level tests of whole user flows. Unit and integration tests for untested functions belong to `test-gap-hunter`.

## 1. Map the app

```bash
python3 scripts/find_flows.py              # project root
python3 scripts/find_flows.py apps/web     # one app in a monorepo
python3 scripts/find_flows.py --json       # for tooling; --all lists everything, --min-score N moves the cut-off
```

Exit codes: `0` routes or endpoints found · `2` nothing recognised (read the router or URL config by hand).

It lists routes for Next.js (app and pages router), React Router, Remix, SvelteKit, Nuxt, Vue Router, Angular, Django, Rails and Laravel, plus Express/Fastify/Koa/Hono endpoints. Each page gets tags (`login`, `signup`, `password-reset`, `checkout`, `billing`, `verify`, `invite`, `delete`, `upload`, `search`, `settings`, `create-edit`) and a score from its path, view or component names, forms and input types. It also reports any existing Playwright or Cypress config (baseURL, testDir, webServer), spec folders, package scripts, CI workflows that already run browser tests, and how many files use `data-testid`.

It is a regex scan. It does not resolve mount prefixes (`app.use('/api', router)`, Django `include()`, nested relative routes), and a page whose form lives in a deeply nested component can score low. Confirm paths in the running app.

## 2. Pick the flows with the user

Rank by business value × breakage risk: money (checkout, billing), getting in (signup, login, reset, invite), the core object (create, edit, delete the thing the product is about), then search and settings. Raise anything recently changed (`git log --since=60.days --name-only`) or with a history of incidents.

Show the top 5–8 as a table (flow, steps, start URL, why it matters) and agree on 3–5 before writing anything. Ask what the base URL is, how test users are created, and which external services (payments, email, SMS) need stubbing or test mode.

## 3. Set up only what is missing

If Playwright is already there, reuse its config, folders, fixtures and naming, and add an auth `setup` project only if there is none. If the project uses Cypress, write Cypress tests (see the Cypress section of the reference) rather than adding a second runner. Rails apps with Capybara system tests and Python apps with pytest-playwright keep their tool too.

With no e2e setup at all, propose `@playwright/test` pinned to an exact version, `npx playwright install --with-deps chromium`, a config whose `baseURL` and `webServer` point at the dev server, and an `e2e/` folder. Ask before installing anything.

[references/playwright-patterns.md](references/playwright-patterns.md) has the config, auth setup project, flow tests, CI job and de-flake checklist.

## 4. Write the tests

Locators come from what the user sees: `getByRole`, `getByLabel`, `getByPlaceholder` and `getByText` first, `getByTestId` when nothing accessible fits. No CSS chains, `nth-child`, XPath or generated class names. If an element has no accessible name or stable hook, propose a label or a `data-testid` and add it only with approval.

Assertions are web-first (`await expect(page).toHaveURL(...)`, `toBeVisible`, `toHaveText`, `toHaveCount`), which retry until they pass or time out. Never use `waitForTimeout` or other fixed sleeps. Assert the outcome the user cares about, such as the order confirmation or the record still in the list after a reload, not only that a button was clicked.

Each test creates its own data through the API or a seed script, with unique values per run (`e2e+<worker>-<timestamp>@example.test`), and deletes it afterwards. No test relies on another test's leftovers or on run order.

Log in once in a setup project and reuse its `storageState`. Keep one test that goes through the real login form, and reset the storage state in logged-out tests.

Write one flow per test, named as behaviour (`a returning customer can pay with a saved card`), and cover the main failure path of each flow: wrong password, declined card, a validation error. Plain tests with small helpers are enough. Add page objects only if the project already uses them.

## 5. Run and de-flake

Run against the local or staging stack and fix failures. When a failure is a real bug, report it and leave the test failing. Then repeat each new file:

```bash
npx playwright test e2e/checkout.spec.ts --repeat-each=10 --retries=0
```

Anything that fails once in ten is flaky. Fix it with the checklist in the reference (trace viewer, web-first assertions, waiting for the right response, unique data). Don't add retries or longer timeouts to hide it.

## 6. Add to CI

If no workflow runs browser tests yet, propose a minimal GitHub Actions job: install with the project's package manager, `npx playwright install --with-deps` for the browsers the config uses, run the tests with test credentials from repository secrets, and upload the HTML report on failure. Pin the Playwright version in the lockfile and pin actions (to commit SHAs if the repo does). If CI exists, add a job or step in the same style. Show the YAML and wait for approval.

Finish with: the flows covered, the test files, the `--repeat-each` result, any `data-testid` added, bugs found, and the flows left for later.

## Rules

- Never run tests against production or with real payment, email or SMS providers. Use local or staging environments, test-mode keys, mail catchers and test accounts.
- Ask before installing packages, adding browsers, or changing app code (including `data-testid`).
- Never commit credentials or `storageState` files. Read test users from environment variables or CI secrets.
- Don't commit, push or edit CI without the user's approval.
