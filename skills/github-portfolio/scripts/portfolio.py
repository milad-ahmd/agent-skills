#!/usr/bin/env python3
"""Build a one-page developer portfolio site from public GitHub data (stdlib only).

Usage:
  portfolio.py --user NAME [--out DIR] [--top N] [--overrides data.json] [--save-raw FILE] [--json]
  portfolio.py --from-json raw.json [--out DIR] [--overrides data.json] [--json]
  portfolio.py --publish-instructions --user NAME [--out DIR]

Writes DIR/index.html (one inline stylesheet, no scripts), DIR/data.json (edit it to change wording,
reorder projects or set "featured": false, then re-run with --overrides DIR/data.json) and DIR/.nojekyll.
Without a token GitHub allows 60 requests per hour per IP. The script counts its requests, fetches
README and language data only for the top projects, and stops those optional calls when the budget
or the rate limit runs out. GITHUB_TOKEN is sent only if it is set. --publish-instructions prints
git commands and never runs them.

Exit codes: 0 site written (or instructions printed) / 2 user not found, API failure or bad input file.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
MAX_REQUESTS = 40  # stays well inside the 60/h unauthenticated limit
USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
NOTE = ("Edit title, blurb, stack and live, reorder projects, or set featured to false to hide one. "
        "Re-run with --overrides data.json to keep these edits.")
PROFILE_KEYS = ("name", "bio", "location", "website", "twitter", "email", "github")
EDITABLE = {"title": str, "blurb": str, "stack": list, "live": str, "featured": bool}


class Fail(Exception):
    """User-facing error, exit 2."""


class RateLimited(Fail):
    pass


class Client:
    def __init__(self, opener=None, max_requests=MAX_REQUESTS, token=None):
        self.opener = opener or urllib.request.urlopen
        self.max_requests = max_requests
        self.token = token
        self.count = 0
        self.remaining = None

    def get(self, path):
        """GET API+path. Returns parsed JSON, or None on 404."""
        if self.count >= self.max_requests or self.remaining == 0:
            raise RateLimited("request budget used up (%d requests)" % self.count)
        req = urllib.request.Request(API + path, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "github-portfolio-skill",
            "X-GitHub-Api-Version": "2022-11-28"})
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        self.count += 1
        try:
            with self.opener(req, timeout=20) as resp:
                self._note(resp.headers)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self._note(e.headers)
            if e.code == 404:
                return None
            if e.code == 429 or (e.code == 403 and self.remaining == 0):
                reset = (e.headers or {}).get("x-ratelimit-reset")
                when = time.strftime("%H:%M UTC", time.gmtime(int(reset))) if reset and reset.isdigit() else "later"
                raise RateLimited("GitHub rate limit reached; it resets at %s. Set GITHUB_TOKEN or retry then." % when)
            raise Fail("GitHub API error %d on %s" % (e.code, path))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Fail("could not reach the GitHub API: %s" % e)

    def _note(self, headers):
        val = (headers or {}).get("x-ratelimit-remaining")
        if val is not None and str(val).isdigit():
            self.remaining = int(val)


def fetch(user, top=6, client=None):
    """Fetch profile, public repos, and README/languages for the top repos. Returns the raw dict."""
    if not USER_RE.match(user or ""):
        raise Fail("not a valid GitHub username: %r" % user)
    c = client or Client(token=os.environ.get("GITHUB_TOKEN") or None)
    profile = c.get("/users/" + user)
    if profile is None:
        raise Fail("GitHub user not found: " + user)
    repos = []
    for page in range(1, 11):
        batch = c.get("/users/%s/repos?per_page=100&type=owner&sort=pushed&page=%d" % (user, page)) or []
        repos += batch
        if len(batch) < 100:
            break
    raw = {"profile": profile, "repos": repos, "readmes": {}, "languages": {}, "partial": False,
           "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    login = profile.get("login", user)
    try:
        for r in select_featured(repos, login, top):
            q = "/repos/%s/%s" % (login, urllib.parse.quote(r["name"]))
            doc = c.get(q + "/readme")
            if doc and doc.get("encoding") == "base64":
                raw["readmes"][r["name"]] = base64.b64decode(doc.get("content", "")).decode("utf-8", "replace")
            raw["languages"][r["name"]] = c.get(q + "/languages") or {}
    except Fail as e:  # optional data: keep what we have
        raw["partial"] = True
        raw["partial_reason"] = str(e)
    raw["requests"] = c.count
    return raw


def _days_since(iso, now):
    try:
        then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 9999
    return max(0, (now - then).days)


def candidates(repos, login):
    """Public, non-fork, non-archived repos with a description, minus the profile and Pages repos."""
    skip = {login.lower(), login.lower() + ".github.io"}
    return [r for r in repos if not r.get("fork") and not r.get("private") and not r.get("archived")
            and (r.get("description") or "").strip() and r.get("name", "").lower() not in skip]


def select_featured(repos, login, top=6, now=None):
    """Rank by stars (log scale) plus recency (full bonus today, none after two years)."""
    now = now or datetime.now(timezone.utc)

    def score(r):
        recency = max(0.0, 1 - _days_since(r.get("pushed_at"), now) / 730.0)
        return math.log2(1 + (r.get("stargazers_count") or 0)) + 4 * recency
    return sorted(candidates(repos, login), key=lambda r: (-score(r), r["name"].lower()))[:top]


def first_paragraph(md, limit=280):
    """First prose paragraph of a Markdown README, without badges, headings, HTML or link syntax."""
    para, fence = [], False
    for line in (md or "").splitlines():
        s = line.strip()
        if s.startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        if re.fullmatch(r"[=-]{3,}", s):  # setext heading underline
            para = []
            continue
        clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
        clean = re.sub(r"\[\s*\]\([^)]*\)", "", clean)
        clean = re.sub(r"<[^>]+>", "", clean).strip()
        if not clean or s.startswith(("#", "|", ">")):
            if para and not s:
                break
            continue
        para.append(clean)
    text = " ".join(para)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`]{1,3}([^*_`]+)[*_`]{1,3}", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "..."
    return text


def safe_url(u):
    """http(s) URL or empty string. Bare domains (example.com) get https://."""
    u = (u or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I):
        u = "https://" + u
    p = urllib.parse.urlparse(u)
    return u if p.scheme in ("http", "https") and p.netloc else ""


def build(raw, overrides=None, top=6, now=None):
    """Turn a raw fetch into the editable site model, applying user overrides."""
    p, repos = raw.get("profile") or {}, raw.get("repos") or []
    login = p.get("login") or ""
    featured = {r["name"] for r in select_featured(repos, login, top, now)}
    projects = []
    for r in candidates(repos, login):
        langs = raw.get("languages", {}).get(r["name"]) or {}
        stack = list(langs)[:4] or ([r["language"]] if r.get("language") else [])
        projects.append({
            "name": r["name"], "title": r["name"], "blurb": r["description"].strip(),
            "stack": stack, "topics": r.get("topics") or [], "stars": r.get("stargazers_count") or 0,
            "forks": r.get("forks_count") or 0, "url": r.get("html_url") or "", "live": r.get("homepage") or "",
            "pushed_at": r.get("pushed_at") or "", "featured": r["name"] in featured,
            "readme_excerpt": first_paragraph(raw.get("readmes", {}).get(r["name"], ""))})
    ranked = {r["name"]: i for i, r in enumerate(select_featured(repos, login, len(repos), now))}
    projects.sort(key=lambda x: (not x["featured"], ranked.get(x["name"], 1e9)))
    data = {
        "_note": NOTE, "login": login, "fetched_at": raw.get("fetched_at", ""),
        "profile": {"name": p.get("name") or login, "bio": p.get("bio") or "", "location": p.get("location") or "",
                    "website": p.get("blog") or "", "twitter": p.get("twitter_username") or "",
                    "email": p.get("email") or "", "github": p.get("html_url") or "https://github.com/" + login,
                    "avatar": p.get("avatar_url") or ""},
        "projects": projects, "languages": language_shares(raw),
        "recent": [{"name": r["name"], "url": r.get("html_url") or "", "pushed_at": r.get("pushed_at") or ""}
                   for r in sorted((r for r in repos if not r.get("fork") and not r.get("private")),
                                   key=lambda r: r.get("pushed_at") or "", reverse=True)[:5]],
    }
    return apply_overrides(data, overrides) if overrides else data


def language_shares(raw, limit=6):
    """Percent of code bytes across the fetched repos, falling back to counts of primary languages."""
    totals = {}
    for langs in (raw.get("languages") or {}).values():
        for k, v in langs.items():
            totals[k] = totals.get(k, 0) + v
    if not totals:
        for r in raw.get("repos") or []:
            if r.get("language") and not r.get("fork"):
                totals[r["language"]] = totals.get(r["language"], 0) + 1
    whole = sum(totals.values())
    if not whole:
        return []
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    out = [[k, round(100.0 * v / whole, 1)] for k, v in ranked[:limit]]
    rest = round(100.0 - sum(x[1] for x in out), 1)
    if len(ranked) > limit and rest > 0:
        out.append(["Other", rest])
    return out


def apply_overrides(data, ov):
    """User wording, order and featured flags win; numbers and links come from the fresh fetch."""
    if not isinstance(ov, dict) or not isinstance(ov.get("projects", []), list):
        raise Fail("overrides file must be a JSON object with a projects list")
    for k in PROFILE_KEYS:
        if isinstance((ov.get("profile") or {}).get(k), str):
            data["profile"][k] = ov["profile"][k]
    fresh = {x["name"]: x for x in data["projects"]}
    merged = []
    for o in ov.get("projects", []):
        if not isinstance(o, dict) or o.get("name") not in fresh:
            continue  # gone from GitHub (deleted, renamed or private): drop it
        x = fresh.pop(o["name"])
        for k, kind in EDITABLE.items():
            if isinstance(o.get(k), kind):  # wrong types are ignored, not rendered
                x[k] = o[k]
        merged.append(x)
    for x in fresh.values():  # new repos since the last run: listed, not featured
        x["featured"] = False
        merged.append(x)
    data["projects"] = merged
    return data


# ---------- HTML ----------

CSS = """
:root{--bg:#ffffff;--fg:#1a1a1a;--muted:#555b65;--card:#f5f6f8;--line:#d9dce1;--link:#0a58ca;
--c1:#3b6fd8;--c2:#2e9e6b;--c3:#d08a1e;--c4:#c0466b;--c5:#7b5cd6;--c6:#2a9bb5;--c7:#8a8f98}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e8e9eb;--muted:#a9afb8;--card:#181b21;
--line:#2c313a;--link:#80b6ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--link)}
a:focus-visible{outline:3px solid var(--link);outline-offset:2px;border-radius:2px}
.wrap{max-width:960px;margin:0 auto;padding:0 16px}
.skip{position:absolute;left:-999px}.skip:focus{left:16px;top:8px;background:var(--bg);padding:4px 8px}
header{padding:48px 0 24px;display:flex;gap:20px;align-items:center;flex-wrap:wrap}
header img{border-radius:50%}
h1{margin:0;font-size:2rem;line-height:1.2}
h2{font-size:1.25rem;margin:40px 0 16px}
h3{margin:0 0 6px;font-size:1.05rem}
.muted{color:var(--muted)}
.links{list-style:none;padding:0;margin:8px 0 0;display:flex;flex-wrap:wrap;gap:4px 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,270px),1fr));gap:16px;list-style:none;padding:0;margin:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;display:flex;flex-direction:column;gap:8px}
.card p{margin:0}
.meta{font-size:.9rem;color:var(--muted);margin-top:auto}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--line)}
.legend{list-style:none;padding:0;margin:12px 0 0;display:flex;flex-wrap:wrap;gap:6px 18px;font-size:.95rem}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.recent{padding-left:20px}
footer{margin:48px 0 32px;padding-top:16px;border-top:1px solid var(--line);font-size:.95rem}
"""


def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _month(iso):
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%b %Y")
    except (TypeError, ValueError):
        return ""


def _link(url, text):
    u = safe_url(url)
    return '<a href="%s">%s</a>' % (e(u), e(text)) if u else ""


def render(data):
    p = data["profile"]
    name = p.get("name") or data.get("login") or "Portfolio"
    links = [_link(p.get("github"), "GitHub"), _link(p.get("website"), "Website")]
    if p.get("twitter"):
        links.append(_link("https://x.com/" + p["twitter"].lstrip("@"), "@" + p["twitter"].lstrip("@")))
    links = [x for x in links if x]
    out = ['<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
           "<title>%s</title>\n" % e(name),
           '<meta name="description" content="%s">\n' % e(p.get("bio") or "Projects by " + name),
           "<style>%s</style>\n</head>\n<body>\n" % CSS,
           '<a class="skip" href="#projects">Skip to projects</a>\n<div class="wrap">\n<header>\n']
    avatar = safe_url(p.get("avatar"))
    if avatar:
        out.append('<img src="%s" width="88" height="88" alt="Photo of %s">\n' % (e(avatar), e(name)))
    out.append("<div>\n<h1>%s</h1>\n" % e(name))
    if p.get("bio"):
        out.append("<p>%s</p>\n" % e(p["bio"]))
    if p.get("location"):
        out.append('<p class="muted">%s</p>\n' % e(p["location"]))
    if links:
        out.append('<nav aria-label="Profile links"><ul class="links">%s</ul></nav>\n'
                   % "".join("<li>%s</li>" % x for x in links))
    out.append("</div>\n</header>\n<main>\n")

    shown = [x for x in data["projects"] if x.get("featured")]
    out.append('<section aria-labelledby="projects"><h2 id="projects">Featured projects</h2>\n<ul class="grid">\n')
    for x in shown:
        meta = ["{:,} stars".format(x.get("stars", 0))]
        if x.get("forks"):
            meta.append("{:,} forks".format(x["forks"]))
        if _month(x.get("pushed_at")):
            meta.append("updated " + _month(x["pushed_at"]))
        acts = [a for a in (_link(x.get("live"), "Live site"), _link(x.get("url"), "Code")) if a]
        out.append('<li class="card">\n<h3>%s</h3>\n<p>%s</p>\n' % (e(x.get("title") or x["name"]), e(x.get("blurb"))))
        if x.get("stack"):
            out.append('<p class="muted">%s</p>\n' % e(" · ".join(map(str, x["stack"]))))
        out.append('<p class="meta">%s</p>\n<p>%s</p>\n</li>\n' % (e(" · ".join(meta)), " · ".join(acts)))
    if not shown:
        out.append("<li>No projects selected yet.</li>\n")
    out.append("</ul>\n</section>\n")

    langs = data.get("languages") or []
    if langs:
        label = "Languages: " + ", ".join("%s %s%%" % (k, v) for k, v in langs)
        out.append('<section aria-labelledby="langs"><h2 id="langs">Languages</h2>\n'
                   '<div class="bar" role="img" aria-label="%s">' % e(label))
        out.append("".join('<span style="width:%s%%;background:var(--c%d)"></span>' % (v, i % 7 + 1)
                           for i, (k, v) in enumerate(langs)))
        out.append('</div>\n<ul class="legend">%s</ul>\n</section>\n' % "".join(
            '<li><span class="sw" style="background:var(--c%d)"></span>%s %s%%</li>' % (i % 7 + 1, e(k), e(v))
            for i, (k, v) in enumerate(langs)))

    if data.get("recent"):
        out.append('<section aria-labelledby="recent"><h2 id="recent">Recent activity</h2>\n<ul class="recent">\n')
        for r in data["recent"]:
            out.append('<li>%s <span class="muted">updated %s</span></li>\n'
                       % (_link(r.get("url"), r["name"]) or e(r["name"]), e(_month(r.get("pushed_at")) or "recently")))
        out.append("</ul>\n</section>\n")

    contact = []
    if p.get("email"):
        contact.append('<a href="mailto:%s">%s</a>' % (e(urllib.parse.quote(p["email"], safe="@.+-_")), e(p["email"])))
    contact += [x for x in (_link(p.get("website"), "website"), _link(p.get("github"), "GitHub")) if x]
    out.append('</main>\n<footer>\n<p>Contact: %s</p>\n<p class="muted">Built from public GitHub data%s.</p>\n'
               "</footer>\n</div>\n</body>\n</html>\n"
               % (" · ".join(contact) or e(name),
                  (" on " + e(data["fetched_at"][:10])) if data.get("fetched_at") else ""))
    return "".join(out)


def write_site(data, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    files = {"index.html": render(data), "data.json": json.dumps(data, indent=2, ensure_ascii=False) + "\n",
             ".nojekyll": ""}
    for name, text in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    return [os.path.join(out_dir, n) for n in files]


def publish_instructions(user, out_dir):
    repo = user.lower() + ".github.io"
    return "\n".join([
        "# 1. Create an empty public repository named %s (no README), on github.com or with:" % repo,
        "#      gh repo create %s --public" % repo,
        "#    If it already exists, clone it and copy index.html, data.json and .nojekyll into it instead.",
        "cd %s" % out_dir,
        "git init",
        "git add index.html data.json .nojekyll",
        'git commit -m "Publish portfolio"',
        "git branch -M main",
        "git remote add origin https://github.com/%s/%s.git" % (user, repo),
        "git push -u origin main",
        "# 2. Repository Settings > Pages > Build and deployment: Source 'Deploy from a branch', main, /(root).",
        "# 3. The site appears at https://%s/ (GitHub says changes can take up to 10 minutes)." % repo,
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="Exit codes: 0 ok, 2 user not found / API failure / bad input file.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--user", help="GitHub username to fetch")
    src.add_argument("--from-json", metavar="FILE", help="build from a saved fetch (offline)")
    ap.add_argument("--out", default="site", help="output directory (default: site)")
    ap.add_argument("--top", type=int, default=6, help="number of featured projects (default 6)")
    ap.add_argument("--overrides", metavar="FILE", help="data.json from an earlier run; keeps your edits")
    ap.add_argument("--save-raw", metavar="FILE", help="also save the raw fetch for --from-json later")
    ap.add_argument("--publish-instructions", action="store_true", help="print git commands for USER.github.io")
    ap.add_argument("--json", action="store_true", help="print a JSON summary")
    a = ap.parse_args(argv)
    try:
        if a.publish_instructions:
            if not a.user or not USER_RE.match(a.user):
                raise Fail("--publish-instructions needs a valid --user")
            print(publish_instructions(a.user, a.out))
            return 0
        if a.user:
            raw = fetch(a.user, a.top)
            if a.save_raw:
                with open(a.save_raw, "w", encoding="utf-8") as fh:
                    json.dump(raw, fh, indent=2)
        else:
            raw = _load(a.from_json, "saved fetch")
        overrides = _load(a.overrides, "overrides file") if a.overrides else None
        data = build(raw, overrides, a.top)
        files = write_site(data, a.out)
    except Fail as err:
        print("error: %s" % err, file=sys.stderr)
        return 2
    shown = [x for x in data["projects"] if x["featured"]]
    summary = {"login": data["login"], "out": a.out, "files": files, "featured": [x["name"] for x in shown],
               "other_candidates": [x["name"] for x in data["projects"] if not x["featured"]],
               "languages": data["languages"], "requests": raw.get("requests", 0),
               "partial": bool(raw.get("partial")), "partial_reason": raw.get("partial_reason", "")}
    if a.json:
        print(json.dumps(summary, indent=2))
        return 0
    print("Portfolio for %s written to %s/index.html" % (data["login"], a.out))
    for x in shown:
        print("  * %-28s %4d stars  %s" % (x["name"], x["stars"], (x["blurb"] or "")[:60]))
    if summary["other_candidates"]:
        print("  other candidates: " + ", ".join(summary["other_candidates"][:10]))
    print("  languages: " + ", ".join("%s %s%%" % (k, v) for k, v in data["languages"]))
    if a.user:
        print("  GitHub API requests: %d" % summary["requests"])
    if summary["partial"]:
        print("  note: README/language data is incomplete (%s)" % summary["partial_reason"])
    print("Edit %s/data.json, then re-run with --overrides %s/data.json" % (a.out, a.out))
    return 0


def _load(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as err:
        raise Fail("cannot read %s %s: %s" % (what, path, err))


if __name__ == "__main__":
    sys.exit(main())
