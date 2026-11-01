#!/usr/bin/env python3
"""Find the agent skill that fits a task, and show whether it is already installed. Stdlib only.

Usage:
  python3 find_skill.py "TASK DESCRIPTION" [--top 5] [--min-score 2.0] [--json] [--project DIR]
  python3 find_skill.py --installed [--json] [--project DIR]
  python3 find_skill.py --build-index SKILLS_DIR [--catalog CATALOG.tsv] [--out index.json]

Search ranks the skills in index.json (next to this script, or --index FILE) with BM25 over each
skill's name, tagline, description, category and example prompts. Every result shows the install
commands and, if the skill is already installed, where. Nothing is installed by this script.

--installed lists SKILL.md folders found in the directories the `skills` CLI (vercel-labs/skills)
installs to for Claude Code, Cursor, Codex, Cline, Windsurf, Gemini CLI, GitHub Copilot and
OpenCode, globally (under $HOME) and in the project (--project, default: current directory),
plus Claude Code plugin skills under ~/.claude/plugins/cache (may include stale versions).

--build-index reads SKILLS_DIR/*/SKILL.md frontmatter (name, description) and, with --catalog,
a TSV with columns name, emoji, tagline, category, examples (separated by |). Output is sorted
and has no timestamp, so rebuilding an unchanged collection gives an identical file.

Exit codes: 0 results found (or index written, or installed skills listed)
            1 no skill scored above --min-score (prints an `npx skills find` query to try),
              or --installed found nothing
            2 usage error, or the index or skills directory can't be read
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INDEX = os.path.join(HERE, "index.json")
SELF = "skill-finder"
REPO = "milad-ahmd/agent-skills"
CURL = ("curl -fsSL -o /tmp/agent-skills-install.sh https://raw.githubusercontent.com/milad-ahmd/agent-skills/main/install.sh"
        " && bash /tmp/agent-skills-install.sh {name}")
PLUGIN = "/plugin install all-skills --marketplace milad-ahmd/agent-skills"
NPX = "npx skills add milad-ahmd/agent-skills --skill {name}"

# Skill folders per agent, from the vercel-labs/skills docs/agents.md table (global, then project).
GLOBAL_DIRS = [
    ("claude-code", ".claude/skills"),
    ("codex/cline (shared)", ".agents/skills"),
    ("universal", ".config/agents/skills"),
    ("cursor", ".cursor/skills"),
    ("windsurf", ".codeium/windsurf/skills"),
    ("gemini-cli", ".gemini/skills"),
    ("github-copilot", ".copilot/skills"),
    ("opencode", ".config/opencode/skills"),
]
PROJECT_DIRS = [
    ("claude-code (project)", ".claude/skills"),
    ("agents (project)", ".agents/skills"),
    ("windsurf (project)", ".windsurf/skills"),
]
PLUGIN_CACHE = ".claude/plugins/cache"

STOP = set("""a an the and or but to of for in on at by with from into onto as is are was were be been being am
it its it's this that these those there here my me mine we us our you your yours i i'm im i've ive he she they them
their what how why who which where when do does did doing done can could would should will shall may might must
have has had get got make made want wanted need needs needed please help helps thanks thank hey hi so if than then
just like some any all also not no yes very really about more most up out over again only own same too
find write before after
skill skills agent claude something thing things way use using used one way""".split())

# A few words users type that rarely appear in skill descriptions. Expansions count at half weight.
ALIASES = {
    "rebase": "git commit history", "merge": "git", "conflict": "git", "squash": "git commit history",
    "pr": "pull request review", "k8s": "kubernetes", "slow": "performance optimize", "leak": "secret",
    "vuln": "vulnerability security", "cve": "vulnerability security", "ci": "github actions workflow",
    "deploy": "devops", "bill": "cost", "tweet": "twitter social", "docs": "documentation", "ui": "frontend",
    "flaky": "test", "regression": "bug", "crash": "bug debug", "error": "bug debug", "outage": "incident",
    "token": "cost", "wordy": "verbose", "verbose": "concise", "shorter": "concise",
}
FIELD_WEIGHTS = (("name", 3), ("tagline", 2), ("examples", 2), ("description", 1), ("category", 1))
K1, B = 1.2, 0.75


# --- frontmatter -----------------------------------------------------------------------
def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        inner = v[1:-1]
        return inner.replace("''", "'") if v[0] == "'" else inner.replace('\\"', '"')
    return v


def parse_frontmatter(text):
    """Top-level scalars of a SKILL.md YAML header: plain, quoted, folded (>) and literal (|).

    Returns a dict, or None when the file has no `---` header."""
    text = text.lstrip("\ufeff")
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not m:
        return None
    lines, out, i = m.group(1).splitlines(), {}, 0
    while i < len(lines):
        km = re.match(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$", lines[i])
        i += 1
        if not km:
            continue
        key, val = km.group(1), km.group(2).strip()
        block = []
        while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
            block.append(lines[i])
            i += 1
        while block and not block[-1].strip():
            block.pop()
        if re.match(r"^[>|][+-]?\d*\s*(#.*)?$", val):
            body = [ln.strip() for ln in block]
            if val.startswith("|"):
                out[key] = "\n".join(body).strip()
            else:  # folded: lines join with spaces, blank lines become newlines
                paras, cur = [], []
                for ln in body:
                    if ln:
                        cur.append(ln)
                    else:
                        paras.append(" ".join(cur))
                        cur = []
                paras.append(" ".join(cur))
                out[key] = "\n".join(p for p in paras if p).strip()
        else:  # plain or quoted, possibly continued on indented lines
            joined = " ".join([val] + [ln.strip() for ln in block if ln.strip()]).strip()
            out[key] = _unquote(joined)
    return out


# --- catalog and index -----------------------------------------------------------------
def read_catalog(path):
    """{name: {emoji, tagline, category, examples}} from a TSV (no header row; # lines ignored)."""
    rows = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            cols += [""] * (5 - len(cols))
            name = cols[0].strip()
            if name:
                rows[name] = {"emoji": cols[1].strip(), "tagline": cols[2].strip(), "category": cols[3].strip(),
                              "examples": [e.strip() for e in cols[4].split("|") if e.strip()]}
    return rows


def build_index(skills_dir, catalog=None):
    """Returns (index dict, warnings list). Raises OSError if skills_dir can't be listed."""
    catalog = catalog or {}
    skills, warnings = [], []
    for folder in sorted(os.listdir(skills_dir)):
        path = os.path.join(skills_dir, folder, "SKILL.md")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            fm = parse_frontmatter(fh.read())
        if not fm or not fm.get("description"):
            warnings.append(f"{folder}: no frontmatter description, skipped")
            continue
        name = fm.get("name") or folder
        if name != folder:
            warnings.append(f"{folder}: frontmatter name is '{name}', using it")
        row = catalog.get(name, {})
        if catalog and not row:
            warnings.append(f"{name}: not in the catalog, indexed from SKILL.md only")
        skills.append({"name": name, "emoji": row.get("emoji", ""), "tagline": row.get("tagline", ""),
                       "category": row.get("category", ""), "description": fm["description"],
                       "examples": row.get("examples", [])})
    names = {s["name"] for s in skills}
    for name in sorted(set(catalog) - names):
        warnings.append(f"{name}: in the catalog but has no SKILL.md, left out")
    return {"source": REPO, "count": len(skills), "skills": skills}, warnings


def load_index(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise ValueError("expected an object with a 'skills' list")
    return data["skills"]


# --- ranking ---------------------------------------------------------------------------
def stem(w):
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("es") and len(w) > 4 and w[-3] in "sxz":
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in "aeiouls":
                w = w[:-1]
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def tokens(text):
    words = re.findall(r"[a-z0-9][a-z0-9+#.]*[a-z0-9+#]|[a-z0-9]", (text or "").lower())
    out = []
    for w in words:
        for part in re.split(r"[.]", w):
            if len(part) > 1 and part not in STOP:
                st = stem(part)
                if st not in _STOP_STEMS:
                    out.append(st)
    return out


_STOP_STEMS = {stem(w) for w in STOP}


def doc_tokens(skill):
    toks = []
    for field, weight in FIELD_WEIGHTS:
        val = skill.get(field) or ""
        if isinstance(val, list):
            val = " ".join(val)
        if field == "name":
            val = val.replace("-", " ")
        toks += tokens(val) * weight
    return toks


def query_terms(query):
    """[(term, weight, origin)]: the query's own terms (weight 1) plus alias expansions (0.5)."""
    aliases = {stem(k): v for k, v in ALIASES.items()}
    own = list(dict.fromkeys(tokens(query)))
    terms = [(t, 1.0, t) for t in own]
    seen = set(own)
    for t in own:
        for x in tokens(aliases.get(t, "")):
            if x not in seen:
                seen.add(x)
                terms.append((x, 0.5, t))
    return terms, own


def rank(query, skills, exclude=(SELF,)):
    """[(score, skill, matched_terms, covered, n_terms)] sorted best first.

    Score is BM25 over the weighted fields. `covered` counts the query's own terms that matched
    directly or through an alias, out of `n_terms`."""
    pool = [s for s in skills if s.get("name") not in exclude]
    docs = [Counter(doc_tokens(s)) for s in pool]
    terms, own = query_terms(query)
    if not docs or not own:
        return []
    n = len(docs)
    avg = sum(sum(d.values()) for d in docs) / n or 1.0
    df = {t: sum(1 for d in docs if t in d) for t, _, _ in terms}
    out = []
    for s, d in zip(pool, docs):
        length, score, hit, covered = sum(d.values()), 0.0, [], set()
        for t, weight, origin in terms:
            tf = d.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += weight * idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avg))
            hit.append(t)
            covered.add(origin)
        out.append((round(score, 2), s, hit, len(covered), len(own)))
    out.sort(key=lambda r: (-r[0], r[1]["name"]))
    return out


def good_enough(result, min_score, query=""):
    """Above the score floor, and either covering two of the query's terms (or half of them) or
    matching a word of the skill's name. One generic shared word ("check", "plan") is not a match."""
    score, skill, _, covered, n_terms = result
    if score < min_score:
        return False
    if covered >= 2 or covered * 2 >= n_terms:
        return True
    name_terms = tokens(skill.get("name", "").replace("-", " "))
    return any(q == n or (len(q) >= 4 and n.startswith(q)) for q in tokens(query) for n in name_terms)


def npx_query(query, limit=4):
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9+#.-]*", query.lower()) if w not in STOP and len(w) > 1]
    return " ".join(list(dict.fromkeys(words))[:limit]) or "<keywords>"


# --- installed skills ------------------------------------------------------------------
def _skill_name(skill_dir):
    try:
        with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8", errors="replace") as fh:
            fm = parse_frontmatter(fh.read(4096)) or {}
    except OSError:
        fm = {}
    return fm.get("name") or os.path.basename(skill_dir.rstrip(os.sep))


def find_installed(home=None, project=None):
    """[{name, agent, path}] for every <dir>/<skill>/SKILL.md under the known skill folders."""
    home = home or os.path.expanduser("~")
    project = os.path.abspath(project or os.getcwd())
    roots = [(agent, os.path.join(home, rel)) for agent, rel in GLOBAL_DIRS]
    if os.path.realpath(project) != os.path.realpath(home):
        roots += [(agent, os.path.join(project, rel)) for agent, rel in PROJECT_DIRS]
    found, seen = [], set()
    for agent, root in roots:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            d = os.path.join(root, entry)
            if os.path.isfile(os.path.join(d, "SKILL.md")):
                key = (os.path.realpath(d), agent)
                if key not in seen:
                    seen.add(key)
                    found.append({"name": _skill_name(d), "agent": agent, "path": d})
    cache = os.path.join(home, PLUGIN_CACHE)
    if os.path.isdir(cache):
        for dirpath, dirnames, filenames in os.walk(cache):
            dirnames[:] = sorted(x for x in dirnames if not x.startswith(".") and x != "node_modules")
            if dirpath[len(cache):].count(os.sep) > 7:
                dirnames[:] = []
            if "SKILL.md" in filenames and os.path.basename(os.path.dirname(dirpath)) == "skills":
                found.append({"name": _skill_name(dirpath), "agent": "claude-code plugin", "path": dirpath})
    return found


def short(path, home=None):
    home = home or os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


# --- CLI -------------------------------------------------------------------------------
def install_commands(name):
    return {"curl": CURL.format(name=name), "claude_plugin": PLUGIN, "npx": NPX.format(name=name)}


def cmd_search(args):
    try:
        skills = load_index(args.index)
    except (OSError, ValueError) as e:
        print(f"error: can't read index {args.index}: {e}", file=sys.stderr)
        return 2
    if not tokens(args.query):
        print("error: the task description has no searchable words", file=sys.stderr)
        return 2
    installed = {}
    for item in find_installed(project=args.project):
        installed.setdefault(item["name"], []).append(item)
    ranked = [r for r in rank(args.query, skills) if good_enough(r, args.min_score, args.query)][:args.top]
    results = [{"name": s["name"], "emoji": s.get("emoji", ""), "score": score,
                "summary": s.get("tagline") or s["description"].split(". ")[0].rstrip(".") + ".",
                "category": s.get("category", ""), "matched": hit,
                "installed": [{"agent": i["agent"], "path": i["path"]} for i in installed.get(s["name"], [])],
                "install": install_commands(s["name"])} for score, s, hit, _, _ in ranked]
    suggestion = f"npx skills find {npx_query(args.query)}"
    if args.json:
        print(json.dumps({"query": args.query, "results": results,
                          "ecosystem_search": None if results else suggestion}, indent=2))
        return 0 if results else 1
    if not results:
        print(f'No skill in this collection matches "{args.query}" well (min score {args.min_score}).')
        print("Search the wider ecosystem (needs Node.js; review a skill before installing it):")
        print(f"  {suggestion}")
        return 1
    print(f'Skills for "{args.query}":\n')
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['name']}  (score {r['score']}; matched: {', '.join(r['matched'])})")
        print(f"   {r['summary']}")
        if r["installed"]:
            print("   installed: " + "; ".join(f"{short(x['path'])} [{x['agent']}]" for x in r["installed"]))
        else:
            print(f"   install:  {r['install']['curl']}")
            print(f"   or:       {r['install']['npx']}")
        print()
    print(f"Claude Code, whole collection: {PLUGIN}")
    return 0


def cmd_installed(args):
    found = find_installed(project=args.project)
    if args.json:
        print(json.dumps(found, indent=2))
    elif not found:
        print("No installed skills found in the known skill folders.")
    else:
        width = max(len(f["name"]) for f in found)
        for f in found:
            print(f"{f['name']:<{width}}  {f['agent']:<22}  {short(f['path'])}")
        print(f"\n{len(found)} skill folder(s)")
    return 0 if found else 1


def cmd_build(args):
    catalog = {}
    try:
        if args.catalog:
            catalog = read_catalog(args.catalog)
        index, warnings = build_index(args.build_index, catalog)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not index["skills"]:
        print(f"error: no SKILL.md files under {args.build_index}", file=sys.stderr)
        return 2
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {args.out}: {index['count']} skills")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="Exit codes: 0 results · 1 nothing above --min-score · 2 error",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="the task, in the user's words")
    ap.add_argument("--top", type=int, default=5, help="results to show (default 5)")
    ap.add_argument("--min-score", type=float, default=2.0, help="drop results below this BM25 score (default 2.0)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--index", default=DEFAULT_INDEX, help="index file (default: index.json next to this script)")
    ap.add_argument("--project", default=None, help="project dir for project-level skill folders (default: cwd)")
    ap.add_argument("--installed", action="store_true", help="list installed skills and where they are")
    ap.add_argument("--build-index", metavar="SKILLS_DIR", help="regenerate the index from SKILLS_DIR/*/SKILL.md")
    ap.add_argument("--catalog", help="TSV: name, emoji, tagline, category, examples (| separated)")
    ap.add_argument("--out", default=DEFAULT_INDEX, help="where --build-index writes (default: the index path)")
    args = ap.parse_args(argv)
    modes = sum(bool(x) for x in (args.query, args.installed, args.build_index))
    if modes != 1:
        ap.print_usage(sys.stderr)
        print("error: give a task description, --installed, or --build-index SKILLS_DIR", file=sys.stderr)
        return 2
    if args.top < 1:
        print("error: --top must be at least 1", file=sys.stderr)
        return 2
    if args.build_index:
        return cmd_build(args)
    if args.installed:
        return cmd_installed(args)
    return cmd_search(args)


if __name__ == "__main__":
    sys.exit(main())
