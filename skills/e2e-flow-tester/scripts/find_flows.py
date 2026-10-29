#!/usr/bin/env python3
"""Inventory a web app's routes, forms and existing e2e setup to seed a list of critical user flows. Stdlib only.

Usage: python3 find_flows.py [root] [--json] [--min-score N] [--all]
  root         project directory (default: .)
  --min-score  score at which a page becomes a critical flow candidate (default: 5)
  --all        list every page, endpoint and form (text output otherwise caps each group at 40)
  --json       machine-readable output

Recognises Next.js (app and pages router), React Router (JSX, route objects, routes.ts), Remix flat
routes, SvelteKit, Nuxt, Vue Router, Angular, Django, Rails, Laravel, and Express/Fastify/Koa/Hono
endpoints. It reads files with regexes and never runs them. Mount prefixes (app.use('/api', router),
Django include(), nested relative routes) are not resolved, so confirm paths in the running app.

Exit codes: 0 routes or endpoints found · 2 nothing recognised
"""
import argparse
import json
import os
import re
import sys

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "out", ".next", ".nuxt", ".output", ".svelte-kit", ".vercel",
             "coverage", "vendor", "venv", ".venv", "env", "__pycache__", "target", "tmp", "storage", ".turbo",
             ".cache", "playwright-report", "test-results", "bower_components"}
KEEP_HIDDEN = {".github", ".circleci"}
JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts")
SRC_EXT = JS_EXT + (".vue", ".svelte", ".astro", ".py", ".rb", ".php", ".html", ".erb", ".haml", ".slim", ".twig")
TEST_PATH = re.compile(r"(^|/)(__tests__|__mocks__|e2e|cypress|playwright|tests?|spec|specs|fixtures|stories)(/|$)"
                       r"|\.(spec|test|cy|stories|e2e)\.[a-z]+$")
MAX_BYTES = 1_000_000

# tag: (weight, matcher for path/file/component names, matcher for form text or None)
TAGS = [
    ("login", 5, r"\b(log ?in|sign ?in|logon|sessions? new)\b", r"\b(log ?in|sign ?in)\b"),
    ("signup", 5, r"\b(sign ?up|register|registration|create account|join|onboard(ing)?|get started)\b",
     r"\b(sign ?up|create (an |your )?account|register)\b"),
    ("password-reset", 5, r"\b(forgot|reset password|password reset|reset|recover (account|password)|passwords? (new|edit))\b", r"\b(forgot|reset (your )?password)"),
    ("checkout", 5, r"\b(checkout|cart|basket|payments?|pay|stripe|paypal|purchase|orders?)\b",
     r"\b(checkout|add to cart|place order|pay now|card ?number)\b|stripe|PaymentElement|CardElement"),
    ("billing", 4, r"\b(billing|subscriptions?|subscribe|plans?|pricing|invoices?|upgrade)\b",
     r"\b(billing|subscription|upgrade plan)\b"),
    ("verify", 3, r"\b(verify|verification|confirm(ation)?|otp|2fa|mfa|two factor|magic link)\b",
     r"\b(verification code|one.time (pass)?code|otp)\b"),
    ("invite", 3, r"\b(invite|invites|invitations?|members)\b", r"\binvite\b"),
    ("delete", 3, r"\b(delete|destroy|remove|deactivate)\b", r"\b(delete|destroy)\b"),
    ("upload", 3, r"\b(upload|uploads|import|attachments?|dropzone)\b", r"\bupload\b|dropzone|file_field|FileField"),
    ("search", 3, r"\b(search|results)\b", r"type=[\"'{]*search|role=[\"']search|placeholder=[\"'][^\"']*search"),
    ("settings", 3, r"\b(settings|preferences|profile|account)\b", r"\b(settings|preferences|save changes)\b"),
    ("create-edit", 3, r"\b(new|create|edit|update|compose)\b", None),
]
AUTH_TAGS = {"login", "signup", "password-reset"}
TAG_RX = [(t, w, re.compile(n), re.compile(c, re.I) if c else None) for t, w, n, c in TAGS]
FORM_RX = re.compile(r"<form\b|<Form\b|onSubmit\s*=|@submit|v-on:submit|\(ngSubmit\)|use:enhance|handleSubmit\(|"
                     r"\bform_(with|for|tag)\b|\{%\s*csrf_token|request\.POST|\.is_valid\(\)|"
                     r"export\s+(const|async\s+function|function)\s+action\b|export\s+const\s+actions\b|"
                     r"\buseForm\(|formAction=|\saction=\{")
INPUTS = [
    ("email", re.compile(r"type=[\"'{]*email|EmailField|EmailInput|email_field|type:\s*[\"']email", re.I)),
    ("password", re.compile(r"type=[\"'{]*password|PasswordInput|password_field|type:\s*[\"']password", re.I)),
    ("file", re.compile(r"type=[\"'{]*file|FileField|ImageField|file_field|useDropzone", re.I)),
    ("search", re.compile(r"type=[\"'{]*search|role=[\"']search|search_field", re.I)),
]
SPEC_RX = re.compile(r"\.(spec|test|cy|e2e|setup)\.[cm]?[jt]sx?$|_spec\.rb$|_test\.rb$|(^|/)test_\w+\.py$")
TESTID_RX = re.compile(r"data-(testid|test-id|test|cy|qa)\s*=")
E2E_DEPS = ("@playwright/test", "playwright", "cypress", "puppeteer", "webdriverio", "@wdio/cli", "testcafe",
            "selenium-webdriver", "nightwatch")
PY_E2E = ("pytest-playwright", "playwright", "selenium", "splinter")
RB_E2E = ("capybara", "cuprite", "selenium-webdriver", "playwright-ruby-client", "capybara-playwright-driver")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def words(s):
    """'/(auth)/forgot-password' -> ' auth forgot password '; 'LoginPage' -> ' login page '."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()) + " "


def norm_key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def line_at(text, pos):
    return text.count("\n", 0, pos) + 1


def join_path(segs):
    segs = [s for s in segs if s not in ("", ".")]
    return "/" + "/".join(segs) if segs else "/"


def slash(p):
    p = p.strip()
    if p in ("", "/"):
        return "/"
    return p if p.startswith("/") else "/" + p


def dyn(seg):
    """Bracket segments (Next, SvelteKit, Nuxt) to :param form."""
    for rx, fmt in ((r"\[\[\.\.\.(\w+)\]\]", ":%s*?"), (r"\[\.\.\.(\w+)\]", ":%s*"), (r"\[\[(\w+)(?:=\w+)?\]\]", ":%s?"),
                    (r"\[(\w+)(?:=\w+)?\]", ":%s")):
        m = re.fullmatch(rx, seg)
        if m:
            return fmt % m.group(1)
    return seg


class Project:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.files = []
        self._cache = {}
        for d, dirs, files in os.walk(self.root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and (not x.startswith(".") or x in KEEP_HIDDEN))
            rel = os.path.relpath(d, self.root).replace(os.sep, "/")
            for f in sorted(files):
                self.files.append(f if rel == "." else rel + "/" + f)
        self.fileset = set(self.files)
        self.src = [f for f in self.files if f.endswith(SRC_EXT) and not TEST_PATH.search(f)]
        self.by_key = {}
        for f in self.src:
            base = f.rsplit("/", 1)[-1].split(".")
            key = norm_key("".join(base[:-1]) if len(base) > 1 else base[0])
            if key == "index" and "/" in f:
                key = norm_key(f.rsplit("/", 2)[-2])
            self.by_key.setdefault(key, []).append(f)
        self.pkgs = {}
        for f in self.files:
            if f.rsplit("/", 1)[-1] == "package.json":
                try:
                    self.pkgs[f] = json.loads(self.read(f) or "{}")
                except ValueError:
                    pass
        self.deps = {}
        for f, p in self.pkgs.items():
            for k in ("dependencies", "devDependencies", "peerDependencies"):
                for name, ver in (p.get(k) or {}).items():
                    self.deps.setdefault(name, (ver, k, f))
        has = lambda rx: any(re.search(rx, f) for f in self.files)  # noqa: E731
        self.is_next = "next" in self.deps or has(r"(^|/)next\.config\.(js|mjs|cjs|ts)$")
        self.remix_like = (any(d.startswith("@remix-run/") for d in self.deps) or "@react-router/dev" in self.deps
                           or has(r"(^|/)(remix|react-router)\.config\.(js|mjs|ts)$"))

    def read(self, rel):
        if rel not in self._cache:
            try:
                p = os.path.join(self.root, rel)
                if os.path.getsize(p) > MAX_BYTES:
                    self._cache[rel] = ""
                else:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        self._cache[rel] = fh.read()
            except OSError:
                self._cache[rel] = ""
        return self._cache[rel]

    def resolve(self, frm, spec):
        """Resolve a relative, '@/' or '~/' import to a project file."""
        if spec.startswith("."):
            base = os.path.normpath(os.path.join(os.path.dirname(frm), spec)).replace(os.sep, "/")
            bases = [base]
        elif spec[:2] in ("@/", "~/"):
            bases = [spec[2:], "src/" + spec[2:], "app/" + spec[2:]]
        else:
            return None
        for b in bases:
            for c in [b] + [b + e for e in JS_EXT + (".vue", ".svelte")] + [b + "/index" + e for e in JS_EXT]:
                if c in self.fileset:
                    return c
        return None

    def component(self, name, frm=None):
        """Files for a component or view name: 'Login' -> Login.tsx, 'LoginComponent' -> login.component.{ts,html}."""
        hits = self.by_key.get(norm_key(name), [])
        if frm and len(hits) > 1:
            top = frm.split("/")[0]
            hits = [h for h in hits if h.split("/")[0] == top] or hits
        return hits[:3]


def route(path, kind, fw, file, line=1, sources=None, names="", methods=None, extra=""):
    return {"path": path, "kind": kind, "framework": fw, "file": file, "line": line, "sources": [file] if sources is None else sources,
            "names": names, "methods": methods or [], "extra": extra}


def export_methods(text):
    found = re.findall(r"export\s+(?:async\s+)?(?:function|const|let)\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", text)
    return sorted(set(found), key=HTTP_METHODS.index) or ["ALL"]


# --- file-based routers --------------------------------------------------------------
def next_app(pj):
    if pj.remix_like and not pj.is_next:
        return []
    out = []
    for f in pj.src:
        parts = f.split("/")
        m = re.fullmatch(r"(page|route)\.(tsx|jsx|ts|js|mdx)", parts[-1])
        if not m or "app" not in parts[:-1]:
            continue
        i = parts.index("app")
        segs, skip = [], False
        for s in parts[i + 1:-1]:
            if s.startswith("(") and s.endswith(")") or s.startswith("@"):
                continue
            if s.startswith("(.") or s.startswith("_"):
                skip = True
                break
            segs.append(dyn(s))
        if skip:
            continue
        if m.group(1) == "page":
            out.append(route(join_path(segs), "page", "next-app", f, names=" ".join(parts[i + 1:-1])))
        else:
            out.append(route(join_path(segs), "api", "next-app", f, methods=export_methods(pj.read(f))))
    return out


def pages_dir(pj, ext, fw):
    out = []
    for f in pj.src:
        parts = f.split("/")
        if not f.endswith(ext) or "pages" not in parts[:-1]:
            continue
        i = parts.index("pages")
        if fw == "next-pages" and not (i == 0 or parts[i - 1] == "src" or "/".join(parts[:i] + ["package.json"]) in pj.fileset):
            continue
        rel = parts[i + 1:]
        rel[-1] = rel[-1].rsplit(".", 1)[0]
        if fw == "next-pages" and (rel[-1].startswith("_") or rel[-1] in ("404", "500")):
            continue
        segs = [dyn(s) for s in rel if s != "index"]
        if fw == "nuxt":
            segs = [":" + s[1:] if s.startswith("_") and len(s) > 1 else s for s in segs]
        if fw == "next-pages" and rel[0] == "api":
            out.append(route(join_path(segs), "api", fw, f, methods=["ALL"]))
        else:
            out.append(route(join_path(segs), "page", fw, f, names=" ".join(parts[i + 1:])))
    return out


def nuxt_server(pj):
    out = []
    if "nuxt" not in pj.deps and not any(re.search(r"(^|/)nuxt\.config\.[jt]s$", f) for f in pj.files):
        return out
    for f in pj.src:
        parts = f.split("/")
        if not (f.startswith("server/api/") or f.startswith("server/routes/")) or not f.endswith(JS_EXT):
            continue
        rel = parts[2:]
        name = rel[-1].rsplit(".", 1)[0]
        method = "ALL"
        mm = re.fullmatch(r"(.+)\.(get|post|put|patch|delete)", name)
        if mm:
            name, method = mm.group(1), mm.group(2).upper()
        segs = [dyn(s) for s in rel[:-1] + [name] if s != "index"]
        out.append(route(join_path((["api"] if parts[1] == "api" else []) + segs), "api", "nuxt", f, methods=[method]))
    return out


def sveltekit(pj):
    out = []
    for f in pj.src:
        parts = f.split("/")
        if "routes" not in parts or parts[-1] not in ("+page.svelte", "+server.ts", "+server.js"):
            continue
        i = parts.index("routes")
        if i == 0 or parts[i - 1] != "src":
            continue
        segs = [dyn(s) for s in parts[i + 1:-1] if not (s.startswith("(") and s.endswith(")"))]
        d = "/".join(parts[:-1])
        if parts[-1] == "+page.svelte":
            srcs = [f] + [d + "/" + n for n in ("+page.server.ts", "+page.server.js", "+page.ts", "+page.js") if d + "/" + n in pj.fileset]
            out.append(route(join_path(segs), "page", "sveltekit", f, sources=srcs, names=" ".join(parts[i + 1:-1])))
        else:
            out.append(route(join_path(segs), "api", "sveltekit", f, methods=export_methods(pj.read(f))))
    return out


def remix_name(name):
    toks, cur, esc = [], "", False
    for ch in name:
        if ch in "[]":
            esc = ch == "["
        elif ch == "." and not esc:
            toks.append(cur)
            cur = ""
        else:
            cur += ch
    toks.append(cur)
    if toks[-1].startswith("_") and toks[-1] != "_index":
        return None  # pathless layout
    segs = []
    for t in toks:
        if t == "_index" or t.startswith("_"):
            continue
        t = t[:-1] if t.endswith("_") else t
        if t == "$":
            segs.append("*")
        elif t.startswith("($") and t.endswith(")"):
            segs.append(":" + t[2:-1] + "?")
        elif t.startswith("$"):
            segs.append(":" + t[1:])
        elif t.startswith("(") and t.endswith(")"):
            segs.append(t[1:-1] + "?")
        else:
            segs.append(t)
    return join_path(segs)


def remix(pj):
    out = []
    for f in pj.src:
        parts = f.split("/")
        if not f.endswith(JS_EXT) or "app" not in parts:
            continue
        i = parts.index("app")
        if parts[i + 1:] and parts[-1].rsplit(".", 1)[0] == "routes" and len(parts) == i + 2:
            out += routes_ts(pj, f, "/".join(parts[:i + 1]))
            continue
        if len(parts) < i + 3 or parts[i + 1] != "routes":
            continue
        if pj.is_next and not pj.remix_like:
            continue
        rts = [g for g in pj.src if g.startswith("/".join(parts[:i + 1]) + "/routes.") and g.count("/") == f.count("/") - 1]
        if rts and "flatRoutes" not in pj.read(rts[0]):
            continue  # routes are listed by hand in app/routes.ts
        if len(parts) == i + 3:
            name = parts[-1].rsplit(".", 1)[0]
        elif len(parts) == i + 4 and parts[-1].split(".")[0] == "route":
            name = parts[-2]
        else:
            continue
        p = remix_name(name)
        if p is None:
            continue
        text = pj.read(f)
        if re.search(r"export\s+default\b", text):
            out.append(route(p, "page", "remix", f, names=name))
        elif re.search(r"export\s+(async\s+)?(function|const)\s+(loader|action)\b", text):
            ms = [m for m, k in (("GET", "loader"), ("POST", "action")) if re.search(r"\b%s\b" % k, text)]
            out.append(route(p, "api", "remix", f, methods=ms))
    return out


def routes_ts(pj, f, appdir):
    """React Router v7 framework mode: route("path", "file") and index("file") in app/routes.ts."""
    text, out = pj.read(f), []
    for m in re.finditer(r"\b(route|index)\(\s*(?:[\"']([^\"']*)[\"']\s*,\s*)?[\"']([^\"']+\.[jt]sx?)[\"']", text):
        target = os.path.normpath(os.path.join(appdir, m.group(3))).replace(os.sep, "/")
        p = "/" if m.group(1) == "index" else slash(m.group(2) or "")
        out.append(route(p, "page", "react-router", f, line_at(text, m.start()),
                         sources=[target] if target in pj.fileset else [], names=m.group(3)))
    return out


# --- config-based routers ------------------------------------------------------------
def js_config_routes(pj):
    out = []
    obj_rx = re.compile(r"\bpath\s*:\s*[\"'`]([^\"'`]*)[\"'`]")
    for f in pj.src:
        if not f.endswith(JS_EXT):
            continue
        text = pj.read(f)
        angular = "@angular/router" in text
        if not angular and not re.search(r"react-router|vue-router|createBrowserRouter|createRouter\(", text):
            continue
        fw = "angular" if angular else ("vue-router" if "vue-router" in text else "react-router")
        found = []  # (path, pos, component names, lazy import path)
        if fw == "react-router":
            for m in re.finditer(r"<Route\b", text):
                nxt = text.find("<Route", m.end())
                win = text[m.end(): min(nxt if nxt > 0 else len(text), m.end() + 500)]
                pm = re.search(r"\bpath\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{\s*[\"'`]([^\"'`]*)[\"'`]\s*\})", win)
                if pm:
                    found.append((next(g for g in pm.groups() if g is not None), m.start()) + components(win, "="))
        for m in obj_rx.finditer(text):
            nxt = obj_rx.search(text, m.end())
            win = text[m.end(): min(nxt.start() if nxt else len(text), m.end() + 400)]
            if "redirectTo" in win.split("}")[0] or m.group(1) == "**":
                continue
            found.append((m.group(1), m.start()) + components(win, ":"))
        # wrappers such as <Page>, <ProtectedRoute>, <Layout> repeat across routes; keep the page component
        freq = {}
        for _, _, names, _ in found:
            for n in set(names):
                freq[n] = freq.get(n, 0) + 1
        for path, pos, names, lazy in found:
            if len(names) > 1:
                names = [n for n in names if freq[n] < 3] or names[-1:]
            srcs = []
            if lazy:
                hit = pj.resolve(f, lazy)
                srcs = [hit] if hit else []
                if hit and fw == "angular" and hit.rsplit(".", 1)[0] + ".html" in pj.fileset:
                    srcs.append(hit.rsplit(".", 1)[0] + ".html")
                names = names or [lazy.rsplit("/", 1)[-1]]
            else:
                srcs = list(dict.fromkeys(h for n in names for h in pj.component(n, f)))
            out.append(route(slash(path), "page", fw, f, line_at(text, pos), sources=srcs, names=" ".join(names)))
    return out


def components(win, op):
    """Component names and lazy-import path in a route definition window ('=' for JSX props, ':' for objects)."""
    names, lazy = [], None
    em = re.search(r"element\s*%s\s*\{?\s*<" % op, win)
    if em:
        names = list(dict.fromkeys(re.findall(r"<\s*([A-Z]\w*)", win[em.start(): em.start() + 300])))
    cm = re.search(r"\b[cC]omponent\s*%s\s*\{?\s*([A-Z]\w*)" % op, win)
    if cm and cm.group(1) not in names:
        names.append(cm.group(1))
    im = re.search(r"import\(\s*[\"']([^\"']+)[\"']\s*\)", win)
    if im and not names:
        lazy = im.group(1)
    return names, lazy


def django(pj):
    out = []
    for f in pj.src:
        if not (f.endswith("/urls.py") or f == "urls.py" or "/urls/" in f and f.endswith(".py")):
            continue
        text, d = pj.read(f), f.rsplit("/", 1)[0] if "/" in f else ""
        for m in re.finditer(r"\b(re_)?path\(\s*r?[\"']([^\"']*)[\"']\s*,\s*([^\n]*)", text):
            rest = m.group(3)
            if "include(" in rest:
                continue
            p = m.group(2)
            if m.group(1):
                p = re.sub(r"\(\?P<(\w+)>[^)]*\)", r":\1", p.strip("^$"))
            p = re.sub(r"<(?:\w+:)?(\w+)>", r":\1", p)
            view = re.match(r"\s*([\w.]+)", rest)
            vname = view.group(1).split(".")[-1] if view else ""
            if vname == "as_view" and view:
                vname = view.group(1).split(".")[-2]
            block, srcs = django_view(pj, d, vname)
            for t in re.findall(r"template_name\s*=\s*[\"']([^\"']+)[\"']", rest + block) + \
                    re.findall(r"render\(\s*request\s*,\s*[\"']([^\"']+)[\"']", block):
                srcs += [x for x in pj.files if x.endswith("templates/" + t)][:1]
            kind = "api" if p.startswith("api/") or re.search(r"APIView|ViewSet|api_view|JsonResponse", rest + block) else "page"
            nm = re.search(r"\bname\s*=\s*[\"']([^\"']+)", rest)
            out.append(route(slash(p), kind, "django", f, line_at(text, m.start()), sources=srcs,
                             names=vname + " " + (nm.group(1) if nm else ""), extra=block,
                             methods=["ALL"] if kind == "api" else []))
        for m in re.finditer(r"\.register\(\s*r?[\"']([^\"']+)[\"']", text):
            out.append(route(slash(m.group(1)), "api", "django", f, line_at(text, m.start()), methods=["ALL"]))
    return out


def django_view(pj, d, name):
    if not name:
        return "", []
    for vf in [d + "/views.py" if d else "views.py"] + [x for x in pj.src if x.startswith((d + "/" if d else "") + "views/")]:
        text = pj.read(vf) if vf in pj.fileset else ""
        m = re.search(r"^(?:def|class)\s+%s\b" % re.escape(name), text, re.M)
        if m:
            end = re.search(r"^(?:def|class|@)\s*\w", text[m.end():], re.M)
            return text[m.start(): m.end() + (end.start() if end else len(text))], []
    return "", []


def rails(pj):
    out = []
    for f in pj.files:
        if not f.endswith("config/routes.rb"):
            continue
        text, stack = pj.read(f), []
        views = lambda c, a: [x for x in pj.files if re.match(r"(.*/)?app/views/%s/%s\.html\.\w+$" % (re.escape(c), a), x)]  # noqa: E731
        for ln, line in enumerate(text.splitlines(), 1):
            s = re.sub(r"(^|\s)#.*$", "", line).strip()
            pre = "".join(stack)
            opens = bool(re.search(r"\bdo(\s*\|[^|]*\|)?$", s))
            m = re.match(r"(namespace|scope)\s+(?::(\w+)|[\"']/?([^\"']+)[\"'])", s)
            res = re.match(r"(resources|resource)\s+:(\w+)(.*)", s)
            if m and opens:
                stack.append("/" + (m.group(2) or m.group(3)))
                continue
            if res:
                plural, name, opts = res.group(1) == "resources", res.group(2), res.group(3)
                acts = ["index", "new", "show", "edit", "create", "update", "destroy"]
                only = re.search(r"only:\s*\[([^\]]*)\]|only:\s*:(\w+)", opts)
                exc = re.search(r"except:\s*\[([^\]]*)\]|except:\s*:(\w+)", opts)
                if only:
                    acts = re.findall(r"\w+", only.group(1) or only.group(2))
                if exc:
                    acts = [a for a in acts if a not in re.findall(r"\w+", exc.group(1) or exc.group(2))]
                base = pre + "/" + name
                member = base + ("/:id" if plural else "")
                ctrl = (pre.strip("/") + "/" if pre and ":" not in pre else "") + (name if plural else name + "s")
                paths = {"index": base, "new": base + "/new", "show": member, "edit": member + "/edit"}
                for a in ("index", "new", "show", "edit"):
                    if a in acts and (plural or a != "index"):
                        out.append(route(paths[a], "page", "rails", f, ln, sources=views(ctrl, a), names="%s %s" % (name, a)))
                for a, meth, p in (("create", "POST", base), ("update", "PATCH", member), ("destroy", "DELETE", member)):
                    if a in acts:
                        out.append(route(p, "api", "rails", f, ln, sources=[], names="%s %s" % (name, a), methods=[meth]))
                if opens:
                    stack.append("/%s/:%s_id" % (name, name.rstrip("s")) if plural else "/" + name)
                continue
            dv = re.match(r"devise_for\s+:(\w+)", s)
            if dv:
                u = pre + "/" + dv.group(1)
                for p, n in (("/sign_in", "sign in"), ("/sign_up", "sign up"), ("/password/new", "forgot password")):
                    out.append(route(u + p, "page", "rails", f, ln, sources=[], names="devise " + n))
                continue
            vm = re.match(r"(get|post|put|patch|delete|match)\s+[\"']([^\"']+)[\"'](.*)", s)
            rm = re.match(r"root\s+(?:to:\s*)?[\"'](\w+(?:/\w+)*)#(\w+)[\"']", s)
            if rm:
                out.append(route(pre or "/", "page", "rails", f, ln, sources=views(rm.group(1), rm.group(2)),
                                 names="%s %s" % (rm.group(1), rm.group(2))))
            elif vm:
                ca = re.search(r"[\"'](\w+(?:/\w+)*)#(\w+)[\"']", vm.group(3))
                p = re.sub(r"\(([^)]*)\)", r"\1", pre + slash(vm.group(2)))
                meth = vm.group(1).upper()
                names = "%s %s %s" % (vm.group(2), ca.group(1), ca.group(2)) if ca else vm.group(2)
                if meth == "GET":
                    out.append(route(p, "page", "rails", f, ln, sources=views(ca.group(1), ca.group(2)) if ca else [], names=names))
                else:
                    out.append(route(p, "api", "rails", f, ln, sources=[], names=names, methods=[meth]))
            elif s == "end" and stack:
                stack.pop()
            elif opens:
                stack.append("")  # draw, member, collection, constraints ... blocks
    return out


def laravel(pj):
    out = []
    for f in pj.files:
        if not re.search(r"(^|/)routes/(web|api)\.php$", f):
            continue
        text, api = pj.read(f), f.endswith("api.php")
        for m in re.finditer(r"Route::(get|post|put|patch|delete|any|view|resource)\(\s*[\"']([^\"']+)[\"']\s*,?\s*([^\n]*)", text):
            verb, p = m.group(1), re.sub(r"\{(\w+)(\?)?\}", lambda x: ":" + x.group(1) + (x.group(2) or ""), m.group(2))
            p = ("/api" if api else "") + slash(p)
            ln = line_at(text, m.start())
            if verb == "resource":
                for sfx, meth in (("", "GET"), ("/create", "GET"), ("/:id/edit", "GET"), ("", "POST"), ("/:id", "DELETE")):
                    kind = "page" if meth == "GET" and not api else "api"
                    out.append(route(p + sfx, kind, "laravel", f, ln, sources=[], names=p + sfx, methods=[] if kind == "page" else [meth]))
                continue
            srcs = []
            if verb == "view":
                vm = re.match(r"[\"']([\w.\-/]+)[\"']", m.group(3))
                if vm:
                    t = "resources/views/" + vm.group(1).replace(".", "/") + ".blade.php"
                    srcs = [x for x in pj.files if x.endswith(t)][:1]
            if verb in ("get", "view") and not api:
                out.append(route(p, "page", "laravel", f, ln, sources=srcs, names=m.group(3)[:80]))
            else:
                out.append(route(p, "api", "laravel", f, ln, sources=[], names=m.group(3)[:80],
                                 methods=["ALL" if verb == "any" else verb.upper()]))
    return out


def node_servers(pj):
    out = []
    call = re.compile(r"\b(app|server|fastify|router|routes|\w+Router|\w+Routes)\.(get|post|put|patch|delete|all)\(\s*[\"'`](/[^\"'`]*)[\"'`]")
    chain = re.compile(r"\.route\(\s*[\"'`](/[^\"'`]*)[\"'`]\s*\)((?:\s*\.(?:get|post|put|patch|delete|all)\()?)")
    fst = re.compile(r"\bmethod\s*:\s*\[?\s*[\"'](\w+)[\"'][^}]*?\burl\s*:\s*[\"'](/[^\"']*)[\"']", re.S)
    for f in pj.src:
        if not f.endswith(JS_EXT):
            continue
        text = pj.read(f)
        if not re.search(r"[\"'](express|fastify|koa|@koa/router|koa-router|hono)[\"'/]", text):
            continue
        fw = next(x for x in ("express", "fastify", "koa", "hono") if x in text)
        for m in call.finditer(text):
            meth = "ALL" if m.group(2) == "all" else m.group(2).upper()
            out.append(route(m.group(3), "api", fw, f, line_at(text, m.start()), sources=[], methods=[meth]))
        for m in chain.finditer(text):
            out.append(route(m.group(1), "api", fw, f, line_at(text, m.start()), sources=[], methods=["ALL"]))
        for m in fst.finditer(text):
            out.append(route(m.group(2), "api", fw, f, line_at(text, m.start()), sources=[], methods=[m.group(1).upper()]))
    return out


# --- forms and scoring ---------------------------------------------------------------
def form_info(pj, r):
    srcs = [s for s in r["sources"] if s]
    texts = [pj.read(s) for s in srcs]
    for s, t in list(zip(srcs, texts)):
        if not s.endswith(JS_EXT + (".vue", ".svelte")):
            continue
        for spec in re.findall(r"""(?:import\s[^;]*?from\s*|import\(\s*)["']([^"']+)["']""", t)[:30]:
            base = words(spec.rsplit("/", 1)[-1])
            if " form " not in base and not any(rx.search(base) for _, _, rx, _ in TAG_RX[:-1]):
                continue
            hit = pj.resolve(s, spec)
            if hit and hit not in srcs and not TEST_PATH.search(hit):
                srcs.append(hit)
                texts.append(pj.read(hit))
    text = "\n".join(texts + [r.get("extra", "")])
    form = bool(FORM_RX.search(text))
    inputs = [n for n, rx in INPUTS if rx.search(text)]
    return {"form": form, "inputs": inputs, "testids": len(TESTID_RX.findall(text)), "files": srcs}, text


def score(r, fi, text):
    """Name evidence (path, route/view/component names, route files) counts in full. Form text is weaker:
    it counts only when the name says nothing, at half weight, for at most two tags, and auth tags need
    an email or password input."""
    primary = r["sources"] if r["sources"] else [r["file"]]
    name_text = words(r["path"] + " " + r["names"] + " " + " ".join(s.rsplit("/", 1)[-1] for s in primary if s))
    tags, reasons, total = [], [], 0
    for tag, w, nrx, _ in TAG_RX:
        m = nrx.search(name_text)
        if m:
            tags.append(tag)
            total += w
            reasons.append("name matches '%s'" % m.group(0).strip())
    if not tags and fi["form"]:
        hits = []
        for tag, w, _, crx in sorted(TAG_RX, key=lambda x: -x[1]):
            if crx is None or tag in AUTH_TAGS and not {"email", "password"} & set(fi["inputs"]):
                continue
            c = crx.search(text)
            if c:
                hits.append((tag, w, c.group(0).strip()))
        for tag, w, word in hits[:2]:
            tags.append(tag)
            total += (w + 1) // 2
            reasons.append("form text mentions '%s'" % word)
    if "DELETE" in r["methods"] and "delete" not in tags:
        tags.append("delete")
        total += 3
        reasons.append("DELETE method")
    if fi["form"]:
        total += 2
        reasons.append("has a form" + (" (%s inputs)" % ", ".join(fi["inputs"]) if fi["inputs"] else ""))
    if "password" in fi["inputs"]:
        total += 2
    if "create-edit" in tags and AUTH_TAGS & set(tags):
        tags.remove("create-edit")  # /password/new is an auth flow, not a core object
        total -= 3
        reasons = [x for x in reasons if not re.search(r"'(new|create|edit|update|compose)'$", x)]
    if "file" in fi["inputs"] and "upload" not in tags:
        tags.append("upload")
        total += 2
    return total, tags, reasons


# --- existing e2e setup --------------------------------------------------------------
def first_string(text, key):
    m = re.search(r"\b%s\s*:\s*([^\n]*)" % key, text)
    if not m:
        return None
    s = re.search(r"[\"'`]([^\"'`]+)[\"'`]", m.group(1))
    return s.group(1) if s else m.group(1).strip().rstrip(",")


def setup(pj):
    out = {"playwright": [], "cypress": [], "spec_dirs": {}, "scripts": {}, "deps": {}, "python": [], "ruby": [],
           "ci": [], "dev_script": None, "package_manager": None, "testid_files": 0}
    for f in pj.files:
        base = f.rsplit("/", 1)[-1]
        if re.fullmatch(r"playwright\.config\.(ts|js|mjs|cjs|mts)", base):
            t = re.sub(r"^\s*//.*$", "", pj.read(f), flags=re.M)
            ws = re.search(r"webServer\s*:\s*\{[^}]*?command\s*:\s*[\"'`]([^\"'`]+)", t, re.S)
            out["playwright"].append({"config": f, "baseURL": first_string(t, "baseURL"), "testDir": first_string(t, "testDir"),
                                      "webServer": ws.group(1) if ws else ("yes" if "webServer" in t else None),
                                      "storageState": "storageState" in t, "projects": re.findall(r"name\s*:\s*[\"'](\w[\w -]*)[\"']", t)})
        elif re.fullmatch(r"cypress\.config\.(ts|js|mjs|cjs)|cypress\.json", base):
            t = re.sub(r"^\s*//.*$", "", pj.read(f), flags=re.M)
            out["cypress"].append({"config": f, "baseUrl": first_string(t, "baseUrl"), "specPattern": first_string(t, "specPattern")})
        m = re.search(r"(^|/)(tests?/e2e|e2e|cypress/e2e|cypress/integration|playwright|integration-tests|spec/system|test/system)/", f)
        if m and SPEC_RX.search(f):
            d = f[:m.end() - 1]
            out["spec_dirs"][d] = out["spec_dirs"].get(d, 0) + 1
        if f.startswith(".github/workflows/") or base in (".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml") \
                or f.startswith(".circleci/"):
            t = pj.read(f).lower()
            tools = [x for x in ("playwright", "cypress", "puppeteer", "selenium") if x in t]
            if tools:
                out["ci"].append({"file": f, "tools": tools})
        if base in ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "Pipfile"):
            t = pj.read(f).lower()
            out["python"] += [x + " (" + f + ")" for x in PY_E2E if re.search(r"(^|[\s\"'])%s\b" % re.escape(x), t, re.M)]
        if base == "Gemfile":
            out["ruby"] += [x for x in RB_E2E if re.search(r"gem\s+[\"']%s[\"']" % re.escape(x), pj.read(f))]
        if base in ("pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lockb", "bun.lock") and not out["package_manager"]:
            out["package_manager"] = {"pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "package-lock.json": "npm"}.get(base, "bun")
    for f, p in pj.pkgs.items():
        for k, v in (p.get("scripts") or {}).items():
            if re.search(r"playwright|cypress|wdio|testcafe|puppeteer", str(v)):
                out["scripts"]["%s:%s" % (f, k) if f != "package.json" else k] = v
        for k in ("dev", "start", "serve", "preview"):
            if not out["dev_script"] and (p.get("scripts") or {}).get(k):
                out["dev_script"] = "%s (%s)" % (k, p["scripts"][k])
    for p in out["playwright"]:  # specs in a custom testDir, e.g. the default tests/
        td = (p["testDir"] or "").strip("./")
        cfg_dir = p["config"].rsplit("/", 1)[0] + "/" if "/" in p["config"] else ""
        if td and not any(d == cfg_dir + td for d in out["spec_dirs"]):
            n = sum(1 for f in pj.files if f.startswith(cfg_dir + td + "/") and SPEC_RX.search(f))
            if n:
                out["spec_dirs"][cfg_dir + td] = n
    for d in E2E_DEPS:
        if d in pj.deps:
            out["deps"][d] = pj.deps[d][0]
    out["python"] = sorted(set(out["python"]))
    out["testid_files"] = sum(1 for f in pj.src if TESTID_RX.search(pj.read(f)))
    out["found"] = bool(out["playwright"] or out["cypress"] or out["spec_dirs"] or out["deps"] or out["python"] or out["ruby"])
    return out


# --- main ------------------------------------------------------------------------------
def analyse(root, min_score=5):
    pj = Project(root)
    found = (next_app(pj) + pages_dir(pj, (".tsx", ".jsx", ".ts", ".js"), "next-pages") if pj.is_next else next_app(pj))
    found += pages_dir(pj, ".vue", "nuxt") + nuxt_server(pj) + sveltekit(pj) + remix(pj) + js_config_routes(pj)
    found += django(pj) + rails(pj) + laravel(pj) + node_servers(pj)
    pages, api, seen, linked = [], [], set(), set()
    for r in found:
        key = (r["kind"], r["path"], r["framework"], tuple(r["methods"]))
        if key in seen:
            continue
        seen.add(key)
        fi, text = form_info(pj, r)
        sc, tags, reasons = score(r, fi, text)
        linked.update(fi["files"])
        item = {"path": r["path"], "framework": r["framework"], "file": r["file"], "line": r["line"], "score": sc,
                "tags": tags, "reasons": reasons, "form": fi["form"], "inputs": fi["inputs"],
                "dynamic": ":" in r["path"] or "*" in r["path"], "sources": fi["files"]}
        if r["kind"] == "api":
            item["methods"] = r["methods"]
            api.append(item)
        else:
            item["critical"] = sc >= min_score
            pages.append(item)
    unlinked = []
    for f in pj.src:
        if f in linked or not f.endswith(JS_EXT + (".vue", ".svelte", ".html", ".erb", ".haml", ".slim", ".php", ".twig")):
            continue
        t = pj.read(f)
        if FORM_RX.search(t):
            fake = route("", "page", "", f, names=f.rsplit("/", 1)[-1].split(".")[0])
            fi, text = form_info(pj, fake)
            sc, tags, _ = score(fake, fi, text)
            unlinked.append({"file": f, "inputs": fi["inputs"], "tags": tags, "score": sc})
    order = lambda x: (-x["score"], x["path"])  # noqa: E731
    pages.sort(key=order)
    api.sort(key=order)
    unlinked.sort(key=lambda x: (-x["score"], x["file"]))
    fws = sorted({x["framework"] for x in pages + api})
    return {"root": pj.root, "frameworks": fws, "min_score": min_score,
            "critical": [p for p in pages if p["critical"]], "pages": [p for p in pages if not p["critical"]],
            "api": api, "unlinked_forms": unlinked, "setup": setup(pj)}


def text_report(res, show_all):
    cap = None if show_all else 40
    out = ["Scanned %s" % res["root"], "Frameworks: %s" % (", ".join(res["frameworks"]) or "none recognised"), ""]
    loc = lambda x: "%s:%s" % (x["file"], x["line"]) if x["line"] > 1 else x["file"]  # noqa: E731

    def group(title, items, fmt):
        if not items:
            return
        out.append("%s (%d)" % (title, len(items)))
        for x in items[:cap]:
            out.extend(fmt(x))
        if cap and len(items) > cap:
            out.append("  ... %d more (use --all or --json)" % (len(items) - cap))
        out.append("")

    group("Critical flow candidates, score >= %d" % res["min_score"], res["critical"], lambda x: [
        "  %3d  %-32s [%s]  %s" % (x["score"], x["path"], ", ".join(x["tags"]), loc(x)),
        "       " + "; ".join(x["reasons"])])
    group("Other pages", res["pages"], lambda x: [
        "  %3d  %-32s %s%s" % (x["score"], x["path"], loc(x), "  [%s]" % ", ".join(x["tags"]) if x["tags"] else "")])
    group("API endpoints", res["api"], lambda x: [
        "  %-7s %-32s %s%s" % (",".join(x["methods"]), x["path"], loc(x), "  [%s]" % ", ".join(x["tags"]) if x["tags"] else "")])
    group("Forms not linked to a route", res["unlinked_forms"], lambda x: [
        "  %3d  %s%s%s" % (x["score"], x["file"], "  inputs: " + ", ".join(x["inputs"]) if x["inputs"] else "",
                          "  [%s]" % ", ".join(x["tags"]) if x["tags"] else "")])
    s = res["setup"]
    out.append("Existing e2e setup")
    for p in s["playwright"]:
        out.append("  Playwright config %s: baseURL %s, testDir %s, webServer %s, storageState %s%s" % (
            p["config"], p["baseURL"] or "-", p["testDir"] or "-", p["webServer"] or "-", "yes" if p["storageState"] else "no",
            ", projects " + ", ".join(p["projects"]) if p["projects"] else ""))
    for c in s["cypress"]:
        out.append("  Cypress config %s: baseUrl %s, specPattern %s" % (c["config"], c["baseUrl"] or "-", c["specPattern"] or "-"))
    for d, n in sorted(s["spec_dirs"].items()):
        out.append("  Spec files: %s/ (%d)" % (d, n))
    for k, v in sorted(s["deps"].items()):
        out.append("  Dependency: %s %s" % (k, v))
    for k, v in sorted(s["scripts"].items()):
        out.append("  Script %s: %s" % (k, v))
    for x in s["python"] + s["ruby"]:
        out.append("  Package: %s" % x)
    for c in s["ci"]:
        out.append("  CI: %s mentions %s" % (c["file"], ", ".join(c["tools"])))
    if not s["found"]:
        out.append("  None found. No Playwright or Cypress config, spec folder or dependency.")
    if not s["ci"]:
        out.append("  No CI workflow runs browser tests yet.")
    out.append("  Dev server: %s; package manager: %s; files with data-testid-style attributes: %d" % (
        s["dev_script"] or "-", s["package_manager"] or "-", s["testid_files"]))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Inventory routes, forms and e2e setup to seed critical user flows.")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-score", type=int, default=5)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if not os.path.isdir(a.root):
        print("Not a directory: %s" % a.root, file=sys.stderr)
        sys.exit(2)
    res = analyse(a.root, a.min_score)
    print(json.dumps(res, indent=2) if a.json else text_report(res, a.all))
    sys.exit(0 if res["critical"] or res["pages"] or res["api"] else 2)


if __name__ == "__main__":
    main()
