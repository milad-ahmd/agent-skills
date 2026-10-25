#!/usr/bin/env python3
"""Extract story-worthy facts from git history (stdlib only).

Usage: python3 git_timeline.py [repo_path] [--max N]
"""
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime

SEP = "\x1f"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    repo = args[0] if args else "."
    mx = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 20000
    raw = git(repo, "log", "--no-merges", f"--max-count={mx}", "--numstat", "--date=iso-strict",
              f"--pretty=format:@@{SEP}%h{SEP}%an{SEP}%ad{SEP}%s")
    commits, cur = [], None
    for line in raw.splitlines():
        if line.startswith("@@" + SEP):
            _, h, a, d, s = line.split(SEP, 4)
            cur = {"h": h, "a": a, "d": datetime.fromisoformat(d.replace("Z", "+00:00")), "s": s, "files": [], "churn": 0}
            commits.append(cur)
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                ad, de, f = parts
                n = (int(ad) if ad.isdigit() else 0) + (int(de) if de.isdigit() else 0)
                cur["files"].append(f)
                cur["churn"] += n
    if not commits:
        sys.exit("No commits found.")
    commits.sort(key=lambda c: c["d"])
    first, last = commits[0], commits[-1]
    years = Counter(c["d"].year for c in commits)
    authors = Counter(c["a"] for c in commits)
    span = defaultdict(lambda: [None, None])
    for c in commits:
        s = span[c["a"]]
        s[0] = s[0] or c["d"]
        s[1] = c["d"]
    files = Counter(f for c in commits for f in c["files"])
    gaps = [(commits[i + 1]["d"] - commits[i]["d"], commits[i], commits[i + 1]) for i in range(len(commits) - 1)]
    gap = max(gaps, key=lambda g: g[0]) if gaps else None
    twists = [c for c in commits if any(k in c["s"].lower() for k in ("revert", "hotfix", "rollback", "urgent"))]

    print(f"📜 {len(commits)} commits · {first['d']:%Y-%m-%d} → {last['d']:%Y-%m-%d} · {len(authors)} authors")
    print(f"🌱 first: {first['h']} \"{first['s']}\" by {first['a']}")
    print(f"📍 latest: {last['h']} \"{last['s']}\"")
    peak = max(years.values())
    print("📈 commits per year:")
    for y in sorted(years):
        print(f"   {y} {'█' * max(1, round(28 * years[y] / peak))} {years[y]}")
    print("🎭 cast (top authors):")
    for a, n in authors.most_common(5):
        print(f"   {a:<22} {n:>5} commits  {span[a][0]:%Y-%m} → {span[a][1]:%Y-%m}")
    print("💥 biggest commits:")
    for c in sorted(commits, key=lambda c: c["churn"], reverse=True)[:3]:
        print(f"   {c['h']} {c['churn']:>7} lines  \"{c['s'][:50]}\"")
    print("👻 cursed files (most changed):")
    for f, n in files.most_common(5):
        print(f"   {n:>4}×  {f}")
    if gap:
        print(f"🌙 longest silence: {gap[0].days} days ({gap[1]['d']:%Y-%m-%d} → {gap[2]['d']:%Y-%m-%d})")
    print(f"🌪  plot twists (revert/hotfix): {len(twists)}" + (f", e.g. {twists[-1]['h']} \"{twists[-1]['s'][:45]}\"" if twists else ""))


if __name__ == "__main__":
    main()
