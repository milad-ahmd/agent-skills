#!/usr/bin/env python3
"""Tests for readme_audit.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/readme-glow-up/scripts -p 'test_*.py'
Each test writes a throwaway repo in a temp dir. The heading cases in test_slug_matches_github were rendered by
GitHub's own Markdown renderer (POST /markdown, mode "markdown") and copied here with the ids it produced.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import readme_audit as ra  # noqa: E402

SCRIPT = os.path.join(HERE, "readme_audit.py")
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", TZ="UTC", LC_ALL="C",
               GIT_AUTHOR_NAME="Ada Example", GIT_AUTHOR_EMAIL="ada@example.com",
               GIT_COMMITTER_NAME="Ada Example", GIT_COMMITTER_EMAIL="ada@example.com",
               GIT_AUTHOR_DATE="2026-01-01T12:00:00Z", GIT_COMMITTER_DATE="2026-01-01T12:00:00Z")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    GIT_ENV.pop(var, None)
MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, files):
        for rel, text in files.items():
            full = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(text)

    def audit(self, readme, **files):
        files = dict({"README.md": readme}, **{k.replace("__", "/"): v for k, v in files.items()})
        self.write(files)
        rel, others = ra.find_readme(self.root)
        return ra.audit(self.root, rel, 30, others)

    def kinds(self, rep, severity=None):
        return [f["kind"] for f in rep["findings"] if severity is None or f["severity"] == severity]

    def messages(self, rep, kind):
        return [f["message"] for f in rep["findings"] if f["kind"] == kind]

    def cli(self, *args):
        r = subprocess.run([sys.executable, SCRIPT, "-C", self.root, *args], capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr


class SlugTests(unittest.TestCase):
    def test_slug_matches_github(self):
        cases = {
            "⚙️ Configuration": "️-configuration",        # the variation selector is a mark: kept
            "🛠️ Ops & Growth": "️-ops--growth",
            "🚀 Quickstart": "-quickstart",
            "Über Straße ÀB": "über-straße-àb",
            "Ünïcode ΣΑΣ": "ünïcode-σασ",
            "Done :rocket: launch": "done-rocket-launch",
            "`code_span` and _italic_ and snake_case_name": "code_span-and-italic-and-snake_case_name",
            "[Link text](https://example.com) more": "link-text-more",
            "C++ & C#": "c--c",
            "100% 1/2 ²³ ½": "100-12--",
            "Emoji 👩\u200d💻 zwj": "emoji-\u200d-zwj",
            '<img src="x.png"> Logo': "-logo",
            "Q&amp;A &lt;tag&gt;": "qa-tag",
            "x_y_ _z_": "x_y_-z",
            "\\_escaped\\_": "_escaped_",
            "日本語 タイトル": "日本語-タイトル",
        }
        for raw, want in cases.items():
            self.assertEqual(ra.slug(ra.rendered_text(raw)), want, raw)

    def test_duplicates_follow_github(self):
        doc = ra.Doc("## A\n\n## A\n\n## A-1\n\n## A\n")
        self.assertEqual([h["anchor"] for h in doc.headings], ["a", "a-1", "a-1-1", "a-2"])

    def test_setext_html_and_fenced_headings(self):
        doc = ra.Doc("Big Title\n=========\n\n<h2 align=\"center\">HTML\n heading</h2>\n\n```bash\n# not a heading\n```\n"
                     "\n- item\n---\n\n<a name=\"named\"></a>\n")
        self.assertEqual([(h["level"], h["anchor"]) for h in doc.headings], [(1, "big-title"), (2, "html-heading")])
        self.assertIn("named", doc.anchors)


class ReadmeDiscoveryTests(Base):
    def test_github_directory_order_and_markdown_first(self):
        self.write({"docs/README.md": "# d\n", "README.rst": "r\n", "README.md": "# root\n"})
        self.assertEqual(ra.find_readme(self.root), ("README.md", ["README.rst", "docs/README.md"]))
        self.write({".github/README.md": "# gh\n"})
        self.assertEqual(ra.find_readme(self.root)[0], ".github/README.md")

    def test_exit_2_without_markdown_readme(self):
        self.assertEqual(self.cli()[0], 2)
        self.write({"README.rst": "Title\n=====\n"})
        code, _out, err = self.cli()
        self.assertEqual(code, 2)
        self.assertIn("not Markdown", err)


class LinkTests(Base):
    def test_relative_links_and_images(self):
        rep = self.audit("# T\n\n[ok](docs/guide.md) [root](/docs/guide.md?plain=1#x) [gone](docs/missing.md)\n"
                         "![shot](assets/My%20Shot.png) ![nope](assets/nope.png) [dir](docs/)\n",
                         docs__guide="g", **{"docs__guide.md": "# Guide\n\n## X\n", "assets__My Shot.png": "png"})
        broken = [f["target"] for f in rep["findings"] if f["kind"].startswith("broken-")]
        self.assertEqual(broken, ["docs/missing.md", "assets/nope.png"])

    def test_case_mismatch_is_broken_on_github(self):
        rep = self.audit("# T\n\n[guide](Docs/Guide.md)\n", **{"docs__guide.md": "x"})
        self.assertIn("different letter case", self.messages(rep, "broken-link")[0])

    def test_outside_repo_and_external_are_not_checked(self):
        rep = self.audit("# T\n\n[issues](../../issues) <https://example.com/a> see https://example.org/b.\n"
                         "[ext](https://example.net/x)\n")
        self.assertEqual(self.kinds(rep, "error"), [])
        self.assertEqual(rep["not_checked"]["outside_repo"], ["../../issues"])
        self.assertEqual(sorted(rep["not_checked"]["external"]),
                         ["https://example.com/a", "https://example.net/x", "https://example.org/b"])

    def test_untracked_target_in_git_repo(self):
        self.write({"README.md": "# T\n\n![d](demo.gif) ![e](tracked.png)\n", "tracked.png": "p", "demo.gif": "g"})
        for cmd in (["init", "-q"], ["add", "README.md", "tracked.png"], ["commit", "-q", "-m", "init"]):
            subprocess.run(["git", *cmd], cwd=self.root, env=GIT_ENV, check=True, capture_output=True)
        rep = ra.audit(self.root, "README.md", 30, [])
        self.assertEqual([f["target"] for f in rep["findings"] if f["kind"] == "untracked-target"], ["demo.gif"])

    def test_reference_links(self):
        rep = self.audit("# T\n\n[Guide][g] and [Lost][nowhere] and [g]\n\n[g]: ./missing.md\n")
        self.assertEqual(len(self.messages(rep, "broken-link")), 2)
        self.assertEqual(len(self.messages(rep, "undefined-reference")), 1)

    def test_footnotes_and_readme_anchor_are_not_links(self):
        rep = self.audit("# T\n\nFast[^1] and see [^note]. [Back](#readme)\n\n[^1]: Some note here.\n[^note]: Other.\n")
        self.assertEqual(self.kinds(rep, "error"), [])
        self.assertNotIn("undefined-reference", self.kinds(rep))
        self.assertEqual(rep["not_checked"]["github_page"], ["#readme"])


class AnchorTests(Base):
    def test_emoji_anchors(self):
        rep = self.audit("# 🧰 Kit\n\n[go](#-quickstart) [cfg](#️-configuration) [enc](#%EF%B8%8F-configuration)"
                         " [top](#top) [id](#custom) [bad](#-configuration) [case](#Quickstart-Now)\n\n"
                         "## 🚀 Quickstart\n\n## ⚙️ Configuration\n\n## Quickstart now\n\n<p id=\"custom\">x</p>\n")
        self.assertEqual(len(self.messages(rep, "broken-anchor")), 1)
        self.assertIn("U+FE0F", self.messages(rep, "broken-anchor")[0])
        self.assertEqual(len(self.messages(rep, "anchor-case")), 1)

    def test_anchor_into_other_markdown_file(self):
        rep = self.audit("# T\n\n[a](docs/guide.md#setup) [b](docs/guide.md#nope)\n",
                         **{"docs__guide.md": "# Guide\n\n## Setup\n"})
        msgs = self.messages(rep, "broken-anchor")
        self.assertEqual(len(msgs), 1)
        self.assertIn("#nope", msgs[0])


class ContentTests(Base):
    def test_alt_text_fences_and_heading_skips(self):
        rep = self.audit("# T\n\n![](a.png) ![ok](a.png)\n<img src=\"a.png\">\n<img src=\"a.png\" alt=\"\">\n\n"
                         "```\nplain\n```\n\n```python\nx = 1\n```\n\n#### Deep\n", **{"a.png": "p"})
        self.assertEqual(self.kinds(rep).count("missing-alt"), 2)
        self.assertEqual(self.kinds(rep).count("empty-alt"), 1)
        self.assertEqual(self.kinds(rep).count("fence-no-language"), 1)
        self.assertIn("h1 to h4", self.messages(rep, "heading-skip")[0])

    def test_first_screen(self):
        rep = self.audit("<div align=\"center\">\n\n# 🚀 Rocket\n\n**Ship static sites from one command, no config.**\n\n"
                         "[Install](#install) · [Usage](#usage)\n\n</div>\n\n## Install\n\n## Usage\n")
        fs = rep["first_screen"]
        self.assertEqual(fs["title"]["text"], "🚀 Rocket")
        self.assertEqual(fs["description"]["line"], 5)
        self.assertEqual(fs["install_or_usage"]["how"], "nav link")

    def test_missing_first_screen_facts(self):
        rep = self.audit("Some words here.\n\n" + "filler\n" * 40 + "## Notes\n")
        for kind in ("no-title", "no-description", "install-not-on-first-screen"):
            self.assertIn(kind, self.kinds(rep, "warning"))

    def test_sections_and_suggestions(self):
        body = "".join("## Part %d\n\n![b](https://img.shields.io/badge/x-%d-blue)\n\n" % (i, i) for i in range(13))
        rep = self.audit("# T\n\n" + body + "## Getting started\n\n## Contributing\n")
        self.assertEqual(sorted(rep["sections"]["present"]), ["contributing", "install", "usage"])
        self.assertIn("toc-suggested", self.kinds(rep))
        self.assertIn("many-badges", self.kinds(rep))

    def test_truncation_limit(self):
        rep = self.audit("# T\n\n" + ("word " * 20 + "\n") * 6000)
        self.assertIn("readme-truncated", self.kinds(rep, "error"))


class LicenseTests(Base):
    def test_license_claims(self):
        rep = self.audit("# T\n\n## License\n\nApache License 2.0\n", LICENSE=MIT)
        self.assertIn("license-mismatch", self.kinds(rep, "warning"))   # medium confidence: never an error
        rep = self.audit("# T\n\n![License: MIT](https://img.shields.io/badge/license-MIT-green)\n", LICENSE=MIT)
        self.assertEqual(rep["license"]["readme_families"], ["MIT"])
        self.assertNotIn("license-mismatch", self.kinds(rep))

    def test_license_file_missing(self):
        rep = self.audit("# T\n\n## License\n\nMIT\n")
        self.assertIn("license-file-missing", self.kinds(rep, "error"))


class CommandTests(Base):
    def cmds(self, block, **files):
        rep = self.audit("# T\n\nA tool that does a thing well.\n\n## Installation\n\n```bash\n%s\n```\n" % block, **files)
        return self.messages(rep, "command-mismatch")

    def test_node(self):
        self.assertIn("no package.json", self.cmds("npm install")[0])
        pkg = json.dumps({"name": "rocket", "scripts": {"build": "tsc"}, "devDependencies": {"typescript": "5"}})
        self.assertIn("npm-shrinkwrap", self.cmds("npm ci", **{"package.json": pkg})[0])
        self.assertIn('"serve" script', self.cmds("npm run build && npm run serve", **{"package.json": pkg})[0])
        self.assertEqual(self.cmds("npm install rocket typescript", **{"package.json": pkg}), [])
        self.assertIn("names this package `rocket`", self.cmds("npm i rockett", **{"package.json": pkg})[0])

    def test_python(self):
        proj = "[project]\nname = \"Rocket_Ship\"\ndependencies = [\"requests>=2\"]\n"
        self.assertEqual(self.cmds("pip install rocket-ship requests\npip install -e .", **{"pyproject.toml": proj}), [])
        os.remove(os.path.join(self.root, "pyproject.toml"))
        msgs = self.cmds("pip install -r requirements-dev.txt\npython -m pip install -e .\npip install rocketship",
                         **{"setup.cfg": "[metadata]\nname = rocket-ship\n"})
        self.assertEqual(len(msgs), 3)
        self.assertIn("pip cannot install it", msgs[1])

    def test_make_compose_docker_go_cargo(self):
        msgs = self.cmds("make build\nmake lint\ndocker compose up -d\ndocker build -t x .\ngo build ./...\ncargo build",
                         Makefile="build:\n\tgo build\n")
        self.assertEqual(len(msgs), 5)
        self.assertIn("no target `lint`", msgs[0])

    def test_cd_rules(self):
        block = ("git clone --depth 1 https://example.com/acme/rocket.git\ncd rocket\nmake\n"
                 "mkdir build && cd build && make\ncd /usr/ports/rocket && make install\n")
        self.assertEqual(len(self.cmds(block)), 1)   # only the make right after cloning, which runs in the repo root
        self.assertEqual(self.cmds("cd web && npm install", **{"web/package.json": "{}"}), [])

    def test_directory_carries_across_blocks_and_starts_at_root(self):
        rep = self.audit("# T\n\nA tool that does a thing well.\n\n## Quick start\n\n```bash\nnpx create-thing my-app\n"
                         "cd my-app\n```\n\n```bash\nnpm run dev\n```\n")
        self.assertEqual(self.messages(rep, "command-mismatch"), [])
        self.write({".github/README.md": "# T\n\nA tool that does a thing well.\n\n## Install\n\n```bash\nnpm install\n```\n",
                    "package.json": "{}"})
        rep = ra.audit(self.root, ".github/README.md", 30, [])
        self.assertEqual(self.messages(rep, "command-mismatch"), [])


class CliTests(Base):
    def test_json_and_exit_codes(self):
        self.write({"README.md": "# T\n\nA tool that does a thing well.\n\n## Install\n\n```bash\nnpm install\n```\n",
                    "package.json": "{}"})
        code, out, _ = self.cli("--json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(rep["readme"], "README.md")
        self.assertIn("not_checked", rep)
        self.write({"README.md": "# T\n\n[x](gone.md)\n"})
        code, out, _ = self.cli()
        self.assertEqual(code, 1)
        self.assertIn("Not checked (no network)", out)


if __name__ == "__main__":
    unittest.main()
