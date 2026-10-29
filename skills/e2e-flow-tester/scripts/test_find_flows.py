#!/usr/bin/env python3
"""Tests for find_flows.py (stdlib unittest, no network, no node).

Run: python3 -m unittest discover -s skills/e2e-flow-tester/scripts -p 'test_*.py'
fixtures/ holds tiny projects (Next app router, React Router, Django, Express, SvelteKit, Rails);
other frameworks are written to a temp dir inside the test.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from find_flows import analyse, remix_name  # noqa: E402

SCRIPT = os.path.join(HERE, "find_flows.py")
FX = os.path.join(HERE, "fixtures")


def run(path, *args):
    r = subprocess.run([sys.executable, SCRIPT, path, *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def scan(name):
    return analyse(os.path.join(FX, name))


def paths(res, group="all"):
    groups = ("critical", "pages") if group == "all" else (group,)
    return {p["path"]: p for g in groups for p in res[g]}


def write_tree(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


class NextApp(unittest.TestCase):
    def setUp(self):
        self.res = scan("next-app")

    def test_routes_groups_and_dynamic_segments(self):
        self.assertEqual(set(paths(self.res)), {"/", "/login", "/projects/:id", "/docs/:slug*"})
        self.assertTrue(paths(self.res)["/projects/:id"]["dynamic"])

    def test_login_is_critical_with_form_from_imported_component(self):
        login = paths(self.res, "critical")["/login"]
        self.assertIn("login", login["tags"])
        self.assertTrue(login["form"])
        self.assertEqual(login["inputs"], ["email", "password"])
        self.assertIn("components/LoginForm.tsx", login["sources"])

    def test_route_handler_is_api_with_methods(self):
        api = {a["path"]: a for a in self.res["api"]}
        self.assertEqual(api["/api/projects"]["methods"], ["GET", "POST"])

    def test_existing_playwright_setup(self):
        s = self.res["setup"]
        pw = s["playwright"][0]
        self.assertEqual(pw["baseURL"], "http://localhost:3000")  # the commented-out baseURL is ignored
        self.assertEqual((pw["testDir"], pw["webServer"]), ("./e2e", "npm run dev"))
        self.assertEqual(s["spec_dirs"], {"e2e": 1})
        self.assertEqual(s["deps"], {"@playwright/test": "1.48.2"})
        self.assertEqual(s["scripts"], {"test:e2e": "playwright test"})
        self.assertEqual(s["ci"], [{"file": ".github/workflows/e2e.yml", "tools": ["playwright"]}])
        self.assertEqual((s["package_manager"], s["testid_files"]), ("npm", 1))
        self.assertTrue(s["found"])


class ReactRouter(unittest.TestCase):
    def test_jsx_and_object_routes(self):
        res = scan("react-router")
        self.assertEqual(set(paths(res)), {"/", "/cart", "/orders/:orderId", "/signup", "/about"})
        crit = paths(res, "critical")
        self.assertEqual(crit["/signup"]["sources"], ["src/pages/Signup.tsx"])
        self.assertEqual(crit["/signup"]["inputs"], ["email", "password"])
        self.assertIn("checkout", crit["/cart"]["tags"])
        self.assertEqual(crit["/orders/:orderId"]["sources"], ["src/pages/Order.tsx"])  # lazy import
        self.assertEqual(paths(res)["/about"]["score"], 0)  # unresolved component does not inherit the router file's imports
        self.assertFalse(res["setup"]["found"])


class Django(unittest.TestCase):
    def test_urls_templates_and_api(self):
        res = scan("django")
        self.assertEqual(set(paths(res)), {"/", "/accounts/login/", "/cart/checkout/", "/items/:pk/delete/", "/archive/:year/"})
        crit = paths(res, "critical")
        self.assertEqual(crit["/accounts/login/"]["sources"], ["shop/templates/registration/login.html"])
        self.assertEqual(crit["/accounts/login/"]["tags"], ["login"])
        self.assertTrue(crit["/cart/checkout/"]["form"])
        self.assertEqual([a["path"] for a in res["api"]], ["/api/items/"])  # include() lines are skipped


class Express(unittest.TestCase):
    def test_endpoints_only_from_server_files(self):
        res = scan("express")
        got = {(",".join(a["methods"]), a["path"]) for a in res["api"]}
        self.assertEqual(got, {("POST", "/login"), ("GET", "/users"), ("POST", "/users/:id/invite"),
                               ("DELETE", "/users/:id"), ("ALL", "/health")})
        self.assertEqual(res["frameworks"], ["express"])
        self.assertEqual(res["critical"] + res["pages"], [])
        code, _ = run(os.path.join(FX, "express"))
        self.assertEqual(code, 0)


class SvelteKit(unittest.TestCase):
    def test_routes_actions_and_server_endpoints(self):
        res = scan("sveltekit")
        self.assertEqual(set(paths(res)), {"/", "/settings", "/blog/:slug"})
        settings = paths(res, "critical")["/settings"]
        self.assertTrue(settings["form"])
        self.assertIn("src/routes/(app)/settings/+page.server.ts", settings["sources"])
        self.assertEqual([(a["path"], a["methods"]) for a in res["api"]], [("/api/search", ["GET"])])


class Rails(unittest.TestCase):
    def test_resources_devise_namespace_and_views(self):
        res = scan("rails")
        self.assertEqual(set(paths(res)), {"/", "/login", "/projects", "/projects/new", "/admin/invoices", "/status",
                                           "/users/sign_in", "/users/sign_up", "/users/password/new"})
        crit = paths(res, "critical")
        self.assertEqual(crit["/login"]["sources"], ["app/views/sessions/new.html.erb"])
        self.assertEqual(crit["/login"]["inputs"], ["email", "password"])
        self.assertEqual(paths(res)["/users/password/new"]["tags"], ["password-reset"])
        self.assertEqual({(a["methods"][0], a["path"]) for a in res["api"]}, {("POST", "/projects"), ("DELETE", "/projects/:id")})


class OtherFrameworks(unittest.TestCase):
    def scan_tree(self, files):
        with tempfile.TemporaryDirectory() as t:
            write_tree(t, files)
            return analyse(t)

    def test_remix_flat_route_names(self):
        self.assertEqual(remix_name("_index"), "/")
        self.assertEqual(remix_name("users.$id.edit"), "/users/:id/edit")
        self.assertEqual(remix_name("_auth.login"), "/login")
        self.assertEqual(remix_name("files.$"), "/files/*")
        self.assertEqual(remix_name("sitemap[.]xml"), "/sitemap.xml")
        self.assertEqual(remix_name("($lang).about"), "/:lang?/about")
        self.assertIsNone(remix_name("_auth"))

    def test_remix_project(self):
        res = self.scan_tree({
            "package.json": '{"dependencies": {"@remix-run/react": "2.0.0"}}',
            "app/routes/_index.tsx": "export default function I() {}",
            "app/routes/checkout.tsx": "export async function action() {}\nexport default function C() { return <Form method='post' /> }",
            "app/routes/api.webhook.ts": "export async function action() {}",
        })
        self.assertEqual(set(paths(res)), {"/", "/checkout"})
        self.assertTrue(paths(res)["/checkout"]["critical"])
        self.assertEqual([(a["path"], a["methods"]) for a in res["api"]], [("/api/webhook", ["POST"])])

    def test_angular_routes_resolve_component_template(self):
        res = self.scan_tree({
            "src/app/app.routes.ts": "import { Routes } from '@angular/router';\nexport const routes: Routes = [\n"
                                     "  { path: '', component: HomeComponent },\n  { path: 'register', component: RegisterComponent },\n"
                                     "  { path: 'old', redirectTo: '' },\n  { path: '**', component: NotFoundComponent },\n];\n",
            "src/app/register/register.component.ts": "@Component({ templateUrl: './register.component.html' })",
            "src/app/register/register.component.html": "<form (ngSubmit)='save()'><input type='password'></form>",
        })
        self.assertEqual(set(paths(res)), {"/", "/register"})
        reg = paths(res)["/register"]
        self.assertTrue(reg["critical"] and reg["form"])
        self.assertIn("src/app/register/register.component.html", reg["sources"])

    def test_nuxt_pages_and_server_api(self):
        res = self.scan_tree({
            "package.json": '{"devDependencies": {"nuxt": "3.14.0"}}',
            "pages/index.vue": "<template><h1/></template>",
            "pages/search.vue": "<template><form @submit.prevent='go'><input type='search'></form></template>",
            "pages/posts/[id].vue": "<template/>",
            "server/api/posts/[id].delete.ts": "export default defineEventHandler(() => null)",
        })
        self.assertEqual(set(paths(res)), {"/", "/search", "/posts/:id"})
        self.assertTrue(paths(res)["/search"]["critical"])
        self.assertEqual([(a["path"], a["methods"]) for a in res["api"]], [("/api/posts/:id", ["DELETE"])])

    def test_next_pages_router_skips_special_files(self):
        res = self.scan_tree({
            "package.json": '{"dependencies": {"next": "14.2.0"}}',
            "pages/_app.tsx": "", "pages/_document.tsx": "", "pages/index.tsx": "", "pages/forgot-password.tsx": "",
            "pages/api/stripe/webhook.ts": "",
        })
        self.assertEqual(set(paths(res)), {"/", "/forgot-password"})
        self.assertIn("password-reset", paths(res)["/forgot-password"]["tags"])
        self.assertEqual([a["path"] for a in res["api"]], ["/api/stripe/webhook"])

    def test_react_router_v7_routes_ts(self):
        res = self.scan_tree({
            "package.json": '{"devDependencies": {"@react-router/dev": "7.0.0"}}',
            "app/routes.ts": 'import { route, index } from "@react-router/dev/routes";\nexport default [\n'
                             '  index("routes/home.tsx"),\n  route("billing", "routes/billing.tsx"),\n];\n',
            "app/routes/home.tsx": "export default function H() {}",
            "app/routes/billing.tsx": "export default function B() {}",
        })
        self.assertEqual(set(paths(res)), {"/", "/billing"})
        self.assertEqual(paths(res)["/billing"]["sources"], ["app/routes/billing.tsx"])

    def test_react_router_wrappers_are_not_the_page(self):
        res = self.scan_tree({
            "package.json": '{"dependencies": {"react-router-dom": "6.28.0"}}',
            "src/App.tsx": 'import { Routes, Route } from "react-router-dom";\n<Routes>\n'
                           '  <Route path="/" element={<Page><Home /></Page>} />\n'
                           '  <Route path="/auth" element={<Page><Auth /></Page>} />\n'
                           '  <Route path="/billing" element={<Page><Guard><Billing /></Guard></Page>} />\n</Routes>\n',
            "src/components/Page.tsx": "export const Page = ({ children }) => <main>{children}<form><input type='search' /></form></main>;",
            "src/pages/Auth.tsx": "export default () => <form><input type='email' /><button>Sign in</button></form>;",
        })
        auth = paths(res)["/auth"]
        self.assertEqual(auth["sources"], ["src/pages/Auth.tsx"])
        self.assertEqual(auth["tags"], ["login"])  # from the form text, since /auth names no flow
        self.assertNotIn("search", paths(res)["/"]["tags"])

    def test_laravel_and_cypress_detection(self):
        res = self.scan_tree({
            "routes/web.php": "<?php\nRoute::get('/checkout', [CheckoutController::class, 'show']);\n"
                              "Route::post('/checkout', [CheckoutController::class, 'store']);\nRoute::view('/login', 'auth.login');\n",
            "routes/api.php": "<?php\nRoute::delete('/orders/{order}', [OrderController::class, 'destroy']);\n",
            "resources/views/auth/login.blade.php": "<form method='POST'><input type='email'><input type='password'></form>",
            "cypress.config.ts": "export default defineConfig({ e2e: { baseUrl: 'http://localhost:8000' } })",
            "cypress/e2e/login.cy.ts": "it('logs in', () => {})",
        })
        self.assertEqual(set(paths(res, "critical")), {"/checkout", "/login"})
        self.assertEqual(paths(res)["/login"]["sources"], ["resources/views/auth/login.blade.php"])
        self.assertIn(("DELETE", "/api/orders/:order"), {(a["methods"][0], a["path"]) for a in res["api"]})
        self.assertEqual(res["setup"]["cypress"][0]["baseUrl"], "http://localhost:8000")
        self.assertEqual(res["setup"]["spec_dirs"], {"cypress/e2e": 1})

    def test_ignores_tests_and_node_modules(self):
        res = self.scan_tree({
            "package.json": '{"dependencies": {"next": "15.0.0"}}',
            "app/page.tsx": "",
            "node_modules/pkg/app/admin/page.tsx": "",
            "e2e/app/login/page.tsx": "",
        })
        self.assertEqual(set(paths(res)), {"/"})


class Cli(unittest.TestCase):
    def test_json_output(self):
        code, out = run(os.path.join(FX, "next-app"), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["frameworks"], ["next-app"])
        self.assertEqual([c["path"] for c in data["critical"]], ["/login"])

    def test_text_output_groups(self):
        code, out = run(os.path.join(FX, "next-app"))
        self.assertEqual(code, 0)
        for heading in ("Critical flow candidates", "Other pages", "API endpoints", "Existing e2e setup"):
            self.assertIn(heading, out)
        self.assertLess(out.index("Critical flow candidates"), out.index("Other pages"))

    def test_min_score(self):
        _, out = run(os.path.join(FX, "next-app"), "--json", "--min-score", "20")
        self.assertEqual(json.loads(out)["critical"], [])

    def test_nothing_recognised_exits_2(self):
        code, out = run(os.path.join(FX, "empty"))
        self.assertEqual(code, 2)
        self.assertIn("none recognised", out)
        self.assertEqual(run(os.path.join(FX, "missing-dir"))[0], 2)


if __name__ == "__main__":
    unittest.main()
