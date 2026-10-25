#!/usr/bin/env python3
"""Collect public GitHub profile facts for a roast (stdlib only).

Usage: python3 github_profile.py <username> [--json]
Env:   GITHUB_TOKEN or GH_TOKEN (optional, raises rate limit)
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

API = "https://api.github.com"
LAZY = re.compile(r"^(update|fix|wip|changes?|stuff|test|asdf+|\.+|minor|misc|commit|final( final)?|ok|done|tmp|temp)\b\.?$", re.I)


def get(path):
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "roast-my-github"})
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(f"User not found: {path}")
        if e.code == 403:
            sys.exit("GitHub rate limit hit. Set GITHUB_TOKEN and retry.")
        raise


def age_days(iso):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days


def collect(user):
    u = get(f"/users/{user}")
    repos = []
    for page in range(1, 11):
        batch = get(f"/users/{user}/repos?per_page=100&page={page}&type=owner")
        repos += batch
        if len(batch) < 100:
            break
    own = [r for r in repos if not r["fork"]]
    events = get(f"/users/{user}/events/public?per_page=100")
    msgs = [c["message"].splitlines()[0] for e in events if e["type"] == "PushEvent" for c in e["payload"].get("commits", [])]
    lazy = [m for m in msgs if LAZY.match(m.strip()) or len(m.strip()) < 6]
    langs = Counter(r["language"] for r in own if r["language"])
    stale = [r for r in own if age_days(r["pushed_at"]) > 365 and not r["archived"]]
    top = sorted(own, key=lambda r: r["stargazers_count"], reverse=True)[:5]
    return {
        "login": u["login"], "name": u.get("name"), "bio": u.get("bio"), "blog": u.get("blog"),
        "followers": u["followers"], "following": u["following"],
        "account_age_years": round(age_days(u["created_at"]) / 365, 1),
        "public_repos": u["public_repos"], "own_repos": len(own), "forks": len(repos) - len(own),
        "total_stars": sum(r["stargazers_count"] for r in own),
        "top_repos": [{"name": r["name"], "stars": r["stargazers_count"], "desc": r["description"]} for r in top],
        "languages": langs.most_common(6),
        "no_description": sum(1 for r in own if not r["description"]),
        "no_license": sum(1 for r in own if not r["license"]),
        "no_topics": sum(1 for r in own if not r.get("topics")),
        "stale_over_1y": len(stale), "archived": sum(1 for r in own if r["archived"]),
        "zero_star_repos": sum(1 for r in own if r["stargazers_count"] == 0),
        "has_profile_readme_repo": any(r["name"].lower() == u["login"].lower() for r in own),
        "recent_commit_msgs": len(msgs), "lazy_commit_msgs": len(lazy), "lazy_examples": lazy[:5],
    }


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = collect(sys.argv[1])
    if "--json" in sys.argv:
        print(json.dumps(d, indent=2))
        return
    pct = lambda n: f"{n}/{d['own_repos']}"
    print(f"👤 {d['login']} ({d['name'] or 'no name'}): {d['account_age_years']}y on GitHub")
    print(f"   bio: {d['bio'] or '— none —'} | site: {d['blog'] or '— none —'} | profile README: {'yes' if d['has_profile_readme_repo'] else 'no'}")
    print(f"👥 followers {d['followers']} · following {d['following']}")
    print(f"📦 repos {d['own_repos']} own + {d['forks']} forks · ⭐ {d['total_stars']} stars total")
    print(f"🧹 no description {pct(d['no_description'])} · no license {pct(d['no_license'])} · no topics {pct(d['no_topics'])}")
    print(f"🪦 untouched >1 year {pct(d['stale_over_1y'])} · zero-star {pct(d['zero_star_repos'])} · archived {d['archived']}")
    print("🗣  languages: " + ", ".join(f"{l} ({n})" for l, n in d["languages"]))
    print("🏆 top: " + ", ".join(f"{r['name']} ★{r['stars']}" for r in d["top_repos"]))
    if d["recent_commit_msgs"]:
        ex = ", ".join(f'"{m}"' for m in d["lazy_examples"][:3])
        print(f"✍️  lazy commit msgs {d['lazy_commit_msgs']}/{d['recent_commit_msgs']} recent" + (f" e.g. {ex}" if ex else ""))


if __name__ == "__main__":
    main()
