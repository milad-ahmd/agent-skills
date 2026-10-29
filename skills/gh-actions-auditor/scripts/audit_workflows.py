#!/usr/bin/env python3
"""Audit GitHub Actions workflows and composite actions for supply-chain and injection risks. Stdlib only.

Usage: python3 audit_workflows.py [path ...] [--json] [--resolve] [--fail-on LEVEL]
  path       repo root, directory, workflow file or action.yml (default: .)
             A directory means <dir>/.github/workflows/*.yml|yaml plus every action.yml|yaml below it.
  --resolve  look up the commit SHA for each unpinned tag via the GitHub API (GITHUB_TOKEN/GH_TOKEN if set).
             Off by default: without it the script makes no network calls.
  --fail-on  lowest severity that makes the exit code 1: critical|high|medium|low|info (default: medium)
  --json     machine-readable output

Exit codes: 0 nothing at or above --fail-on · 1 findings at or above it · 2 nothing to audit
Parser limits: reads block-style YAML the way workflows are written (maps, lists, | and > blocks,
one-line [a, b] and {a: b}). It does not support anchors/aliases, multi-document files, multi-line
flow collections or tabs for indentation; those parts are skipped, so a clean result is not a proof.
It never executes or imports anything it reads.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

SEV = ["critical", "high", "medium", "low", "info"]
ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "vendor", "__pycache__", "dist", "build"}


# --- minimal YAML reader --------------------------------------------------------------
class S(str):
    line, comment = 0, ""


class M(dict):
    line, at = 0, None  # at: key -> line number


class L(list):
    line = 0


def mk(v, line, comment=""):
    s = S(v)
    s.line, s.comment = line, comment
    return s


KEY = re.compile(r"""^(?:"([^"]*)"|'([^']*)'|([^\s'"\[\]{}#][^:'"]*?))\s*:(?:\s+(.*))?$""")
BLOCK = re.compile(r"^[|>][-+0-9]*$")


def strip_comment(s):
    q = None
    for i, ch in enumerate(s):
        if q:
            q = None if ch == q else q
        elif ch in "'\"" and (i == 0 or s[i - 1] in " \t:[{,"):
            q = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip(), s[i + 1:].strip()
    return s.rstrip(), ""


def unquote(v):
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def split_flow(s):
    out, depth, cur, q = [], 0, "", None
    for ch in s:
        if q:
            q = None if ch == q else q
        elif ch in "'\"":
            q = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    return [x for x in out + [cur.strip()] if x]


class Yaml:
    def __init__(self, text):
        self.raw = text.splitlines()
        self.t, self.i = [], 0
        for n, line in enumerate(self.raw, 1):
            st = line.strip()
            if st and not st.startswith("#") and st not in ("---", "..."):
                self.t.append([n, len(line) - len(line.lstrip(" ")), st])

    def parse(self):
        return self.node(self.t[0][1]) if self.t else M()

    def is_item(self, c):
        return c == "-" or c.startswith("- ")

    def node(self, ind):
        return self.seq(ind) if self.is_item(self.t[self.i][2]) else self.map(ind)

    def map(self, ind):
        m = M()
        m.line, m.at = (self.t[self.i][0] if self.i < len(self.t) else 0), {}
        while self.i < len(self.t):
            n, ti, c = self.t[self.i]
            if ti < ind or (ti == ind and self.is_item(c)):
                break
            km = KEY.match(c) if ti == ind else None
            self.i += 1
            if km:
                key = next(g for g in km.groups()[:3] if g is not None)
                m.at[key] = n
                m[key] = self.value(km.group(4) or "", n, ind)
        return m

    def seq(self, ind):
        lst = L()
        lst.line = self.t[self.i][0]
        while self.i < len(self.t):
            n, ti, c = self.t[self.i]
            if ti < ind or (ti == ind and not self.is_item(c)):
                break
            if ti > ind:
                self.i += 1
                continue
            rest = c[1:].lstrip()
            sub = ind + len(c) - len(rest)
            if not rest:
                self.i += 1
                nxt = self.t[self.i] if self.i < len(self.t) else None
                lst.append(self.node(nxt[1]) if nxt and nxt[1] > ind else mk("", n))
            elif self.is_item(rest) or (KEY.match(rest) and rest[0] not in "[{"):
                self.t[self.i] = [n, sub, rest]  # "- key: v" == a map that starts at the item's content column
                lst.append(self.node(sub))
            else:
                self.i += 1
                lst.append(self.value(rest, n, ind))
        return lst

    def value(self, rest, n, ind):
        rest, comment = strip_comment(rest)
        if BLOCK.match(rest):
            return self.block_scalar(n, ind)
        nxt = self.t[self.i] if self.i < len(self.t) else None
        if not rest:
            if nxt and (nxt[1] > ind or (nxt[1] == ind and self.is_item(nxt[2]))):
                return self.node(nxt[1])
            return mk("", n, comment)
        if rest[0] in "[{":
            closer = "]" if rest[0] == "[" else "}"
            while not rest.endswith(closer) and nxt and nxt[1] > ind:  # flow collection over a few lines
                rest += " " + strip_comment(nxt[2])[0]
                self.i += 1
                nxt = self.t[self.i] if self.i < len(self.t) else None
            body = split_flow(rest.strip()[1:-1])
            if closer == "]":
                out = L(mk(unquote(x), n) for x in body)
            else:
                out = M()
                for x in body:
                    k, _, v = x.partition(":")
                    out[unquote(k.strip())] = mk(unquote(v.strip()), n)
            out.line = n
            return out
        val = rest
        while nxt and nxt[1] > ind and not (val[:1] in "'\"" and len(val) > 1 and val[-1] == val[0]):
            val += " " + nxt[2]  # plain or quoted scalar continued on more-indented lines
            self.i += 1
            nxt = self.t[self.i] if self.i < len(self.t) else None
        return mk(unquote(val), n, comment)

    def block_scalar(self, n, ind):
        lines, j = [], n  # raw[j] is the line after the key (0-based index n)
        while j < len(self.raw) and (not self.raw[j].strip() or len(self.raw[j]) - len(self.raw[j].lstrip(" ")) > ind):
            lines.append(self.raw[j])
            j += 1
        while lines and not lines[-1].strip():
            lines.pop()
        first = next((k for k, x in enumerate(lines) if x.strip()), 0)
        body = lines[first:]
        cut = min((len(x) - len(x.lstrip(" ")) for x in body if x.strip()), default=0)
        while self.i < len(self.t) and self.t[self.i][0] <= n + len(lines):
            self.i += 1
        return mk("\n".join(x[cut:] for x in body), n + 1 + first)


def scalars(node):
    if isinstance(node, S):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from scalars(v)
    elif isinstance(node, list):
        for v in node:
            yield from scalars(v)


def get(node, *keys):
    for k in keys:
        node = node.get(k) if isinstance(node, dict) else None
    return node


# --- rules ----------------------------------------------------------------------------
FIRST_PARTY = ("actions", "github")
SHA = re.compile(r"^[0-9a-f]{40}$")
TAGLIKE = re.compile(r"^v?\d+(\.\d+)*([-.+][\w.]+)?$")
EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_IDX = r"(?:\[[^\]]*\]|\.\*)?"
UNTRUSTED = re.compile(r"""github\.(?:
    event\.(?:issue|pull_request|discussion)\.(?:title|body)
  | event\.(?:comment|review|review_comment)\.body
  | event\.pages""" + _IDX + r"""\.page_name
  | event\.commits""" + _IDX + r"""\.(?:message|author\.(?:email|name))
  | event\.head_commit\.(?:message|author\.(?:email|name)|committer\.(?:email|name))
  | event\.pull_request\.head\.(?:ref|label|repo\.default_branch)
  | event\.workflow_run\.(?:head_branch|display_title|head_commit\.(?:message|author\.(?:email|name))
                         |pull_requests""" + _IDX + r"""\.head\.ref)
  | head_ref\b)
  | toJSON\(\s*github\.event(?:\.(?:issue|pull_request|comment|review|review_comment|discussion|head_commit|commits|pages|workflow_run))?\s*\)""",
    re.X | re.I)
PR_HEAD = re.compile(r"github\.event\.pull_request\.head\.(?:sha|ref)|github\.head_ref|github\.event\.workflow_run\.head_(?:sha|branch)"
                     r"|refs/pull/|github\.event\.pull_request\.merge_commit_sha|github\.event\.pull_request\.head\.repo\.full_name")
FETCH_PR = re.compile(r"\bgh\s+pr\s+checkout\b|\bgit\s+fetch\b[^\n]*\bpull/")
PIPE_SH = re.compile(r"\b(curl|wget)\b[^\n|;]*\|\s*(sudo\s+(-\S+\s+)*)?(ba|z|da|k)?sh\b|\b(curl|wget)\b[^\n|;]*\|\s*(sudo\s+)?(python3?|node|perl)(\s+-)?\s*($|[;&|)])"
                     r"|\b(ba|z)?sh\s+(-c\s+)?[\"']?(<\(|\$\()\s*(curl|wget)\b")
UNSAFE_CMDS = re.compile(r"ACTIONS_ALLOW_UNSECURE_COMMANDS\s*:\s*['\"]?true", re.I)
PRIV = {"pull_request_target", "workflow_run", "issue_comment", "issues", "discussion", "discussion_comment"}
PR_EVENTS = {"pull_request", "pull_request_target", "workflow_run"}


class Audit:
    def __init__(self, resolve=False):
        self.findings, self.resolve, self.cache = [], resolve, {}

    def add(self, sev, rule, path, line, msg, why, fix, snippet=""):
        self.findings.append({"severity": sev, "rule": rule, "file": path, "line": line, "message": msg,
                              "why": why, "fix": fix, "snippet": snippet.strip()[:160]})

    def sha_for(self, repo, ref):
        if not self.resolve:
            return None
        if (repo, ref) not in self.cache:
            req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits/{ref}",
                                         headers={"Accept": "application/vnd.github.sha", "User-Agent": "gh-actions-auditor"})
            tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if tok:
                req.add_header("Authorization", f"Bearer {tok}")
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    sha = r.read().decode().strip()
                self.cache[(repo, ref)] = sha if SHA.match(sha) else None
            except Exception:
                self.cache[(repo, ref)] = None
        return self.cache[(repo, ref)]

    # each check gets the file path, raw lines and the parsed tree
    def uses(self, path, raw, u, where):
        if not isinstance(u, S) or not u or u.startswith("./"):
            return
        if u.startswith("docker://"):
            if "@sha256:" not in u:
                tag = u.rsplit(":", 1)[-1] if ":" in u[9:] else ""
                sev = "medium" if tag in ("", "latest") else "low"
                self.add(sev, "unpinned-image", path, u.line, f"{where} uses Docker image `{u[9:]}` without a digest",
                         "Image tags are mutable: whoever controls the registry can change what runs in your job.",
                         f"Pin by digest: `{u.split('@')[0]}@sha256:<digest>` (docker buildx imagetools inspect <image>).", raw[u.line - 1])
            return
        target, _, ref = u.partition("@")
        if SHA.match(ref):
            return
        parts = target.split("/")
        repo = "/".join(parts[:2])
        first = parts[0].lower() in FIRST_PARTY
        branch = not TAGLIKE.match(ref)
        sev = ("medium" if branch else "low") if first else ("high" if branch else "medium")
        sha = self.sha_for(repo, ref) if ref else None
        pin = f"{target}@{sha or '<40-char commit SHA>'} # {ref or 'version'}"
        how = "" if sha else (f" Get the SHA with --resolve or `gh api repos/{repo}/commits/{ref} -H 'Accept: application/vnd.github.sha'`."
                              if ref else "")
        what = f"`{ref}` is a branch: it changes on every push" if branch and ref else (f"tag `{ref}` can be moved" if ref else "no ref")
        self.add(sev, "unpinned-action", path, u.line, f"{where} uses `{u}`: not pinned to a commit SHA ({what})",
                 ("First-party, so lower risk, but tags and branches can still be repointed." if first else
                  "A compromised or sold third-party action can push new code under the same tag and it runs with your token and secrets (see tj-actions/changed-files, March 2025)."),
                 f"Pin it: `uses: {pin}` and let Dependabot/Renovate bump SHAs.{how}", raw[u.line - 1])

    def run_text(self, path, raw, s, triggers, kind):
        if not isinstance(s, S) or not s:
            return
        for m in EXPR.finditer(s):
            expr = m.group(1).strip()
            line = s.line + s[:m.start()].count("\n")
            if UNTRUSTED.search(expr):
                sev = "critical" if triggers & PRIV else "high"
                var = re.sub(r"[^A-Z0-9]+", "_", expr.split(".")[-1].upper()).strip("_") or "VALUE"
                var = {"TITLE": "PR_OR_ISSUE_TITLE", "BODY": "TEXT_BODY", "REF": "HEAD_REF", "MESSAGE": "COMMIT_MESSAGE"}.get(var, var)
                use = f'process.env.{var}' if kind == "script" else f'"${var}"'
                self.add(sev, "script-injection", path, line, f"`${{{{ {expr} }}}}` is pasted straight into a {kind}",
                         "Whoever opens the issue, PR, comment or branch controls this text. Expressions are pasted into the code before it runs, "
                         "so a title such as `a\"; id; #` runs `id` (or anything else) with this job's token and secrets in reach.",
                         f"Pass it through env instead: `env: {{ {var}: ${{{{ {expr} }}}} }}` and use {use} in the {kind}.", raw[line - 1])
            elif kind != "script" and path.endswith(("action.yml", "action.yaml")) and re.match(r"inputs\.", expr):
                self.add("low", "script-injection", path, line, f"`${{{{ {expr} }}}}` is pasted into a composite-action run step",
                         "Callers often pass PR titles or branch names as inputs; the value becomes shell code.",
                         f"Map it to env (`env: {{ X: ${{{{ {expr} }}}} }}`) and use \"$X\" in the script.", raw[line - 1])
        if kind == "run script":
            for m in PIPE_SH.finditer(s):
                line = s.line + s[:m.start()].count("\n")
                self.add("medium", "curl-pipe-shell", path, line, "downloads a script and pipes it straight into a shell",
                         "Nothing checks what was downloaded; a compromised host or redirect runs arbitrary code with the job's token and secrets.",
                         "Download to a file, verify a checksum or signature, then run it; or use a pinned action or package.", raw[line - 1])

    def audit_steps(self, path, raw, steps, triggers, job_name):
        if not isinstance(steps, list):
            return False
        pr_checkout, persisted = None, None
        for idx, st in enumerate(steps):
            if not isinstance(st, dict):
                continue
            u = st.get("uses")
            self.uses(path, raw, u, f"step {idx + 1} of `{job_name}`")
            self.run_text(path, raw, st.get("run"), triggers, "run script")
            if isinstance(u, S) and re.match(r"actions/github-script@", u):
                self.run_text(path, raw, get(st, "with", "script"), triggers, "script")
            later = [x for x in steps[idx + 1:] if isinstance(x, dict) and (x.get("run") or str(x.get("uses", "")).startswith("./"))]
            if isinstance(u, S) and u.startswith("actions/checkout@"):
                ref = " ".join(str(get(st, "with", k) or "") for k in ("ref", "repository"))
                if triggers & {"pull_request_target", "workflow_run", "issue_comment"} and PR_HEAD.search(ref) and not pr_checkout:
                    pr_checkout = (getattr(get(st, "with", "ref"), "line", 0) or u.line, later)
                pc = str(get(st, "with", "persist-credentials") or "").lower()
                if pc != "false" and later and triggers & PR_EVENTS and not persisted:
                    persisted = u.line
            run = st.get("run")
            if isinstance(run, S) and FETCH_PR.search(run) and triggers & {"pull_request_target", "workflow_run", "issue_comment"} and not pr_checkout:
                pr_checkout = (run.line, later)
        if pr_checkout:
            line, later = pr_checkout
            ev = "/".join(sorted(triggers & {"pull_request_target", "workflow_run", "issue_comment"}))
            if later:
                self.add("critical", "pwn-request", path, line, f"`{job_name}` ({ev}) checks out the PR's code and then runs commands in it",
                         f"{ev} runs with a write token and your secrets. Build scripts, package.json hooks, Makefiles and local actions "
                         "in the checked-out PR are attacker-controlled, so a fork PR can steal secrets or push to the repo.",
                         "Use `on: pull_request` for anything that builds or tests PR code. If you need privileges, split it: an unprivileged "
                         "`pull_request` workflow uploads results as an artifact and a `workflow_run` workflow reads them as data only.", raw[line - 1])
            else:
                self.add("medium", "pwn-request", path, line, f"`{job_name}` ({ev}) checks out the PR's code in a privileged workflow",
                         "Nothing visibly executes it yet, but any later step or tool that reads config from the workspace runs attacker input.",
                         "Prefer `on: pull_request`. If you keep this, never run, build or `source` anything from that checkout and set `persist-credentials: false`.",
                         raw[line - 1])
        if persisted:
            self.add("info", "persist-credentials", path, persisted, f"checkout in `{job_name}` keeps the token in .git/config",
                     "Later steps in this PR-triggered job (and any artifact that uploads the workspace) can read the GITHUB_TOKEN from .git/config.",
                     "Add `with: { persist-credentials: false }` unless a later step needs to push.", raw[persisted - 1])
        return bool(pr_checkout)

    def permissions(self, path, raw, root, jobs, triggers):
        top = root.get("permissions")
        if top is None:
            loose = [k for k, j in jobs.items() if isinstance(j, dict) and "permissions" not in j]
            if loose:
                self.add("low" if triggers == {"workflow_call"} else "medium", "missing-permissions", path, (root.at or {}).get("jobs", 1),
                         f"no top-level `permissions:` and {len(loose)} job(s) don't set their own ({', '.join(loose[:4])})",
                         "The GITHUB_TOKEN then gets the repo or org default, which is read-write on older repos and orgs. A compromised step can push code or tamper with releases.",
                         "Add `permissions: { contents: read }` (or the block form) at the top and grant extra scopes only on the jobs that need them.")
        for scope, node, label in [("workflow", top, "top level")] + [("job", get(j, "permissions"), f"job `{k}`") for k, j in jobs.items()]:
            if isinstance(node, S) and node == "write-all":
                self.add("high", "write-all", path, node.line, f"`permissions: write-all` at {label}",
                         "Every scope is writable: contents, packages, actions, id-token, security events and more.",
                         "List only what the job needs, e.g. `permissions: { contents: read }`, and add single write scopes per job.", raw[node.line - 1])
            elif isinstance(node, dict):
                writes = [k for k, v in node.items() if str(v) == "write"]
                if writes and scope == "workflow":
                    self.add("medium" if len(jobs) > 1 else "low", "broad-write-scope", path, node.line,
                             f"write scopes at the workflow level: {', '.join(writes)}",
                             "Top-level permissions apply to every job, including ones that only build or test.",
                             "Set `permissions: { contents: read }` at the top and move each write scope to the one job that uses it.", raw[node.line - 1])
                elif writes and triggers & PRIV:
                    self.add("low", "broad-write-scope", path, node.line, f"{label} has write scopes ({', '.join(writes)}) in a workflow untrusted users can trigger",
                             "Check: if anything in this job handles issue/PR text or PR code, a bug there gets this write access.",
                             "Keep only the scopes the job's steps actually call (check each API call or action).", raw[node.line - 1])

    def workflow(self, path, raw, root, owner):
        on = root.get("on", root.get("true"))
        triggers = set(on) if isinstance(on, (list, dict)) else ({str(on)} if on else set())
        jobs = root.get("jobs") if isinstance(root.get("jobs"), dict) else {}
        self.permissions(path, raw, root, jobs, triggers)
        privileged = sorted(triggers & {"pull_request_target", "workflow_run"})
        env_secrets = [s for s in scalars(root.get("env")) if re.search(r"secrets\.(?!GITHUB_TOKEN\b)\w+", s)]
        if privileged and env_secrets:
            s0 = env_secrets[0]
            self.add("medium", "secrets-in-privileged-pr", path, s0.line, f"workflow-level env exposes secrets to every job of a {'/'.join(privileged)} workflow",
                     "Every step in every job, including ones that touch PR content, can read them.",
                     "Move the secret into the env of the single step that needs it.", raw[s0.line - 1])
        for name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            self.uses(path, raw, job.get("uses"), f"job `{name}` (reusable workflow)")
            pwn = self.audit_steps(path, raw, job.get("steps"), triggers, name)
            ru = job.get("runs-on")
            if triggers & {"pull_request", "pull_request_target"} and any("self-hosted" in s for s in scalars(ru)):
                self.add("medium", "self-hosted-pr", path, ru.line if hasattr(ru, "line") else 1, f"`{name}` runs PR code on a self-hosted runner",
                         "Check: in a public repo any fork PR can run code on that machine and persist on it between jobs.",
                         "Use GitHub-hosted runners for PRs, or require approval for fork PRs and use ephemeral self-hosted runners.")
            ju = job.get("uses")
            if isinstance(ju, S) and not ju.startswith("./") and str(job.get("secrets", "")) == "inherit":
                jowner = ju.split("/")[0].lower()
                if owner is None or jowner != owner:
                    sec = job.get("secrets")
                    self.add("high" if owner else "medium", "secrets-inherit", path, sec.line,
                             f"`{name}` passes all secrets (`secrets: inherit`) to `{ju.split('@')[0]}`" + ("" if owner else " (check the owner)"),
                             "Every repo and org secret becomes readable by code you don't control.",
                             "Pass only the named secrets the workflow declares: `secrets: { NPM_TOKEN: ${{ secrets.NPM_TOKEN }} }`.", raw[sec.line - 1])
            if privileged:
                used = [s for s in scalars(job) if re.search(r"secrets\.(?!GITHUB_TOKEN\b)\w+|secrets\[", s)]
                if used:
                    s0 = used[0]
                    self.add("high" if pwn else "medium", "secrets-in-privileged-pr", path, s0.line,
                             f"`{name}` uses secrets in a {'/'.join(privileged)} workflow"
                             + (" that also runs PR code" if pwn else " (check)"),
                             "These triggers run for fork PRs with secrets available. Any step that touches PR-controlled code or text can leak them.",
                             "Keep secrets in jobs that never check out or interpolate PR content; gate with an environment that needs approval.", raw[s0.line - 1])
        return triggers

    def action(self, path, raw, root):
        runs = root.get("runs")
        if isinstance(runs, dict) and str(runs.get("using", "")) == "composite":
            self.audit_steps(path, raw, runs.get("steps"), set(), "composite action")

    def file(self, path, owner):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            self.add("info", "unreadable", path, 0, f"could not read: {e}", "", "")
            return
        raw = text.splitlines() + [""]
        try:
            root = Yaml(text).parse()
        except (IndexError, ValueError, StopIteration) as e:
            self.add("info", "parse-error", path, 0, f"could not parse ({e.__class__.__name__}); audit by hand", "", "")
            return
        for n, line in enumerate(raw, 1):
            if UNSAFE_CMDS.search(line):
                self.add("high", "unsecure-commands", path, n, "ACTIONS_ALLOW_UNSECURE_COMMANDS is enabled",
                         "Re-enables the deprecated set-env/add-path commands, so any log line (e.g. printed PR text) can set env vars or PATH for later steps.",
                         "Remove it and write to $GITHUB_ENV / $GITHUB_PATH instead.", line)
        if not isinstance(root, dict):
            return
        if "jobs" in root:
            self.workflow(path, raw, root, owner)
        elif "runs" in root:
            self.action(path, raw, root)


def repo_owner(start):
    d = os.path.abspath(start if os.path.isdir(start) else os.path.dirname(start))
    while True:
        cfg = os.path.join(d, ".git", "config")
        if os.path.isfile(cfg):
            with open(cfg, errors="ignore") as fh:
                m = re.search(r"url\s*=\s*\S*github\.com[:/]([^/\s]+)/", fh.read())
            return m.group(1).lower() if m else None
        if os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def looks_like_workflow(fp):
    with open(fp, encoding="utf-8", errors="ignore") as fh:
        return bool(re.search(r"^(jobs|runs):", fh.read(200_000), re.M))


def discover(paths):
    out = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
            continue
        wf = os.path.join(p, ".github", "workflows")
        for d in (wf, p):  # also accept a workflows directory passed directly
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    fp = os.path.join(d, f)
                    if f.endswith((".yml", ".yaml")) and os.path.isfile(fp) and (d == wf or looks_like_workflow(fp)):
                        out.append(fp)
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            out += [os.path.join(root, f) for f in files if f in ("action.yml", "action.yaml")]
    seen = set()
    return [f for f in out if not (os.path.abspath(f) in seen or seen.add(os.path.abspath(f)))]


def main():
    ap = argparse.ArgumentParser(description="Audit GitHub Actions workflows for supply-chain and injection risks.")
    ap.add_argument("paths", nargs="*", default=["."])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--resolve", action="store_true", help="resolve tags to SHAs via the GitHub API (network)")
    ap.add_argument("--fail-on", default="medium", choices=SEV)
    a = ap.parse_args()
    files = discover(a.paths)
    if not files:
        print("No workflow or action files found (looked for .github/workflows/*.yml and action.yml).", file=sys.stderr)
        sys.exit(2)
    au = Audit(resolve=a.resolve)
    for f in files:
        au.file(f, repo_owner(f))
    fs = sorted(au.findings, key=lambda x: (SEV.index(x["severity"]), x["file"], x["line"]))
    counts = {s: sum(1 for x in fs if x["severity"] == s) for s in SEV}
    if a.json:
        print(json.dumps({"files": files, "counts": counts, "findings": fs}, indent=2))
    else:
        print(f"🛡️  Audited {len(files)} file(s)" + (" (resolving SHAs via api.github.com)" if a.resolve else ""))
        explained = set()
        for x in fs:
            print(f"\n{ICON[x['severity']]} {x['severity'].upper():<8} {x['rule']:<24} {x['file']}:{x['line']}")
            print(f"    {x['message']}")
            if x["snippet"]:
                print(f"    > {x['snippet']}")
            if x["why"] and (x["rule"], x["why"]) not in explained:
                explained.add((x["rule"], x["why"]))
                print(f"    why: {x['why']}")
            if x["fix"]:
                print(f"    fix: {x['fix']}")
        print("\n" + (", ".join(f"{v} {k}" for k, v in counts.items() if v) or "✅ No findings."))
    sys.exit(1 if any(SEV.index(x["severity"]) <= SEV.index(a.fail_on) for x in fs) else 0)


if __name__ == "__main__":
    main()
