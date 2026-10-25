#!/usr/bin/env python3
"""Build the SEO static site (GitHub Pages) into ./docs. Stdlib only.

Usage: python3 scripts/build_site.py
Inputs: skills/*/SKILL.md, scripts/catalog.tsv, install.sh (packs), assets/*.gif
"""
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from site_home import build_home  # noqa: E402
from site_layout import SITE, TODAY, page  # noqa: E402
from site_skill import skill_page  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs")


def load():
    cat = {}
    for line in open(os.path.join(ROOT, "scripts", "catalog.tsv"), encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 5:
            cat[p[0]] = {"emoji": p[1], "tagline": p[2], "category": p[3], "examples": p[4].split("|")}
    lst = subprocess.run(["bash", os.path.join(ROOT, "install.sh"), "--list"], capture_output=True, text=True).stdout
    packs = {l[5:].split(":", 1)[0].strip(): l.split(":", 1)[1].split() for l in lst.splitlines() if l.startswith("pack ")}
    skills = {}
    for name in sorted(os.listdir(os.path.join(ROOT, "skills"))):
        f = os.path.join(ROOT, "skills", name, "SKILL.md")
        if not os.path.isfile(f):
            continue
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", open(f, encoding="utf-8").read(), re.S)
        fm = dict(x.split(":", 1) for x in m.group(1).splitlines() if ":" in x)
        info = cat.get(name, {"emoji": "🧩", "tagline": fm["description"].strip()[:110], "category": "Other", "examples": []})
        skills[name] = {**info, "description": fm["description"].strip(), "body": m.group(2)}
    return skills, packs


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    skills, packs = load()
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "assets"))
    for g in os.listdir(os.path.join(ROOT, "assets")):
        shutil.copy(os.path.join(ROOT, "assets", g), os.path.join(OUT, "assets", g))
    for name, s in skills.items():
        write(os.path.join(OUT, "skills", name, "index.html"), skill_page(name, s, skills, packs, OUT))
    write(os.path.join(OUT, "index.html"), build_home(skills, packs))
    write(os.path.join(OUT, "404.html"), page("Page not found | Agent Skills", "Page not found.", "404.html",
          '<section class="hero"><h1>404</h1><p><a href="/agent-skills/">Browse all 51 agent skills →</a></p></section>'))
    write(os.path.join(OUT, ".nojekyll"), "")
    write(os.path.join(OUT, "robots.txt"), f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n")
    urls = [SITE + "/"] + [f"{SITE}/skills/{n}/" for n in skills]
    write(os.path.join(OUT, "sitemap.xml"), '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join(f"  <url><loc>{u}</loc><lastmod>{TODAY}</lastmod><changefreq>weekly</changefreq></url>\n" for u in urls) + "</urlset>\n")
    llms = ["# Agent Skills by Milad Ahmadi", "",
            "> 51 open-source Agent Skills (SKILL.md) for Claude Code, Cursor, Codex, Cline and other AI agents. "
            "Install: `/plugin install all-skills --marketplace milad-ahmd/agent-skills` or `npx skills add milad-ahmd/agent-skills`.", "", "## Skills", ""]
    llms += [f"- [{n}]({SITE}/skills/{n}/): {s['tagline']}" for n, s in skills.items()]
    write(os.path.join(OUT, "llms.txt"), "\n".join(llms) + "\n")
    key = open(os.path.join(ROOT, "scripts", "indexnow.key")).read().strip()
    write(os.path.join(OUT, f"{key}.txt"), key)
    write(os.path.join(OUT, "urls.txt"), "\n".join(urls) + "\n")
    print(f"built {len(skills)} skill pages + home -> docs/ ({len(urls)} URLs)")


if __name__ == "__main__":
    main()
