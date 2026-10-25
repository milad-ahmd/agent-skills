"""Per-skill page for the agent-skills site."""
import os

from site_layout import E, REPO, SITE, TODAY, page
from site_md import md_to_html


def skill_page(name, s, skills, packs, out_dir):
    title = f"{name}: {s['tagline']} | Free Agent Skill"
    if len(title) > 95:
        title = f"{name}: Free Agent Skill for Claude Code, Cursor & Codex"
    desc = (s["tagline"] + ". Free open-source SKILL.md for Claude Code, Cursor, Codex and Cline.")[:300]
    has_gif = os.path.exists(os.path.join(out_dir, "assets", f"demo-{name}.gif"))
    img = (f'<p><img class="demo" src="../../assets/demo-{name}.gif" alt="{name} demo with real terminal output" '
           f'loading="lazy" width="760" height="502"></p>') if has_gif else ""
    ex = "".join(f"<li><code>{E(e)}</code></li>" for e in s["examples"])
    related = [n for n, o in skills.items() if o["category"] == s["category"] and n != name][:6]
    rel = "".join(f'<a class="card" href="../{n}/"><h3>{skills[n]["emoji"]} {n}</h3><p>{E(skills[n]["tagline"])}</p></a>' for n in related)
    inpacks = [p for p, v in packs.items() if name in v]
    packs_html = ("<p>Included in packs: " + ", ".join(f"<code>{p}-pack</code>" for p in inpacks) + "</p>") if inpacks else ""
    body = f"""<p class="crumbs"><a href="../../">Agent Skills</a> › <a href="../../#skills">{E(s['category'])}</a> › {name}</p>
<article><span class="tag">{E(s['category'])}</span>
<h1>{s['emoji']} {name}</h1><p class="lead">{E(s['tagline'])}. A free, open-source Agent Skill for Claude Code, Cline, Cursor, Codex and 75+ AI agents.</p>{img}
<h2>Install {name}</h2>
<pre><code># Claude Code (all 51 skills)
/plugin install all-skills --marketplace milad-ahmd/agent-skills

# Cursor, Codex, Cline, Windsurf, Copilot + 75 more agents
npx skills add milad-ahmd/agent-skills --skill {name}

# Shell installer
curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash -s -- {name}</code></pre>{packs_html}
<h2>What to say to your agent</h2><ul>{ex}</ul>
<h2>When it triggers</h2><p>{E(s['description'])}</p>
<h2>Full skill instructions (SKILL.md)</h2><div class="skillmd">{md_to_html(s['body'])}</div>
<p><a class="btn" href="{REPO}/tree/main/skills/{name}">View source on GitHub</a> <a class="btn alt" href="{REPO}">⭐ Star the collection</a></p>
{f'<h2>Related skills</h2><div class="grid">{rel}</div>' if rel else ''}</article>"""
    jsonld = [
        {"@context": "https://schema.org", "@type": "SoftwareSourceCode", "name": name, "description": s["description"],
         "codeRepository": f"{REPO}/tree/main/skills/{name}", "license": "https://opensource.org/licenses/MIT", "dateModified": TODAY,
         "author": {"@type": "Person", "name": "Milad Ahmadi", "url": "https://github.com/milad-ahmd"},
         "keywords": f"agent skill, SKILL.md, claude code skill, cursor, codex, {name}, {s['category']}"},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Agent Skills", "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": name, "item": f"{SITE}/skills/{name}/"}]},
    ]
    return page(title, desc, f"skills/{name}/", body, jsonld)
