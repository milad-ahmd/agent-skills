#!/usr/bin/env python3
"""Audit a repository's Markdown README before rewriting it. Stdlib only, read-only, no network.

Usage: python3 readme_audit.py [-C REPO] [--readme PATH] [--first-screen 30] [--top 20] [--json]
  -C REPO           repository to audit (default: current directory)
  --readme PATH     audit this file instead of the one GitHub would show
  --first-screen N  lines that count as the first screen (default 30)
  --top N           rows per list in the text report (default 20; --json lists everything)
  --json            machine-readable report on stdout

Which README: like GitHub, the first of .github/, the repo root, then docs/ that holds a README* file.
Inside one directory GitHub's order is not documented; a Markdown file (.md, .markdown ...) wins.

What it reports (every finding has a severity and a confidence):
  links      relative links and images whose file is missing, exists only with different letter case
             (GitHub paths are case-sensitive) or is not tracked by git; in-page #anchors that match no
             heading, id= or name=; [text][ref] links with no definition
  anchors    heading ids follow GitHub's rules: Unicode lower-case per character, keep letters, marks
             (so U+FE0F after an emoji stays), digits, _, -, ZWJ; spaces become -, everything else goes;
             :shortcode: emoji count as their name; duplicates get -1, -2 (github-slugger's loop)
  content    images with no alt text, code fences with no language, headings that skip a level,
             heading count (TOC hint), badge count, length (GitHub truncates past 500 KiB)
  first      title, a one-line description, and where install or usage first shows up
  sections   install, usage, examples, configuration, contributing, license found by heading words;
             LICENSE / COPYING files and the license family the README claims vs the file
  commands   shell blocks under install/usage/development headings and above the first section:
             npm|yarn|pnpm|bun install/ci/run, pip -r/-e/., package names vs pyproject/package.json/
             Cargo.toml/go.mod, cargo, go, make targets, docker compose/build, local scripts

Cannot see (say so instead of guessing):
  - external URLs, badges and absolute links to the repo itself: listed as "not checked", never fetched
  - links that leave the repo (../../issues) and paths inside git submodules: listed, not checked
  - it reads Markdown line by line, not with a full CommonMark parser: links split over lines, indented
    code blocks and HTML block edge cases can be missed; reStructuredText / AsciiDoc READMEs are refused
  - TOML, setup.py and Makefiles are read with regular expressions; included makefiles, pattern rules and
    workspaces skip the related check; files a block downloads or builds first are not known
  - license families come from key phrases, not full license matching; section names are English words

Exit codes: 0 audit done, nothing broken (warnings and suggestions may remain)
            1 audit done, at least one error: a high-confidence fact such as a broken link, image or anchor,
              a file or script a command needs, no LICENSE file for a claimed license, README over 500 KiB
              (medium- and low-confidence findings are never errors)
            2 no README found, README is not Markdown, or a bad path
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from urllib.parse import unquote

README_DIRS = (".github", "", "docs")
MD_EXT = {".md", ".markdown", ".mdown", ".mkdn", ".mkd", ".mdwn"}
GITHUB_TRUNCATE = 500 * 1024
TOC_HEADINGS = 12      # level 2-3 headings past which a table of contents is suggested
MANY_BADGES = 10
LONG_WORDS = 5000
SHELL_LANGS = {"", "sh", "bash", "shell", "zsh", "console", "terminal", "shell-session", "sh-session", "text",
               "powershell", "ps", "ps1", "pwsh", "cmd", "bat", "fish"}
PROMPT_ONLY = {"console", "shell-session", "sh-session", "terminal"}
SECTION_RE = {
    "install": r"\binstall|\bset ?up\b|getting started|get started|quick ?start|\bdownload",
    "usage": r"\busage\b|quick ?start|getting started|get started|how to use|\bbasic use|\btutorial|\busing\b",
    "examples": r"\bexamples?\b|\bdemos?\b|\bsamples?\b|\bshowcase",
    "configuration": r"\bconfig|\boptions\b|\bsettings\b|environment variables|\bcustomi[sz]",
    "contributing": r"\bcontribut|\bdevelopment\b|\bdeveloping\b|\bhacking\b",
    "license": r"\blicen[cs]",
}
COMMAND_SECTION_RE = r"\binstall|\bset ?up\b|getting started|get started|quick ?start|\busage\b|\bdevelop|" \
                     r"\bcontribut|\bbuild|\brunning\b|\brun\b|\bhacking\b|\btest"
INSTALL_CMD_RE = re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:i|install|add|ci)\b|\bpipx?3?\s+install\b|"
                            r"\buv\s+(?:add|pip|tool)\b|\bgo\s+(?:install|get)\b|\bcargo\s+(?:install|add)\b|"
                            r"\bbrew\s+install\b|\bgem\s+install\b|\bcomposer\s+require\b|\bdocker\s+(?:run|pull)\b|"
                            r"\bapt(?:-get)?\s+install\b|/plugin\s+install\b|\bnpx\s|\bcurl\b.*\|\s*(?:ba)?sh\b")
BADGE_RE = re.compile(r"shields\.io|badgen\.net|badge\.fury\.io|/badges?[/.]|badge\.svg|codecov\.io|coveralls\.io|"
                      r"travis-ci\.|circleci\.com|goreportcard\.com|pkg\.go\.dev/badge|readthedocs\.org/projects/|"
                      r"pepy\.tech|godoc\.org|snyk\.io|deepwiki\.com/badge|api\.netlify\.com", re.I)
LICENSE_FILE_RE = re.compile(r"^(?:licen[cs]e|copying|unlicense)(?:[-._].*)?$", re.I)
# Ordered: the specific families are removed from the text before the generic GPL / BSD phrases are tried.
LICENSE_CLAIM = [("AGPL", r"\bagpl|affero"), ("LGPL", r"\blgpl|lesser general public"),
                 ("GPL", r"\bgpl|general public licen[cs]e"), ("Apache", r"\bapache\b"),
                 ("MPL", r"\bmpl\b|mozilla public"), ("BSD", r"\bbsd\b|\d-clause"), ("ISC", r"\bisc\b"),
                 ("Unlicense", r"\bunlicense\b"), ("MIT", r"\bmit\b"), ("CC", r"creative commons|\bcc[- ]?by|\bcc0\b")]
LICENSE_TEXT = [("AGPL", r"gnu affero general public license"), ("LGPL", r"gnu lesser general public license"),
                ("GPL", r"gnu general public license"), ("Apache", r"apache license"),
                ("MPL", r"mozilla public license"), ("Unlicense", r"free and unencumbered software"),
                ("ISC", r"permission to use, copy, modify, and/or distribute this software"),
                ("MIT", r"permission is hereby granted, free of charge"),
                ("BSD", r"redistribution and use in source and binary forms"), ("CC", r"creative commons")]

DEST = (r"\(\s*(?:<([^>\n]*)>|((?:[^\s()\\]|\\.|\((?:[^\s()\\]|\\.)*\))*))"
        r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
TEXT = r"((?:[^\[\]\\]|\\.|\[[^\]]*\])*)"
IMG_RE = re.compile(r"!\[" + TEXT + r"\]" + DEST)
LINK_RE = re.compile(r"(?<![\\!])\[" + TEXT + r"\]" + DEST)
REF_IMG_RE = re.compile(r"!\[" + TEXT + r"\]\[(?!\^)([^\]]*)\]")
REF_LINK_RE = re.compile(r"(?<![\\!\]])\[(?!\^)" + TEXT + r"\]\[(?!\^)([^\]]*)\]")
SHORTCUT_RE = re.compile(r"(?<![\\\]])(!?)\[(?!\^)([^\]\[]+)\](?![\[(:])")
REF_DEF_RE = re.compile(r"^ {0,3}\[(?!\^)([^\]]+)\]:\s*(?:<([^>]*)>|(\S+))")  # [^1]: is a footnote
ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?=[ \t]|$)(.*)$")
SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
TAG_RE = re.compile(r"<(img|a|source|h[1-6])\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.I)
ATTR_RE = re.compile(r"([a-zA-Z_:][\w:.-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")
ID_RE = re.compile(r"<[a-zA-Z][^>]*?\s(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", re.S)
BARE_URL_RE = re.compile(r"\bhttps?://[^\s<>\"'`)\]]+")
SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


# ---------------------------------------------------------------- GitHub heading anchors

def slug(text):
    """GitHub's heading id for already-rendered heading text (no uniqueness suffix)."""
    out = []
    for ch in text:
        for c in ch.lower():  # per character: GitHub keeps a word-final capital sigma as plain sigma
            cat = unicodedata.category(c)
            if c == " ":
                out.append("-")
            elif c in "-\u200c\u200d" or cat[0] in "LM" or cat in ("Nd", "Nl", "Pc"):
                out.append(c)
    return "".join(out)


class Slugger:
    """Duplicate handling copied from github-slugger, which matches GitHub for 'A', 'A', 'A-1', 'A'."""

    def __init__(self):
        self.seen = {}

    def __call__(self, text):
        base = result = slug(text)
        while result in self.seen:
            self.seen[base] += 1
            result = "%s-%d" % (base, self.seen[base])
        self.seen[result] = 0
        return result


def rendered_text(raw):
    """Approximate the text GitHub renders for a heading's inline Markdown."""
    kept = []

    def protect(s):
        kept.append(s)
        return "\x00%d\x00" % (len(kept) - 1)

    s = re.sub(r"(`+)(.+?)\1", lambda m: protect(m.group(2).strip() if m.group(2).strip() else m.group(2)), raw)
    s = re.sub(r"\\([!-/:-@\[-`{-~])", lambda m: protect(m.group(1)), s)
    s = re.sub(r"!\[[^\]]*\](?:\([^)]*\)|\[[^\]]*\])", "", s)
    s = re.sub(r"\[([^\]]*)\](?:\([^)]*\)|\[[^\]]*\])", r"\1", s)
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"(?<!\w)(__?)(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", s)
    s = html.unescape(s)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], s)


# ---------------------------------------------------------------- Markdown scanning

class Doc:
    """Line-based view of a Markdown file: fences, prose without code, headings, anchors."""

    def __init__(self, text):
        self.text = text
        self.lines = text.split("\n")
        self.fences = []          # dict(line, lang, body=[lines], section=heading index or None)
        in_fence = [False] * len(self.lines)
        cur = None
        for i, line in enumerate(self.lines):
            body = re.sub(r"^\s*(?:>\s?)+", "", line)
            if cur:
                in_fence[i] = True
                m = re.match(r"^\s*(`{3,}|~{3,})\s*$", body)
                if m and m.group(1)[0] == cur["char"] and len(m.group(1)) >= cur["len"]:
                    cur = None
                else:
                    cur["body"].append(body)
                continue
            m = re.match(r"^\s*(`{3,}|~{3,})(.*)$", body)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                info = m.group(2).strip()
                cur = {"line": i + 1, "lang": info.split()[0].strip("{}.").lower() if info else "",
                       "char": m.group(1)[0], "len": len(m.group(1)), "body": []}
                self.fences.append(cur)
                in_fence[i] = True
        prose = "\n".join("" if f else ln for ln, f in zip(self.lines, in_fence))
        prose = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group().count("\n"), prose, flags=re.S)
        self.prose = prose                                   # code spans kept (headings need them)
        self.prose_lines = prose.split("\n")
        self.nocode_lines = [re.sub(r"(`+).+?\1", lambda m: " " * len(m.group()), ln) for ln in self.prose_lines]
        self.nocode = "\n".join(self.nocode_lines)
        self.ref_defs = {}
        self.def_lines = set()
        for i, ln in enumerate(self.nocode_lines):
            m = REF_DEF_RE.match(ln)
            if m:
                self.ref_defs.setdefault(norm_label(m.group(1)), (m.group(2) if m.group(2) is not None
                                                                  else m.group(3), i + 1))
                self.def_lines.add(i)
        self.headings = self._headings()
        slugger = Slugger()
        for h in self.headings:
            h["anchor"] = slugger(h["text"])
        self.anchors = {h["anchor"] for h in self.headings} | set(ID_RE.findall(self.nocode))
        for f in self.fences:
            before = [h for h in self.headings if h["line"] < f["line"]]
            f["heading"] = before[-1] if before else None

    def _headings(self):
        found = []
        self.heading_lines = set()   # 1-based lines that belong to a heading (HTML headings can span lines)
        lines = self.prose_lines
        for i, ln in enumerate(lines):
            body = re.sub(r"^\s*(?:>\s?)+", "", ln)
            m = ATX_RE.match(body)
            if m:
                raw = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", m.group(2)).strip()
                found.append((i, 0, len(m.group(1)), raw))
                self.heading_lines.add(i + 1)
                continue
            m = SETEXT_RE.match(body)
            if m and i > 0 and lines[i - 1].strip() and m.group(1)[0] in "=-":
                prev = lines[i - 1]
                if (ATX_RE.match(prev) or re.match(r"^\s*(?:[-*+]|\d+[.)])\s|^\s*>|^\s*<|^\s*\|", prev)
                        or SETEXT_RE.match(prev) or (m.group(1)[0] == "-" and "|" in prev)):
                    continue
                start = i - 1
                while start > 0 and lines[start - 1].strip() and not ATX_RE.match(lines[start - 1]):
                    start -= 1
                raw = " ".join(x.strip() for x in lines[start:i])
                found.append((start, 0, 1 if m.group(1)[0] == "=" else 2, raw))
                self.heading_lines.update(range(start + 1, i + 2))
        for m in re.finditer(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", self.prose, re.I | re.S):
            line = self.prose.count("\n", 0, m.start())
            self.heading_lines.update(range(line + 1, line + 2 + m.group().count("\n")))
            found.append((line, m.start() - self.prose.rfind("\n", 0, m.start()), int(m.group(1)),
                          m.group(2)))   # HTML heading text keeps its inner whitespace on GitHub
        found.sort()
        return [{"line": i + 1, "level": lvl, "raw": raw, "text": rendered_text(raw)} for i, _c, lvl, raw in found]


def norm_label(label):
    return " ".join(label.split()).casefold()


def attrs(s):
    return {m.group(1).lower(): next(g for g in m.groups()[1:] if g is not None) for m in ATTR_RE.finditer(s)}


def collect_refs(doc):
    """Every link and image target: dicts with kind (link/image), dest, line, alt (images), html flag."""
    refs = []
    undefined = []
    for i, ln in enumerate(doc.nocode_lines):
        if i in doc.def_lines:
            continue
        work = ln
        for rx, kind in ((IMG_RE, "image"), (REF_IMG_RE, "image"), (LINK_RE, "link"), (REF_LINK_RE, "link")):
            for m in rx.finditer(work):
                if rx in (IMG_RE, LINK_RE):
                    dest = m.group(2) if m.group(2) is not None else (m.group(3) or "")
                    dest = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", dest)
                else:
                    label = norm_label(m.group(2) or m.group(1))
                    if label not in doc.ref_defs:
                        undefined.append({"line": i + 1, "label": m.group(2) or m.group(1)})
                        continue
                    dest = doc.ref_defs[label][0]
                refs.append({"kind": kind, "dest": dest.strip(), "line": i + 1, "alt": m.group(1), "html": False})
            work = rx.sub(lambda m: "x" * len(m.group()) if kind == "image" else m.group(), work)
        for m in SHORTCUT_RE.finditer(work):
            label = norm_label(m.group(2))
            if label in doc.ref_defs:
                refs.append({"kind": "image" if m.group(1) else "link", "dest": doc.ref_defs[label][0].strip(),
                             "line": i + 1, "alt": m.group(2), "html": False})
    for m in TAG_RE.finditer(doc.nocode):
        tag, a = m.group(1).lower(), attrs(m.group(2))
        line = doc.nocode.count("\n", 0, m.start()) + 1
        if tag == "img" and "src" in a:
            refs.append({"kind": "image", "dest": a["src"].strip(), "line": line, "alt": a.get("alt"), "html": True})
        elif tag == "source" and a.get("srcset"):
            refs.append({"kind": "image", "dest": a["srcset"].split(",")[0].split()[0], "line": line,
                         "alt": "", "html": True, "source": True})
        elif tag == "a" and "href" in a:
            refs.append({"kind": "link", "dest": a["href"].strip(), "line": line, "alt": None, "html": True})
    refs.sort(key=lambda r: r["line"])
    return refs, undefined


# ---------------------------------------------------------------- repository facts

class Repo:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.tracked = None
        self.submodules = []
        r = run_git(self.root, "ls-files", "-z", "--cached")
        if r is not None:
            self.tracked = set()
            for p in r.split("\0"):
                if p:
                    parts = p.split("/")
                    for k in range(1, len(parts) + 1):
                        self.tracked.add("/".join(parts[:k]))
        gm = read(os.path.join(self.root, ".gitmodules")) or ""
        self.submodules = [p.strip().strip("/") for p in re.findall(r"^\s*path\s*=\s*(.+)$", gm, re.M)]
        self._docs = {}

    def exact(self, rel):
        """'ok', 'case' (exists only with other letter case) or 'missing' for a repo-relative path."""
        cur = self.root
        status = "ok"
        for part in [p for p in rel.split("/") if p not in ("", ".")]:
            try:
                names = os.listdir(cur)
            except OSError:
                return "missing"
            if part in names:
                cur = os.path.join(cur, part)
                continue
            alt = [n for n in names if n.lower() == part.lower()]
            if not alt:
                return "missing"
            status = "case"
            cur = os.path.join(cur, alt[0])
        return status

    def doc(self, rel):
        if rel not in self._docs:
            text = read(os.path.join(self.root, rel))
            self._docs[rel] = Doc(text) if text is not None else None
        return self._docs[rel]


def run_git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C"))
    except OSError:
        return None
    if r.returncode:
        return None
    return r.stdout.decode("utf-8", "replace")


def read(path, limit=None):
    try:
        with open(path, "rb") as fh:
            data = fh.read() if limit is None else fh.read(limit)
    except OSError:
        return None
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def find_readme(root):
    """GitHub order: .github/, root, docs/. Returns (chosen, others) as repo-relative paths."""
    found = []
    for d in README_DIRS:
        full = os.path.join(root, d)
        try:
            names = sorted(os.listdir(full))
        except OSError:
            continue
        hits = [n for n in names if os.path.isfile(os.path.join(full, n))
                and re.match(r"^readme(?:\.[A-Za-z0-9]+)?$", n, re.I)]
        hits.sort(key=lambda n: (os.path.splitext(n)[1].lower() not in MD_EXT, n))
        found += [os.path.join(d, n) if d else n for n in hits]
    return (found[0] if found else None), found[1:]


def toml_table(text, name):
    m = re.search(r"^\[%s\]\s*$(.*?)(?=^\[|\Z)" % re.escape(name), text, re.M | re.S)
    return m.group(1) if m else ""


def toml_str(table, key):
    m = re.search(r"^\s*%s\s*=\s*[\"']([^\"']+)[\"']" % re.escape(key), table, re.M)
    return m.group(1) if m else None


def pep503(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def python_project(path):
    """(name, set of dependency names) from pyproject.toml / setup.cfg / setup.py / requirements*.txt."""
    names, deps = set(), set()
    pp = read(os.path.join(path, "pyproject.toml")) or ""
    for table in ("project", "tool.poetry"):
        n = toml_str(toml_table(pp, table), "name")
        if n:
            names.add(pep503(n))
    m = re.search(r"^\[metadata\]\s*$(.*?)(?=^\[|\Z)", read(os.path.join(path, "setup.cfg")) or "", re.M | re.S)
    if m and toml_str(m.group(1), "name") is None:
        n = re.search(r"^\s*name\s*=\s*(\S+)", m.group(1), re.M)
        if n:
            names.add(pep503(n.group(1)))
    n = re.search(r"\bname\s*=\s*[\"']([^\"']+)[\"']", read(os.path.join(path, "setup.py")) or "")
    if n:
        names.add(pep503(n.group(1)))
    blob = pp + "\n".join(read(os.path.join(path, f)) or "" for f in safe_listdir(path)
                          if re.match(r"requirements.*\.(txt|in)$", f))
    for s in re.findall(r"[\"']\s*([A-Za-z0-9][A-Za-z0-9._-]*)", pp) + re.findall(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", blob, re.M):
        deps.add(pep503(s))
    return names, deps


def node_project(path):
    try:
        with open(os.path.join(path, "package.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    deps = set()
    for k in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        if isinstance(data.get(k), dict):
            deps |= set(data[k])
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    return {"name": data.get("name"), "private": bool(data.get("private")), "deps": deps, "scripts": set(scripts)}


def safe_listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def license_families(text, table):
    t = " ".join(text.lower().split())
    fams = []
    for fam, rx in table:
        if re.search(rx, t):
            fams.append(fam)
            t = re.sub(rx, " ", t)
    return fams


# ---------------------------------------------------------------- the audit

def audit(root, readme_rel, first_screen, others):
    repo = Repo(root)
    raw = read(os.path.join(repo.root, readme_rel))
    size = os.path.getsize(os.path.join(repo.root, readme_rel))
    doc = Doc(raw)
    readme_dir = os.path.dirname(readme_rel)
    findings = []

    def add(kind, severity, confidence, line, message, **extra):
        f = {"kind": kind, "severity": severity, "confidence": confidence, "line": line, "message": message}
        f.update(extra)
        findings.append(f)

    refs, undefined = collect_refs(doc)
    external, outside, submodule, page = [], [], [], []
    for u in undefined:
        add("undefined-reference", "warning", "medium", u["line"],
            "[%s] has no link definition, so GitHub shows the brackets as plain text" % u["label"])
    for r in refs:
        dest = r["dest"]
        what = "image" if r["kind"] == "image" else "link"
        if r["kind"] == "image" and not r.get("source"):
            alt = r["alt"]
            if alt is None:
                add("missing-alt", "warning", "high", r["line"], "<img> has no alt attribute: `%s`" % short(dest))
            elif not alt.strip():
                if r["html"]:
                    add("empty-alt", "info", "low", r["line"],
                        "<img alt=\"\"> marks the image decorative; fine for a spacer, not for a logo, badge or screenshot: `%s`"
                        % short(dest))
                else:
                    add("missing-alt", "warning", "high", r["line"], "image has empty alt text: `%s`" % short(dest))
        if dest.lower() == "#readme" and "readme" not in doc.anchors:
            page.append(dest)   # an id on GitHub's repo page, not in the file
        elif dest.startswith("#") or not dest:
            check_anchor(doc, dest[1:], r["line"], "README", add)
        elif SCHEME_RE.match(dest) or dest.startswith("//"):
            external.append(dest)
        else:
            path, _, frag = dest.partition("#")
            path = unquote(path.split("?")[0])
            if not path:
                continue
            base = "" if path.startswith("/") else readme_dir
            rel = os.path.normpath(os.path.join(base, path.lstrip("/"))).replace(os.sep, "/")
            if rel == ".." or rel.startswith("../"):
                outside.append(dest)
                continue
            if any(rel == s or rel.startswith(s + "/") for s in repo.submodules):
                submodule.append(dest)
                continue
            state = repo.exact(rel) if rel != "." else "ok"
            if state == "missing":
                add("broken-" + what, "error", "high", r["line"], "%s target `%s` does not exist in the repo" % (what, dest),
                    target=dest)
            elif state == "case":
                add("broken-" + what, "error", "high", r["line"],
                    "%s target `%s` exists only with different letter case; GitHub paths are case-sensitive" % (what, dest),
                    target=dest)
            elif repo.tracked is not None and rel != "." and rel not in repo.tracked:
                add("untracked-target", "warning", "medium", r["line"],
                    "`%s` exists here but is not tracked by git, so GitHub will not have it until it is committed" % dest,
                    target=dest)
            elif frag and os.path.splitext(rel)[1].lower() in MD_EXT:
                other = repo.doc(rel)
                if other is not None:
                    check_anchor(other, frag, r["line"], rel, add)
    external = list(dict.fromkeys(external))
    seen = set(external)
    for m in BARE_URL_RE.finditer(doc.nocode):
        u = m.group().rstrip(".,;:!?*_~")
        if u not in seen:
            seen.add(u)
            external.append(u)

    for f in doc.fences:
        if not f["lang"]:
            add("fence-no-language", "warning", "high", f["line"], "code fence has no language tag")

    prev = None
    for h in doc.headings:
        if prev and h["level"] > prev["level"] + 1:
            add("heading-skip", "warning", "high", h["line"],
                "heading jumps from h%d to h%d: \"%s\"" % (prev["level"], h["level"], short(h["text"].strip(), 60)))
        prev = h

    images = [r for r in refs if r["kind"] == "image" and not r.get("source")]
    badges = [r for r in images if BADGE_RE.search(r["dest"])]
    words = len(re.findall(r"\w+", doc.nocode))
    mid = [h for h in doc.headings if h["level"] in (2, 3)]
    anchor_lines = [ln for ln in doc.nocode_lines if re.search(r"\]\(#", ln)]
    has_toc = len(anchor_lines) >= 3 or any(len(re.findall(r"\]\(#", ln)) >= 3 for ln in anchor_lines)
    if len(mid) >= TOC_HEADINGS and not has_toc:
        add("toc-suggested", "info", "low", None,
            "%d level-2/3 headings and no table of contents; GitHub's outline button helps, but a short TOC or "
            "nav line under the title helps more" % len(mid))
    if len(badges) > MANY_BADGES:
        add("many-badges", "info", "low", None, "%d badges; keep the ones a visitor acts on" % len(badges))
    if size > GITHUB_TRUNCATE:
        add("readme-truncated", "error", "high", None, "README is %d KiB; GitHub truncates past 500 KiB" % (size // 1024))
    elif words > LONG_WORDS:
        add("long-readme", "info", "low", None, "%d words; consider moving reference material to docs/" % words)

    first = first_screen_facts(doc, refs, first_screen, add)
    sections = section_facts(doc)
    for name in ("install", "usage"):
        if name not in sections:
            add("missing-section", "warning", "medium", None, "no %s section (matched by heading words)" % name)
    for name in ("examples", "configuration", "contributing", "license"):
        if name not in sections:
            add("missing-section", "info", "low", None, "no %s section (matched by heading words)" % name)
    lic = license_facts(repo, doc, sections, refs, add)
    check_commands(repo, doc, add)

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["line"] or 0))
    summary = {s: sum(1 for f in findings if f["severity"] == s) for s in ("error", "warning", "info")}
    return {
        "readme": readme_rel,
        "other_readmes": others,
        "stats": {"lines": len(doc.lines), "words": words, "bytes": size, "headings": len(doc.headings),
                  "images": len(images), "badges": len(badges), "links": len(refs) - len(images),
                  "code_fences": len(doc.fences), "git_tracking_checked": repo.tracked is not None},
        "first_screen": first,
        "sections": {"present": sections, "missing": [s for s in SECTION_RE if s not in sections]},
        "license": lic,
        "headings": [{"line": h["line"], "level": h["level"], "text": h["text"].strip(), "anchor": h["anchor"]}
                     for h in doc.headings],
        "findings": findings,
        "not_checked": {"external": external, "outside_repo": outside, "submodule": submodule, "github_page": page},
        "summary": summary,
    }


def check_anchor(doc, frag, line, where, add):
    frag = unquote(frag)
    if frag in doc.anchors or frag.lower() == "top" or frag == "":
        return  # an empty fragment or #top scrolls to the top of the page (HTML spec)
    if frag.lower() in {a.lower() for a in doc.anchors}:
        add("anchor-case", "warning", "medium", line,
            "#%s matches a heading only if case is ignored; GitHub's ids are lower-case" % frag)
        return
    near = [a for a in doc.anchors if a.replace("\ufe0f", "").strip("-") == frag.replace("\ufe0f", "").strip("-")]
    hint = ""
    if near and "\ufe0f" in near[0]:
        hint = (" (the heading's emoji ends in an invisible U+FE0F that GitHub keeps in the id: link to #%s)"
                % near[0].replace("\ufe0f", "%EF%B8%8F"))
    elif near:
        hint = " (closest id: #%s)" % near[0]
    add("broken-anchor", "error", "high", line, "#%s matches no heading or id in %s%s" % (frag, where, hint),
        target="#" + frag)


def short(s, n=80):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 3] + "..."


def plain(line):
    s = re.sub(r"!\[[^\]]*\](?:\([^)]*\)|\[[^\]]*\])", "", line)
    s = re.sub(r"\[([^\]]*)\](?:\([^)]*\)|\[[^\]]*\])", r"\1", s)
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"^\s*(?:>\s*)+|^\s*(?:[-*+]|\d+[.)])\s+|[*_~`]", "", s)
    return " ".join(html.unescape(s).split())


def first_screen_facts(doc, refs, n, add):
    heads = [h for h in doc.headings if h["line"] <= n]
    title = next((h for h in heads if h["level"] == 1), None)
    if title is None and heads and not any(plain(ln) for ln in doc.nocode_lines[:heads[0]["line"] - 1]
                                           if not re.match(r"^\s*<", ln)):
        title = heads[0]   # an h2-h6 that opens the page still works as the title
        add("title-level", "info", "low", title["line"], "the title is an h%d, not h1" % title["level"])
    if title is None:
        logo = next((r for r in refs if r["kind"] == "image" and r["line"] <= (heads[0]["line"] if heads else n)
                     and not BADGE_RE.search(r["dest"]) and (r["alt"] or "").strip()), None)
        first = " (first heading: h%d \"%s\" at line %d)" % (heads[0]["level"], short(heads[0]["text"].strip(), 40),
                                                            heads[0]["line"]) if heads else ""
        if logo:
            add("no-title", "info", "low", None, "no h1 in the first %d lines%s; the image at line %d (alt \"%s\") may "
                "stand in for one" % (n, first, logo["line"], short(logo["alt"], 40)))
        else:
            add("no-title", "warning", "medium", None, "no h1 in the first %d lines%s" % (n, first))
    heading_lines = doc.heading_lines
    desc = None
    for i in range(min(n, len(doc.nocode_lines))):
        ln = doc.nocode_lines[i]
        if i + 1 in heading_lines or SETEXT_RE.match(ln) or i in doc.def_lines:
            continue
        text = plain(ln)
        links = len(re.findall(r"\]\(|<a\s", ln))
        outside = plain(re.sub(r"\[[^\]]*\]\([^)]*\)|<a\s[^>]*>.*?</a>", "", ln))
        if len(re.findall(r"\w+", text)) >= 4 and not (links and len(re.findall(r"\w+", outside)) < 3):
            j = i + 1   # a paragraph can wrap over several source lines
            while (j < len(doc.nocode_lines) and doc.nocode_lines[j].strip() and j + 1 not in heading_lines
                   and not re.match(r"^\s*(?:[-*+>|<]|\d+[.)]\s)", doc.nocode_lines[j])
                   and not re.match(r"^\s*(?:[-*+]|\d+[.)])\s", ln)):
                text += " " + plain(doc.nocode_lines[j])
                j += 1
            desc = {"line": i + 1, "text": short(text, 200), "chars": len(text)}
            break
    if desc is None:
        add("no-description", "warning", "medium", None, "no one-line description in the first %d lines" % n)
    seen = None
    for h in heads:
        if re.search(SECTION_RE["install"] + "|" + SECTION_RE["usage"], h["text"].lower()):
            seen = {"line": h["line"], "how": "heading", "text": short(h["text"].strip(), 60)}
            break
    for f in doc.fences:
        if f["line"] <= n and any(INSTALL_CMD_RE.search(b) for b in f["body"]):
            if seen is None or f["line"] < seen["line"]:
                seen = {"line": f["line"], "how": "command", "text": short(next(
                    b for b in f["body"] if INSTALL_CMD_RE.search(b)).strip(), 60)}
            break
    for i, ln in enumerate(doc.nocode_lines[:n]):
        for m in re.finditer(r"\[([^\]]*)\]\(#([^)]*)\)", ln):
            if re.search(SECTION_RE["install"] + "|" + SECTION_RE["usage"], (m.group(1) + " " + m.group(2)).lower()):
                if seen is None or i + 1 < seen["line"]:
                    seen = {"line": i + 1, "how": "nav link", "text": short(m.group(1), 60)}
                break
    if seen is None:
        add("install-not-on-first-screen", "warning", "medium", None,
            "neither an install/usage heading, an install command nor a link to one in the first %d lines" % n)
    return {"lines": n, "title": title and {"line": title["line"], "level": title["level"],
                                            "text": short(title["text"].strip(), 100)},
            "description": desc, "install_or_usage": seen}


def section_facts(doc):
    present = {}
    for h in doc.headings:
        t = h["text"].lower()
        for name, rx in SECTION_RE.items():
            if name not in present and re.search(rx, t):
                present[name] = {"line": h["line"], "heading": short(h["text"].strip(), 60)}
    return present


def section_body(doc, line):
    head = next(h for h in doc.headings if h["line"] == line)
    nxt = [h["line"] for h in doc.headings if h["line"] > line and h["level"] <= head["level"]]
    end = nxt[0] - 1 if nxt else len(doc.lines)
    return "\n".join(doc.nocode_lines[line:end])


def license_facts(repo, doc, sections, refs, add):
    files = sorted(n for n in safe_listdir(repo.root) if LICENSE_FILE_RE.match(n)
                   and os.path.isfile(os.path.join(repo.root, n)))
    file_fams = []
    for f in files:
        for fam in license_families(read(os.path.join(repo.root, f), 20000) or "", LICENSE_TEXT):
            if fam not in file_fams:
                file_fams.append(fam)
    claim_text = ""
    if "license" in sections:
        claim_text += sections["license"]["heading"] + "\n" + section_body(doc, sections["license"]["line"])
    for r in refs:
        if r["kind"] == "image" and ("licen" in r["dest"].lower() or "licen" in (r["alt"] or "").lower()):
            claim_text += "\n" + r["dest"].replace("-", " ") + " " + (r["alt"] or "")
    claims = license_families(claim_text, LICENSE_CLAIM) if claim_text else []
    if claim_text and not files:
        add("license-file-missing", "error", "high", sections.get("license", {}).get("line"),
            "the README mentions a license%s but the repo root has no LICENSE, LICENCE or COPYING file"
            % (" (%s)" % ", ".join(claims) if claims else ""))
    elif claims and file_fams and set(claims).isdisjoint(file_fams):
        add("license-mismatch", "warning", "medium", sections.get("license", {}).get("line"),
            "the README says %s but %s reads like %s" % (", ".join(claims), ", ".join(files), ", ".join(file_fams)))
    return {"files": files, "file_families": file_fams, "readme_mentions_license": bool(claim_text),
            "readme_families": claims}


# ---------------------------------------------------------------- install / usage commands

def shell_commands(fence):
    lines, buf = [], ""
    for b in fence["body"]:
        s = b.rstrip()
        if fence["lang"] in PROMPT_ONLY:
            m = re.match(r"^\s*[$%]\s+(.*)$", s)
            if not m and not buf:
                continue
            s = m.group(1) if m else s
        else:
            s = re.sub(r"^\s*(?:\$|%|PS[^>]*>|>)\s+", "", s)
        if s.endswith("\\") or s.endswith("`"):
            buf += s[:-1] + " "
            continue
        lines.append(buf + s)
        buf = ""
    if buf:
        lines.append(buf)
    cmds = []
    for ln in lines:
        if ln.lstrip().startswith("#"):
            continue
        for part in re.split(r"&&|\|\||;|\|", ln):
            try:
                words = shlex.split(part, comments=True)
            except ValueError:
                words = part.split()
            while words and (words[0] in ("sudo", "time", "env") or re.match(r"^[A-Za-z_]\w*=", words[0])):
                words = words[1:]
            if words:
                cmds.append(words)
    return cmds


def up_has(repo, cwd, names):
    """True if one of names exists in repo-relative cwd or any parent inside the repo."""
    d = cwd
    while True:
        if any(os.path.isfile(os.path.join(repo.root, d, n)) for n in names):
            return True
        if d in ("", "."):
            return False
        d = os.path.dirname(d)


def check_commands(repo, doc, add):
    """Commands start in the repo root; the directory carries over between blocks under the same heading."""
    clones = set()
    cwd, section = "", None
    for f in doc.fences:
        if f["lang"] not in SHELL_LANGS:
            continue
        head = f["heading"]
        top = head is None or head["level"] == 1 or head is doc.headings[0]
        chain, lvl = [], 7
        for h in reversed([h for h in doc.headings if h["line"] < f["line"]]):
            if h["level"] < lvl:
                chain.append(h["text"].lower())
                lvl = h["level"]
        if not top and not any(re.search(COMMAND_SECTION_RE, t) for t in chain):
            continue
        names_ok = top or any(re.search(SECTION_RE["install"], t) for t in chain)
        if head is not section:
            cwd, section = "", head
        downloaded = " ".join(b for b in f["body"] if re.search(r"\b(curl|wget|Invoke-WebRequest|iwr)\b", b))
        for words in shell_commands(f):
            if words[:2] == ["git", "clone"]:
                args, k = [], 2
                while k < len(words):
                    if words[k] in ("--depth", "-b", "--branch", "-o", "--origin", "-c", "--config", "--reference",
                                    "-j", "--jobs", "--template", "-u", "--upload-pack", "--separate-git-dir"):
                        k += 1
                    elif not words[k].startswith("-"):
                        args.append(words[k])
                    k += 1
                if args:
                    clones.add(args[1] if len(args) > 1 else re.sub(r"\.git$", "", args[0].rstrip("/").split("/")[-1]))
            cwd = check_command(repo, words, cwd, f["line"], names_ok, downloaded, add, clones)


def at(cwd, p):
    """Repo-relative path of p run from cwd, or None when it points outside the repo (absolute, ~, $VAR, ..)."""
    if cwd is None or p.startswith(("/", "~", "$", "%")) or re.match(r"^[A-Za-z]:", p):
        return None
    rel = os.path.normpath(os.path.join(cwd, p)).replace(os.sep, "/")
    return None if rel == ".." or rel.startswith("../") else rel


def exists(repo, rel, *names):
    """True when rel (or rel/<one of names>) is a file in the repo; also True when rel is outside it (unknown)."""
    if rel is None:
        return True
    return any(os.path.isfile(os.path.join(repo.root, rel, n)) for n in names) if names \
        else os.path.exists(os.path.join(repo.root, rel))


def check_command(repo, w, cwd, line, names_ok, downloaded, add, clones=()):
    cmd = w[0]
    if cmd == "cd":
        if len(w) < 2 or w[1].startswith(("/", "~", "$", "-", "%")) or re.match(r"^[A-Za-z]:", w[1]):
            return None   # left the repo (home, absolute path, variable): stop checking this block
        target = at(cwd, w[1])
        if target is None:
            return None
        if os.path.isdir(os.path.join(repo.root, target)):
            return "" if target == "." else target
        # the folder `git clone` just made is the repo root; any other unknown folder (mkdir build) is not
        return "" if w[1].rstrip("/") in clones else None
    if cwd is None:
        return None
    where = cwd or "the repo root"
    pj = os.path.join(repo.root, cwd, "package.json")
    if cmd in ("npm", "pnpm", "yarn", "bun") and (len(w) == 1 and cmd == "yarn" or len(w) > 1):
        sub = w[1] if len(w) > 1 else "install"
        args = [a for a in w[2:] if not a.startswith("-")]
        if sub in ("install", "i", "ci", "add") and not args and sub != "add":
            if not os.path.isfile(pj):
                add("command-mismatch", "error", "high", line, "`%s` but there is no package.json in %s" % (" ".join(w[:2]), where))
            elif cmd == "npm" and sub == "ci" and not any(os.path.isfile(os.path.join(repo.root, cwd, n))
                                                           for n in ("package-lock.json", "npm-shrinkwrap.json")):
                add("command-mismatch", "error", "high", line,
                    "`npm ci` needs package-lock.json or npm-shrinkwrap.json and %s has neither" % where)
        elif sub in ("install", "i", "add") and args and names_ok and "-g" not in w and "--global" not in w:
            node = node_project(os.path.join(repo.root, cwd))
            if node and node["name"] and not node["private"]:
                pkgs = [npm_name(a) for a in args]
                pkgs = [p for p in pkgs if p]
                if pkgs and node["name"] not in pkgs and not set(pkgs) <= node["deps"]:
                    add("command-mismatch", "warning", "medium", line,
                        "`%s` installs %s but package.json names this package `%s`"
                        % (short(" ".join(w), 60), ", ".join(p for p in pkgs if p not in node["deps"]), node["name"]))
        elif sub in ("run", "run-script") and len(w) > 2 and not (cmd == "bun" and "." in w[2]):
            node = node_project(os.path.join(repo.root, cwd))
            if node is None:
                add("command-mismatch", "error", "high", line, "`%s` but there is no package.json in %s" % (short(" ".join(w[:3])), where))
            elif w[2] not in node["scripts"]:
                add("command-mismatch", "error", "high", line,
                    "`%s`: package.json has no \"%s\" script" % (short(" ".join(w[:3])), w[2]))
        return cwd
    pip = None
    if re.match(r"^pip[0-9.]*$", cmd) and len(w) > 1 and w[1] == "install":
        pip = w[2:]
    elif re.match(r"^python[0-9.]*$|^py$", cmd) and w[1:4] == ["-m", "pip", "install"]:
        pip = w[4:]
    elif cmd == "uv" and w[1:3] == ["pip", "install"]:
        pip = w[3:]
    elif cmd == "pipx" and w[1:2] == ["install"]:
        pip = w[2:]
    elif cmd == "uv" and w[1:2] == ["add"]:
        pip = w[2:]
    if pip is not None:
        check_pip(repo, pip, cwd, line, names_ok, add, " ".join(w))
        return cwd
    if cmd == "cargo" and len(w) > 1:
        if w[1] == "install":
            if "--path" in w:
                p = w[w.index("--path") + 1] if w.index("--path") + 1 < len(w) else "."
                if not exists(repo, at(cwd, p), "Cargo.toml"):
                    add("command-mismatch", "error", "high", line, "`cargo install --path %s` but there is no Cargo.toml there" % p)
            elif names_ok:
                ct = read(os.path.join(repo.root, cwd, "Cargo.toml")) or ""
                name = toml_str(toml_table(ct, "package"), "name")
                crates = [a for a in w[2:] if not a.startswith("-")]
                if name and "[workspace]" not in ct and crates and name not in crates and "--git" not in w:
                    add("command-mismatch", "warning", "medium", line,
                        "`%s` installs %s but Cargo.toml names this crate `%s`" % (short(" ".join(w), 60), ", ".join(crates), name))
        elif w[1] in ("build", "run", "test", "check", "bench") and not up_has(repo, cwd, ["Cargo.toml"]):
            add("command-mismatch", "error", "high", line, "`cargo %s` but there is no Cargo.toml in %s or above" % (w[1], where))
        return cwd
    if cmd == "go" and len(w) > 1:
        args = [a for a in w[2:] if not a.startswith("-")]
        local = not args or any(a.startswith(".") for a in args)
        if w[1] in ("build", "run", "test", "install", "generate", "vet") and local:
            if not up_has(repo, cwd, ["go.mod"]):
                add("command-mismatch", "error", "high", line, "`go %s` but there is no go.mod in %s or above" % (w[1], where))
        elif w[1] in ("install", "get") and names_ok:
            gm = read(os.path.join(repo.root, cwd, "go.mod")) or ""
            m = re.search(r"^module\s+(\S+)", gm, re.M)
            if m:
                mod = m.group(1)
                paths = [a.split("@")[0] for a in args]
                if paths and not any(p == mod or p.startswith(mod + "/") or p in gm for p in paths):
                    add("command-mismatch", "warning", "medium", line,
                        "`%s` installs %s but go.mod declares module `%s`" % (short(" ".join(w), 60), ", ".join(paths), mod))
        return cwd
    if cmd in ("make", "gmake"):
        check_make(repo, w[1:], cwd, line, add)
        return cwd
    if cmd in ("docker", "docker-compose", "podman"):
        args = w[1:] if cmd != "docker-compose" else ["compose"] + w[1:]
        if args[:1] == ["compose"]:
            files = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-f", "--file")]
            for fpath in files:
                if not exists(repo, at(cwd, fpath)):
                    add("command-mismatch", "error", "high", line, "`%s` but %s does not exist" % (short(" ".join(w), 60), fpath))
            if not files and not up_has(repo, cwd, ["compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"]):
                add("command-mismatch", "error", "high", line,
                    "`%s` but there is no compose.yaml, compose.yml, docker-compose.yaml or docker-compose.yml in %s"
                    % (short(" ".join(w), 60), where))
        elif args[:1] == ["build"] or args[:2] == ["buildx", "build"] or args[:2] == ["image", "build"]:
            check_docker_build(repo, args[1:] if args[0] == "build" else args[2:], cwd, line, add, " ".join(w))
        return cwd
    script = None
    if re.match(r"^\./.+\.(sh|py|js|mjs|ts|rb|pl|ps1|bat)$", cmd):
        script = cmd
    elif cmd in ("bash", "sh", "zsh", "python", "python3", "node", "ruby", "perl", "pwsh") and len(w) > 1 \
            and re.match(r"^[\w./-]+\.(sh|py|js|mjs|ts|rb|pl|ps1)$", w[1]) and not w[1].startswith("-"):
        script = w[1]
    if script and os.path.basename(script) not in downloaded and not exists(repo, at(cwd, script)):
        add("command-mismatch", "warning", "medium", line, "`%s` but %s does not exist in %s" % (short(" ".join(w), 60), script, where))
    return cwd


def npm_name(arg):
    if re.match(r"^(?:\.|/|~|file:|git|https?:|github:|[\w.-]+/[\w.-]+$)", arg):
        return None   # path, URL or user/repo shorthand: not a registry name
    if arg.startswith("@"):
        parts = arg.split("@")
        return "@" + parts[1]
    return arg.split("@")[0] or None


def check_pip(repo, args, cwd, line, names_ok, add, text):
    names = []
    i = 0
    while i < len(args):
        a = args[i]
        val = None
        for opt in ("-r", "--requirement", "-c", "--constraint", "-e", "--editable"):
            if a == opt and i + 1 < len(args):
                val, i = (opt, args[i + 1]), i + 1
            elif a.startswith(opt) and len(a) > len(opt) and (opt.startswith("--") and a[len(opt)] == "=" or not opt.startswith("--")):
                val = (opt, a[len(opt):].lstrip("="))
        i += 1
        if val:
            opt, v = val
            if opt in ("-r", "--requirement", "-c", "--constraint"):
                if not SCHEME_RE.match(v) and not exists(repo, at(cwd, v)):
                    add("command-mismatch", "error", "high", line, "`%s` but %s does not exist" % (short(text, 60), v))
                continue
            a = v
        if a.startswith("-"):
            continue
        if a.startswith((".", "/")) or (val and not SCHEME_RE.match(a)):
            p = at(cwd, re.sub(r"\[.*\]$", "", a))
            if not exists(repo, p, "pyproject.toml", "setup.py"):
                add("command-mismatch", "error", "high", line,
                    "`%s` but %s has no pyproject.toml or setup.py, so pip cannot install it" % (short(text, 60), p or "."))
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", a)
        if m and not SCHEME_RE.match(a):
            names.append(pep503(m.group(1)))
    if names and names_ok:
        proj, deps = python_project(os.path.join(repo.root, cwd))
        if proj and proj.isdisjoint(names) and not set(names) <= deps:
            add("command-mismatch", "warning", "medium", line,
                "`%s` installs %s but the project is named %s" % (short(text, 60), ", ".join(n for n in names if n not in deps),
                                                                  ", ".join(sorted(proj))))


def check_make(repo, args, cwd, line, add):
    i, targets, d = 0, [], cwd
    while i < len(args):
        a = args[i]
        if a in ("-C", "--directory") and i + 1 < len(args):
            d, i = at(cwd, args[i + 1]), i + 2
            if d is None:
                return
            continue
        if a in ("-f", "--file", "--makefile", "-j", "--jobs", "-o", "-W") and i + 1 < len(args):
            if a in ("-f", "--file", "--makefile"):
                return   # an explicit makefile: not worth guessing
            i += 2
            continue
        if not a.startswith("-") and "=" not in a:
            targets.append(a)
        i += 1
    mk = next((n for n in ("GNUmakefile", "makefile", "Makefile") if os.path.isfile(os.path.join(repo.root, d, n))), None)
    where = d or "the repo root"
    if mk is None:
        add("command-mismatch", "error", "high", line, "`make%s` but there is no Makefile in %s"
            % ("".join(" " + t for t in targets), where))
        return
    text = read(os.path.join(repo.root, d, mk)) or ""
    heads = "".join(re.findall(r"^[^\t#\n][^:=\n]*:", text, re.M))
    if re.search(r"^\s*-?s?include\s", text, re.M) or "%" in heads or "$" in heads:
        return   # included makefiles, pattern rules or $(VAR) targets: targets cannot be listed reliably
    defined = set()
    for m in re.finditer(r"^([^\t#\n:=][^:=\n]*?)\s*::?(?!=)", text, re.M):
        defined |= set(m.group(1).split())
    for t in targets:
        if t not in defined and "$" not in t:
            add("command-mismatch", "error", "high", line, "`make %s` but %s/%s has no target `%s`" % (t, where, mk, t))


def check_docker_build(repo, args, cwd, line, add, text):
    takes_value = {"-t", "--tag", "-f", "--file", "--build-arg", "--target", "--platform", "-o", "--output",
                   "--label", "--secret", "--ssh", "--cache-from", "--cache-to", "--network", "--progress",
                   "--build-context", "--iidfile", "--metadata-file", "--add-host", "--shm-size", "-m", "--memory"}
    i, pos, dockerfile = 0, [], None
    while i < len(args):
        a = args[i]
        if a in takes_value and i + 1 < len(args):
            if a in ("-f", "--file"):
                dockerfile = args[i + 1]
            i += 2
            continue
        if a.startswith("--file=") or (a.startswith("-f") and len(a) > 2):
            dockerfile = a.split("=", 1)[1] if "=" in a else a[2:]
        elif a.startswith("-"):
            if "=" not in a and a not in ("-q", "--quiet", "--no-cache", "--pull", "--load", "--push", "--rm"):
                return   # an option we do not know might take a value: do not guess the context
        else:
            pos.append(a)
        i += 1
    if len(pos) != 1 or SCHEME_RE.match(pos[0]) or pos[0] == "-":
        return
    ctx = at(cwd, pos[0])
    if dockerfile == "-" or ctx is None:
        return
    path = at(cwd, dockerfile) if dockerfile else ctx + "/Dockerfile"
    if not exists(repo, path):
        add("command-mismatch", "error", "high", line, "`%s` but %s does not exist" % (short(text, 60), path.lstrip("./") or path))


# ---------------------------------------------------------------- output

def text_report(rep, top):
    out = []
    st = rep["stats"]
    out.append("README audit: %s (%d lines, %d words, %d headings, %d images, %d badges, %d code fences)"
               % (rep["readme"], st["lines"], st["words"], st["headings"], st["images"], st["badges"], st["code_fences"]))
    if rep["other_readmes"]:
        out.append("  other README files GitHub does not show first: %s" % ", ".join(rep["other_readmes"]))
    fs = rep["first_screen"]
    out.append("")
    out.append("First %d lines" % fs["lines"])
    out.append("  title:       %s" % ("line %d h%d \"%s\"" % (fs["title"]["line"], fs["title"]["level"], fs["title"]["text"])
                                     if fs["title"] else "none"))
    out.append("  description: %s" % ("line %d \"%s\"" % (fs["description"]["line"], short(fs["description"]["text"], 90))
                                     if fs["description"] else "none"))
    io = fs["install_or_usage"]
    out.append("  install/usage: %s" % ("line %d (%s) %s" % (io["line"], io["how"], io["text"]) if io else "not in the first screen"))
    sec = rep["sections"]
    out.append("")
    out.append("Sections: " + ", ".join("%s (line %d)" % (k, v["line"]) for k, v in sec["present"].items())
               + ("; missing: " + ", ".join(sec["missing"]) if sec["missing"] else ""))
    lic = rep["license"]
    out.append("License:  files %s%s; README %s"
               % (", ".join(lic["files"]) or "none", " (%s)" % ", ".join(lic["file_families"]) if lic["file_families"] else "",
                  ("mentions " + (", ".join(lic["readme_families"]) or "a license")) if lic["readme_mentions_license"]
                  else "does not mention a license"))
    s = rep["summary"]
    out.append("")
    out.append("Findings: %d errors, %d warnings, %d suggestions" % (s["error"], s["warning"], s["info"]))
    for sev in ("error", "warning", "info"):
        group = [f for f in rep["findings"] if f["severity"] == sev]
        for f in group[:top]:
            out.append("  %-7s %-6s %-26s %s%s" % (sev, f["confidence"], f["kind"],
                                                   "line %d: " % f["line"] if f["line"] else "", f["message"]))
        if len(group) > top:
            out.append("  ... %d more %s findings (--json lists all)" % (len(group) - top, sev))
    nc = rep["not_checked"]
    out.append("")
    out.append("Not checked (no network): %d external URLs, %d links leaving the repo, %d in submodules, %d GitHub page anchors"
               % (len(nc["external"]), len(nc["outside_repo"]), len(nc["submodule"]), len(nc["github_page"])))
    for u in (nc["outside_repo"] + nc["submodule"] + nc["github_page"] + nc["external"])[:top]:
        out.append("  " + u)
    rest = sum(len(v) for v in nc.values()) - top
    if rest > 0:
        out.append("  ... %d more (--json lists all)" % rest)
    if not st["git_tracking_checked"]:
        out.append("Not a git work tree (or git missing): files were not checked for being tracked.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="\n".join(__doc__.split("\n")[2:]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-C", dest="repo", default=".")
    ap.add_argument("--readme")
    ap.add_argument("--first-screen", type=int, default=30)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = os.path.realpath(a.repo)
    if not os.path.isdir(root):
        print("error: %s is not a directory" % a.repo, file=sys.stderr)
        return 2
    others = []
    if a.readme:
        full = os.path.realpath(a.readme if os.path.isabs(a.readme) else os.path.join(root, a.readme))
        if not os.path.isfile(full) or not (full == root or full.startswith(root + os.sep)):
            print("error: %s is not a file inside %s" % (a.readme, a.repo), file=sys.stderr)
            return 2
        rel = os.path.relpath(full, root).replace(os.sep, "/")
    else:
        rel, others = find_readme(root)
        if rel is None:
            print("error: no README in .github/, the repo root or docs/", file=sys.stderr)
            return 2
    if os.path.splitext(rel)[1].lower() not in MD_EXT:
        print("error: %s is not Markdown; this audit reads Markdown READMEs only" % rel, file=sys.stderr)
        return 2
    rep = audit(root, rel, max(1, a.first_screen), others)
    print(json.dumps(rep, indent=2, ensure_ascii=False) if a.json else text_report(rep, max(1, a.top)))
    return 1 if rep["summary"]["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
