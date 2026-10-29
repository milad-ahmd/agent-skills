#!/usr/bin/env python3
"""Scan an agent's instruction files and tool configs for prompt injection, hidden text and risky commands. Stdlib only.

Usage: python3 scan_agent_config.py [path ...] [--user] [--plugins] [--root DIR] [--exclude GLOB ...] [--json] [--fail-on LEVEL]
  (no path)  the project at --root (default: cwd): CLAUDE.md / AGENTS.md / GEMINI.md anywhere in it, .claude/,
             .cursor/rules, .cursor/mcp.json, .cursorrules, .clinerules, .windsurfrules, .windsurf/rules,
             .github/copilot-instructions.md, .github/instructions, .mcp.json, .vscode/mcp.json
  path       scan these files or directories instead (e.g. a skill you just downloaded)
  --user     also ~/.claude/{CLAUDE.md,skills,commands,agents,settings.json,settings.local.json}, the MCP servers in
             ~/.claude.json, ~/.cursor/mcp.json and the Claude Desktop config
  --plugins  also installed plugins in ~/.claude/plugins/cache (third-party skills, hooks and MCP configs)
  --fail-on  lowest severity that makes the exit code 1: critical|high|medium|low|info (default: medium)

Exit codes: 0 nothing at or above --fail-on · 1 findings at or above it · 2 nothing to scan
It only reads files as text (and parses JSON). It never executes, imports or sources anything it scans,
and it masks secret values in everything it prints.
"""
import argparse
import base64
import binascii
import fnmatch
import json
import os
import re
import sys

SEV = ["critical", "high", "medium", "low", "info"]
ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
HOME = os.path.expanduser("~")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build", ".next"}
TEXT_EXT = (".md", ".mdc", ".markdown", ".txt", ".json", ".jsonc", ".yaml", ".yml", ".toml", ".sh", ".bash", ".zsh", ".fish",
            ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".html", ".xml", ".cfg", ".ini", ".rules", "")
DOC_EXT = (".md", ".mdc", ".markdown", ".txt", ".html", ".rules", "")
INSTRUCTION_NAMES = {"CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "GEMINI.md", ".cursorrules", ".windsurfrules"}
PROJECT_PATHS = [".claude", ".cursor/rules", ".cursor/mcp.json", ".clinerules", ".windsurf/rules", ".github/copilot-instructions.md",
                 ".github/instructions", ".mcp.json", ".vscode/mcp.json"]
USER_PATHS = ["~/.claude/CLAUDE.md", "~/.claude/skills", "~/.claude/commands", "~/.claude/agents", "~/.claude/settings.json",
              "~/.claude/settings.local.json", "~/.claude.json", "~/.cursor/mcp.json",
              "~/Library/Application Support/Claude/claude_desktop_config.json", "~/.config/Claude/claude_desktop_config.json"]
MAX_BYTES = 2_000_000

# --- masking ------------------------------------------------------------------------
SECRET_SHAPES = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_\w{20,}"
                           r"|\bxox[baprs]-[\w-]{10,}|\bAIza[\w-]{30,}|\b[rs]k_(?:live|test)_[0-9a-zA-Z]{16,}|\bglpat-[\w-]{16,}|\bnpm_[A-Za-z0-9]{30,}")
SECRET_WORD = r"(?:[A-Za-z0-9]*[_-])*(?:SECRET|PASSWORD|PASSWD|PASS|PWD|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|AUTH|CREDENTIALS?|COOKIE|SESSION)(?:[_-][A-Za-z0-9]*)*"
SECRET_KEY = re.compile(rf"(?i)^{SECRET_WORD}$")
ASSIGN = re.compile(rf"""(?i)(\b{SECRET_WORD}["']?\s*[:=]\s*["']?|\bBearer\s+|://[^\s:/@'"]+:)([^\s"'`,;)@]{{6,}})""")
REF = re.compile(r"^(\$\{?[\w:.-]+\}?|\$\(.*\)|<[^>]*>|\{\{.*\}\}|)$")


def mask(v):
    if v.lower().startswith(("bearer ", "basic ", "token ")):
        head, _, rest = v.partition(" ")
        return f"{head} {mask(rest)}"
    return (v[:4] if len(v) >= 12 else "") + "****"


def redact(text):
    text = SECRET_SHAPES.sub(lambda m: mask(m.group(0)), text)
    return ASSIGN.sub(lambda m: m.group(1) + (m.group(2) if REF.match(m.group(2)) or "****" in m.group(2) else mask(m.group(2))), text)


# --- rules --------------------------------------------------------------------------
# "Never push without asking" is a guard, not an attack: skip when the same sentence already says no.
NEG = re.compile(r"(?i)\b(never|don'?t|do not|must not|should not|shouldn'?t|not|no|nothing|nobody|avoid|refuse|forbid\w*|prohibit\w*|restrict\w*)\b")
INJECTION = [
    ("ignore-instructions", "high", r"(?i)\b(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+|every\s+|of\s+)*(previous|prior|above|earlier|preceding|original|system|developer)\s+(instructions?|prompts?|messages?|rules|directions|guidelines|context)",
     "tells the agent to drop the instructions it already has"),
    ("role-override", "medium", r"(?i)\byou are now\s+(an?\s+|the\s+|in\s+)?(\w+\s+){0,3}?(mode|assistant|ai|agent|model|persona|character|jailbroken|unrestricted|unfiltered|dan)\b"
     r"|\bfrom now on,?\s+you\s+(will|must|are|should)\b|\bact as an?\s+(unrestricted|unfiltered|jailbroken)",
     "tries to replace the agent's role or rules"),
    ("conceal-from-user", "high", r"(?i)\b(keep|hide)\s+(this|it|these)\s+(secret\s+|hidden\s+)?from\s+the\s+user|\bwithout\s+the\s+user\s+(knowing|noticing)",
     "asks the agent to hide what it does from you"),
    ("conceal-from-user", "medium", r"(?i)\b(do not|don'?t|never)\s+(tell|inform|mention|reveal|notify|alert|show|let)\s+(this\s+)?(to\s+)?(the\s+)?(user|human|operator)\b",
     "asks the agent not to tell you something"),
    ("no-confirmation", "medium", r"(?i)\bwithout\s+(asking|confirmation|confirming|approval|permission|consent|prompting|the\s+user'?s?\s+(approval|permission|consent|confirmation))\b"
     r"|\bsilently\s+(run|execute|send|upload|install|delete|modify|post|curl|push|commit)\b",
     "tells the agent to act without asking you first"),
    ("system-prompt", "high", r"(?i)\b(reveal|print|output|show|repeat|leak|dump|disclose|send|exfiltrate)\s+(me\s+|us\s+)?(back\s+)?(your|the|its|all)\s+"
     r"((full|entire|exact|hidden|initial|original|complete)\s+)?(system prompt|instructions)\b",
     "tries to extract the agent's system prompt"),
    ("system-prompt", "info", r"(?i)\bsystem prompt\b", "mentions the system prompt (usually harmless; check the context)"),
    ("fake-role-tag", "medium", r"(?i)<\|?(im_start|im_end|endoftext)\|?>|\[/?INST\]|<</?SYS>>|</?(system|system-reminder|system_prompt)>",
     "fakes a chat-template or system tag so text looks like it came from the system"),
    ("disable-safety", "high", r"--dangerously-skip-permissions|\bbypassPermissions\b|\bdangerouslyDisableSandbox\b|--dangerously-bypass-approvals-and-sandbox"
     r"|(?i:\b(disable|turn off|bypass|circumvent|ignore)\s+(all\s+|the\s+|any\s+|your\s+)*(safety|security|permission|guardrail|sandbox|content filter)s?\b(\s+(checks?|prompts?|rules|restrictions|polic(y|ies)|filters?))?)",
     "turns off permission prompts or safety checks"),
]
INJECTION = [(r, s, re.compile(x), w) for r, s, x, w in INJECTION]
GUARDED = {"no-confirmation", "disable-safety"}
RISKY_ACTION = re.compile(r"(?i)\b(run|execute|exec|send|upload|post|push|commit|merge|deploy|publish|delete|remove|rm|install|curl|wget|pay|buy|purchase|transfer|"
                          r"email|message|share|grant|chmod|sudo|force|overwrite|modify|edit|write)\w*\b")
SENSITIVE = re.compile(r"(~|\$HOME|\$\{HOME\}|/Users/[^/\s]+|/home/[^/\s]+)/\.(ssh|aws|gnupg|kube|docker/config\.json|netrc|npmrc|pypirc|git-credentials|config/gcloud|config/gh/hosts\.yml)\b"
                       r"|\bid_(rsa|ed25519|ecdsa)\b|\.aws/credentials|/etc/shadow|\blogin\.keychain|Library/Keychains|\bsecurity\s+(find|dump)-(generic|internet)-password|\bdump-keychain"
                       r"|Library/Application Support/(Google/Chrome|BraveSoftware|Microsoft Edge|Firefox|Arc)|\.mozilla/firefox|\bLogin Data\b|\bwallet\.dat\b")
DOTENV = re.compile(r"(?<![\w.-])\.env(\.(local|production|prod))?\b(?!\.(example|sample|template))")
SEND = re.compile(r"(?i)\bcurl\b[^\n]*\s(-d|--data(-binary|-raw|-urlencode)?|-F|--form|-T|--upload-file|-X\s*(POST|PUT)|--request\s+(POST|PUT))\b|\bwget\b[^\n]*--post-(data|file)"
                  r"|\brequests\.(post|put)\(|\bhttpx?\.(post|put)\(|\bfetch\([^)]*method\s*:\s*['\"](POST|PUT)|\bnc\s+(-\w+\s+)*[\w.-]+\s+\d{2,5}\b|/dev/tcp/|Invoke-(WebRequest|RestMethod)[^\n]*-Method\s+(Post|Put)")
NET = re.compile(r"(?i)\b(curl|wget|nc|ncat|socat|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b|https?://|\bfetch\(|\brequests\.|\burllib\.request")
NETCMD = re.compile(r"(?i)\b(curl|wget|nc|ncat|socat|scp|rsync|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b|\bfetch\(|\brequests\.(get|post|put)|\burllib\.request")
EXFIL_HOST = re.compile(r"(?i)\b(webhook\.site|requestbin\.(com|net)|pipedream\.net|[\w-]+\.ngrok(-free)?\.(io|app|dev)|pastebin\.com|paste\.ee|hastebin\.com|transfer\.sh|0x0\.st"
                        r"|termbin\.com|burpcollaborator\.net|oastify\.com|interact\.sh|oast\.(fun|pro|live|site|online|me)|requestcatcher\.com|beeceptor\.com"
                        r"|hooks\.slack\.com/services|discord(app)?\.com/api/webhooks|api\.telegram\.org/bot)")
FILE_DATA = re.compile(r"(?i)(-d|--data[\w-]*|-F|--form|-T|--upload-file)\s+['\"]?[\w=]*@|\$\(\s*(cat|base64|tar|zip)\b|\bopen\([^)]*\)\.read\(|readFileSync\(|<\s*[~/$]")
SECRET_URL = re.compile(r"(?i)https?://\S*[?&][\w-]+=[\"']?(\$\{?\(?[\w-]*(key|token|secret|pass|pwd|cred|auth)\w*|\$\((cat|env|printenv|security)\b)")
PIPE_SH = re.compile(r"(?i)\b(curl|wget)\b[^\n|;]*\|\s*(sudo\s+(-\S+\s+)*)?(ba|z|da|k)?sh\b|\b(curl|wget)\b[^\n|;]*\|\s*(sudo\s+)?(python3?|node|perl|ruby)(\s+-)?\s*($|[;&|)'\"`])"
                     r"|\b(ba|z)?sh\s+(-c\s+)?[\"']?(<\(|\$\()\s*(curl|wget)\b|\b(iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n|]*\|\s*(iex|Invoke-Expression)\b"
                     r"|\bbase64\s+(-d|-D|--decode)\b[^\n|]*\|\s*(ba|z)?sh\b|\beval\s+[\"']?\$\((curl|wget)\b")
DANGER = [
    ("destructive-rm", "critical", re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*(r[a-zA-Z]*f|f[a-zA-Z]*r)[a-zA-Z]*\s+(--no-preserve-root\s+)?[\"']?(~/?|/|\$HOME/?|\$\{HOME\}/?|/\*|~/\*)[\"']?(\s|$|;|&|\|)"),
     "deletes your home directory or the whole filesystem", "Remove it. No legitimate skill needs this."),
    ("pipe-to-shell", "medium", PIPE_SH, "downloads code and runs it without any check",
     "Download to a file, read or checksum it, then run it; or install a pinned package instead."),
    ("shell-rc-edit", "medium", re.compile(r"(>>?|\btee\s+(-a\s+)?)\s*[\"']?(~|\$HOME|\$\{HOME\})/\.(bashrc|zshrc|bash_profile|profile|zprofile|zshenv|zlogin|config/fish/config\.fish)\b"
                                           r"|\bsed\s+-i[^\n]*\.(bashrc|zshrc|bash_profile|zprofile|zshenv)\b"),
     "edits your shell startup files, so code runs in every new terminal", "Make the change yourself if you want it; an agent skill shouldn't."),
    ("persistence", "medium", re.compile(r"\|\s*crontab\s+-|\bcrontab\s+(-e\b|[\w./~-]+\.(txt|cron)\b)|/etc/cron|\bLaunchAgents\b|\bLaunchDaemons\b|\blaunchctl\s+(load|bootstrap|submit|enable)\b"
                                         r"|\bsystemctl\s+(--user\s+)?enable\b|\.config/autostart|\bschtasks\s+/create\b|CurrentVersion\\+Run\b"),
     "installs something that keeps running after the session (cron, launchd, systemd, autostart)", "Remove it unless you asked for a scheduled job and know what it runs."),
]
AGENT_WORDS = re.compile(r"(?i)\b(you|assistant|claude|ai|agent|llm|model|copilot|cursor)\b")
IMPERATIVE = re.compile(r"(?i)\b(must|always|never|ignore|do not|don'?t|run|execute|send|upload|post|fetch|read|delete|remove|curl|wget|install|instead)\b")
ZW = {0x200B: "ZWSP", 0x200C: "ZWNJ", 0x200D: "ZWJ", 0x2060: "WJ", 0x2061: "FA", 0x2062: "IT", 0x2063: "IS", 0x2064: "IP", 0x180E: "MVS", 0xFEFF: "BOM"}
BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
B64 = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=_-])")
HEX = re.compile(r"(?<![0-9a-fA-Fx])(?:[0-9a-fA-F]{2}){20,}(?![0-9a-fA-F])")
URL = re.compile(r"(?i)https?://[^\s'\"]+")
SHELLISH = re.compile(r"(?i)\b(curl|wget|bash|sh -c|eval|exec|rm -rf|chmod|powershell|/etc/passwd|nc -e)\b")
WIDE_GAP = re.compile(r"\S([ \t\u00a0\u3000]{80,})(\S.*)$")


def is_rtl_or_joining(cp):
    return 0x0590 <= cp <= 0x08FF or 0x0900 <= cp <= 0x0DFF or 0xFB1D <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFC


def is_emoji(cp):
    return cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or cp in (0xFE0F, 0x20E3) or 0x1F3FB <= cp <= 0x1F3FF


def show(ch):
    return f"<U+{ord(ch):04X}>"


class Scanner:
    def __init__(self):
        self.findings, self.seen, self.files = [], set(), 0

    def add(self, sev, rule, path, line, msg, why, fix, snippet=""):
        key = (path, line, rule, msg)
        if key in self.seen:
            return
        self.seen.add(key)
        snippet = redact(snippet.strip())
        self.findings.append({"severity": sev, "rule": rule, "file": path, "line": line, "message": msg, "why": why, "fix": fix,
                              "snippet": snippet[:200] + ("…" if len(snippet) > 200 else "")})

    # --- hidden text ----------------------------------------------------------------
    def hidden(self, path, n, line):
        if line.isascii():
            return
        cps = [ord(c) for c in line]
        i, zw, bidi, rtl = 0, [], [], any(is_rtl_or_joining(c) for c in cps)
        while i < len(cps):
            c = cps[i]
            if 0xE0000 <= c <= 0xE007F:
                j = i
                while j < len(cps) and 0xE0000 <= cps[j] <= 0xE007F:
                    j += 1
                decoded = "".join(chr(x - 0xE0000) for x in cps[i:j] if 0x20 <= x - 0xE0000 < 0x7F)
                if not (i > 0 and cps[i - 1] == 0x1F3F4 and re.fullmatch(r"[a-z0-9]{2,7}", decoded)):  # flag emoji tags
                    self.add("critical", "unicode-tag-smuggling", path, n, f"{j - i} invisible Unicode tag characters spell: \"{redact(decoded)[:300]}\"",
                             "Tag characters are invisible in editors and on GitHub, but models read them as text: a classic way to hide instructions.",
                             "Delete the invisible characters and treat the whole file as hostile.", line.replace("".join(map(chr, cps[i:j])), "<hidden>"))
                i = j
                continue
            if 0xFE00 <= c <= 0xFE0F or 0xE0100 <= c <= 0xE01EF:
                j = i
                while j < len(cps) and (0xFE00 <= cps[j] <= 0xFE0F or 0xE0100 <= cps[j] <= 0xE01EF):
                    j += 1
                if j - i >= 4:
                    data = bytes((x - 0xFE00) if x < 0xE0000 else (x - 0xE0100 + 16) for x in cps[i:j])
                    self.add("high", "variation-selector-smuggling", path, n, f"{j - i} variation selectors in a row decode to: \"{redact(data.decode('utf-8', 'replace'))[:200]}\"",
                             "Runs of variation selectors are invisible and can carry arbitrary bytes.", "Delete them and treat the file as hostile.", "")
                i = j
                continue
            if c in ZW and not (c == 0xFEFF and n == 1 and i == 0):
                prev, nxt = (cps[i - 1] if i else 0), (cps[i + 1] if i + 1 < len(cps) else 0)
                if not ((c == 0x200D and is_emoji(prev) and is_emoji(nxt)) or (c in (0x200C, 0x200D) and (is_rtl_or_joining(prev) or is_rtl_or_joining(nxt)))):
                    zw.append(i)
            elif c in BIDI:
                bidi.append(i)
            i += 1
        if zw:
            self.add("high" if len(zw) >= 8 else "medium", "zero-width", path, n, f"{len(zw)} zero-width character(s) ({', '.join(sorted({ZW[cps[k]] for k in zw}))})",
                     "Invisible characters can split trigger words past filters or encode a hidden message.",
                     "Remove them unless they're part of real text in a script that needs them.", "".join(show(ch) if k in zw else ch for k, ch in enumerate(line)))
        if bidi:
            override = any(cps[k] in (0x202D, 0x202E) for k in bidi)
            self.add("low" if rtl else ("high" if override else "medium"), "bidi-control", path, n, f"{len(bidi)} bidirectional control character(s)",
                     "Bidi overrides make text display in a different order than the model and tools read it (Trojan Source).",
                     "Remove them; they're only needed inside real right-to-left text.", "".join(show(ch) if k in bidi else ch for k, ch in enumerate(line)))

    def encoded(self, path, n, line):
        for rx, kind in ((B64, "base64"), (HEX, "hex")):
            for m in list(rx.finditer(line))[:5]:
                blob = m.group(0)
                try:
                    raw = binascii.unhexlify(blob) if kind == "hex" else base64.b64decode(blob + "=" * (-len(blob) % 4))
                    text = raw.decode("utf-8")
                except (binascii.Error, ValueError, UnicodeDecodeError):
                    continue
                if len(text) < 12 or sum(ch.isprintable() or ch in "\n\t" for ch in text) / len(text) < 0.95:
                    continue
                hit = next((r for r, sv, rx2, _ in INJECTION if sv != "info" and rx2.search(text)), None)
                if hit or SHELLISH.search(text) or SENSITIVE.search(text):
                    sev, what = "high", "instructions or shell commands"
                elif URL.search(text):
                    sev, what = "medium", "a URL"
                else:
                    continue
                self.add(sev, "encoded-payload", path, n, f"{kind} blob decodes to {what}: \"{redact(text)[:160]}\"",
                         "Encoding hides text from people and keyword filters while an agent can still decode and follow it.",
                         "Decode it yourself and remove it if you can't explain why it's there.", line[:m.start()] + f"<{kind} {len(blob)} chars>")

    # --- text files -----------------------------------------------------------------
    def text(self, path, text):
        ext = os.path.splitext(path)[1].lower()
        lines = text.splitlines()
        spans = []
        if ext in DOC_EXT:
            for m in re.finditer(r"<!--(.*?)-->", text, re.S):
                start = text.count("\n", 0, m.start()) + 1
                spans.append((start, start + m.group(0).count("\n")))
                body = m.group(1)
                if len(body.strip()) > 12 and AGENT_WORDS.search(body) and IMPERATIVE.search(body) and not any(r[2].search(body) for r in INJECTION if r[1] != "info"):
                    self.add("medium", "hidden-comment", path, start, "HTML comment with instructions aimed at an agent",
                             "HTML comments don't show when Markdown is rendered, so a reviewer on GitHub never sees them, but the agent reads the raw file.",
                             "Read the comment. Remove it if it gives the agent instructions you didn't write.", body.strip().splitlines()[0][:200])
        blank = 0
        for n, line in enumerate(lines, 1):
            if not line.strip():
                blank += 1
                continue
            if blank >= 30 and ext in DOC_EXT:
                self.add("low", "hidden-whitespace", path, n, f"text resumes after {blank} blank lines",
                         "Content far below the visible end of a file is easy to miss when reviewing.", "Scroll down and read it.", line)
            blank = 0
            if len(line) > 20000:
                line = line[:20000]
            in_comment = any(a <= n <= b for a, b in spans)
            self.hidden(path, n, line)
            gap = WIDE_GAP.search(line)
            if gap:
                self.add("medium", "hidden-whitespace", path, n, f"{len(gap.group(1))} spaces push text off-screen: \"{gap.group(2)[:120]}\"",
                         "Long runs of spaces hide the end of a line in editors and diffs.", "Read the hidden part; remove it if it's instructions.", line.strip()[:60] + " … " + gap.group(2))
            self.encoded(path, n, line)
            hit = set()
            for rule, sev, rx, why in INJECTION:
                m = rx.search(line)
                if m and rule not in hit:
                    if rule in GUARDED and NEG.search(re.split(r"[.;:!?]\s", line[:m.start()])[-1]):
                        continue
                    hit.add(rule)
                    if rule == "no-confirmation" and not RISKY_ACTION.search(line):
                        sev = "low"  # "without asking" next to nothing dangerous is usually workflow advice
                    if in_comment and sev not in ("critical", "info"):
                        sev = SEV[max(0, SEV.index(sev) - 1)]
                    self.add(sev, rule, path, n, why + (" (inside an HTML comment, hidden when rendered)" if in_comment else ""),
                             "Text an agent loads as instructions can steer it like a user can. This is how injected skills, rules and READMEs take over sessions.",
                             "Remove the line, or the file if you didn't write it. If it's a doc quoting an attack, keep it out of auto-loaded instruction files.", line)
            for rule, sev, rx, why, fix in DANGER:
                if rx.search(line):
                    self.add(sev, rule, path, n, why, "Agents follow instructions in skills and configs, and scripts run with your permissions.", fix, line)
            self.exfil(path, n, line)

    def exfil(self, path, n, line):
        send = SEND.search(line) or EXFIL_HOST.search(line)
        sens, net = SENSITIVE.search(line) or (DOTENV.search(line) if send else None), send or NETCMD.search(line)
        why = "Reading credentials and sending data out in one step is how malicious skills steal keys."
        if sens and net:
            self.add("critical", "exfiltration", path, n, f"reads `{sens.group(0)}` and talks to the network on the same line", why,
                     "Don't run it. Remove it, and rotate the credentials it points at if it has already run.", line)
        elif sens:
            self.add("medium", "sensitive-path", path, n, f"touches credentials or private data: `{sens.group(0)}`",
                     "SSH keys, cloud credentials, keychains and browser profiles are what attackers go for.",
                     "Check why this needs that path. Instructions to read it are a red flag in a third-party skill.", line)
        if SECRET_URL.search(line):
            self.add("high", "secret-in-url", path, n, "builds a URL query string from a secret or command output", why,
                     "Remove it. Secrets in URLs end up in server logs, proxies and shell history.", line)
        elif SEND.search(line) and FILE_DATA.search(line):
            self.add("high", "uploads-file", path, n, "sends file or command output to a remote server", why,
                     "Check the destination and what file is sent. Remove it if you can't explain it.", line)
        if EXFIL_HOST.search(line):
            self.add("medium", "exfil-host", path, n, f"uses a paste, tunnel or webhook host: {EXFIL_HOST.search(line).group(0)}",
                     "Throwaway paste and webhook endpoints are the usual drop boxes for stolen data.", "Check what is sent there and why.", line)

    # --- JSON configs ---------------------------------------------------------------
    def config(self, path, text, data, project_root):
        if not isinstance(data, dict):
            return
        line = lambda needle, default=1: next((i for i, l in enumerate(text.splitlines(), 1) if needle in l), default)  # noqa: E731
        perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
        if perms.get("defaultMode") == "bypassPermissions":
            self.add("high", "bypass-permissions", path, line("bypassPermissions"), "default permission mode is bypassPermissions",
                     "Every tool call, including shell commands and file writes, runs without asking.", "Use `default` or `acceptEdits`.", '"defaultMode": "bypassPermissions"')
            self.findings = [f for f in self.findings if not (f["file"] == path and f["rule"] == "disable-safety" and f["line"] == line("bypassPermissions"))]
        for rule in perms.get("allow", []) if isinstance(perms.get("allow"), list) else []:
            r = str(rule).replace(" ", "")
            m = re.match(r"^Bash\(([\w./-]+)(:\*|\s*\*|\s+:\*)\)$", str(rule).strip())
            if r in ("Bash", "Bash(*)", "Bash(:*)", "Bash(**)"):
                self.add("high", "broad-allow", path, line(str(rule)), f"permission allow-list contains `{rule}`",
                         "Any shell command runs without a prompt, which makes every injected instruction executable.",
                         "Allow specific commands instead, e.g. `Bash(npm test:*)`.", f'"{rule}"')
            elif m and m.group(1) in ("curl", "wget", "bash", "sh", "zsh", "python", "python3", "node", "npx", "uvx", "eval", "ssh", "scp", "nc", "sudo", "perl", "ruby", "osascript", "rm"):
                self.add("medium", "broad-allow", path, line(str(rule)), f"allow-list lets `{m.group(1)}` run with any arguments: `{redact(str(rule))}`",
                         "Interpreters and network tools with wildcard arguments can run or fetch anything.", "Narrow it to the exact command you need.", f'"{rule}"')
        for flag, sev, why in (("enableAllProjectMcpServers", "medium", "Every MCP server in any cloned repo's .mcp.json starts without asking."),
                               ("skipDangerousModePermissionPrompt", "medium", "The warning before bypass mode is switched off.")):
            if data.get(flag) is True:
                self.add(sev, flag, path, line(flag), f"`{flag}` is true", why, f"Set `{flag}` to false or remove it.", f'"{flag}": true')
        if data.get("apiKeyHelper"):
            self.add("low", "api-key-helper", path, line("apiKeyHelper"), "apiKeyHelper runs a command to fetch API keys",
                     "Check: the command runs on every session start.", "Make sure you know what the command does.", f'"apiKeyHelper": "{data["apiKeyHelper"]}"')
        env = data.get("env") if isinstance(data.get("env"), dict) else {}
        for k, v in env.items():
            v = str(v)
            if re.search(r"(?i)BASE_URL|API_URL|_PROXY$", k) and v and not re.search(r"(?i)^https?://(localhost|127\.0\.0\.1|\[::1\]|api\.anthropic\.com|api\.openai\.com)\b", v):
                self.add("medium", "api-redirect", path, line(k), f"`{k}` sends API traffic to {URL.sub(lambda m: m.group(0).split('?')[0], v)[:80]}",
                         "Everything you and the agent send, plus your API key, goes through that host.", "Remove it unless it's your own proxy or gateway.", f'"{k}": "{v}"')
            elif SECRET_KEY.match(k) and v and not REF.match(v):
                self.add("medium", "plaintext-secret", path, line(k), f"`{k}` holds a secret in plain text ({mask(v)})",
                         "Config files get copied, synced and committed.", "Use a reference to your environment or keychain instead.", f'"{k}": "{mask(v)}"')
        self.hooks(path, data, line, project_root)
        servers = {}
        for key in ("mcpServers", "servers"):
            if isinstance(data.get(key), dict):
                servers.update(data[key])
        for pdir, pcfg in (data.get("projects") or {}).items() if isinstance(data.get("projects"), dict) else []:
            for name, cfg in ((pcfg or {}).get("mcpServers") or {}).items() if isinstance(pcfg, dict) else []:
                servers[f"{name} (project {os.path.basename(pdir)})"] = cfg
        for name, cfg in servers.items():
            if isinstance(cfg, dict):
                committed = os.path.basename(path) == ".mcp.json" or (project_root is not None and os.path.basename(path) == "mcp.json")
                self.mcp(path, name, cfg, line(f'"{name.split(" (project ")[0]}"'), committed)
        self.escaped(path, text, data)

    def escaped(self, path, text, data):
        """Invisible characters written as JSON escapes (\\u200b, surrogate pairs) only appear after decoding."""
        def walk(node):
            if isinstance(node, str):
                yield node
            elif isinstance(node, dict):
                for k, v in node.items():
                    yield k
                    yield from walk(v)
            elif isinstance(node, list):
                for v in node:
                    yield from walk(v)
        for st in walk(data):
            if not st.isascii() and any(0xE0000 <= ord(c) <= 0xE007F or ord(c) in ZW or ord(c) in BIDI for c in st):
                needle = json.dumps(st)[1:30]
                n = next((i for i, l in enumerate(text.splitlines(), 1) if needle in l or st[:20] in l), 1)
                self.hidden(path, n, st)

    def hooks(self, path, data, line, project_root):
        cmds = []

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("command"), str):
                    cmds.append(node["command"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(data.get("hooks"))
        sl = data.get("statusLine")
        if isinstance(sl, dict) and isinstance(sl.get("command"), str):
            cmds.append(sl["command"])
        for c in cmds:
            n = line(json.dumps(c)[1:40])
            if NET.search(c) and not re.match(r"^\s*https?://", c):
                self.add("high", "hook-network", path, n, "a hook or status-line command uses the network",
                         "Hooks run automatically on every matching event, outside the agent's permission prompts, and can see tool input and output.",
                         "Read the command. Remove it if you don't know why it sends data anywhere.", c)
            local = re.sub(r"\"?\$\{?(?!HOME\b)\w+\}?\"?/\S*", " ", c)  # $CLAUDE_PROJECT_DIR/..., ${CLAUDE_PLUGIN_ROOT}/...
            for p in re.findall(r"(?:^|[\s\"'=])((?:/|~/|\$HOME/|\$\{HOME\}/)[^\s\"';|&]+)", local):
                full = os.path.expanduser(p.replace("${HOME}", HOME).replace("$HOME", HOME))
                if full.startswith(("/bin/", "/usr/", "/opt/homebrew/", "/dev/null")):
                    continue
                if project_root and not os.path.abspath(full).startswith(os.path.abspath(project_root) + os.sep):
                    self.add("medium", "hook-outside-repo", path, n, f"project hook runs `{p}`, outside the repo",
                             "A cloned repo's settings can point hooks at anything on your machine.", "Check that file and what puts it there.", c)
                elif not project_root:
                    self.add("low", "hook-script", path, n, f"hook runs `{p}`", "Check: this runs automatically, outside permission prompts.",
                             "Make sure you wrote or reviewed that script.", c)

    def mcp(self, path, name, cfg, n, committed):
        cmd = str(cfg.get("command", ""))
        args = [str(a) for a in cfg.get("args", [])] if isinstance(cfg.get("args"), list) else []
        full = " ".join([cmd] + args)
        base = os.path.basename(cmd)
        shown = redact(full)
        pkg = None
        if base in ("npx", "bunx", "pnpx") or (base in ("pnpm", "yarn", "bun") and args[:1] in (["dlx"], ["x"])):
            rest = args[1:] if base in ("pnpm", "yarn", "bun") else args
            pkg = next((a for a in rest if not a.startswith("-")), None)
            name_only = ("@" + pkg[1:].split("@")[0]) if pkg and pkg.startswith("@") else (pkg or "").split("@")[0]
            if pkg and not re.search(r"^(@[\w.-]+/)?[\w.-]+@\d+\.\d+\.\d+([-+][\w.]+)?$", pkg):
                if re.match(r"(?i)^(github:|git\+|https?:|[\w.-]+/[\w.-]+$)", pkg):
                    self.add("high", "mcp-from-url", path, n, f"MCP server `{name}` runs code straight from a git repo or URL: {pkg}",
                             "There's no version or registry record: whatever is on that branch today runs on your machine with your access.",
                             "Install from a registry at a pinned version, or pin to a commit and review it.", shown)
                else:
                    self.add("medium", "mcp-unpinned", path, n, f"MCP server `{name}` runs `{pkg}` without a pinned version",
                             "npx/uvx fetch the newest release at every start, so a hijacked or typo-squatted package runs automatically.",
                             f"Pin an exact version, e.g. `{name_only}@1.2.3`, after checking it.", shown)
        elif base in ("uvx", "pipx") or (base == "uv" and args[:2] == ["tool", "run"]):
            rest = [a for a in args if a not in ("run", "tool")]
            frm = rest[rest.index("--from") + 1] if "--from" in rest[:-1] else None
            pkg = frm or next((a for a in rest if not a.startswith("-")), None)
            if pkg and re.match(r"(?i)^(git\+|https?:)", pkg) and not re.search(r"@[0-9a-f]{40}$", pkg):
                self.add("high", "mcp-from-url", path, n, f"MCP server `{name}` installs from a URL or git branch: {pkg}",
                         "There's no version or registry record: whatever is there today runs with your access.", "Pin to a release or a full commit SHA after reviewing it.", shown)
            elif pkg and not re.search(r"(==|@)\d+(\.\d+)*", pkg) and not re.match(r"(?i)^(git\+|https?:)", pkg):
                self.add("medium", "mcp-unpinned", path, n, f"MCP server `{name}` runs `{pkg}` without a pinned version",
                         "uvx/pipx fetch the newest release at every start, so a hijacked or typo-squatted package runs automatically.",
                         f"Pin it: `{pkg.split('@')[0]}==1.2.3` (or `--from {pkg.split('@')[0]}==1.2.3`).", shown)
        if PIPE_SH.search(full) or (base in ("bash", "sh", "zsh") and NET.search(full)) or re.search(r"(?i)https?://\S*(raw\.githubusercontent|gist\.github|pastebin)\S*", full):
            self.add("high", "mcp-from-url", path, n, f"MCP server `{name}` downloads and runs a script at startup",
                     "Whoever controls that URL controls a process on your machine.", "Install a pinned, reviewed copy instead.", shown)
        url = str(cfg.get("url") or cfg.get("serverUrl") or "")
        if url.startswith("http://") and not re.match(r"http://(localhost|127\.0\.0\.1|\[::1\])\b", url):
            self.add("medium", "mcp-plain-http", path, n, f"MCP server `{name}` uses unencrypted http: {url.split('?')[0]}",
                     "Anyone on the network path can read or change tool calls and results.", "Use https.", url.split("?")[0])
        elif url.startswith("https://"):
            self.add("info", "mcp-remote", path, n, f"MCP server `{name}` is remote: {url.split('?')[0][:80]}",
                     "Whatever the agent sends to it leaves your machine, and its tool descriptions can carry injected instructions.",
                     "Keep it only if you trust the operator.", url.split("?")[0])
        if "?" in url and re.search(r"(?i)[?&][\w-]*(key|token|secret|auth)[\w-]*=", url):
            self.add("medium", "plaintext-secret", path, n, f"MCP server `{name}` has a secret in its URL query string",
                     "URLs are logged by proxies and servers.", "Move it to a header that references an environment variable.", redact(url))
        for label, kv in (("env", cfg.get("env")), ("headers", cfg.get("headers"))):
            for k, v in (kv or {}).items() if isinstance(kv, dict) else []:
                v = str(v)
                literal = v and not REF.match(v) and not re.match(r"^(Bearer\s+)?\$\{?[\w:.-]+\}?$", v)
                if (SECRET_KEY.match(k) or SECRET_SHAPES.search(v) or k.lower() == "authorization") and literal:
                    self.add("high" if committed else "medium", "plaintext-secret", path, n,
                             f"MCP server `{name}` gets {label} `{k}` as a plain-text value ({mask(v)})" + (" in a project file that may be committed" if committed else ""),
                             "Config files get copied, synced and committed, and the server process can read it.",
                             ("Reference an env var instead (`\"Authorization\": \"Bearer ${API_TOKEN}\"`)" if label == "headers" else f"Reference it instead (`\"{k}\": \"${{{k}}}\"`)")
                             + " and keep the value in your shell or keychain; rotate it if this file was ever shared.", f'"{k}": "{mask(v)}"')
                if re.match(r"(?i)^(AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|GOOGLE_APPLICATION_CREDENTIALS|AZURE_CLIENT_SECRET|KUBECONFIG|GH_TOKEN|GITHUB_TOKEN|GITHUB_PERSONAL_ACCESS_TOKEN|GITHUB_PAT|DATABASE_URL|\w*ADMIN\w*|\w*ROOT\w*)$", k):
                    self.add("low", "broad-secret", path, n, f"MCP server `{name}` gets `{k}`",
                             "Check: broad cloud, GitHub or database credentials give this server everything they can reach.",
                             "Give it a separate token scoped to what the server needs (read-only where possible).", f'"{k}": "{mask(v) if literal else v}"')
        for a in args:
            m = re.match(r"(?i)^--?([\w-]*(key|token|secret|password)[\w-]*)=(.+)$", a)
            if m and not REF.match(m.group(3)):
                self.add("medium", "plaintext-secret", path, n, f"MCP server `{name}` gets `--{m.group(1)}` as a plain-text argument",
                         "Command-line arguments are visible to every process on the machine (ps).", "Pass it through an env reference instead.", redact(a))

    # --- driver ---------------------------------------------------------------------
    def scan_file(self, path, project_root=None):
        try:
            if os.path.getsize(path) > MAX_BYTES:
                return
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return
        if b"\0" in raw[:4096]:
            return
        text = raw.decode("utf-8", "replace")
        self.files += 1
        is_claude_json = os.path.basename(path) == ".claude.json"
        if not is_claude_json:  # ~/.claude.json holds session state; only its MCP servers are checked
            self.text(path, text)
        if path.endswith((".json", ".jsonc")):
            try:
                data = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.M) if path.endswith(".jsonc") else text)
            except ValueError:
                data = None
            if is_claude_json and isinstance(data, dict):
                data = {"mcpServers": data.get("mcpServers") or {}, "projects": data.get("projects") or {}}
            self.config(path, text, data, project_root)


def collect(paths, excludes, skip):
    out = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isfile(p):
            out.append(p)
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (d == "worktrees" and os.path.basename(root) == ".claude")
                                 and os.path.realpath(os.path.join(root, d)) not in skip)
                out += [os.path.join(root, f) for f in sorted(files) if os.path.splitext(f)[1].lower() in TEXT_EXT]
    seen, res = set(), []
    for f in out:
        rp = os.path.realpath(f)
        if rp in seen or rp in skip or any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(os.path.basename(f), g) for g in excludes):
            continue
        seen.add(rp)
        res.append(f)
    return res


def project_targets(root):
    out = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (d.startswith(".") and d not in (".claude", ".github", ".cursor"))]
        out += [os.path.join(r, f) for f in files if f in INSTRUCTION_NAMES]
        if r.count(os.sep) - root.count(os.sep) >= 6:
            dirs[:] = []
    out += [os.path.join(root, p) for p in PROJECT_PATHS if os.path.exists(os.path.join(root, p))]
    return out


def main():
    ap = argparse.ArgumentParser(description="Scan agent instruction files and tool configs for prompt injection.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--user", action="store_true", help="also scan ~/.claude, ~/.claude.json MCP servers, ~/.cursor/mcp.json")
    ap.add_argument("--plugins", action="store_true", help="also scan ~/.claude/plugins/cache")
    ap.add_argument("--root", default=".", help="project root for the default targets (default: cwd)")
    ap.add_argument("--exclude", action="append", default=[], help="glob to skip (repeatable)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on", default="medium", choices=SEV)
    a = ap.parse_args()
    here = os.path.dirname(os.path.realpath(__file__))
    skip = {os.path.realpath(__file__), os.path.join(here, "fixtures"), os.path.join(here, "test_scan_agent_config.py")}
    root = os.path.abspath(a.root)
    groups = [(a.paths, None)] if a.paths else [(project_targets(root), root)]
    if a.user:
        groups.append(([p for p in USER_PATHS if os.path.exists(os.path.expanduser(p))], None))
    if a.plugins:
        groups.append(([os.path.join(HOME, ".claude", "plugins", "cache")], None))
    sc = Scanner()
    for paths, proj in groups:
        for f in collect(paths, a.exclude, skip):
            sc.scan_file(f, proj)
    if not sc.files:
        print("Nothing to scan: no agent instruction or config files found.", file=sys.stderr)
        sys.exit(2)
    fs = sorted(sc.findings, key=lambda x: (SEV.index(x["severity"]), x["file"], x["line"]))
    for x in fs:
        x["file"] = x["file"].replace(HOME, "~", 1) if x["file"].startswith(HOME) else x["file"]
    counts = {s: sum(1 for x in fs if x["severity"] == s) for s in SEV}
    if a.json:
        print(json.dumps({"files_scanned": sc.files, "counts": counts, "findings": fs}, indent=2, ensure_ascii=False))
    else:
        print(f"🧪 Scanned {sc.files} file(s)")
        explained = set()
        for x in fs:
            print(f"\n{ICON[x['severity']]} {x['severity'].upper():<8} {x['rule']:<22} {x['file']}:{x['line']}")
            print(f"    {x['message']}")
            if x["snippet"]:
                print(f"    > {x['snippet']}")
            if (x["rule"], x["why"]) not in explained:
                explained.add((x["rule"], x["why"]))
                print(f"    why: {x['why']}")
            print(f"    fix: {x['fix']}")
        print("\n" + (", ".join(f"{v} {k}" for k, v in counts.items() if v) or "✅ No findings.")
              + "\nA clean scan is not proof of safety: read anything from an untrusted source yourself.")
    sys.exit(1 if any(SEV.index(x["severity"]) <= SEV.index(a.fail_on) for x in fs) else 0)


if __name__ == "__main__":
    main()
