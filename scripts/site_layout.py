"""Page layout + CSS for the agent-skills static site."""
import html
import json
from datetime import date

SITE = "https://milad-ahmd.github.io/agent-skills"
REPO = "https://github.com/milad-ahmd/agent-skills"
TODAY = date.today().isoformat()
E = html.escape

CSS = """
:root{--bg:#0f1117;--card:#171a23;--fg:#e8e9ee;--dim:#9aa0ad;--acc:#8b5cf6;--acc2:#22d3ee;--line:#262a36}
*{box-sizing:border-box}body{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--acc2);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px}
header.top{border-bottom:1px solid var(--line);padding:14px 0}header.top .wrap{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.logo{font-weight:800;color:var(--fg);font-size:18px}.nav a{margin-left:16px;color:var(--dim)}
.hero{padding:56px 0 28px;text-align:center}.hero h1{font-size:clamp(30px,5vw,48px);line-height:1.15;margin:0 0 14px}
.hero p{color:var(--dim);font-size:19px;max-width:760px;margin:0 auto 22px}
.btn{display:inline-block;background:var(--acc);color:#fff;padding:12px 20px;border-radius:10px;font-weight:700;margin:4px}
.btn.alt{background:transparent;border:1px solid var(--line);color:var(--fg)}
pre{background:#0b0d12;border:1px solid var(--line);border-radius:10px;padding:14px;overflow:auto;font-size:14px;white-space:pre-wrap;word-break:break-word}
code{font-family:ui-monospace,Menlo,monospace}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;display:block;color:var(--fg)}
.card:hover{border-color:var(--acc);text-decoration:none}.card h3{margin:0 0 6px;font-size:17px}.card p{margin:0;color:var(--dim);font-size:14px}
h2{margin-top:44px}.tag{display:inline-block;font-size:12px;color:var(--acc2);border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin-bottom:8px}
.demo{max-width:100%;height:auto;border-radius:10px;border:1px solid var(--line)}
.crumbs{color:var(--dim);font-size:14px;margin:20px 0 0}article{max-width:820px}
article h1{font-size:clamp(28px,4vw,40px);margin:8px 0}.lead{font-size:19px;color:var(--dim)}
.skillmd h2,.skillmd h3{margin-top:28px}.skillmd table{border-collapse:collapse;width:100%;font-size:14px;display:block;overflow:auto}
.skillmd td,.skillmd th{border:1px solid var(--line);padding:6px 8px;text-align:left}
footer{border-top:1px solid var(--line);margin-top:60px;padding:24px 0;color:var(--dim);font-size:14px}
"""


def page(title, desc, path, body, jsonld=None, image="assets/og.png"):
    url = f"{SITE}/{path}"
    depth = path.count("/")
    pre = "../" * depth
    ld = "".join(f'<script type="application/ld+json">{json.dumps(j, ensure_ascii=False)}</script>' for j in (jsonld or []))
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)}</title><meta name="description" content="{E(desc)}">
<link rel="canonical" href="{url}"><meta name="robots" content="index,follow">
<meta property="og:type" content="website"><meta property="og:title" content="{E(title)}"><meta property="og:description" content="{E(desc)}">
<meta property="og:url" content="{url}"><meta property="og:image" content="{SITE}/{image}"><meta property="og:site_name" content="Agent Skills by Milad Ahmadi">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{E(title)}"><meta name="twitter:description" content="{E(desc)}"><meta name="twitter:image" content="{SITE}/{image}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🧰</text></svg>">
<style>{CSS}</style>{ld}</head><body>
<header class="top"><div class="wrap"><a class="logo" href="{pre or './'}">🧰 Agent Skills</a>
<nav class="nav"><a href="{pre}#skills">Skills</a><a href="{pre}guides/">Guides</a><a href="{pre}#packs">Packs</a><a href="{pre}#install">Install</a><a href="{REPO}">GitHub ⭐</a></nav></div></header>
<main class="wrap">{body}</main>
<footer><div class="wrap">Open source (MIT) by <a href="https://github.com/milad-ahmd">Milad Ahmadi</a> · <a href="{REPO}">GitHub</a> · Works with Claude Code, Cline, Cursor, Codex, Windsurf, Copilot and any agent that reads SKILL.md · Updated {TODAY}</div></footer>
</body></html>"""
