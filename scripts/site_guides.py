"""Guides (tutorial articles) for the agent-skills site."""
import os
import re

from site_layout import E, REPO, SITE, page
from site_md import md_to_html


def load_guides(root):
    gdir = os.path.join(root, "guides")
    guides = []
    if not os.path.isdir(gdir):
        return guides
    for f in sorted(os.listdir(gdir)):
        if not f.endswith(".md"):
            continue
        text = open(os.path.join(gdir, f), encoding="utf-8").read()
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        fm = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)}
        guides.append({"slug": f[:-3], "title": fm["title"], "description": fm["description"], "date": fm.get("date", ""), "body": m.group(2)})
    return sorted(guides, key=lambda g: g["date"], reverse=True)


def guide_page(g):
    body = f"""<p class="crumbs"><a href="../../">Agent Skills</a> › <a href="../">Guides</a> › {E(g['title'])}</p>
<article><span class="tag">Guide · {E(g['date'])}</span>
<h1>{E(g['title'])}</h1><p class="lead">{E(g['description'])}</p>
<p style="color:var(--dim);font-size:14px">By <a href="https://github.com/milad-ahmd">Milad Ahmadi</a></p>
<div class="skillmd">{md_to_html(g['body'])}</div>
<p><a class="btn" href="../../">Browse all 51 skills</a> <a class="btn alt" href="{REPO}">⭐ Star on GitHub</a></p></article>"""
    jsonld = [{"@context": "https://schema.org", "@type": "TechArticle", "headline": g["title"], "description": g["description"],
               "datePublished": g["date"], "dateModified": g["date"], "url": f"{SITE}/guides/{g['slug']}/",
               "author": {"@type": "Person", "name": "Milad Ahmadi", "url": "https://github.com/milad-ahmd"},
               "publisher": {"@type": "Person", "name": "Milad Ahmadi"}},
              {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                  {"@type": "ListItem", "position": 1, "name": "Agent Skills", "item": SITE + "/"},
                  {"@type": "ListItem", "position": 2, "name": "Guides", "item": SITE + "/guides/"},
                  {"@type": "ListItem", "position": 3, "name": g["title"], "item": f"{SITE}/guides/{g['slug']}/"}]}]
    return page(f"{g['title']} | Agent Skills Guide", g["description"], f"guides/{g['slug']}/", body, jsonld)


def guides_index(guides):
    cards = "".join(f'<a class="card" href="{g["slug"]}/"><h3>{E(g["title"])}</h3><p>{E(g["description"])}</p></a>' for g in guides)
    body = f"""<section class="hero"><h1>Agent Skills Guides</h1>
<p>Practical tutorials on writing, testing and using Agent Skills (SKILL.md) with Claude Code, Cursor, Codex and Cline.</p></section>
<div class="grid">{cards}</div>"""
    return page("Agent Skills Guides: Tutorials for Claude Code Skills (SKILL.md)",
                "Practical tutorials on writing, testing and using Agent Skills (SKILL.md) for Claude Code, Cursor, Codex and Cline.",
                "guides/", body)
