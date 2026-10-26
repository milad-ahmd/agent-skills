"""Home page for the agent-skills site."""
import re

from site_layout import E, REPO, SITE, page

ORDER = ["Fun & Viral", "Social Platforms", "Marketing", "Agent Productivity", "Founders", "Growth", "Writing", "DevOps",
         "Git & Workflow", "Testing & Quality", "Security & Maintenance", "Backend & Data", "Frontend", "Refactoring", "Documentation", "Other"]

FAQ = [
    ("What are Agent Skills?", "Agent Skills are folders with a SKILL.md file: a YAML name and description, then instructions and optional scripts. AI agents like Claude Code load a skill automatically when your request matches its description, so they follow a proven workflow instead of improvising."),
    ("Which AI agents do these skills work with?", "Claude Code natively, through its plugin marketplace. Cursor, Codex, Cline, Windsurf, GitHub Copilot, Gemini CLI, OpenCode and 75+ other agents work through npx skills. Any agent can also use a SKILL.md pasted into its rules file."),
    ("How do I install all skills in Claude Code?", "Run /plugin install all-skills --marketplace milad-ahmd/agent-skills inside Claude Code, or install a role pack such as creator-pack or startup-cto-pack."),
    ("Is there an AI agent skill for Instagram, TikTok and YouTube?", "Yes: instagram-growth-manager, tiktok-viral-creator, youtube-seo-optimizer and youtube-script-writer. social-media-manager coordinates them along with the LinkedIn, X, Facebook, Pinterest, Threads, Reddit, Telegram, WhatsApp and Google Business Profile agents."),
    ("Are the skills free?", "Yes. All 51 skills are open source under the MIT license. They are plain Markdown with optional Python scripts that use only the standard library, and they need no paid API."),
]


def slug(c):
    return re.sub(r"[^a-z]+", "-", c.lower()).strip("-")


def build_home(skills, packs, guides=()):
    cats = {}
    for n, s in skills.items():
        cats.setdefault(s["category"], []).append(n)
    secs = ""
    for c in sorted(cats, key=lambda c: ORDER.index(c) if c in ORDER else 99):
        cards = "".join(f'<a class="card" href="skills/{n}/"><h3>{skills[n]["emoji"]} {n}</h3><p>{E(skills[n]["tagline"])}</p></a>' for n in sorted(cats[c]))
        secs += f'<h2 id="{slug(c)}">{E(c)}</h2><div class="grid">{cards}</div>'
    packrows = "".join(
        f'<div class="card"><h3>🎒 {p}-pack</h3><p>{E(", ".join(v[:6]))}{"…" if len(v) > 6 else ""}</p>'
        f'<p><code>/plugin install {p}-pack --marketplace milad-ahmd/agent-skills</code></p></div>' for p, v in packs.items())
    guides_html = ""
    if guides:
        gcards = "".join(f'<a class="card" href="guides/{g["slug"]}/"><h3>📘 {E(g["title"])}</h3><p>{E(g["description"][:140])}…</p></a>' for g in guides)
        guides_html = f'<h2 id="guides">Guides</h2><div class="grid">{gcards}</div>'
    faqhtml = "".join(f"<h3>{E(q)}</h3><p>{E(a)}</p>" for q, a in FAQ)
    body = f"""<section class="hero"><h1>51 Agent Skills for Claude Code, Cursor &amp; Codex</h1>
<p>Free, open-source SKILL.md workflows: an AI agent for every social platform (Instagram, TikTok, YouTube, LinkedIn, X), senior-engineer dev workflows, and fun ones like roast-my-github.</p>
<a class="btn" href="#install">Install in 10 seconds</a><a class="btn alt" href="{REPO}">⭐ Star on GitHub</a>
<p style="margin-top:26px"><img class="demo" src="assets/demo-install.gif" alt="Installing agent skills with one command" width="760" height="502"></p></section>
<h2 id="install">Install</h2>
<pre><code># Claude Code
/plugin install all-skills --marketplace milad-ahmd/agent-skills

# Cursor, Codex, Cline, Windsurf, Copilot + 75 more agents
npx skills add milad-ahmd/agent-skills

# Shell (any agent that reads ~/.claude/skills)
curl -fsSL https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh | bash</code></pre>
{guides_html}
<h2 id="packs">Packs: install by who you are</h2><div class="grid">{packrows}</div>
<div id="skills"></div>{secs}
<h2>FAQ</h2><div class="faq">{faqhtml}</div>"""
    jsonld = [
        {"@context": "https://schema.org", "@type": "SoftwareSourceCode", "name": "Agent Skills", "url": SITE + "/", "codeRepository": REPO,
         "description": "51 open-source Agent Skills (SKILL.md) for Claude Code, Cursor, Codex and Cline", "license": "https://opensource.org/licenses/MIT",
         "author": {"@type": "Person", "name": "Milad Ahmadi", "url": "https://github.com/milad-ahmd"}},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in FAQ]},
        {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": f"{SITE}/skills/{n}/", "name": n} for i, n in enumerate(skills)]},
    ]
    return page("51 Agent Skills for Claude Code, Cursor & Codex (Free SKILL.md Collection)",
                "Free open-source Agent Skills (SKILL.md) for Claude Code, Cursor, Codex and Cline: Instagram, TikTok, YouTube and LinkedIn agents, PR review, secret scanning, roast-my-github and more.",
                "", body, jsonld)
