#!/usr/bin/env python3
"""Scan a git repo for leaked secrets in tracked files and (optionally) history. Stdlib only.

Usage: python3 scan_secrets.py [repo_path] [--history]
Exit code 1 if likely secrets are found. Values are always redacted.
"""
import re
import subprocess
import sys

RULES = [
    ("AWS access key", r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("OpenAI key", r"\bsk-(proj-)?[A-Za-z0-9_-]{20,}\b"),
    ("Anthropic key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("GitHub token", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    ("Slack token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("Stripe live key", r"\b(sk|rk)_live_[0-9a-zA-Z]{24,}\b"),
    ("Private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("DB URL with password", r"\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:/@]+:[^\s@/]{3,}@"),
    ("Hard-coded secret", r"(?i)\b(password|passwd|secret|api_?key|access_?token|auth_?token)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
]
RULES = [(n, re.compile(p)) for n, p in RULES]
RISKY_FILES = re.compile(r"(^|/)(\.env(\.[\w-]+)?|id_rsa|id_ed25519|.*\.pem|.*\.p12|.*\.keystore|.*\.jks|credentials(\.json)?|service-account.*\.json)$", re.I)
SAFE_FILES = re.compile(r"\.env\.(example|sample|template)$", re.I)
PLACEHOLDER = re.compile(r"(?i)(example|dummy|placeholder|changeme|your[_-]|xxxx|<.*>|\$\{|test)")


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, errors="ignore").stdout


def redact(s):
    s = s.strip()
    return s[:4] + "****" + (f"({len(s)} chars)" if len(s) > 8 else "")


def scan_text(text, where, findings, seen):
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000:
            continue
        for name, rx in RULES:
            for m in rx.finditer(line):
                val = m.group(0)
                if PLACEHOLDER.search(val):
                    continue
                key = (name, val)
                if key in seen:
                    continue
                seen.add(key)
                findings.append((name, f"{where}:{i}" if where else f"line {i}", redact(val)))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    repo = args[0] if args else "."
    if not git(repo, "rev-parse", "--is-inside-work-tree").strip():
        sys.exit("Not a git repository.")
    findings, seen, risky = [], set(), []
    files = [f for f in git(repo, "ls-files").splitlines() if f]
    for f in files:
        if RISKY_FILES.search(f) and not SAFE_FILES.search(f):
            risky.append(f)
        try:
            with open(f"{repo}/{f}", encoding="utf-8") as fh:
                scan_text(fh.read(), f, findings, seen)
        except (UnicodeDecodeError, OSError):
            pass
    hist = 0
    if "--history" in sys.argv:
        log = git(repo, "log", "-p", "--all", "--no-color", "--pretty=format:@@commit %h")
        commit = "?"
        added = []
        for line in log.splitlines():
            if line.startswith("@@commit "):
                commit = line.split()[1]
            elif line.startswith("+") and not line.startswith("+++"):
                added.append((commit, line[1:]))
        before = len(findings)
        for c, line in added:
            scan_text(line, f"history@{c}", findings, seen)
        hist = len(findings) - before
    print(f"🔐 Scanned {len(files)} tracked files" + (" + full git history" if "--history" in sys.argv else ""))
    for f in risky:
        print(f"  🟠 risky file tracked: {f}  →  git rm --cached '{f}' && add to .gitignore")
    for name, where, val in findings:
        print(f"  🔴 {name:<22} {where:<40} {val}")
    if not findings and not risky:
        print("  ✅ No likely secrets found.")
    else:
        print(f"\n{len(findings)} likely secret(s) ({hist} only in history), {len(risky)} risky file(s).")
        print("➡️  Rotate/revoke exposed keys FIRST, then remove them from code (and history if pushed).")
    sys.exit(1 if findings or risky else 0)


if __name__ == "__main__":
    main()
