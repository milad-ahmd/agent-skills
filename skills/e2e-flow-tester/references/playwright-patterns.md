# Playwright patterns

Copy what fits, then rename to match the project. Everything here uses `@playwright/test`. If the project already has a config, change only what a flow needs.

## Config

```ts
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.BASE_URL ?? 'http://localhost:3000';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: /.*\.setup\.ts/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], storageState: 'playwright/.auth/user.json' },
      dependencies: ['setup'],
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
```

Add `playwright/.auth/`, `test-results/` and `playwright-report/` to `.gitignore`. The storage state file holds a live session cookie.

## Log in once with a setup project

```ts
// e2e/auth.setup.ts
import { test as setup, expect } from '@playwright/test';

const authFile = 'playwright/.auth/user.json';

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill(process.env.E2E_USER_EMAIL!);
  await page.getByLabel('Password').fill(process.env.E2E_USER_PASSWORD!);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await page.context().storageState({ path: authFile });
});
```

If the app has a login API, `await page.request.post('/api/login', { data: { email, password } })` before `storageState` is faster than filling the form. Keep one UI login test so the form itself stays covered.

Tests that must start logged out:

```ts
test.use({ storageState: { cookies: [], origins: [] } });
```

When tests change the user's data (settings, billing), give each worker its own account so parallel runs don't collide. The Playwright docs call this "one account per parallel worker" and key it on `test.info().parallelIndex`.

## A flow test

```ts
// e2e/signup.spec.ts
import { test, expect } from '@playwright/test';

test.describe('sign up', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('a new visitor can create an account and reach the dashboard', async ({ page }, testInfo) => {
    const email = `e2e+${testInfo.parallelIndex}-${Date.now()}@example.test`;

    await page.goto('/signup');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password', { exact: true }).fill(`Pw-${Date.now()}-x9`);
    await page.getByRole('button', { name: /create account/i }).click();

    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole('heading', { name: /welcome/i })).toBeVisible();
  });

  test('shows an error for an email that is already registered', async ({ page }) => {
    await page.goto('/signup');
    await page.getByLabel('Email').fill(process.env.E2E_USER_EMAIL!);
    await page.getByLabel('Password', { exact: true }).fill('Another-pass-1');
    await page.getByRole('button', { name: /create account/i }).click();

    await expect(page.getByRole('alert')).toContainText(/already/i);
    await expect(page).toHaveURL(/\/signup/);
  });
});
```

## Seed through the API, clean up after

```ts
// e2e/projects.spec.ts
import { test, expect } from '@playwright/test';

let projectId: string | undefined;

test.afterEach(async ({ page }) => {
  if (projectId) await page.request.delete(`/api/projects/${projectId}`);
  projectId = undefined;
});

test('a user can rename a project', async ({ page }) => {
  const res = await page.request.post('/api/projects', { data: { name: `e2e-${Date.now()}` } });
  expect(res.ok()).toBeTruthy();
  projectId = (await res.json()).id;

  await page.goto(`/projects/${projectId}/settings`);
  await page.getByLabel('Project name').fill('Renamed by e2e');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('status')).toHaveText(/saved/i);
  await page.reload();
  await expect(page.getByLabel('Project name')).toHaveValue('Renamed by e2e');
});
```

`page.request` shares cookies with the page, so it is logged in as the same user. Only the UI step under test goes through the browser; setup and cleanup go through the API.

## Locators and waiting

| Use | Avoid |
|---|---|
| `getByRole('button', { name: 'Save' })` | `.btn-primary`, `div > button:nth-child(2)` |
| `getByLabel('Email')`, `getByPlaceholder(...)` | `#input-37`, generated class names |
| `getByText('No results')` for static copy | XPath |
| `getByTestId('cart-total')` when nothing accessible fits | `page.waitForTimeout(2000)` |
| `expect(locator).toBeVisible()`, `toHaveText`, `toHaveURL`, `toHaveCount` | `expect(await locator.isVisible()).toBe(true)` |
| `page.waitForResponse('**/api/orders')` before asserting on its result | `networkidle` as a general wait |
| `expect.poll(() => fetchStatus()).toBe('paid')` for async backend state | loops with sleeps |

Scope to a region when text repeats: `page.getByRole('dialog').getByRole('button', { name: 'Delete' })`. `npx playwright codegen <url>` suggests locators. If it falls back to CSS, the element probably needs a label or a `data-testid`.

## Third parties

- Stripe and other payment providers: use test mode keys and the published test card `4242 4242 4242 4242` on the staging or local stack only. Stripe Elements renders in an iframe: `page.frameLocator('iframe[title*="card"]').getByPlaceholder('Card number')`. If the provider's hosted page is out of scope, assert you were redirected to it and stop.
- Email (verification, reset links): read from the dev mail catcher (Mailpit, MailHog, a test inbox API) or a test-only endpoint. Never a real mailbox.
- Anything else external: `page.route('**/api.thirdparty.com/**', route => route.fulfill({ json: {...} }))`.

## De-flake checklist

Run the new tests many times before calling them done:

```bash
npx playwright test e2e/signup.spec.ts --repeat-each=10 --retries=0 --workers=4
```

When one fails only sometimes:

1. Open the trace: `npx playwright show-trace test-results/<test>/trace.zip` (or `--trace on` for the rerun).
2. Replace manual waits and `isVisible()` checks with web-first assertions, which retry until the timeout.
3. Wait for the request the UI depends on (`waitForResponse`) instead of a delay.
4. Make data unique per test and worker. Shared rows, fixed emails and "the first item in the list" collide in parallel runs.
5. Remove ordering assumptions between tests. Each test must pass alone (`npx playwright test -g "name"`).
6. Animations and toasts: assert the end state, not the transition.
7. Clock or timezone dependent UI: `page.clock.setFixedTime(...)` and `use: { timezoneId: 'UTC', locale: 'en-US' }`.

CI retries hide flakiness. A test reported as "flaky" in the HTML report still needs fixing.

## CI job (GitHub Actions)

```yaml
# .github/workflows/e2e.yml
name: e2e
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v7          # pin to a commit SHA, see gh-actions-auditor
      - uses: actions/setup-node@v7
        with:
          node-version: lts/*
          cache: npm
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npx playwright test
        env:
          E2E_USER_EMAIL: ${{ secrets.E2E_USER_EMAIL }}
          E2E_USER_PASSWORD: ${{ secrets.E2E_USER_PASSWORD }}
      - uses: actions/upload-artifact@v7
        if: ${{ !cancelled() }}
        with:
          name: playwright-report
          path: playwright-report/
          retention-days: 14
```

- Pin `@playwright/test` to an exact version in `package.json` and commit the lockfile. `npx playwright install` then downloads the browsers for that version, so local and CI match.
- Use the project's package manager (`pnpm install --frozen-lockfile`, `yarn install --immutable`) and its existing Node setup if a workflow already has one.
- Install only the browsers the config uses. `--with-deps` adds the OS libraries they need.
- The alternative is the official image `mcr.microsoft.com/playwright:v<same version>-noble` as the job `container`. Its tag must match the `@playwright/test` version.
- Test accounts come from repository secrets. The job needs a database or a seeded staging stack: start it in the job (service container, `docker compose up -d`, `webServer`) rather than pointing at production.

## If the project uses Cypress

Keep Cypress. The same rules apply with its API:

- Locators: `cy.get('[data-cy=submit]')`, `cy.contains('button', 'Save')`. `cy.findByRole` needs `@testing-library/cypress`, so ask before adding it.
- Login once: `cy.session(['user', email], () => { ...log in... })` inside a custom `cy.login()` command.
- Waiting: `cy.intercept('POST', '/api/orders').as('order')` then `cy.wait('@order')`. Never `cy.wait(2000)`.
- Seeding: `cy.request()` or `cy.task()` in `beforeEach`.
- De-flake: `npx cypress run --spec cypress/e2e/signup.cy.ts` in a loop, or the `retries` config with flaky runs treated as failures.
- CI: the `cypress-io/github-action`, pinned like any other action.
