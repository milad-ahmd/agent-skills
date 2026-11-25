#!/usr/bin/env python3
"""Tests for hooks_plan.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/pre-commit-setup/scripts -p 'test_*.py'
Each test builds a throwaway git repo (Python with ruff, TS with eslint+prettier, Go, a polyglot
monorepo, existing husky/pre-commit/custom hooks) and checks the plan, the exit code and --write.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hooks_plan  # noqa: E402

SCRIPT = os.path.join(HERE, "hooks_plan.py")
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C",
           GIT_AUTHOR_NAME="Ada Tester", GIT_AUTHOR_EMAIL="ada@example.com",
           GIT_COMMITTER_NAME="Ada Tester", GIT_COMMITTER_EMAIL="ada@example.com",
           GIT_AUTHOR_DATE="2026-10-01T12:00:00Z", GIT_COMMITTER_DATE="2026-10-01T12:00:00Z")
for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    ENV.pop(k, None)


class Repo:
    def __init__(self, path, init=True):
        self.path = path
        os.makedirs(path)
        if init:
            self.git("init", "-q", "-b", "main")

    def git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.path, env=ENV, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def write(self, **files):
        for name, text in files.items():
            full = os.path.join(self.path, name.replace("__", "/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)

    def run(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "-C", self.path, *args], env=ENV, capture_output=True,
                           text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def plan(self, *args):
        code, out, err = self.run("--json", *args)
        self.last_code = code
        return json.loads(out)

    def exists(self, name):
        return os.path.exists(os.path.join(self.path, name))


PYPROJECT_RUFF = '[project]\nname = "app"\n\n[tool.ruff]\nline-length = 100\n'
PKG_TS = json.dumps({"name": "web", "private": True, "devDependencies": {"eslint": "^9", "prettier": "^3"}})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def repo(self, name="r", init=True):
        return Repo(os.path.join(self.tmp, name), init)

    def tools(self, plan):
        return sorted((h["name"], h.get("dir", ".")) for h in plan["hooks"])


class PythonRepo(Base):
    def make(self, **extra):
        r = self.repo()
        files = {"pyproject.toml": PYPROJECT_RUFF, "uv.lock": "", "app__main.py": "print(1)\n"}
        files.update(extra)
        r.write(**files)
        return r

    def test_recommends_pre_commit_with_ruff(self):
        p = self.make().plan()
        self.assertEqual(p["manager"], "pre-commit")
        self.assertIn(("ruff-check", "."), self.tools(p))
        self.assertIn("uv", p["package_managers"])
        cfg = p["files"][".pre-commit-config.yaml"]
        self.assertIn("repo: https://github.com/astral-sh/ruff-pre-commit", cfg)
        self.assertIn("id: ruff-check\n        args: [--fix]", cfg)
        self.assertIn("id: check-added-large-files", cfg)
        self.assertIn("id: gitleaks", cfg)

    def test_ruff_format_not_added_unless_used(self):
        cfg = self.make().plan()["files"][".pre-commit-config.yaml"]
        self.assertNotIn("ruff-format", cfg)

    def test_ruff_format_added_when_makefile_uses_it(self):
        cfg = self.make(Makefile="fmt:\n\truff format .\n").plan()["files"][".pre-commit-config.yaml"]
        self.assertIn("id: ruff-format", cfg)

    def test_black_wins_over_ruff_format(self):
        r = self.repo()
        r.write(**{"pyproject.toml": PYPROJECT_RUFF + "\n[tool.ruff.format]\n\n[tool.black]\n", "a.py": ""})
        names = [h["name"] for h in r.plan()["hooks"]]
        self.assertIn("black", names)
        self.assertNotIn("ruff-format", names)

    def test_every_rev_is_pinned(self):
        cfg = self.make(**{"pyproject.toml": PYPROJECT_RUFF + "[tool.black]\n"}).plan()["files"][
            ".pre-commit-config.yaml"]
        repos = [l for l in cfg.splitlines() if "- repo:" in l]
        revs = [l for l in cfg.splitlines() if l.strip().startswith("rev:")]
        self.assertEqual(len(repos), len(revs))
        self.assertTrue(all(l.split(":", 1)[1].strip() in hooks_plan.REVS.values() for l in revs))

    def test_ci_mirrors_with_pre_commit(self):
        r = self.make()
        p = r.plan()
        self.assertEqual(p["ci"], [{"dir": ".", "run": "pre-commit run --all-files --show-diff-on-failure"}])
        self.assertEqual(r.last_code, 0)


class TypeScriptRepo(Base):
    def make(self):
        r = self.repo()
        r.write(**{"package.json": PKG_TS, "package-lock.json": "{}", "eslint.config.js": "export default []\n",
                   ".prettierrc": "{}\n", "src__index.ts": "export const a = 1\n"})
        return r

    def test_recommends_husky_lint_staged(self):
        p = self.make().plan()
        self.assertEqual(p["manager"], "husky")
        self.assertEqual(p["js_package_manager"], "npm")
        self.assertEqual(set(p["files"]), {".husky/pre-commit", ".lintstagedrc.json"})
        self.assertIn("lint-staged", p["files"][".husky/pre-commit"])
        self.assertIn("npm install --save-dev husky lint-staged", p["setup"])

    def test_lint_staged_globs_do_not_overlap(self):
        cfg = json.loads(self.make().plan()["files"][".lintstagedrc.json"])
        js_key = next(k for k in cfg if "tsx" in k)
        self.assertEqual(cfg[js_key], ["eslint --fix", "prettier --write --ignore-unknown"])
        other = [k for k in cfg if k != js_key]
        self.assertEqual(len(other), 1)
        self.assertNotIn("ts", other[0].strip("*.{}").split(","))
        self.assertEqual(cfg[other[0]], "prettier --write --ignore-unknown")

    def test_ci_uses_check_modes(self):
        runs = [c["run"] for c in self.make().plan()["ci"]]
        self.assertIn("npx eslint .", runs)
        self.assertIn("npx prettier --check .", runs)
        self.assertIn("gitleaks git --redact --verbose", runs)

    def test_pnpm_lockfile_picks_pnpm(self):
        r = self.repo()
        r.write(**{"package.json": PKG_TS, "pnpm-lock.yaml": "", "biome.json": "{}", "a.ts": ""})
        p = r.plan()
        self.assertEqual(p["js_package_manager"], "pnpm")
        self.assertIn(("biome", "."), self.tools(p))
        self.assertEqual(p["warnings"], [])
        self.assertIn("pnpm add --save-dev husky lint-staged", p["setup"])

    def test_biome_next_to_prettier_warns_and_eslint_runs_first(self):
        r = self.repo()
        r.write(**{"package.json": json.dumps({"prettier": {}}), "biome.json": "{}", "eslint.config.mjs": "",
                   "a.ts": ""})
        p = r.plan()
        self.assertEqual(len(p["warnings"]), 2)
        cfg = json.loads(p["files"][".lintstagedrc.json"])
        self.assertEqual([c.split()[0] for c in cfg[next(k for k in cfg if "tsx" in k)]],
                         ["eslint", "prettier", "biome"])


class GoAndMonorepo(Base):
    def test_go_project_uses_lefthook(self):
        r = self.repo()
        r.write(**{"go.mod": "module x\n", "go.sum": "", "main.go": "package main\n", ".golangci.yml": ""})
        p = r.plan()
        self.assertEqual(p["manager"], "lefthook")
        cfg = p["files"]["lefthook.yml"]
        self.assertIn("run: gofmt -l -w {staged_files}", cfg)
        self.assertIn("run: golangci-lint run --new-from-rev HEAD --fix", cfg)
        self.assertIn('glob: "*.go"', cfg)
        self.assertIn("stage_fixed: true", cfg)

    def test_polyglot_monorepo_gets_roots(self):
        r = self.repo()
        r.write(**{"pnpm-workspace.yaml": "packages: [web]\n", "package.json": "{}", "pnpm-lock.yaml": "",
                   "web__package.json": PKG_TS, "web__eslint.config.js": "", "web__src__a.tsx": "",
                   "api__pyproject.toml": PYPROJECT_RUFF, "api__app.py": "",
                   "svc__go.mod": "module svc\n", "svc__main.go": "package main\n"})
        p = r.plan()
        self.assertEqual(p["manager"], "lefthook")
        self.assertIn("pnpm-workspace.yaml", p["monorepo_markers"])
        self.assertEqual(self.tools(p), sorted([("large-files", "."), ("gitleaks", "."), ("eslint", "web"),
                                                ("ruff-check", "api"), ("gofmt", "svc")]))
        cfg = p["files"]["lefthook.yml"]
        self.assertIn('root: "web/"', cfg)
        self.assertIn("run: pnpm exec eslint --fix {staged_files}", cfg)
        self.assertIn({"dir": "api", "run": "ruff check ."}, p["ci"])

    def test_rust_workspace_one_rustfmt(self):
        r = self.repo()
        r.write(**{"Cargo.toml": "[workspace]\nmembers = [\"a\"]\n", "a__Cargo.toml": "[package]\n",
                   "a__src__lib.rs": ""})
        p = r.plan()
        self.assertEqual([h for h in p["hooks"] if h["name"] == "rustfmt"][0]["dir"], ".")
        self.assertIn("Cargo.toml [workspace]", p["monorepo_markers"])
        self.assertIn("run: cargo fmt --all -- --check", p["files"]["lefthook.yml"])

    def test_empty_repo_falls_back_to_generic_pre_commit(self):
        r = self.repo()
        r.write(**{"README.md": "hi\n"})
        p = r.plan()
        self.assertEqual(p["manager"], "pre-commit")
        self.assertEqual([h["name"] for h in p["hooks"]], ["large-files", "gitleaks"])


class ExistingHooks(Base):
    def test_husky_exits_1_and_writes_nothing(self):
        r = self.repo()
        r.write(**{"package.json": PKG_TS, ".husky__pre-commit": "npm test\n", "eslint.config.js": ""})
        code, out, _ = r.run("--write")
        self.assertEqual(code, 1)
        self.assertIn("husky: .husky/", out)
        self.assertIn("--write refused", out)
        self.assertFalse(r.exists(".lintstagedrc.json"))
        with open(os.path.join(r.path, ".husky", "pre-commit")) as fh:
            self.assertEqual(fh.read(), "npm test\n")

    def test_pre_commit_config_detected(self):
        r = self.repo()
        r.write(**{".pre-commit-config.yaml": "repos: []\n", "a.py": ""})
        p = r.plan()
        self.assertEqual(r.last_code, 1)
        self.assertIn("pre-commit: .pre-commit-config.yaml", p["existing_hooks"])

    def test_custom_git_hook_and_hooks_path(self):
        r = self.repo()
        r.write(**{".git__hooks__pre-commit": "#!/bin/sh\n", "a.py": ""})
        r.git("config", "core.hooksPath", "tools/hooks")
        p = r.plan()
        self.assertIn("custom script: .git/hooks/pre-commit", p["existing_hooks"])
        self.assertIn("core.hooksPath = tools/hooks", p["existing_hooks"])
        self.assertEqual(r.last_code, 1)

    def test_package_json_keys_detected(self):
        r = self.repo()
        r.write(**{"package.json": json.dumps({"simple-git-hooks": {"pre-commit": "x"}, "lint-staged": {}})})
        p = r.plan()
        self.assertIn("simple-git-hooks: key in package.json", p["existing_hooks"])
        self.assertIn("lint-staged: key in package.json", p["existing_hooks"])

    def test_sample_hooks_are_ignored(self):
        r = self.repo()
        r.write(**{"a.py": ""})
        self.assertEqual(r.plan()["existing_hooks"], [])
        self.assertEqual(r.last_code, 0)


class Write(Base):
    def test_write_creates_pre_commit_config(self):
        r = self.repo()
        r.write(**{"pyproject.toml": PYPROJECT_RUFF, "a.py": ""})
        code, out, _ = r.run("--write")
        self.assertEqual(code, 0, out)
        self.assertIn("Wrote: .pre-commit-config.yaml", out)
        self.assertTrue(r.exists(".pre-commit-config.yaml"))
        code, out, _ = r.run("--write")  # second run sees its own config and refuses
        self.assertEqual(code, 1)
        self.assertIn("--write refused", out)

    def test_write_husky_files(self):
        r = self.repo()
        r.write(**{"package.json": PKG_TS, "package-lock.json": "{}", ".prettierrc": "{}", "a.ts": ""})
        code, out, _ = r.run("--write")
        self.assertEqual(code, 0, out)
        self.assertTrue(r.exists(".husky/pre-commit") and r.exists(".lintstagedrc.json"))

    def test_write_files_never_overwrites(self):
        r = self.repo()
        r.write(**{"lefthook.yml": "keep\n"})
        err, written = hooks_plan.write_files(r.path, {"lefthook.yml": "new\n", "other.yml": "x\n"})
        self.assertIn("refused", err)
        self.assertEqual(written, [])
        self.assertFalse(r.exists("other.yml"))
        with open(os.path.join(r.path, "lefthook.yml")) as fh:
            self.assertEqual(fh.read(), "keep\n")

    def test_without_write_nothing_is_created(self):
        r = self.repo()
        r.write(**{"go.mod": "module x\n", "a.go": ""})
        code, out, _ = r.run()
        self.assertEqual(code, 0)
        self.assertIn("--- lefthook.yml ---", out)
        self.assertFalse(r.exists("lefthook.yml"))


class Misc(Base):
    def test_not_a_git_repo_exits_2(self):
        r = self.repo(init=False)
        code, _, err = r.run()
        self.assertEqual(code, 2)
        self.assertIn("not a git repository", err)

    def test_large_file_snippet_blocks_big_new_file(self):
        r = self.repo()
        r.write(**{"small.txt": "x"})
        with open(os.path.join(r.path, "big.bin"), "wb") as fh:
            fh.write(b"\0" * (hooks_plan.LARGE_KB * 1024 + 1))
        r.git("add", "small.txt")
        ok = subprocess.run(["sh", "-c", hooks_plan.LARGE_FILES_SH], cwd=r.path, env=ENV, capture_output=True)
        self.assertEqual(ok.returncode, 0)
        r.git("add", "big.bin")
        bad = subprocess.run(["sh", "-c", hooks_plan.LARGE_FILES_SH], cwd=r.path, env=ENV, capture_output=True,
                             text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("big.bin", bad.stdout)

    def test_fixture_dirs_are_not_the_stack(self):
        r = self.repo()
        r.write(**{"a.py": "", "tests__fixtures__web__package.json": "{}", "tests__fixtures__web__x.ts": "",
                   "pkg__testdata__go.mod": "module t\n"})
        p = r.plan()
        self.assertEqual(p["languages"], ["python"])
        self.assertEqual(p["monorepo_markers"], [])

    def test_json_has_no_absolute_paths(self):
        r = self.repo()
        r.write(**{"a.py": ""})
        code, out, _ = r.run("--json")
        self.assertNotIn(self.tmp, out)
        self.assertEqual(json.loads(out)["root"], ".")


if __name__ == "__main__":
    unittest.main()
