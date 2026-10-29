#!/usr/bin/env python3
"""Audit Agent Skills directories (SKILL.md). Stdlib only.

Usage: python3 skills_doctor.py [--strict] [--overlap 0.20] [--format text|github] [skills_dir ...]
Defaults: ~/.claude/skills and ./.claude/skills
--format github: issues become GitHub Actions annotations (::error file=…,line=…::msg); if
GITHUB_STEP_SUMMARY is set, a markdown summary is appended to it.

Exit codes: 0 clean · 1 errors · 2 warnings or overlaps only (with --strict)
"""
import argparse
import itertools
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skill_checks import NAME_RX, find_secrets, jaccard, parse_frontmatter, trigger_clause  # noqa: E402


SELF_FILES = {"skill_checks.py", "test_skill_checks.py"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
SKIP_EXT = (".pyc", ".pyo", ".so", ".dylib", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2")


def line_of(text, rx, default=1):
    m = re.search(rx, text, re.M)
    return text.count("\n", 0, m.start()) + 1 if m else default


def check(skill_dir):
    """Return (folder, description, errors, warnings, (SKILL.md path, description line)); issues are (msg, path, line)."""
    errs, warns = [], []
    folder = os.path.basename(skill_dir.rstrip("/"))
    md = os.path.join(skill_dir, "SKILL.md")
    text = open(md, encoding="utf-8", errors="replace").read()
    fm, body = parse_frontmatter(text)
    desc = ""
    name_ln, desc_ln = line_of(text, r"^name\s*:", 2), line_of(text, r"^description\s*:", 2)
    if fm is None:
        errs.append(("missing or unclosed YAML frontmatter (--- ... ---)", md, 1))
    else:
        name, desc = fm.get("name", ""), fm.get("description", "")
        if not name:
            errs.append(("frontmatter has no `name`", md, 1))
        elif not NAME_RX.match(name) or len(name) > 64:
            errs.append((f"name `{name}` must be kebab-case and ≤ 64 chars", md, name_ln))
        elif name != folder:
            warns.append((f"name `{name}` ≠ folder `{folder}`", md, name_ln))
        if not desc:
            errs.append(("frontmatter has no `description`", md, 1))
        else:
            if len(desc) < 60:
                warns.append((f"description is short ({len(desc)} chars): may never trigger", md, desc_ln))
            if len(desc) > 1024:
                warns.append((f"description is long ({len(desc)} chars > 1024)", md, desc_ln))
            if not trigger_clause(desc) and str(fm.get("disable-model-invocation", "")).lower() != "true":
                warns.append(("description lacks 'Use when…' trigger language", md, desc_ln))
    lines = text.count("\n") + 1
    if lines > 500:
        warns.append((f"SKILL.md is {lines} lines (> 500): move detail into references/", md, 1))
    for ref in sorted(set(re.findall(r"\b(scripts/[\w./-]+\.(?:py|sh|js|ts|rb))", body))):
        if not os.path.exists(os.path.join(skill_dir, ref)):
            errs.append((f"referenced file missing: {ref}", md, line_of(text, r"\b" + re.escape(ref))))
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if f.endswith(SKIP_EXT):
                continue
            p = os.path.join(root, f)
            if f in SELF_FILES or os.path.getsize(p) > 2_000_000:
                continue  # the detector's own patterns/fixtures are intentional
            try:
                for kind, ln, val in find_secrets(open(p, encoding="utf-8", errors="ignore").read()):
                    errs.append((f"possible secret in {os.path.relpath(p, skill_dir)}:{ln}: {kind} {val} (remove & rotate)", p, ln))
            except OSError:
                pass
    return folder, desc, errs, warns, (md, desc_ln)


# --- GitHub Actions output ------------------------------------------------------------
def gh_escape(s, prop=False):
    s = str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return s.replace(":", "%3A").replace(",", "%2C") if prop else s


def gh_path(p):
    """Path relative to the checkout so annotations attach to the PR diff."""
    try:
        p = os.path.relpath(p, os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    except ValueError:  # different drive on Windows
        pass
    return p.replace(os.sep, "/")


def gh_annotation(level, msg, path, line, title):
    return f"::{level} file={gh_escape(gh_path(path), True)},line={line},title={gh_escape(title, True)}::{gh_escape(msg)}"


def md_cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def gh_summary(results, overlaps, closest, ne, nw, max_rows=100):
    n = len(results)
    status = "❌ errors found" if ne else ("⚠️ warnings or overlaps" if nw or overlaps else "✅ clean")
    out = ["## 🩺 Skills Doctor", "", f"**{status}**", "",
           "| Skills checked | Errors | Warnings | Overlaps |", "|---:|---:|---:|---:|", f"| {n} | {ne} | {nw} | {len(overlaps)} |", ""]
    rows = [("❌ error", name, path, ln, m) for name, _, errs, _, _ in results for m, path, ln in errs]
    rows += [("⚠️ warning", name, path, ln, m) for name, _, _, warns, _ in results for m, path, ln in warns]
    rows += [("🔀 overlap", f"{x} ↔ {y}", path, ln, f"description {jd:.2f}, trigger clause {jt:.2f}")
             for _, jd, jt, x, y, path, ln in sorted(overlaps, reverse=True)]
    if rows:
        out += ["| | Skill | Location | Problem |", "|---|---|---|---|"]
        out += [f"| {sev} | {md_cell(name)} | `{md_cell(gh_path(path))}:{ln}` | {md_cell(m)} |" for sev, name, path, ln, m in rows[:max_rows]]
        if len(rows) > max_rows:
            out.append(f"\n…and {len(rows) - max_rows} more (see the job log).")
        out.append("")
    if closest:
        out += ["**Closest pairs** (\"0 overlaps\" only means nothing crossed the line)", "",
                "| Measure | Score | Threshold | Status | Pair |", "|---|---:|---:|---|---|"]
        out += [f"| {label} | {score:.2f} | {thr:.2f} | {note} | {md_cell(x)} ↔ {md_cell(y)} |" for label, score, thr, note, x, y in closest]
        out.append("")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Audit Agent Skills (SKILL.md).")
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--strict", action="store_true", help="exit 2 on warnings/overlaps (for CI)")
    ap.add_argument("--overlap", type=float, default=0.20, help="Jaccard threshold, whole description (default 0.20)")
    ap.add_argument("--trigger-overlap", type=float, default=0.30, help="Jaccard threshold, 'Use when…' clause only (default 0.30)")
    ap.add_argument("--format", choices=("text", "github"), default="text",
                    help="github: GitHub Actions annotations + $GITHUB_STEP_SUMMARY (default text)")
    a = ap.parse_args()
    gh = a.format == "github"
    show = (lambda v: re.sub(r"[\r\n]", " ", v)) if gh else str  # a stray newline must not start a workflow command
    dirs = a.dirs or [os.path.expanduser("~/.claude/skills"), ".claude/skills"]
    skills = []
    for d in dirs:
        if os.path.isfile(os.path.join(d, "SKILL.md")):
            skills.append(d)
        elif os.path.isdir(d):
            skills += [os.path.join(d, e) for e in sorted(os.listdir(d)) if os.path.isfile(os.path.join(d, e, "SKILL.md"))]
    if not skills:
        if gh:
            print(f"::error title=skills-doctor::{gh_escape('No skills found in: ' + ', '.join(dirs))}")
        sys.exit(f"No skills found in: {', '.join(dirs)}")
    results = [check(s) for s in skills]
    ne = nw = 0
    for name, _, errs, warns, _ in results:
        print(f"{'❌' if errs else '⚠️ ' if warns else '✅'} {show(name)}")
        for e, path, ln in errs:
            print(gh_annotation("error", e, path, ln, f"skills-doctor: {name}") if gh else f"     ❌ {e}")
        for w, path, ln in warns:
            print(gh_annotation("warning", w, path, ln, f"skills-doctor: {name}") if gh else f"     ⚠️  {w}")
        ne, nw = ne + len(errs), nw + len(warns)
    overlaps, top_d, top_t = [], (0.0, "", ""), (0.0, "", "")
    for (x, dx, *_, (md, desc_ln)), (y, dy, *_) in itertools.combinations(results, 2):
        jd, jt = jaccard(dx, dy), jaccard(trigger_clause(dx), trigger_clause(dy))
        top_d, top_t = max(top_d, (jd, x, y)), max(top_t, (jt, x, y))
        if jd >= a.overlap or jt >= a.trigger_overlap:
            overlaps.append((max(jd, jt), jd, jt, x, y, md, desc_ln))
    for _, jd, jt, x, y, md, desc_ln in sorted(overlaps, reverse=True)[:15]:
        msg = f"overlap {x} ↔ {y}: description {jd:.2f}, trigger clause {jt:.2f}. Give each distinct trigger phrases, or merge"
        print(gh_annotation("warning", msg, md, desc_ln, "skills-doctor: overlap") if gh else f"🔀 {msg}")
    print(f"\n{len(results)} skills · {ne} errors · {nw} warnings · {len(overlaps)} overlaps at or above threshold")
    closest = []
    if len(results) > 1:
        # Report the closest pair as a reading, not just a pass/fail claim: "0 overlaps" only means nothing crossed the line.
        for label, (score, x, y), thr in (("description", top_d, a.overlap), ("trigger clause", top_t, a.trigger_overlap)):
            gap = thr - score
            note = "over" if gap <= 0 else ("close: review" if gap < 0.05 else "ok")
            closest.append((label, score, thr, note, x, y))
            print(f"   closest {label:<15} {score:.2f} / threshold {thr:.2f}  ({note})  {show(x)} ↔ {show(y)}")
    if gh and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(gh_summary(results, overlaps, closest, ne, nw))
    if ne:
        sys.exit(1)
    if a.strict and (nw or overlaps):
        sys.exit(2)


if __name__ == "__main__":
    main()
