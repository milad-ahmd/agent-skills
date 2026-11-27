#!/usr/bin/env python3
"""Tests for repo_diet.py (stdlib unittest, no network, never rewrites history).

Run: python3 -m unittest discover -s skills/repo-diet/scripts -p 'test_*.py'
Each test builds a throwaway git repo in a temp dir with fixed author, committer and dates.
Big files use os.urandom so they do not compress and their on-disk size stays close to their size.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import repo_diet as rd  # noqa: E402

SCRIPT = os.path.join(HERE, "repo_diet.py")
MB = 1000 * 1000
ENV = dict(os.environ, GIT_AUTHOR_NAME="Ada Example", GIT_AUTHOR_EMAIL="ada@example.com",
           GIT_COMMITTER_NAME="Ada Example", GIT_COMMITTER_EMAIL="ada@example.com",
           GIT_AUTHOR_DATE="2024-03-01T12:00:00Z", GIT_COMMITTER_DATE="2024-03-01T12:00:00Z",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
os.environ.update({k: ENV[k] for k in ENV if k.startswith("GIT_")})


class Repo(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._td.name)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")

    def tearDown(self):
        self._td.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(["git"] + list(args), cwd=cwd or self.repo, env=ENV, check=True,
                              capture_output=True, text=True).stdout

    def write(self, path, data=b"x", size=None):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if size is not None:
            data = os.urandom(size)
        with open(full, "wb") as fh:
            fh.write(data.encode() if isinstance(data, str) else data)

    def commit(self, msg="change", *paths):
        self.git("add", "-A", *paths)
        self.git("commit", "-q", "-m", msg)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, "-C", self.repo] + list(args), capture_output=True,
                              text=True, env=ENV, timeout=60)

    def diet(self, **kw):
        return rd.diet(self.repo, **kw)

    def lean(self):
        self.write("README.md", "# demo\n")
        self.write(".gitignore", "node_modules/\n")
        self.write("src/app.py", "print('hi')\n")
        self.commit("init")


class Basics(Repo):
    def test_not_a_repo_exits_2(self):
        p = subprocess.run([sys.executable, SCRIPT, "-C", self.root], capture_output=True, text=True, env=ENV)
        self.assertEqual(p.returncode, 2)
        self.assertIn("not a git repository", p.stderr)

    def test_missing_dir_exits_2(self):
        self.assertEqual(self.run_cli_path(os.path.join(self.root, "nope")).returncode, 2)

    def run_cli_path(self, path):
        return subprocess.run([sys.executable, SCRIPT, "-C", path], capture_output=True, text=True, env=ENV)

    def test_lean_repo_exits_0(self):
        self.lean()
        p = self.run_cli()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Verdict: lean", p.stdout)
        self.assertIn("No junk committed", p.stdout)

    def test_empty_repo_without_commits(self):
        d = self.diet()
        self.assertEqual(d["checkout_files"], 0)
        self.assertFalse(d["findings"])
        self.assertTrue(any("no commits" in n for n in d["notes"]))

    def test_checkout_size_matches_head(self):
        self.write("a.txt", "12345")
        self.write("b/c.txt", "123")
        self.commit()
        d = self.diet()
        self.assertEqual((d["checkout_files"], d["checkout_bytes"]), (2, 8))


class History(Repo):
    def setUp(self):
        super().setUp()
        self.lean()
        self.write("assets/video.mp4", size=int(1.5 * MB))
        self.commit("add video")
        self.added = self.git("rev-parse", "HEAD").strip()
        self.write("data.bin", size=300 * 1000)
        self.commit("v1")
        self.write("data.bin", size=200 * 1000)
        self.commit("v2")
        self.git("rm", "-q", "assets/video.mp4")
        self.commit("drop video")

    def test_deleted_blob_is_biggest_and_flagged(self):
        b = self.diet()["biggest_blobs"][0]
        self.assertEqual(b["path"], "assets/video.mp4")
        self.assertEqual(b["status"], "deleted")
        self.assertGreater(b["disk"], 1.4 * MB)
        self.assertEqual(b["bytes"], int(1.5 * MB))

    def test_commit_and_date_that_added_it(self):
        b = self.diet()["biggest_blobs"][0]
        self.assertEqual(b["commit"], self.added[:10])
        self.assertEqual(b["date"], "2024-03-01")

    def test_old_version_and_in_head(self):
        by = {(b["path"], b["bytes"]): b["status"] for b in self.diet()["biggest_blobs"]}
        self.assertEqual(by[("data.bin", 300 * 1000)], "old version")
        self.assertEqual(by[("data.bin", 200 * 1000)], "in HEAD")

    def test_deleted_share_and_headline(self):
        d = self.diet()
        self.assertTrue(d["history_bloat"])
        self.assertGreater(d["deleted_share"], 0.6)
        self.assertLess(d["deleted_share"], 0.9)
        self.assertIn("is files you already deleted", d["headline"])
        self.assertIn("floppy disks", d["headline"])

    def test_heaviest_paths_sum_versions(self):
        paths = {p["path"]: p for p in self.diet()["heaviest_paths"]}
        self.assertEqual(paths["data.bin"]["versions"], 2)
        self.assertEqual(paths["assets/video.mp4"]["status"], "deleted")

    def test_filter_repo_suggestions_only_for_deleted_paths(self):
        cmds = self.diet()["filter_repo_suggestions"]
        self.assertEqual(cmds[0], "git filter-repo --invert-paths --path assets/video.mp4")
        # largest file in HEAD is 200 KB, so a 1M cut keeps HEAD whole and strips the 1.5 MB video
        self.assertIn("git filter-repo --strip-blobs-bigger-than 1M", cmds)

    def test_cli_exit_1_and_never_rewrites(self):
        before = self.git("rev-parse", "HEAD")
        p = self.run_cli()
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("NOT run, suggestions only", p.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_top_limits_rows(self):
        d = self.diet(top=2)
        self.assertEqual(len(d["biggest_blobs"]), 2)
        self.assertEqual(len(d["heaviest_paths"]), 2)

    def test_authors_hidden_unless_asked(self):
        hidden = self.run_cli("--json").stdout
        self.assertNotIn("Ada Example", hidden)
        self.assertNotIn("Ada Example", self.run_cli().stdout)
        shown = json.loads(self.run_cli("--json", "--show-authors").stdout)
        self.assertEqual(shown["biggest_blobs"][0]["author"], "Ada Example")

    def test_bare_clone_works_and_skips_worktree_checks(self):
        bare = os.path.join(self.root, "bare.git")
        self.git("clone", "-q", "--bare", "--no-local", self.repo, bare, cwd=self.root)
        d = rd.diet(bare)
        self.assertEqual(d["biggest_blobs"][0]["status"], "deleted")
        self.assertTrue(any("bare" in n for n in d["notes"]))


class Junk(Repo):
    def setUp(self):
        super().setUp()
        self.write("README.md", "hi\n")
        self.write(".gitignore", "*.log\n")
        self.write(".DS_Store", "junk")
        self.write("web/node_modules/left-pad/index.js", "module.exports=1\n")
        self.write("web/node_modules/left-pad/package.json", "{}")
        self.write("pkg/__pycache__/mod.cpython-311.pyc", "c")
        self.write("dist/bundle.js", "b")
        self.write("server.log", "log line\n")
        self.write("backup.zip", "PK")
        self.write("tests/fixtures/node_modules/x/index.js", "fixture")
        self.git("add", "-A")
        self.git("add", "-f", "server.log")
        self.git("commit", "-q", "-m", "junk")

    def junk(self):
        return {g["category"]: g for g in self.diet()["junk"]}

    def test_categories_found(self):
        self.assertEqual(set(self.junk()), {"OS junk", "dependencies", "python cache", "build output", "logs",
                                            "archives"})

    def test_dir_root_and_gitignore_line(self):
        g = self.junk()["dependencies"]
        self.assertEqual(g["files"], 2)
        self.assertEqual(g["roots"], ["web/node_modules"])
        self.assertEqual(g["gitignore"], ["node_modules/"])

    def test_gitignore_check(self):
        j = self.junk()
        self.assertEqual(j["logs"]["ignore_rule"], "all")
        self.assertEqual(j["OS junk"]["ignore_rule"], "none")

    def test_fixtures_are_not_junk(self):
        roots = [r for g in self.diet()["junk"] for r in g["roots"]]
        self.assertFalse(any("fixtures" in r for r in roots))

    def test_findings_exit_1(self):
        p = self.run_cli()
        self.assertEqual(p.returncode, 1)
        self.assertIn("NOT in .gitignore", p.stdout)

    def test_vendored_without_lockfile(self):
        self.write("vendor/lib/a.php", "<?php")
        self.commit("vendor")
        self.assertIn("vendored, no lockfile", self.junk())
        self.write("composer.lock", "{}")
        self.commit("lock")
        self.assertNotIn("vendored, no lockfile", self.junk())


class Lfs(Repo):
    def test_large_binary_is_candidate(self):
        self.write("art/cover.psd", size=int(1.2 * MB))
        self.write("notes.txt", size=int(1.2 * MB))
        self.commit()
        d = self.diet()
        self.assertEqual([c["path"] for c in d["lfs_candidates"]], ["art/cover.psd"])
        self.assertTrue(d["findings"])

    def test_lfs_rule_clears_candidate(self):
        self.write(".gitattributes", "*.psd filter=lfs diff=lfs merge=lfs -text\n")
        self.write("art/cover.psd", size=int(1.2 * MB))
        self.commit()
        self.assertEqual(self.diet()["lfs_candidates"], [])


class Output(Repo):
    def test_svg_is_valid_xml(self):
        self.lean()
        out = os.path.join(self.root, "card.svg")
        p = self.run_cli("--svg", out)
        self.assertEqual(p.returncode, 0, p.stderr)
        root = ET.parse(out).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("REPO DIET", ET.tostring(root, encoding="unicode"))
        self.assertIn("milad-ahmd/agent-skills", ET.tostring(root, encoding="unicode"))
        self.run_cli("--svg", out, "--no-credit")
        with open(out, encoding="utf-8") as fh:
            self.assertNotIn("milad-ahmd/agent-skills", fh.read())

    def test_svg_unwritable_exits_3(self):
        self.lean()
        p = self.run_cli("--svg", os.path.join(self.root, "no", "such", "dir", "card.svg"))
        self.assertEqual(p.returncode, 3)

    def test_paths_with_spaces_are_quoted(self):
        cmd = rd.filter_repo_commands(["old assets/big file.mov"], [], 0)[0]
        self.assertEqual(cmd, "git filter-repo --invert-paths --path 'old assets/big file.mov'")

    def test_strip_cut_stays_above_head(self):
        self.assertEqual(rd.filter_repo_commands([], [9 * MB], int(2.5 * MB)),
                         ["git filter-repo --strip-blobs-bigger-than 3M"])
        self.assertEqual(rd.filter_repo_commands([], [2 * MB], int(2.5 * MB)), [])

    def test_headline_math(self):
        self.assertEqual(rd.headline(1474560 * 10, 0.5),
                         "14.7 MB of history; 50% is files you already deleted. That is 10 floppy disks, "
                         "or 1.2 seconds to clone at 100 Mbit/s.")


if __name__ == "__main__":
    unittest.main()
