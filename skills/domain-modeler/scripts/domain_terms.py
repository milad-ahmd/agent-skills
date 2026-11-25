#!/usr/bin/env python3
"""List the domain terms a codebase uses and flag naming that may hide one concept under several names.
Stdlib only, read-only.

Usage:
  python3 domain_terms.py [PATH] [--json] [--top N] [--examples N] [--exclude GLOB ...]
  python3 domain_terms.py [PATH] --glossary GLOSSARY.md [--json]

Reads declarations, not behaviour: class / interface / type / struct / record / enum names and enum
members in Python, TS/JS, Go, Java, Kotlin, C# and Ruby; CREATE TABLE and CREATE TYPE ... AS ENUM in
.sql files; Prisma models and enums; table names from __tablename__, db_table, @Entity("..."),
@Table(name = "..."), [Table("...")] and create_table. Test files, vendored and build folders are skipped.

Each name is split into words (CamelCase, snake_case), technical suffixes and prefixes are dropped
(Service, Controller, Repository, Dto, Impl, Handler, Model, Entity, Request, Base, Abstract, I...),
plural words are made singular, and the rest is the term. Names with a technical word such as Http,
Api, Json or Redis are left out. The report shows:
  terms            each term, its declarations and how many files mention those names
  synonyms         words from one built-in synonym group used side by side (customer/client/user...)
  multi-defined    the same term declared in several folders (two bounded contexts, or a copy?)
  near-duplicates  spellings that differ slightly (cancelled/canceled) or split differently (CheckOut/Checkout)
These are candidates to ask about, not facts: the script cannot tell what a name means.

--glossary reads "Avoid:" lines under each "## Term" heading of a glossary file (see SKILL.md) and
reports every declaration whose name uses an avoided word, with the term to use instead. Identifiers
listed on an "Allow:" line are exempt.

Exit codes: 0 report printed (or glossary check clean) · 1 glossary violations found ·
            2 nothing to scan (bad path, no supported files, no declarations) or unreadable glossary
"""
from __future__ import annotations

import argparse
import bisect
import difflib
import fnmatch
import json
import os
import re
import sys
from collections import Counter, defaultdict

LANG_BY_EXT = {
    ".py": "py", ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts", ".js": "ts", ".jsx": "ts",
    ".mjs": "ts", ".cjs": "ts", ".go": "go", ".java": "java", ".kt": "kt", ".kts": "kt", ".cs": "cs",
    ".rb": "rb", ".sql": "sql", ".prisma": "prisma",
}
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", "out", "target", "bin", "obj",
             "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache", ".next", ".nuxt",
             "coverage", ".gradle", ".idea", ".vscode", "generated", "__generated__",
             "test", "tests", "__tests__", "spec", "specs", "testdata", "__mocks__"}
TEST_FILE_RX = re.compile(r"(^test_.*\.py|_test\.(py|go|rb)|\.(test|spec)\.\w+|_spec\.rb|(Test|Tests|IT)\.(java|kt|cs)"
                          r"|\.d\.ts)$")
MAX_BYTES = 1_000_000

# Words that say how code is built, not what the business is. Dropped from either end of a name.
TECH_SUFFIX = {
    "service", "services", "controller", "repository", "repo", "manager", "dto", "impl", "handler", "model",
    "models", "entity", "entities", "request", "response", "factory", "builder", "helper", "helpers", "util",
    "utils", "adapter", "mapper", "serializer", "schema", "view", "viewset", "form", "config", "configuration",
    "exception", "error", "dao", "vo", "props", "args", "input", "output", "payload", "params", "options",
    "listener", "consumer", "producer", "resolver", "router", "module", "component", "interface",
    "abstract", "base", "default", "generic", "mock", "fake", "stub", "test", "tests", "spec", "fixture",
    "record", "row", "table", "struct", "mixin", "meta", "admin", "resource",
}
TECH_PREFIX = {"abstract", "base", "default", "generic", "mock", "fake", "stub", "test", "i"}
# A name containing any of these is plumbing (HttpClient, RedisCache, JsonPayload): left out entirely.
TECH_WORDS = {
    "http", "https", "api", "rest", "grpc", "rpc", "graphql", "json", "xml", "yaml", "csv", "redis", "kafka",
    "sql", "db", "database", "cache", "s3", "aws", "gcp", "azure", "jwt", "oauth", "cookie", "logger",
    "logging", "metrics", "middleware", "env", "settings", "app", "main", "migration", "seeder", "orm",
    "sqlalchemy", "django", "prisma", "typeorm", "spring", "express", "react", "socket", "websocket", "cron",
    "util", "utils", "string", "int", "dict", "array", "tuple", "ctx", "wrapper", "proxy", "plugin", "hook",
}
GO_BUILTIN = re.compile(r"^(u?int\d*|float\d+|complex\d+|string|bool|byte|rune|uintptr)$")
STRING_RX = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`[^`]*`")

# Built-in synonym groups: words people use for one concept. Variants map to their group's key.
SYNONYMS = {
    "party": ["customer", "client", "user", "account", "member", "buyer", "patron", "shopper"],
    "order": ["order", "purchase", "booking", "reservation"],
    "product": ["product", "item", "article", "sku", "good", "merchandise"],
    "invoice": ["invoice", "bill", "receipt"],
    "cancel": ["cancel", "cancelled", "canceled", "cancellation", "void", "voided", "revoke", "revoked",
               "revocation", "annul", "annulled"],
    "delete": ["delete", "deleted", "remove", "removed", "destroy", "destroyed", "purge", "purged"],
    "shipment": ["shipment", "delivery", "consignment", "parcel", "package"],
    "supplier": ["supplier", "vendor", "seller", "merchant"],
    "cart": ["cart", "basket", "bag"],
    "employee": ["employee", "staff", "worker", "personnel"],
    "company": ["company", "organization", "organisation", "org", "business", "firm"],
    "payment": ["payment", "charge", "transaction"],
    "address": ["address", "location"],
}
SYN_OF = {w: k for k, ws in SYNONYMS.items() for w in ws}

# --- declaration patterns ---------------------------------------------------------------------
MODS = (r"(?:(?:export|default|public|private|protected|internal|abstract|sealed|static|final|partial|data|"
        r"open|enum|declare|readonly|inner|value|const|async|annotation)\s+)*")
BRACE_DECL = {
    "ts": re.compile(r"^[ \t]*" + MODS + r"(class|interface|enum|type)\s+([A-Za-z_$][\w$]*)", re.M),
    "java": re.compile(r"^[ \t]*(?:@\w+(?:\([^)]*\))?\s+)*" + MODS + r"(class|interface|enum|record)\s+([A-Za-z_]\w*)", re.M),
    "kt": re.compile(r"^[ \t]*(?:@\w+(?:\([^)]*\))?\s+)*" + MODS + r"(class|interface|object)\s+([A-Za-z_]\w*)", re.M),
    "cs": re.compile(r"^[ \t]*(?:\[[^\]]*\]\s*)*" + MODS + r"(class|interface|enum|record|struct)\s+([A-Za-z_]\w*)", re.M),
    "prisma": re.compile(r"^[ \t]*(model|enum|type|view)\s+([A-Za-z_]\w*)\s*\{", re.M),
}
PY_CLASS = re.compile(r"^([ \t]*)class\s+([A-Za-z_]\w*)\s*(?:\(([^)]*)\))?\s*:", re.M)
PY_ENUM_BASE = re.compile(r"\b(Enum|IntEnum|StrEnum|Flag|IntFlag|TextChoices|IntegerChoices|Choices)\b")
GO_TYPE = re.compile(r"^type\s+([A-Za-z_]\w*)\s+(struct|interface|string|u?int\d*|float\d*)\b", re.M)
GO_CONST_BLOCK = re.compile(r"^const\s*\((.*?)^\)", re.M | re.S)
RB_CLASS = re.compile(r"^[ \t]*class\s+((?:[A-Z]\w*::)*([A-Z]\w*))", re.M)
SQL_TABLE = re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                       r"((?:[`\"\[]?\w+[`\"\]]?\.)*[`\"\[]?(\w+)[`\"\]]?)", re.I)
SQL_ENUM = re.compile(r"\bCREATE\s+TYPE\s+(?:[`\"]?\w+[`\"]?\.)?[`\"]?(\w+)[`\"]?\s+AS\s+ENUM\s*\(([^)]*)\)", re.I)
TABLE_HINTS = re.compile(
    r"__tablename__\s*=\s*['\"](\w+)|\bdb_table\s*=\s*['\"](\w+)|@Entity\(\s*(?:\{\s*name\s*:\s*)?['\"](\w+)"
    r"|@Table\(\s*name\s*=\s*\"(\w+)|\[Table\(\s*\"(\w+)|\bcreate_table\s*\(?\s*[:'\"](\w+)")
SPLIT_RX = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def split_words(name):
    """'OrderLineItemDTO' -> ['order', 'line', 'item', 'dto']; 'order_status' -> ['order', 'status']."""
    out = []
    for part in re.split(r"[_\-$\s.]+", name):
        out += [w.lower() for w in SPLIT_RX.findall(part)]
    return out


def singular(w):
    # shortcut: suffix rules only, wrong for some irregular words (e.g. "causes" -> "caus"); fine for matching
    if len(w) <= 3 or w.endswith(("ss", "is", "ics")) or (w.endswith("us") and len(w) > 4):
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("sses", "shes", "ches", "xes", "zes", "uses")):
        return w[:-2]
    if w.endswith("s"):
        return w[:-1]
    return w


def term_words(name):
    """Domain words of an identifier, or [] when it is plumbing or only technical words."""
    words = split_words(name)
    if any(w in TECH_WORDS for w in words):
        return []
    while len(words) > 1 and words[0] in TECH_PREFIX:
        words = words[1:]
    while words and words[-1] in TECH_SUFFIX:
        words = words[:-1]
    words = [w for w in words if not w.isdigit()]
    if not words or (len(words) == 1 and len(words[0]) < 2):
        return []
    return [singular(w) for w in words]


# --- scanning ---------------------------------------------------------------------------------
def iter_files(root, excludes):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root)
            if os.path.splitext(fn)[1].lower() not in LANG_BY_EXT or TEST_FILE_RX.search(fn):
                continue
            if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(fn, g) for g in excludes):
                continue
            yield path


def brace_body(text, start):
    """(body, offset) of the {...} block that opens at or after `start`."""
    i = text.find("{", start)
    if i < 0:
        return "", start
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], i + 1
    return text[i + 1:], i + 1


def enum_members(body, offset, lang):
    """[(name, absolute offset)] of the constants in an enum body."""
    clean = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group()), body, flags=re.S)
    clean = re.sub(r"//[^\n]*", lambda m: " " * len(m.group()), clean)
    clean = STRING_RX.sub(lambda m: " " * len(m.group()), clean)
    if lang in ("java", "kt"):
        semi = clean.find(";")
        clean = clean[:semi] if semi >= 0 else clean
    out = []
    if lang == "prisma":
        for m in re.finditer(r"^[ \t]*([A-Za-z_]\w*)\b", clean, re.M):
            out.append((m.group(1), offset + m.start(1)))
        return out
    depth, entry_start = 0, True
    for m in re.finditer(r"[(){}\[\],]|[A-Za-z_]\w*", clean):
        tok = m.group()
        if tok in "([{":
            depth += 1
        elif tok in ")]}":
            depth -= 1
        elif tok == ",":
            entry_start = entry_start or depth == 0
        elif depth == 0 and entry_start:
            out.append((tok, offset + m.start()))
            entry_start = False
    return out


def declarations(path, lang, text):
    """[(identifier, kind, offset, parent)] declared in one file."""
    out = []
    if lang == "py":
        for m in PY_CLASS.finditer(text):
            is_enum = bool(m.group(3) and PY_ENUM_BASE.search(m.group(3)))
            out.append((m.group(2), "enum" if is_enum else "class", m.start(2), None))
            if is_enum:
                indent, member_indent = len(m.group(1).expandtabs()), None
                pos = text.find("\n", m.end())
                while 0 <= pos < len(text):
                    nxt = text.find("\n", pos + 1)
                    line = text[pos + 1: nxt if nxt >= 0 else len(text)]
                    ind = len(line.expandtabs()) - len(line.expandtabs().lstrip())
                    if line.strip() and ind <= indent:
                        break
                    if line.strip() and member_indent is None:
                        member_indent = ind
                    mm = re.match(r"[ \t]+([A-Za-z_]\w*)\s*(?::[^=]*)?=(?!=)", line)
                    if mm and ind == member_indent and not mm.group(1).startswith("_"):
                        out.append((mm.group(1), "enum-member", pos + 1 + mm.start(1), m.group(2)))
                    pos = nxt
    elif lang == "go":
        for m in GO_TYPE.finditer(text):
            out.append((m.group(1), "struct" if m.group(2) == "struct" else
                        "interface" if m.group(2) == "interface" else "type", m.start(1), None))
        for blk in GO_CONST_BLOCK.finditer(text):
            parent = None  # the named type of an iota block; untyped or builtin-typed consts are not enum members
            for m in re.finditer(r"^[ \t]*([A-Za-z_]\w*)(?:[ \t]+([A-Za-z_]\w*))?[ \t]*(=|//|$)", blk.group(1), re.M):
                if m.group(3) == "=":
                    parent = m.group(2) if m.group(2) and not GO_BUILTIN.match(m.group(2)) else None
                if parent:
                    out.append((m.group(1), "enum-member", blk.start(1) + m.start(1), parent))
    elif lang == "rb":
        for m in RB_CLASS.finditer(text):
            out.append((m.group(2), "class", m.start(2), None))
    elif lang == "sql":
        for m in SQL_TABLE.finditer(text):
            out.append((m.group(2), "table", m.start(2), None))
        for m in SQL_ENUM.finditer(text):
            out.append((m.group(1), "enum", m.start(1), None))
            for v in re.finditer(r"'([^']+)'", m.group(2)):
                out.append((v.group(1), "enum-member", m.start(2) + v.start(1), m.group(1)))
    else:
        rx = BRACE_DECL[lang]
        for m in rx.finditer(text):
            kind, name = m.group(1), m.group(2)
            if lang == "prisma":
                kind = {"model": "model", "enum": "enum"}.get(kind, "type")
            elif re.search(r"\benum\s", m.group(0)) and kind != "enum":
                kind = "enum"  # Kotlin `enum class`
            out.append((name, kind, m.start(2), None))
            if kind == "enum":
                body, off = brace_body(text, m.start(2))
                out += [(n, "enum-member", o, name) for n, o in enum_members(body, off, lang)]
    if lang != "sql":
        for m in TABLE_HINTS.finditer(text):
            name = next(g for g in m.groups() if g)
            out.append((name, "table", m.start(), None))
    return out


def scan(root, excludes=()):
    root = os.path.abspath(root)
    base = root if os.path.isdir(root) else os.path.dirname(root)
    decls, files, mentions = [], 0, Counter()
    for path in iter_files(root, excludes):
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        files += 1
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        mentions.update(set(re.findall(r"[A-Za-z_]\w*", text)))
        starts = [0] + [m.end() for m in re.finditer("\n", text)]
        lang = LANG_BY_EXT[os.path.splitext(path)[1].lower()]
        for name, kind, off, parent in declarations(path, lang, text):
            words = term_words(name)
            if not words:
                continue
            bare = len(words) == len([w for w in split_words(name) if not w.isdigit()])
            decls.append({"name": name, "kind": kind, "bare": bare, "file": rel, "line": bisect.bisect_right(starts, off),
                          "parent": parent, "words": words, "term": " ".join(words)})
    for d in decls:
        d["mentioned_in_files"] = mentions[d["name"]]
    return files, decls


# --- analysis ---------------------------------------------------------------------------------
def loc(d):
    return "%s:%d" % (d["file"], d["line"])


def analyse(decls, examples=3):
    by_term = defaultdict(list)
    for d in decls:
        by_term[d["term"]].append(d)
    terms = sorted(({"term": t, "declarations": len(ds), "kinds": dict(Counter(d["kind"] for d in ds)),
                     "mentioned_in_files": max(d["mentioned_in_files"] for d in ds),
                     "names": sorted({d["name"] for d in ds}), "examples": [loc(d) for d in ds[:examples]]}
                    for t, ds in by_term.items()), key=lambda x: (-x["declarations"], -x["mentioned_in_files"], x["term"]))

    by_word = defaultdict(list)
    for d in decls:
        for w in set(d["words"]):
            by_word[w].append(d)
    groups = defaultdict(dict)
    for w, ds in by_word.items():
        if w in SYN_OF:
            groups[SYN_OF[w]][w] = {"declarations": len(ds), "names": sorted({d["name"] for d in ds})[:6],
                                    "examples": [loc(d) for d in ds[:examples]]}
    synonyms = [{"group": g, "words": dict(sorted(ws.items(), key=lambda kv: -kv[1]["declarations"]))}
                for g, ws in sorted(groups.items()) if len(ws) > 1]

    multi = []
    for t, ds in by_term.items():
        # bare names only (Order, not OrderService), and not the SQL mirror of a code model
        defs = [d for d in ds if d["bare"] and d["kind"] not in ("enum-member", "table") and not d["file"].endswith(".sql")]
        dirs = sorted({os.path.dirname(d["file"]) or "." for d in defs})
        if len(dirs) > 1:
            multi.append({"term": t, "folders": dirs,
                          "examples": ["%s (%s %s)" % (loc(d), d["kind"], d["name"]) for d in defs[:max(examples, len(dirs))]]})

    near = []
    words = sorted(w for w in by_word if len(w) >= 5)
    for i, a in enumerate(words):
        for b in words[i + 1:]:
            if b[0] != a[0]:
                break
            if abs(len(a) - len(b)) > 2 or b.startswith(a) or a.startswith(b):
                continue
            sm = difflib.SequenceMatcher(None, a, b)
            if sm.quick_ratio() >= 0.88 and sm.ratio() >= 0.88:
                near.append({"kind": "spelling", "variants": [a, b],
                             "examples": [loc(by_word[a][0]), loc(by_word[b][0])]})
    joined = defaultdict(set)
    for t in by_term:
        joined[t.replace(" ", "")].add(t)
    for key, ts in sorted(joined.items()):
        if len(ts) > 1:
            ts = sorted(ts)
            near.append({"kind": "word split", "variants": ts, "examples": [loc(by_term[t][0]) for t in ts]})
    return {"terms": terms, "synonyms": synonyms, "multi_defined": sorted(multi, key=lambda x: x["term"]),
            "near_duplicates": near}


# --- glossary check ---------------------------------------------------------------------------
def parse_glossary(text):
    """{'avoid': {word phrase: preferred term}, 'allow': {identifiers}, 'terms': [headings]}."""
    avoid, allow, terms, current = {}, set(), [], None
    for line in text.splitlines():
        h = re.match(r"^#{2,6}\s+(.+?)\s*#*\s*$", line)
        if h:
            current = h.group(1).strip("`*_ ")
            terms.append(current)
            continue
        m = re.match(r"^\s*(?:[-*+]\s*)?\**\s*(avoid|allow)\s*\**\s*:\s*\**\s*(.+)$", line, re.I)
        if not m:
            continue
        items = [x.strip(" `*_.\"'") for x in re.split(r"[,;]", m.group(2))]
        items = [x for x in items if x]
        if m.group(1).lower() == "allow":
            allow.update(items)
        elif current:
            for x in items:
                key = " ".join(singular(w) for w in split_words(x))
                if key:
                    avoid[key] = current
    return {"avoid": avoid, "allow": allow, "terms": terms}


def check_glossary(decls, gloss):
    # "Order (Checkout)" is the term Order in one context
    heading_keys = {" ".join(singular(w) for w in split_words(re.sub(r"\s*\(.*\)\s*$", "", t))) for t in gloss["terms"]}
    conflicts = sorted(k for k in gloss["avoid"] if k in heading_keys)
    out = []
    for d in decls:
        if d["name"] in gloss["allow"]:
            continue
        words = d["words"]
        for key, use in gloss["avoid"].items():
            if key in heading_keys:
                continue
            kw = key.split()
            if any(words[i:i + len(kw)] == kw for i in range(len(words) - len(kw) + 1)):
                out.append({"file": d["file"], "line": d["line"], "name": d["name"], "kind": d["kind"],
                            "avoid": key, "use": use})
    return out, conflicts


# --- output -----------------------------------------------------------------------------------
def print_report(root, files, decls, res, top):
    print("domain_terms: %d terms from %d declarations in %d files under %s" % (len(res["terms"]), len(decls), files, root))
    print("Candidates from names only; the code cannot say what a name means. Confirm with the user.\n")
    print("Terms (declarations, files mentioning, kinds, where)")
    for t in res["terms"][:top]:
        kinds = " ".join("%s×%d" % kv for kv in sorted(t["kinds"].items()))
        print("  %-24s %3d %4d  %-28s %s" % (t["term"], t["declarations"], t["mentioned_in_files"], kinds, ", ".join(t["examples"])))
    if len(res["terms"]) > top:
        print("  ... %d more (--top N or --json)" % (len(res["terms"]) - top))
    print("\nPossible synonyms: one concept under several names?")
    for g in res["synonyms"]:
        print("  [%s] %s" % (g["group"], " / ".join(g["words"])))
        for w, info in g["words"].items():
            print("    %-14s %3d  %s  e.g. %s" % (w, info["declarations"], ", ".join(info["names"][:4]), ", ".join(info["examples"])))
    if not res["synonyms"]:
        print("  none from the built-in table")
    print("\nSame term declared in several folders: two bounded contexts, or a copy?")
    for m in res["multi_defined"]:
        print("  %-20s %s" % (m["term"], "; ".join(m["examples"])))
    if not res["multi_defined"]:
        print("  none")
    print("\nNear-duplicate spellings")
    for n in res["near_duplicates"]:
        print("  %-11s %s  (%s)" % (n["kind"], " / ".join(n["variants"]), ", ".join(n["examples"])))
    if not res["near_duplicates"]:
        print("  none")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog="Exit codes: 0 report / glossary clean · 1 glossary violations · 2 nothing to scan",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".", help="folder or file to scan (default: .)")
    ap.add_argument("--glossary", metavar="FILE", help="check names against the Avoid: lines of this glossary")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--top", type=int, default=40, help="terms to print in the text report (default 40)")
    ap.add_argument("--examples", type=int, default=3, help="file:line examples per finding (default 3)")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip matching paths (repeatable)")
    a = ap.parse_args(argv)

    if not os.path.exists(a.path):
        print("domain_terms: %s does not exist" % a.path, file=sys.stderr)
        return 2
    files, decls = scan(a.path, a.exclude)
    if not decls:
        print("domain_terms: no declarations found in %d supported files under %s" % (files, a.path), file=sys.stderr)
        return 2

    if a.glossary:
        try:
            with open(a.glossary, encoding="utf-8") as fh:
                gloss = parse_glossary(fh.read())
        except OSError as e:
            print("domain_terms: cannot read glossary: %s" % e, file=sys.stderr)
            return 2
        if not gloss["avoid"]:
            print("domain_terms: %s has no 'Avoid:' lines under a '## Term' heading" % a.glossary, file=sys.stderr)
            return 2
        found, conflicts = check_glossary(decls, gloss)
        if a.json:
            print(json.dumps({"violations": found, "conflicts": conflicts, "declarations_checked": len(decls)}, indent=2))
        else:
            for c in conflicts:
                print("warning: '%s' is both a glossary term and an Avoid: word; not checked" % c)
            for f in found:
                print("%s:%d: %s %s uses '%s'; the glossary term is '%s'" % (f["file"], f["line"], f["kind"], f["name"], f["avoid"], f["use"]))
            print("%d glossary violation(s) in %d declarations" % (len(found), len(decls)))
        return 1 if found else 0

    res = analyse(decls, a.examples)
    if a.json:
        print(json.dumps(dict(res, root=a.path, files_scanned=files, declarations=decls), indent=2))
    else:
        print_report(a.path, files, decls, res, a.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
