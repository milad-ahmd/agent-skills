#!/usr/bin/env python3
"""Render a 1280x640 social preview card (SVG, plus PNG when a converter exists) from real repo data.

Reads the local repo: name from the git remote (or folder), tagline from --tagline, the GitHub
description or the README's first prose line, languages by file-extension counts, license from
LICENSE/COPYING, and the latest tag. --github owner/name adds stars, forks, topics and the
description from the GitHub REST API without a token; when offline it warns and carries on.

Never uploads anything. GitHub's Social preview upload (Settings > General) is manual.

Exit codes: 0 card written (PNG conversion problems are warnings) · 2 bad input.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

W, H = 1280, 640
MARGIN = 80
GITHUB_MAX_BYTES = 1000000  # GitHub: "PNG, JPG, or GIF file under 1 MB"

# Colors from github-linguist languages.yml. Data/prose formats (Markdown, JSON, YAML) are not counted.
LANGS = {
    "Python": ("#3572A5", ".py .pyi"), "Jupyter Notebook": ("#DA5B0B", ".ipynb"),
    "JavaScript": ("#f1e05a", ".js .mjs .cjs .jsx"), "TypeScript": ("#3178c6", ".ts .tsx .mts .cts"),
    "Go": ("#00ADD8", ".go"), "Rust": ("#dea584", ".rs"), "Java": ("#b07219", ".java"),
    "Kotlin": ("#A97BFF", ".kt .kts"), "Swift": ("#F05138", ".swift"), "C": ("#555555", ".c .h"),
    "C++": ("#f34b7d", ".cc .cpp .cxx .hpp .hh .hxx"), "C#": ("#7355dd", ".cs"), "Ruby": ("#701516", ".rb"),
    "PHP": ("#4F5D95", ".php"), "Shell": ("#89e051", ".sh .bash .zsh"), "HTML": ("#e34c26", ".html .htm"),
    "CSS": ("#663399", ".css"), "SCSS": ("#c6538c", ".scss"), "Vue": ("#41b883", ".vue"),
    "Svelte": ("#ff3e00", ".svelte"), "Astro": ("#ff5a03", ".astro"), "Dart": ("#00B4AB", ".dart"),
    "Scala": ("#c22d40", ".scala"), "Elixir": ("#8847B9", ".ex .exs"), "Erlang": ("#B83998", ".erl"),
    "Haskell": ("#5e5086", ".hs"), "Lua": ("#000080", ".lua"), "Objective-C": ("#438eff", ".m"),
    "R": ("#198CE7", ".r"), "Zig": ("#ec915c", ".zig"), "Clojure": ("#db5855", ".clj .cljs .cljc"),
    "OCaml": ("#ef7a08", ".ml .mli"), "Perl": ("#0298c3", ".pl .pm"), "PowerShell": ("#012456", ".ps1"),
    "Groovy": ("#4298b8", ".groovy"), "Julia": ("#a270ba", ".jl"), "Nix": ("#7e7eff", ".nix"),
    "HCL": ("#844FBA", ".tf .hcl"), "Solidity": ("#AA6746", ".sol"), "F#": ("#b845fc", ".fs .fsx"),
    "Elm": ("#60B5CC", ".elm"), "Nim": ("#ffc200", ".nim"), "Gleam": ("#ffaff3", ".gleam"),
    "SQL": ("#e38c00", ".sql"), "Dockerfile": ("#384d54", ""), "Makefile": ("#427819", ""),
}
EXT = {e: name for name, (_, exts) in LANGS.items() for e in exts.split()}
SPECIAL_FILES = {"dockerfile": "Dockerfile", "makefile": "Makefile", "gnumakefile": "Makefile"}
SKIP_DIRS = {".git", "node_modules", "vendor", "third_party", "dist", "build", "out", "target", ".venv",
             "venv", "env", "__pycache__", ".next", ".nuxt", ".tox", ".mypy_cache", "coverage", "site-packages"}
DEFAULT_ACCENT = "#0969da"

THEMES = {
    "light": dict(bg="#ffffff", fg="#1f2328", muted="#59636e", pill="#f6f8fa", line="#d1d9e0", bar="#eaeef2",
                  outer="#e6e9ef"),
    "dark": dict(bg="#0d1117", fg="#f0f6fc", muted="#9198a1", pill="#151b23", line="#3d444d", bar="#262c36",
                 outer="#010409"),
}
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', 'Liberation Mono', monospace"


class BadInput(Exception):
    pass


# ---------------------------------------------------------------- repo data

def git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def parse_remote(url):
    """'git@github.com:owner/name.git' or 'https://host/owner/name' -> (host, owner, name)."""
    m = re.match(r"^(?:[\w+.-]+://)?(?:[^@/]+@)?([^/:]+)[:/](?:\d+/)?(.+?)(?:\.git)?/?$", url.strip())
    if not m:
        return None, None, None
    parts = m.group(2).split("/")
    return m.group(1), (parts[-2] if len(parts) > 1 else None), parts[-1]


def readme_line(repo):
    """First line of prose in the README: skips headings, badges, images, HTML and blank lines."""
    for fname in sorted(os.listdir(repo)):
        if fname.lower().split(".")[0] != "readme" or not os.path.isfile(os.path.join(repo, fname)):
            continue
        with open(os.path.join(repo, fname), encoding="utf-8", errors="replace") as fh:
            in_code = False
            for raw in fh:
                line = raw.strip()
                if line.startswith("```") or line.startswith("~~~"):
                    in_code = not in_code
                    continue
                if (in_code or not line or line[0] in "#<![|=-*>" or line.startswith("---")
                        or re.match(r"^\[!\[", line)):
                    continue
                line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)        # images
                line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)     # links -> text
                line = re.sub(r"[*_`]+", "", line).strip()
                if len(line) > 3:
                    return line
        break
    return ""


def detect_license(repo):
    for fname in sorted(os.listdir(repo)):
        if not re.match(r"^(licen[sc]e|copying)(\.(md|txt|rst))?$", fname, re.I):
            continue
        with open(os.path.join(repo, fname), encoding="utf-8", errors="replace") as fh:
            t = re.sub(r"\s+", " ", fh.read(6000)).upper()
        rules = [("MIT LICENSE", "MIT"), ("PERMISSION IS HEREBY GRANTED, FREE OF CHARGE", "MIT"),
                 ("APACHE LICENSE", "Apache-2.0"), ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0"),
                 ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL"),
                 ("MOZILLA PUBLIC LICENSE VERSION 2.0", "MPL-2.0"), ("ISC LICENSE", "ISC"),
                 ("FREE AND UNENCUMBERED SOFTWARE", "Unlicense")]
        for needle, spdx in rules:
            if needle in t:
                return spdx
        if "GNU GENERAL PUBLIC LICENSE" in t:
            return "GPL-3.0" if "VERSION 3" in t else "GPL-2.0" if "VERSION 2" in t else "GPL"
        if "REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS" in t:
            return "BSD-3-Clause" if "NEITHER THE NAME" in t else "BSD-2-Clause"
        return "License"
    return ""


def list_files(repo):
    out = git(repo, "ls-files", "-z")
    if out:
        return [p for p in out.split("\0") if p and not (set(p.split("/")[:-1]) & SKIP_DIRS)]
    files = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        files += [os.path.relpath(os.path.join(root, n), repo) for n in names]
    return files


def detect_languages(repo, top=4):
    """[(name, percent, color)] by file counts, top N plus 'Other' for the rest."""
    counts = {}
    for path in list_files(repo):
        base = os.path.basename(path).lower()
        lang = SPECIAL_FILES.get(base) or EXT.get(os.path.splitext(base)[1])
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    total = sum(counts.values())
    if not total:
        return []
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    langs = [(n, round(100.0 * c / total, 1), LANGS[n][0]) for n, c in ranked[:top]]
    rest = sum(c for _, c in ranked[top:])
    if rest:
        langs.append(("Other", round(100.0 * rest / total, 1), "#8c959f"))
    return langs


def fetch_github(slug, opener=None):
    """Public repo metadata without a token. Returns (dict or None, warning or None)."""
    req = urllib.request.Request("https://api.github.com/repos/" + slug, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "repo-card-skill",
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=15) as resp:
            d = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        why = "not found or private" if e.code == 404 else "rate limited" if e.code in (403, 429) else str(e.code)
        return None, "GitHub API: %s (%s); using local data only" % (slug, why)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, "GitHub API unreachable (%s); using local data only" % getattr(e, "reason", e)
    lic = (d.get("license") or {}).get("spdx_id") or ""
    return {"stars": d.get("stargazers_count"), "forks": d.get("forks_count"), "topics": d.get("topics") or [],
            "description": d.get("description") or "", "license": "" if lic == "NOASSERTION" else lic,
            "url": d.get("html_url") or "https://github.com/" + slug}, None


def gather(repo, args, opener=None):
    if not os.path.isdir(repo):
        raise BadInput("not a directory: %s" % repo)
    host, owner, name = parse_remote(git(repo, "remote", "get-url", "origin"))
    warnings = []
    gh = None
    if args.github:
        if not re.match(r"^[\w.-]+/[\w.-]+$", args.github):
            raise BadInput("--github must look like owner/name, got %r" % args.github)
        gh, warn = fetch_github(args.github, opener)
        if warn:
            warnings.append(warn)
        owner, name = args.github.split("/")
    data = {
        "owner": args.owner if args.owner is not None else (owner or ""),
        "name": args.name or name or os.path.basename(os.path.abspath(repo)),
        "tagline": args.tagline or (gh or {}).get("description") or readme_line(repo),
        "languages": detect_languages(repo),
        "license": (gh or {}).get("license") or detect_license(repo),
        "tag": git(repo, "describe", "--tags", "--abbrev=0"),
        "stars": (gh or {}).get("stars"), "forks": (gh or {}).get("forks"),
        "topics": (gh or {}).get("topics", []),
        "url": ((gh or {}).get("url") or ("https://github.com/" + args.github if args.github else "")
                or ("https://%s/%s/%s" % (host, owner, name) if host and owner else "")),
    }
    if not data["tagline"]:
        warnings.append("no tagline found; pass --tagline \"one short, concrete sentence\"")
    return data, warnings


# ---------------------------------------------------------------- text fitting

NARROW = set("iljtfrI.,:;'|!()[]{} ")
WIDE = set("mwMW@%")


def text_width(s, size, mono=False, bold=False):
    """Approximate rendered width in px. Errs wide so real text fits."""
    if mono:
        return len(s) * 0.62 * size
    w = 0.0
    for ch in s:
        if ord(ch) >= 0x2E80:
            w += 1.0           # CJK and other full-width glyphs
        elif ch in NARROW:
            w += 0.3
        elif ch in WIDE:
            w += 0.86
        elif ch.isupper():
            w += 0.66
        else:
            w += 0.56
    return w * size * (1.06 if bold else 1.0)


def wrap(text, size, max_w, **kw):
    """Greedy wrap; breaks after spaces, '-', '_', '.', '/' and inside words longer than a line."""
    lines, cur = [], ""
    for tok in re.split(r"(?<=[\s\-_./])", text):
        if text_width((cur + tok).rstrip(), size, **kw) <= max_w:
            cur += tok
            continue
        if cur.strip():
            lines.append(cur.rstrip())
        cur = ""
        while text_width(tok.rstrip(), size, **kw) > max_w:
            n = len(tok)
            while n > 1 and text_width(tok[:n], size, **kw) > max_w:
                n -= 1
            lines.append(tok[:n])
            tok = tok[n:]
        cur = tok.lstrip()
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def ellipsize(line, size, max_w, **kw):
    while line and text_width(line + "…", size, **kw) > max_w:
        line = line[:-1]
    return line.rstrip(" ,.;:-_/") + "…"


def fit(text, max_w, start, minimum, max_lines=2, one_line_min=None, multi_start=None, **kw):
    """(size, lines): one line while size >= one_line_min, then up to max_lines from multi_start down,
    then ellipsis at the minimum size."""
    text = " ".join(text.split())
    if not text:
        return minimum, []
    for size in range(start, (one_line_min or start + 1) - 1, -2):
        lines = wrap(text, size, max_w, **kw)
        if len(lines) == 1:
            return size, lines
    for size in range(multi_start or one_line_min or start, minimum - 1, -2):
        lines = wrap(text, size, max_w, **kw)
        if len(lines) <= max_lines:
            return size, lines
    lines = wrap(text, minimum, max_w, **kw)
    pos = 0
    for line in lines[:max_lines - 1]:
        pos = text.index(line, pos) + len(line)
    return minimum, lines[:max_lines - 1] + [ellipsize(text[pos:].strip(), minimum, max_w, **kw)]


# ---------------------------------------------------------------- SVG

def clean(s):
    """Escape for XML text and attributes; drop control characters XML 1.0 forbids."""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(s))
    return escape(s, {'"': "&quot;", "'": "&#39;"})


def short_num(n):
    if n >= 1000000:
        return ("%.1fM" % (n / 1e6)).replace(".0M", "M")
    if n >= 1000:
        return ("%.1fk" % (n / 1e3)).replace(".0k", "k")
    return str(n)


def luminance(hexcolor):
    rgb = [int(hexcolor[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def mix(hexcolor, other, t):
    a = [int(hexcolor[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def text_el(lines, x, y, size, fill, family, weight=400, step=1.15):
    """<text> with one tspan per line; y is the first baseline. Returns (svg, y after the block)."""
    spans = "".join('<tspan x="%d" y="%d">%s</tspan>' % (x, y + i * size * step, clean(l))
                    for i, l in enumerate(lines))
    svg = '<text font-family="%s" font-size="%d" font-weight="%d" fill="%s">%s</text>' % (
        family, size, weight, fill, spans)
    return svg, y + (len(lines) - 1) * size * step


def star(cx, cy, r, fill):
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.45
        a = math.pi / 2 + i * math.pi / 5
        pts.append("%.1f,%.1f" % (cx + rad * math.cos(a), cy - rad * math.sin(a)))
    return '<polygon points="%s" fill="%s"/>' % (" ".join(pts), fill)


def badges(data):
    """Only real values: no zero counts, nothing invented."""
    out = []
    if data.get("stars"):
        out.append(("star", short_num(data["stars"]) + (" star" if data["stars"] == 1 else " stars")))
    if data.get("forks"):
        out.append(("", short_num(data["forks"]) + (" fork" if data["forks"] == 1 else " forks")))
    for key in ("license", "tag"):
        if data.get(key):
            out.append(("", data[key]))
    out += [("", "#" + t) for t in data.get("topics", [])[:3]]
    return out


def pills(items, x, y, max_x, t, accent, mono):
    family = MONO if mono else SANS
    parts, size, pad = [], 26, 20
    for icon, label in items:
        w = text_width(label, size, mono=mono) + 2 * pad + (30 if icon else 0)
        if x + w > max_x:
            break
        parts.append('<rect x="%d" y="%d" width="%d" height="48" rx="24" fill="%s" stroke="%s" stroke-width="2"/>'
                     % (x, y, w, t["pill"], t["line"]))
        tx = x + pad
        if icon:
            parts.append(star(tx + 11, y + 23, 12, accent))
            tx += 30
        parts.append('<text x="%d" y="%d" font-family="%s" font-size="%d" fill="%s">%s</text>'
                     % (tx, y + 33, family, size, t["fg"], clean(label)))
        x += w + 14
    return "".join(parts)


def lang_bar(langs, x, y, width, t, mono):
    if not langs:
        return ""
    family = MONO if mono else SANS
    parts = ['<clipPath id="bar"><rect x="%d" y="%d" width="%d" height="16" rx="8"/></clipPath>' % (x, y, width),
             '<rect x="%d" y="%d" width="%d" height="16" rx="8" fill="%s"/>' % (x, y, width, t["bar"]),
             '<g clip-path="url(#bar)">']
    total = sum(p for _, p, _ in langs) or 1
    cx = float(x)
    for _, pct, color in langs:
        seg = width * pct / total
        parts.append('<rect x="%.1f" y="%d" width="%.1f" height="16" fill="%s"/>' % (cx, y, seg + 0.5, color))
        cx += seg
    parts.append("</g>")
    lx = x
    for name, pct, color in langs:
        label = "%s %s%%" % (name, ("%.1f" % pct).rstrip("0").rstrip("."))
        w = 30 + text_width(label, 26, mono=mono) + 36
        if lx + w > x + width:
            break
        parts.append('<circle cx="%d" cy="%d" r="9" fill="%s"/>' % (lx + 9, y + 53, color))
        parts.append('<text x="%d" y="%d" font-family="%s" font-size="26" fill="%s">%s</text>'
                     % (lx + 28, y + 62, family, t["muted"], clean(label)))
        lx += w
    return "".join(parts)


def logo_el(logo, x, y, size):
    if not logo:
        return ""
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
            ".svg": "image/svg+xml", ".webp": "image/webp"}[os.path.splitext(logo)[1].lower()]
    with open(logo, "rb") as fh:
        uri = "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode("ascii"))
    return ('<image x="%d" y="%d" width="%d" height="%d" preserveAspectRatio="xMidYMid meet" href="%s" '
            'xlink:href="%s"/>' % (x, y, size, size, uri, uri))


def render(data, template="clean", theme="light", logo=None):
    t = dict(THEMES[theme])
    accent = data["languages"][0][2] if data["languages"] else DEFAULT_ACCENT
    mono = template == "terminal"
    family = MONO if mono else SANS
    body = []
    left, right, top, bottom = MARGIN, W - MARGIN, 96, 430

    if template == "terminal":
        bg = t["outer"]
        body.append('<rect x="48" y="40" width="1184" height="560" rx="20" fill="%s" stroke="%s" stroke-width="2"/>'
                    % (t["bg"], t["line"]))
        body += ['<circle cx="%d" cy="78" r="9" fill="%s"/>' % (88 + i * 30, c)
                 for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))]
        left, right, top = 96, W - 96, 120
    elif template == "bold":
        bg = mix(accent, "#ffffff", 0.82) if theme == "light" else mix(accent, "#000000", 0.72)
        dark_text = luminance(bg) > 0.18
        t.update(fg="#111111" if dark_text else "#ffffff", muted="#3b3b3b" if dark_text else "#d6d6d6",
                 pill=mix(bg, "#ffffff", 0.5 if dark_text else 0.08),
                 line=mix(bg, "#000000" if dark_text else "#ffffff", 0.25),
                 bar=mix(bg, "#000000", 0.12))
        body.append('<rect x="0" y="0" width="28" height="%d" fill="%s"/>' % (H, accent))
    else:
        bg = t["bg"]
        body.append('<rect x="0" y="0" width="%d" height="12" fill="%s"/>' % (W, accent))

    if logo:
        body.append(logo_el(logo, right - 150, top - 16, 150))
    text_w = right - left - (190 if logo else 0)

    # Text block: measured first, then centred vertically between top and bottom.
    blocks = []
    if mono:
        cmd = "$ git clone " + data["url"].split("://")[-1] if data["url"] else "$ cd " + data["name"]
        blocks.append(fit(cmd, text_w, 28, 26, max_lines=1) + (t["muted"], 400, 26))
    elif data["owner"]:
        blocks.append(fit(data["owner"] + " /", text_w, 34, 28, max_lines=1) + (t["muted"], 400, 22))
    # (start, one-line minimum, two-line start, minimum) keeps the text block above the badges.
    big = (112, 76, 68, 56) if template == "bold" else (88, 64, 60, 52)
    blocks.append(fit(data["name"], text_w, big[0], big[3], one_line_min=big[1], multi_start=big[2], mono=mono,
                      bold=True)
                  + (t["fg"], 700, 28))
    tagline = ("# " if mono else "") + data["tagline"]
    if data["tagline"]:
        blocks.append(fit(tagline, text_w, 40, 30, mono=mono) + (t["muted"] if mono else t["fg"], 400, 0))
    height = sum(s * 1.15 * len(lines) + gap for s, lines, _, _, gap in blocks)
    y = top + max(0, (bottom - top - height) / 2)
    for size, lines, fill, weight, gap in blocks:
        svg, last = text_el(lines, left, y + size * 0.9, size, fill, family, weight)
        body.append(svg)
        y = last + size * 0.25 + gap + size * 0.1

    body.append(pills(badges(data), left, 448, right, t, accent, mono))
    body.append(lang_bar(data["languages"], left, 526, right - left, t, mono))

    title = clean(data["name"] + (": " + data["tagline"] if data["tagline"] else ""))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="%s">'
            '<title>%s</title><rect width="%d" height="%d" fill="%s"/>%s</svg>\n'
            % (W, H, W, H, title, title, W, H, bg, "".join(body)))


# ---------------------------------------------------------------- PNG conversion

CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
CHROME_APPS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Chromium.app/Contents/MacOS/Chromium")


def cairosvg_available():
    try:
        import cairosvg  # noqa: F401
        return True
    except Exception:  # cairosvg raises OSError when the cairo library is missing
        return False


def find_converter(path=None):
    """(name, executable or None) for the first converter found, else (None, None). Detects, never installs."""
    exe = shutil.which("rsvg-convert", path=path)
    if exe:
        return "rsvg-convert", exe
    if cairosvg_available():
        return "cairosvg", None
    exe = shutil.which("magick", path=path)
    if exe:
        return "magick", exe
    exe = shutil.which("convert", path=path)
    if exe and "imagemagick" in run_quiet([exe, "-version"]).lower():  # Windows has an unrelated convert.exe
        return "convert", exe
    for name in CHROME_NAMES:
        exe = shutil.which(name, path=path)
        if exe:
            return "chrome", exe
    for app in CHROME_APPS:
        if os.access(app, os.X_OK):
            return "chrome", app
    return None, None


def run_quiet(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except (OSError, subprocess.SubprocessError):
        return ""


def convert_cmd(name, exe, svg, png, profile_dir="<empty temp dir>"):
    if name == "rsvg-convert":
        return [exe, "--width=%d" % W, "--height=%d" % H, "--output=" + png, svg]
    if name in ("magick", "convert"):
        return [exe, "-density", "96", svg, "-resize", "%dx%d!" % (W, H), png]
    if name == "chrome":
        return [exe, "--headless", "--disable-gpu", "--hide-scrollbars", "--user-data-dir=" + profile_dir,
                "--window-size=%d,%d" % (W, H), "--screenshot=" + png, "file://" + os.path.abspath(svg)]
    return None


MANUAL = ("rsvg-convert --width=1280 --height=640 --output={png} {svg}\n"
          "  or: magick -density 96 {svg} -resize 1280x640! {png}\n"
          "  or: open {svg} in a browser at 100% zoom and take a 1280x640 screenshot")


def png_size(png):
    with open(png, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def to_png(svg, png, path=None):
    """Returns (converter name or None, list of notes)."""
    name, exe = find_converter(path)
    if not name:
        return None, ["no SVG-to-PNG converter found (looked for rsvg-convert, the cairosvg module, "
                      "ImageMagick, Chrome/Chromium). GitHub's social preview takes PNG, JPG or GIF, not SVG. "
                      "Convert with:\n  " + MANUAL.format(svg=svg, png=png)]
    try:
        if name == "cairosvg":
            import cairosvg
            cairosvg.svg2png(url=svg, write_to=png, output_width=W, output_height=H)
        else:
            with tempfile.TemporaryDirectory() as prof:
                r = subprocess.run(convert_cmd(name, exe, svg, png, prof), capture_output=True, text=True,
                                   timeout=120)
            if r.returncode != 0 or not os.path.exists(png):
                raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or "exit %d" % r.returncode)
    except Exception as e:
        return name, ["%s failed (%s). Convert by hand:\n  %s" % (name, e, MANUAL.format(svg=svg, png=png))]
    notes = []
    dims = png_size(png)
    if dims != (W, H):
        notes.append("PNG is %s, expected 1280x640; check the converter output" % (dims,))
    if os.path.getsize(png) >= GITHUB_MAX_BYTES:
        notes.append("PNG is %d bytes; GitHub wants under 1 MB (drop the logo or use a smaller one)"
                     % os.path.getsize(png))
    return name, notes


# ---------------------------------------------------------------- OG tags

def og_html(data, site_url, image_url):
    alt = data["name"] + (": " + data["tagline"] if data["tagline"] else "")
    tags = [("property", "og:title", data["name"]), ("property", "og:description", data["tagline"]),
            ("property", "og:type", "website"), ("property", "og:url", site_url),
            ("property", "og:image", image_url), ("property", "og:image:type", "image/png"),
            ("property", "og:image:width", str(W)), ("property", "og:image:height", str(H)),
            ("property", "og:image:alt", alt), ("name", "twitter:card", "summary_large_image"),
            ("name", "twitter:image:alt", alt[:420])]
    return "\n".join('<meta %s="%s" content="%s">' % (k, n, clean(v)) for k, n, v in tags if v)


# ---------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(
        description="Render a 1280x640 social preview card (SVG + PNG when a converter exists) from repo data.",
        epilog="Exit codes: 0 card written (PNG problems are warnings), 2 bad input. Never uploads anything.")
    p.add_argument("-C", "--repo", default=".", help="repository directory (default: .)")
    p.add_argument("--github", metavar="OWNER/NAME", help="add stars, forks, topics, description from the GitHub API")
    p.add_argument("--tagline", help="one short sentence (overrides GitHub description and README)")
    p.add_argument("--name", help="display name (default: from the git remote or folder)")
    p.add_argument("--owner", help="owner shown above the name; pass '' to hide it")
    p.add_argument("--template", choices=("clean", "terminal", "bold"), default="clean")
    p.add_argument("--theme", choices=("light", "dark"), default="light")
    p.add_argument("--logo", help="PNG/JPG/GIF/SVG/WebP shown top right")
    p.add_argument("--out", default="card.svg", help="SVG path (default: card.svg)")
    p.add_argument("--png", help="PNG path (default: --out with .png)")
    p.add_argument("--no-png", action="store_true", help="write only the SVG")
    p.add_argument("--og-html", metavar="SITE_URL", help="also print og:/twitter: meta tags for this page URL")
    p.add_argument("--image-url", help="public URL of the PNG for --og-html (default: SITE_URL/<png name>)")
    p.add_argument("--json", action="store_true", help="print data, outputs and warnings as JSON")
    return p


def main(argv=None, opener=None):
    args = build_parser().parse_args(argv)
    try:
        if args.logo:
            ext = os.path.splitext(args.logo)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp") or not os.path.isfile(args.logo):
                raise BadInput("--logo must be an existing PNG, JPG, GIF, SVG or WebP file: %s" % args.logo)
            if os.path.getsize(args.logo) > 500000:
                raise BadInput("--logo is over 500 KB; the PNG must stay under GitHub's 1 MB limit")
        if not os.path.isdir(os.path.dirname(os.path.abspath(args.out))):
            raise BadInput("--out folder does not exist: %s" % args.out)
        if args.og_html and not re.match(r"^https?://\S+$", args.og_html):
            raise BadInput("--og-html needs an http(s) URL, got %r" % args.og_html)
        data, warnings = gather(args.repo, args, opener)
    except BadInput as e:
        print("repo_card: " + str(e), file=sys.stderr)
        return 2

    svg = render(data, args.template, args.theme, args.logo)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    png = None if args.no_png else (args.png or os.path.splitext(args.out)[0] + ".png")
    converter = None
    if png:
        converter, notes = to_png(args.out, png)
        warnings += notes
        if not os.path.exists(png):
            png = None

    result = {"data": data, "svg": args.out, "png": png, "converter": converter, "warnings": warnings}
    if args.og_html:
        image = args.image_url or args.og_html.rstrip("/") + "/" + os.path.basename(
            png or os.path.splitext(args.out)[0] + ".png")
        result["og_html"] = og_html(data, args.og_html, image)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    langs = ", ".join("%s %s%%" % (n, p) for n, p, _ in data["languages"]) or "none detected"
    print("card: %s (%s, %s)" % (args.out, args.template, args.theme))
    print("png:  %s" % ("%s (via %s)" % (png, converter) if png else "not written"))
    print("name: %s%s" % (data["owner"] + "/" if data["owner"] else "", data["name"]))
    print("tagline: %s" % (data["tagline"] or "-"))
    print("languages: %s" % langs)
    print("license: %s  tag: %s  stars: %s  forks: %s" % (
        data["license"] or "-", data["tag"] or "-", data["stars"] if data["stars"] is not None else "-",
        data["forks"] if data["forks"] is not None else "-"))
    for w in warnings:
        print("warning: " + w, file=sys.stderr)
    if "og_html" in result:
        print("\n" + result["og_html"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
