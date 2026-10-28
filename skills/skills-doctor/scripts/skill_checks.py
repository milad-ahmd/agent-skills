"""Shared checks for skills-doctor and lint_skill (stdlib only, no PyYAML needed).

Covers: YAML frontmatter incl. folded (>) / literal (|) / quoted scalars, trigger-clause
extraction for overlap detection, and secret detection by key *shape* AND by *assignment*.
"""
import re

NAME_RX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STOP = set("a an the and or to of for in on with when user users asks ask use used is are it this that by from as at be your "
           "you their into its can any all via also not no do does".split())

# --- secrets --------------------------------------------------------------------------
SECRET_SHAPES = [
    ("AWS access key id", r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("OpenAI/Anthropic key", r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{20,}\b"),
    ("GitHub token", r"\b(gh[pousr]_[A-Za-z0-9]{36}|github_pat_\w{50,})\b"),
    ("Slack token", r"\bxox[baprs]-[\w-]{10,}\b"),
    ("Google API key", r"\bAIza[\w-]{35}\b"),
    ("Stripe live key", r"\b[rs]k_live_[0-9a-zA-Z]{24,}\b"),
    ("Private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("Credentials in URL", r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@'\"]+:[^\s@/'\"]{3,}@[^\s'\"]+"),
]
# NAME = value / NAME: value / "name": "value" where NAME contains a secret-ish *word*
# (token boundaries are start, end, _ or -, so `authors`, `tokenizer`, `passwords_list` don't match)
_SECRET_WORD = r"(?:SECRET|PASSWORD|PASSWD|PWD|TOKEN|APIKEY|API_KEY|ACCESS_KEY|PRIVATE_KEY|AUTH_TOKEN|CREDENTIALS?)"
SECRET_ASSIGN = re.compile(
    r"""(?ix)(?<![\w`])([A-Z0-9]+[_-])*""" + _SECRET_WORD + r"""([_-][A-Z0-9]+)*(?![\w`])
        ["']?[ \t]*[:=][ \t]*["']?([^\s"'#,;`]{8,})""")
PLACEHOLDER = re.compile(r"(?i)^(<.*>|\$\{?.*|x{4,}.*|\*{3,}|changeme.*|your[_-].*|example.*|dummy.*|placeholder.*|redacted|none|null|true|false|\.\.\.|process\.env.*|os\.environ.*|env\(.*|getenv.*)$")
CODE_VALUE = re.compile(r"[()\[\]{}\\]|^re\.|^r[\"']|\.get$")  # function calls, regexes, indexing = code, not a secret


def redact(v):
    return v[:4] + "****"


def find_secrets(text):
    """Return list of (kind, line_no, redacted_value). Never returns full secret values."""
    hits, seen = [], set()
    for i, line in enumerate(text.splitlines(), 1):
        for kind, rx in SECRET_SHAPES:
            for m in re.finditer(rx, line):
                val = m.group(0)
                if kind == "Credentials in URL":
                    pw = re.search(r"://[^:/@]+:([^@]+)@", val).group(1)
                    if PLACEHOLDER.match(pw) or pw.lower() in ("password", "pass", "user"):
                        continue
                if (kind, val) not in seen:
                    seen.add((kind, val))
                    hits.append((kind, i, redact(val)))
        for m in SECRET_ASSIGN.finditer(line):
            name, val = m.group(0).split("=")[0].split(":")[0].strip(" \"'"), m.group(m.lastindex)
            if PLACEHOLDER.match(val) or CODE_VALUE.search(val) or re.fullmatch(r"[A-Za-z_.]+", val):
                continue  # placeholders, code expressions, bare identifiers/attribute refs
            if any(v[0] == "Credentials in URL" and v[1] == i for v in hits):
                continue
            if ("assign", name, val) not in seen:
                seen.add(("assign", name, val))
                hits.append((f"Secret assignment ({name})", i, redact(val)))
    return hits


# --- frontmatter ----------------------------------------------------------------------
def parse_frontmatter(text):
    """Parse top-level YAML scalars incl. >, |, >-, |-, and quoted values. Returns (dict, body) or (None, text)."""
    if not text.startswith("---"):
        return None, text
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|$)", text, re.S)
    if not m:
        return None, text
    lines, fm, i = m.group(1).splitlines(), {}, 0
    while i < len(lines):
        km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        if not km:
            i += 1
            continue
        key, val = km.group(1), km.group(2).strip()
        if re.match(r"^[>|][+-]?\d*\s*(#.*)?$", val):
            style, block = val[0], []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            fm[key] = (" ".join(b for b in block if b) if style == ">" else "\n".join(block)).strip()
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        elif val and val[0] in "'\"":  # multi-line quoted scalar
            q, parts = val[0], [val[1:]]
            i += 1
            while i < len(lines) and not lines[i].rstrip().endswith(q):
                parts.append(lines[i].strip())
                i += 1
            if i < len(lines):
                parts.append(lines[i].strip()[:-1])
            val = " ".join(p for p in parts if p)
        else:  # plain multi-line scalar continuation
            while i + 1 < len(lines) and lines[i + 1].startswith((" ", "\t")) and lines[i + 1].strip():
                i += 1
                val += " " + lines[i].strip()
        fm[key] = val
        i += 1
    return fm, text[m.end():]


# --- overlap --------------------------------------------------------------------------
TRIGGER_RX = re.compile(r"(?is)\b(use (?:it |this )?(?:when|for|if|before)|when the user)\b(.*)$")


def trigger_clause(desc):
    m = TRIGGER_RX.search(desc or "")
    return m.group(2) if m else ""


def words(s):
    return {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", (s or "").lower()) if w not in STOP}


def jaccard(a, b):
    a, b = words(a), words(b)
    return len(a & b) / len(a | b) if a and b else 0.0
