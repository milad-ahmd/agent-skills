#!/usr/bin/env python3
"""Plan git hooks for a repository from the tools it already uses.

Detects the stack (languages, package managers, monorepo markers), any existing hook setup
(pre-commit, husky, lefthook, simple-git-hooks, lint-staged, custom .git/hooks scripts,
core.hooksPath) and the formatter/linter configs already in the repo. Prints the hook manager
it recommends, the hooks it would set up, the config file text, the CI commands that mirror
the hooks, and the commands that install them.

Read-only unless --write is given. --write creates the config file(s) only when no hook setup
exists and none of the target files exist; it never overwrites.

Exit codes:
  0  plan printed (and written with --write)
  1  a hook setup already exists: it is reported, nothing is written
  2  not a git repository, or a git error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# Revs checked with `git ls-remote --tags` on 2026-11-25. Run `pre-commit autoupdate` after writing.
REVS = {
    "https://github.com/pre-commit/pre-commit-hooks": "v6.0.0",
    "https://github.com/gitleaks/gitleaks": "v8.30.1",
    "https://github.com/astral-sh/ruff-pre-commit": "v0.17.6",
    "https://github.com/psf/black-pre-commit-mirror": "26.10.1",
}
LARGE_KB = 500  # same default as pre-commit-hooks check-added-large-files

JS_EXT = ["js", "jsx", "ts", "tsx", "mjs", "cjs", "mts", "cts"]
LANG_EXT = {
    "python": {"py", "pyi"},
    "javascript": set(JS_EXT),
    "go": {"go"},
    "rust": {"rs"},
}
# name: (ecosystem, extensions, hook command, fixes files?, ci command); "{files}" = staged files
TOOLS = {
    "ruff-check": ("python", ["py", "pyi"], "ruff check --fix {files}", True, "ruff check ."),
    "ruff-format": ("python", ["py", "pyi"], "ruff format {files}", True, "ruff format --check ."),
    "black": ("python", ["py", "pyi"], "black {files}", True, "black --check ."),
    "eslint": ("javascript", JS_EXT, "eslint --fix {files}", True, "eslint ."),
    "prettier": ("javascript", JS_EXT + ["json", "jsonc", "json5", "md", "mdx", "css", "scss", "less",
                                         "html", "vue", "yaml", "yml", "graphql"],
                 "prettier --write --ignore-unknown {files}", True, "prettier --check ."),
    "biome": ("javascript", JS_EXT + ["json", "jsonc", "css"],
              "biome check --write --no-errors-on-unmatched --files-ignore-unknown=true {files}", True,
              "biome ci ."),
    "gofmt": ("go", ["go"], "gofmt -l -w {files}", True, 'test -z "$(gofmt -l .)"'),
    "golangci-lint": ("go", ["go"], "golangci-lint run --new-from-rev HEAD --fix", True, "golangci-lint run"),
    "rustfmt": ("rust", ["rs"], "cargo fmt --all -- --check", False, "cargo fmt --all -- --check"),
}
ESLINT_FILES = ["eslint.config." + e for e in ("js", "mjs", "cjs", "ts", "mts", "cts")] + \
    [".eslintrc"] + [".eslintrc." + e for e in ("js", "cjs", "yaml", "yml", "json")]
PRETTIER_FILES = [".prettierrc"] + [".prettierrc." + e for e in (
    "json", "yaml", "yml", "json5", "js", "cjs", "mjs", "ts", "cts", "mts", "toml")] + \
    ["prettier.config." + e for e in ("js", "cjs", "mjs", "ts", "cts", "mts")]
GOLANGCI_FILES = [".golangci." + e for e in ("yml", "yaml", "toml", "json")]
LEFTHOOK_FILES = [p + "." + e for p in ("lefthook", ".lefthook", ".config/lefthook")
                  for e in ("yml", "yaml", "toml", "json", "jsonc")]
LINTSTAGED_FILES = [".lintstagedrc"] + [".lintstagedrc." + e for e in (
    "json", "yaml", "yml", "mjs", "cjs", "js")] + ["lint-staged.config." + e for e in ("mjs", "cjs", "js")]
SIMPLE_HOOKS_FILES = [p + e for p in (".simple-git-hooks", "simple-git-hooks")
                      for e in (".json", ".js", ".cjs", ".mjs")]
LOCKFILES = {"package-lock.json": "npm", "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "bun.lockb": "bun",
             "bun.lock": "bun", "uv.lock": "uv", "poetry.lock": "poetry", "Pipfile.lock": "pipenv",
             "go.sum": "go", "Cargo.lock": "cargo"}
JS_EXEC = {"npm": "npx", "pnpm": "pnpm exec", "yarn": "yarn", "bun": "bunx"}
JS_ADD = {"npm": "npm install --save-dev", "pnpm": "pnpm add --save-dev", "yarn": "yarn add --dev",
          "bun": "bun add --dev"}
# Test fixtures and vendored code are not the project's own stack.
SKIP_DIRS = {"fixtures", "__fixtures__", "testdata", "vendor", "third_party", "node_modules"}
MONOREPO_MARKERS = ["pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json", "rush.json", "go.work"]
RUFF_FORMAT_HINT_FILES = ["Makefile", "justfile", "tox.ini", "noxfile.py", "pyproject.toml", "package.json",
                          ".pre-commit-config.yaml"]

# POSIX sh: fail when a file staged for addition is over the limit (pre-commit's rule, in plain git).
LARGE_FILES_SH = (
    "git diff --cached --name-only --diff-filter=A | while IFS= read -r f; do "
    "s=$(git cat-file -s \":$f\"); if [ \"$s\" -gt %d ]; then "
    "echo \"$f is $s bytes, over the %d KB limit (use Git LFS or leave it out)\"; exit 1; fi; done"
    % (LARGE_KB * 1024, LARGE_KB))
GITLEAKS_SH = (
    "if command -v gitleaks >/dev/null 2>&1; then gitleaks git --pre-commit --redact --staged --verbose; "
    "else echo \"gitleaks not installed: secret scan skipped here, CI still runs it\"; fi")


def git(root, *args):
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def load_json(path):
    try:
        return json.loads(read(path) or "null") or {}
    except ValueError:
        return {}


def list_files(root):
    r = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       capture_output=True, text=True)
    return sorted(set(p for p in r.stdout.split("\0") if p and not SKIP_DIRS & set(p.split("/")[:-1])))


def detect(root):
    files = list_files(root)
    fileset = set(files)
    names = {}
    for f in files:
        names.setdefault(os.path.basename(f), []).append(f)

    def dirs_with(*basenames):
        return sorted({os.path.dirname(f) or "." for b in basenames for f in names.get(b, [])})

    counts = {}
    for f in files:
        ext = f.rsplit(".", 1)[-1] if "." in os.path.basename(f) else ""
        for lang, exts in LANG_EXT.items():
            if ext in exts:
                counts[lang] = counts.get(lang, 0) + 1
    manifests = {"python": dirs_with("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
                 "javascript": dirs_with("package.json"), "go": dirs_with("go.mod"), "rust": dirs_with("Cargo.toml")}
    languages = sorted(set(counts) | {k for k, v in manifests.items() if v})

    managers = sorted({LOCKFILES[b] for b in LOCKFILES if b in names})
    root_pkg = load_json(os.path.join(root, "package.json")) if "package.json" in fileset else {}
    js_pm = next((LOCKFILES[b] for b in ("pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
                                         "package-lock.json") if b in fileset), "npm")

    markers = [m for m in MONOREPO_MARKERS if m in fileset]
    if root_pkg.get("workspaces"):
        markers.append("package.json workspaces")
    if "Cargo.toml" in fileset and re.search(r"^\[workspace\]", read(os.path.join(root, "Cargo.toml")), re.M):
        markers.append("Cargo.toml [workspace]")
    for lang, ds in manifests.items():
        if lang != "rust" and len(ds) > 1:
            markers.append("%d %s manifests (%s)" % (len(ds), lang, ", ".join(ds[:4])))

    tools = detect_tools(root, files, names, manifests)
    return {"root": root, "languages": languages, "file_counts": counts, "package_managers": managers,
            "js_package_manager": js_pm if "javascript" in languages else None,
            "monorepo_markers": markers, "existing_hooks": detect_hooks(root, fileset, root_pkg),
            "tools": tools}


def detect_tools(root, files, names, manifests):
    found = []  # dicts: tool, dir, reason

    def add(tool, d, reason):
        if not any(t["tool"] == tool and t["dir"] == d for t in found):
            found.append({"tool": tool, "dir": d, "reason": reason})

    def hits(candidates):
        return [f for c in candidates for f in names.get(c, [])]

    for f in names.get("pyproject.toml", []):
        text, d = read(os.path.join(root, f)), os.path.dirname(f) or "."
        if re.search(r"^\[tool\.ruff[\].]", text, re.M):
            add("ruff-check", d, "[tool.ruff] in %s" % f)
            if re.search(r"^\[tool\.ruff\.format\]", text, re.M):
                add("ruff-format", d, "[tool.ruff.format] in %s" % f)
        if re.search(r"^\[tool\.black\]", text, re.M):
            add("black", d, "[tool.black] in %s" % f)
    for f in hits(["ruff.toml", ".ruff.toml"]):
        d = os.path.dirname(f) or "."
        add("ruff-check", d, f)
        if re.search(r"^\[format\]", read(os.path.join(root, f)), re.M):
            add("ruff-format", d, "[format] in %s" % f)
    if any(t["tool"] == "ruff-check" for t in found) and not any(t["tool"] == "ruff-format" for t in found):
        hint_files = [f for f in files if os.path.basename(f) in RUFF_FORMAT_HINT_FILES
                      or f.startswith(".github/workflows/")]
        for f in hint_files:
            if re.search(r"ruff[ -]format", read(os.path.join(root, f))):
                d = next(t["dir"] for t in found if t["tool"] == "ruff-check")
                add("ruff-format", d, "'ruff format' used in %s" % f)
                break

    for f in names.get("package.json", []):
        pkg, d = load_json(os.path.join(root, f)), os.path.dirname(f) or "."
        if "eslintConfig" in pkg:
            add("eslint", d, "eslintConfig in %s" % f)
        if isinstance(pkg.get("prettier"), (dict, str)):
            add("prettier", d, "prettier key in %s" % f)
    for f in hits(ESLINT_FILES):
        add("eslint", os.path.dirname(f) or ".", f)
    for f in hits(PRETTIER_FILES):
        add("prettier", os.path.dirname(f) or ".", f)
    for f in hits(["biome.json", "biome.jsonc"]):
        add("biome", os.path.dirname(f) or ".", f)

    for d in manifests["go"]:
        add("gofmt", d, "go.mod in %s (gofmt ships with Go)" % d)
    for f in hits(GOLANGCI_FILES):
        d = os.path.dirname(f) or "."
        mod = max((m for m in manifests["go"] if m == "." or d == m or d.startswith(m + "/")), key=len, default=d)
        add("golangci-lint", mod, f)
    rust_roots = [d for d in manifests["rust"]
                  if not any(o != d and (o == "." or d.startswith(o + "/")) for o in manifests["rust"])]
    for d in rust_roots:
        add("rustfmt", d, "Cargo.toml in %s (rustfmt ships with rustup)" % d)

    if any(t["tool"] == "black" for t in found):  # two Python formatters would fight
        found = [t for t in found if t["tool"] != "ruff-format"]
    return found


def detect_hooks(root, fileset, root_pkg):
    hooks = []
    for f in (".pre-commit-config.yaml", ".pre-commit-config.yml"):
        if f in fileset or os.path.exists(os.path.join(root, f)):
            hooks.append("pre-commit: %s" % f)
    if os.path.isdir(os.path.join(root, ".husky")):
        hooks.append("husky: .husky/")
    hooks += ["lefthook: %s" % f for f in LEFTHOOK_FILES if os.path.exists(os.path.join(root, f))]
    hooks += ["simple-git-hooks: %s" % f for f in SIMPLE_HOOKS_FILES if os.path.exists(os.path.join(root, f))]
    hooks += ["lint-staged: %s" % f for f in LINTSTAGED_FILES if os.path.exists(os.path.join(root, f))]
    for key in ("simple-git-hooks", "lint-staged", "husky"):
        if key in root_pkg:
            hooks.append("%s: key in package.json" % key)
    deps = dict(root_pkg.get("devDependencies") or {}, **(root_pkg.get("dependencies") or {}))
    for dep in ("husky", "lefthook", "simple-git-hooks", "lint-staged"):
        if dep in deps:
            hooks.append("%s: in package.json dependencies" % dep)
    code, hooks_path = git(root, "config", "--get", "core.hooksPath")
    if code == 0 and hooks_path:
        hooks.append("core.hooksPath = %s" % hooks_path)
    code, common = git(root, "rev-parse", "--git-common-dir")  # not --git-path: that follows core.hooksPath
    git_hooks = os.path.join(root, common, "hooks") if code == 0 else ""
    if git_hooks and os.path.isdir(git_hooks):
        for h in sorted(os.listdir(git_hooks)):
            if not h.endswith(".sample") and os.path.isfile(os.path.join(git_hooks, h)):
                hooks.append("custom script: .git/hooks/%s" % h)
    return hooks


def choose(info):
    langs = set(info["languages"]) & set(LANG_EXT)
    if info["monorepo_markers"]:
        return "lefthook", "monorepo: one binary, a `root:` per package"
    if langs == {"javascript"}:
        return "husky", "JS/TS project with package.json: husky + lint-staged install through %s" % (
            info["js_package_manager"])
    if langs <= {"python"}:
        why = "Python project" if langs else "no language tooling found"
        return "pre-commit", why + ": pre-commit pins each hook's version and installs it in its own env"
    if len(langs) == 1:
        return "lefthook", "%s project: lefthook is one binary, no Python or Node needed" % next(iter(langs)).title()
    return "lefthook", "polyglot repo (%s): one config for every language" % ", ".join(sorted(langs))


def glob_for(exts):
    return "*.%s" % exts[0] if len(exts) == 1 else "*.{%s}" % ",".join(exts)


def render_precommit(tools):
    names = [t["tool"] for t in tools]
    out = ["# Git hooks for this repo. Install: pre-commit install. Update revs: pre-commit autoupdate",
           "# Skip one hook: SKIP=<id> git commit ...   Skip all: git commit --no-verify (CI still runs them)",
           "repos:"]

    def repo(url, hooks):
        out.extend(["  - repo: %s" % url, "    rev: %s" % REVS[url], "    hooks:"] + hooks)

    repo("https://github.com/pre-commit/pre-commit-hooks", ["      - id: check-added-large-files"])
    repo("https://github.com/gitleaks/gitleaks", ["      - id: gitleaks"])
    ruff = []
    if "ruff-check" in names:
        ruff += ["      - id: ruff-check", "        args: [--fix]"]
    if "ruff-format" in names:
        ruff += ["      - id: ruff-format"]
    if ruff:
        repo("https://github.com/astral-sh/ruff-pre-commit", ruff)
    if "black" in names:
        repo("https://github.com/psf/black-pre-commit-mirror", ["      - id: black"])
    return {".pre-commit-config.yaml": "\n".join(out) + "\n"}


def render_husky(tools, pm):
    by_ext = {}
    for t in tools:
        for ext in TOOLS[t["tool"]][1]:
            by_ext.setdefault(ext, []).append(t["tool"])
    groups = {}  # same tool sequence -> one key, so no two keys touch the same file at once
    for ext, seq in by_ext.items():
        groups.setdefault(tuple(seq), []).append(ext)
    config = {}
    for seq, exts in groups.items():
        cmds = [TOOLS[n][2].replace(" {files}", "") for n in seq]
        config[glob_for(exts)] = cmds if len(cmds) > 1 else cmds[0]
    hook = "\n".join([LARGE_FILES_SH, GITLEAKS_SH] + (["lint-staged"] if config else [])) + "\n"
    files = {".husky/pre-commit": hook}
    if config:
        files[".lintstagedrc.json"] = json.dumps(config, indent=2) + "\n"
    return files


def render_lefthook(tools, pm):
    out = ["# Git hooks for this repo. Install once per clone: lefthook install",
           "# Skip once: LEFTHOOK=0 git commit ...  or  git commit --no-verify (CI still runs the checks)",
           "pre-commit:", "  jobs:",
           "    - name: large-files", "      run: %s" % yaml_str(LARGE_FILES_SH),
           "    - name: gitleaks", "      run: %s" % yaml_str(GITLEAKS_SH)]
    for t in tools:
        eco, exts, cmd, fixes, _ = TOOLS[t["tool"]]
        cmd = cmd.replace("{files}", "{staged_files}")
        if eco == "javascript":
            cmd = "%s %s" % (JS_EXEC.get(pm or "npm", "npx"), cmd)
        name = t["tool"] if t["dir"] == "." else "%s-%s" % (t["tool"], t["dir"].replace("/", "-"))
        out += ["    - name: %s" % name]
        if t["dir"] != ".":
            out += ['      root: "%s/"' % t["dir"]]
        out += ['      glob: "%s"' % glob_for(exts), "      run: %s" % cmd]
        if fixes and "{staged_files}" in cmd:
            out += ["      stage_fixed: true"]
    return {"lefthook.yml": "\n".join(out) + "\n"}


def yaml_str(s):
    return "'%s'" % s.replace("'", "''")


def ci_commands(manager, tools, pm):
    if manager == "pre-commit":
        return [{"dir": ".", "run": "pre-commit run --all-files --show-diff-on-failure"}]
    cmds = [{"dir": ".", "run": "gitleaks git --redact --verbose"}]
    for t in tools:
        eco, ci = TOOLS[t["tool"]][0], TOOLS[t["tool"]][4]
        if eco == "javascript":
            ci = "%s %s" % (JS_EXEC.get(pm or "npm", "npx"), ci)
        cmds.append({"dir": t["dir"], "run": ci})
    return cmds


def setup_steps(manager, tools, pm):
    if manager == "pre-commit":
        return ["pipx install pre-commit   # or: uv tool install pre-commit / pip install pre-commit",
                "pre-commit install", "pre-commit autoupdate", "pre-commit run --all-files"]
    if manager == "husky":
        pm = pm or "npm"
        return ["%s husky lint-staged" % JS_ADD[pm], "npm pkg set scripts.prepare=husky",
                "%s husky   # sets core.hooksPath=.husky/_ now; prepare re-runs it on every install" % JS_EXEC[pm],
                "brew install gitleaks   # or a release binary; the hook skips the scan if it is missing"]
    return ["brew install lefthook   # or: npm install --save-dev lefthook / pipx install lefthook / go install",
            "lefthook install", "lefthook run pre-commit --all-files   # first run over every file"]


def missing_binaries(root, manager, tools):
    need = {"pre-commit": ["pre-commit"], "lefthook": ["lefthook", "gitleaks"], "husky": ["gitleaks"]}[manager]
    for t in tools:
        if TOOLS[t["tool"]][0] == "javascript":
            if not os.path.exists(os.path.join(root, t["dir"], "node_modules", ".bin", t["tool"])):
                need.append(t["tool"] + " (node_modules/.bin)")
        elif manager == "lefthook":
            need.append({"ruff-check": "ruff", "ruff-format": "ruff", "rustfmt": "cargo"}.get(t["tool"], t["tool"]))
    return sorted({n for n in need if "(" in n or not shutil.which(n)})


def build_plan(root):
    info = detect(root)
    manager, reason = choose(info)
    tools = sorted(info["tools"], key=lambda t: (t["dir"], list(TOOLS).index(t["tool"])))  # linters before formatters
    warnings = ["biome and %s are both configured in %s: keep one, or they rewrite each other's output"
                % (t["tool"], t["dir"]) for t in tools if t["tool"] in ("eslint", "prettier")
                and any(b["tool"] == "biome" and b["dir"] == t["dir"] for b in tools)]
    pm = info["js_package_manager"]
    render = {"pre-commit": render_precommit, "husky": lambda ts: render_husky(ts, pm),
              "lefthook": lambda ts: render_lefthook(ts, pm)}[manager]
    hooks = [{"name": "large-files", "why": "blocks new files over %d KB" % LARGE_KB},
             {"name": "gitleaks", "why": "scans the staged diff for secrets"}]
    hooks += [{"name": t["tool"], "dir": t["dir"], "why": t["reason"]} for t in tools]
    info.update({"manager": manager, "reason": reason, "hooks": hooks, "files": render(tools),
                 "ci": ci_commands(manager, tools, pm), "setup": setup_steps(manager, tools, pm),
                 "missing": missing_binaries(root, manager, tools), "warnings": warnings})
    return info


def write_files(root, files):
    clash = [p for p in files if os.path.exists(os.path.join(root, p))]
    if clash:
        return "refused: %s already exist(s)" % ", ".join(clash), []
    written = []
    for path, text in files.items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full) or root, exist_ok=True)
        with open(full, "x", encoding="utf-8") as fh:  # "x" never overwrites
            fh.write(text)
        written.append(path)
    return "", written


def print_text(p, write_note):
    counts = ", ".join("%s (%d files)" % (k, p["file_counts"].get(k, 0)) for k in p["languages"]) or "none"
    print("Stack: %s" % counts)
    print("Package managers: %s" % (", ".join(p["package_managers"]) or "none"))
    print("Monorepo: %s" % ("; ".join(p["monorepo_markers"]) or "no"))
    if p["existing_hooks"]:
        print("\nExisting hook setup (extend it, do not replace it):")
        for h in p["existing_hooks"]:
            print("  - " + h)
    print("\nRecommended: %s (%s)" % (p["manager"], p["reason"]))
    print("Hooks (staged files only):")
    for h in p["hooks"]:
        where = "" if h.get("dir", ".") == "." else " [%s]" % h["dir"]
        print("  %-15s %s%s" % (h["name"], h["why"], where))
    for path, text in p["files"].items():
        print("\n--- %s ---\n%s--- end ---" % (path, text))
    print("\nCI (same checks, not skippable with --no-verify):")
    for c in p["ci"]:
        print("  %s%s" % ("" if c["dir"] == "." else "cd %s && " % c["dir"], c["run"]))
    print("\nSetup:")
    for s in p["setup"]:
        print("  " + s)
    for w in p["warnings"]:
        print("\nWarning: " + w)
    if p["missing"]:
        print("\nNot found on this machine: %s" % ", ".join(p["missing"]))
    if write_note:
        print("\n" + write_note)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-C", dest="root", default=".", help="repository to inspect (default: current dir)")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    ap.add_argument("--write", action="store_true",
                    help="write the config file(s) if no hook setup exists; never overwrites")
    args = ap.parse_args(argv)

    code, top = git(os.path.abspath(args.root), "rev-parse", "--show-toplevel")
    if code != 0:
        print("not a git repository: %s" % args.root, file=sys.stderr)
        return 2
    plan = build_plan(top)
    status, note = (1 if plan["existing_hooks"] else 0), ""
    plan["written"] = []
    if args.write:
        if status:
            note = "--write refused: a hook setup already exists."
        else:
            err, plan["written"] = write_files(top, plan["files"])
            status = 1 if err else 0
            note = ("--write " + err) if err else "Wrote: " + ", ".join(plan["written"])
    plan["root"] = "."  # keep absolute paths out of shared output
    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print_text(plan, note)
    return status


if __name__ == "__main__":
    sys.exit(main())
