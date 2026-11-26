#!/usr/bin/env python3
"""Tests for repo_card.py (stdlib unittest, no network, no real image conversion).

Run: python3 -m unittest discover -s skills/repo-card/scripts -p 'test_*.py'
"""
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import repo_card as rc  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}"
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C",
           GIT_AUTHOR_NAME="Ada Tester", GIT_AUTHOR_EMAIL="ada@example.com",
           GIT_COMMITTER_NAME="Ada Tester", GIT_COMMITTER_EMAIL="ada@example.com",
           GIT_AUTHOR_DATE="2026-10-01T12:00:00Z", GIT_COMMITTER_DATE="2026-10-01T12:00:00Z")
ENV.pop("GIT_DIR", None)
ENV.pop("GIT_WORK_TREE", None)

DATA = {"owner": "acme", "name": "widget", "tagline": "Turns YAML into diagrams.", "license": "MIT",
        "tag": "v1.2.0", "stars": 1234, "forks": 56, "topics": ["cli", "yaml"], "url": "https://github.com/acme/widget",
        "languages": [("Go", 70.0, "#00ADD8"), ("Shell", 30.0, "#89e051")]}


def write(root, rel, text=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def png_bytes(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr
            + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xffffffff))


class Args:
    def __init__(self, **kw):
        self.github = self.tagline = self.name = self.owner = None
        self.__dict__.update(kw)


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = os.path.realpath(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def git_repo(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        run = lambda *a: subprocess.run(["git", *a], cwd=repo, env=ENV, check=True, capture_output=True)  # noqa
        run("init", "-q", "-b", "main")
        write(repo, "main.go", "package main\n")
        write(repo, "cmd/tool.go", "package cmd\n")
        write(repo, "web/app.ts", "export {}\n")
        write(repo, "scripts/build.sh", "#!/bin/sh\n")
        write(repo, "Dockerfile", "FROM scratch\n")
        write(repo, "docs/guide.md", "# docs\n")
        write(repo, "vendor/lib/x.go", "package x\n")
        write(repo, "README.md", "# Widget\n\n[![CI](https://x/badge.svg)](https://x)\n\n"
                                 "Turns [YAML](https://yaml.org) into **diagrams**.\n")
        write(repo, "LICENSE", "MIT License\n\nCopyright (c) 2026\n")
        run("add", "-A")
        run("commit", "-q", "-m", "init")
        run("tag", "v0.9.0")
        run("commit", "-q", "--allow-empty", "-m", "next")
        run("tag", "v1.0.0")
        run("remote", "add", "origin", "git@github.com:acme/widget.git")
        return repo


class TestSvg(unittest.TestCase):
    def parse(self, svg):
        return ET.fromstring(svg.encode("utf-8"))

    def test_every_template_and_theme_is_well_formed_1280x640(self):
        for template in ("clean", "terminal", "bold"):
            for theme in ("light", "dark"):
                root = self.parse(rc.render(DATA, template, theme))
                self.assertEqual(root.tag, SVG_NS + "svg")
                self.assertEqual((root.get("width"), root.get("height")), ("1280", "640"), template + theme)
                self.assertEqual(root.get("viewBox"), "0 0 1280 640")

    def test_hostile_description_is_escaped(self):
        evil = dict(DATA, name='x"><script>alert(1)</script>', tagline="<b>&amp; ]]> \x00\x07 'q' \"dq\"",
                    topics=["<img src=x onerror=1>"])
        for template in ("clean", "terminal", "bold"):
            svg = rc.render(evil, template)
            root = self.parse(svg)
            self.assertFalse([e for e in root.iter() if e.tag.endswith("script") or e.tag.endswith("img")])
            text = "".join(root.itertext())
            self.assertIn("<script>", text)
            self.assertIn("<b>&amp;", text)
            self.assertNotIn("\x00", svg)

    def test_logo_is_embedded_as_data_uri(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(png_bytes(10, 10))
        try:
            root = self.parse(rc.render(DATA, "clean", "dark", logo=fh.name))
        finally:
            os.unlink(fh.name)
        images = [e for e in root.iter() if e.tag == SVG_NS + "image"]
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].get("href").startswith("data:image/png;base64,"))

    def test_badges_show_only_real_values(self):
        self.assertEqual(rc.badges(dict(DATA, stars=0, forks=None, topics=[], license="", tag="")), [])
        labels = [label for _, label in rc.badges(dict(DATA, stars=1, forks=2400))]
        self.assertEqual(labels[:2], ["1 star", "2.4k forks"])
        self.assertEqual((rc.short_num(999), rc.short_num(1000), rc.short_num(1250000)), ("999", "1k", "1.2M"))

    def test_bold_text_colour_contrasts_with_background(self):
        light = self.parse(rc.render(dict(DATA, languages=[("JavaScript", 100.0, "#f1e05a")]), "bold", "light"))
        fills = [e.get("fill") for e in light.iter(SVG_NS + "text") if e.get("font-weight") == "700"]
        self.assertEqual(fills, ["#111111"])
        dark = self.parse(rc.render(DATA, "bold", "dark"))
        fills = [e.get("fill") for e in dark.iter(SVG_NS + "text") if e.get("font-weight") == "700"]
        self.assertEqual(fills, ["#ffffff"])


class TestFit(unittest.TestCase):
    def test_short_text_stays_on_one_line_at_full_size(self):
        self.assertEqual(rc.fit("widget", 1120, 88, 52, one_line_min=64), (88, ["widget"]))

    def test_name_shrinks_before_wrapping(self):
        size, lines = rc.fit("a-fairly-long-repository-name", 1120, 88, 52, one_line_min=64, bold=True)
        self.assertEqual(len(lines), 1)
        self.assertLess(size, 88)

    def test_long_tagline_wraps_to_two_lines_with_ellipsis(self):
        text = "Turns every YAML manifest in the repository into a readable diagram " * 6
        size, lines = rc.fit(text, 1120, 40, 30)
        self.assertEqual(size, 30)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("…"))
        for line in lines:
            self.assertLessEqual(rc.text_width(line, size), 1120)

    def test_ellipsis_keeps_separators_from_the_original_text(self):
        name = "the-extremely-long-repository-name-that-keeps-going-and-going-forever-and-ever"
        _, lines = rc.fit(name, 900, 88, 52, one_line_min=64, multi_start=60, bold=True)
        self.assertEqual(len(lines), 2)
        self.assertNotIn(" ", lines[1])
        self.assertTrue(name.startswith(lines[0] + lines[1][:-1]))

    def test_unbreakable_word_is_split(self):
        lines = rc.wrap("x" * 200, 40, 500)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(rc.text_width(line, 40) <= 500 for line in lines))

    def test_mono_width_is_per_character(self):
        self.assertEqual(rc.text_width("iiii", 10, mono=True), rc.text_width("MMMM", 10, mono=True))
        self.assertLess(rc.text_width("iiii", 10), rc.text_width("MMMM", 10))


class TestRepoData(TempDir):
    def test_languages_from_tracked_files(self):
        langs = rc.detect_languages(self.git_repo())
        names = [n for n, _, _ in langs]
        self.assertEqual(names[0], "Go")  # main.go + cmd/tool.go; vendor/ ignored, Markdown not counted
        self.assertEqual(dict((n, p) for n, p, _ in langs)["Go"], 40.0)
        self.assertNotIn("Markdown", names)
        self.assertIn("Dockerfile", names)

    def test_languages_without_git_walk_the_folder(self):
        write(self.tmp, "a.py", "")
        write(self.tmp, "b.py", "")
        write(self.tmp, "node_modules/x/y.js", "")
        write(self.tmp, ".hidden/z.js", "")
        self.assertEqual(rc.detect_languages(self.tmp), [("Python", 100.0, "#3572A5")])

    def test_top_languages_then_other(self):
        for i, ext in enumerate(["py", "go", "rs", "rb", "ts", "java"]):
            write(self.tmp, "f%d.%s" % (i, ext), "")
        langs = rc.detect_languages(self.tmp)
        self.assertEqual(len(langs), 5)
        self.assertEqual(langs[-1][0], "Other")

    def test_license_detection(self):
        cases = [("MIT License\n", "MIT"), ("Apache License\nVersion 2.0, January 2004", "Apache-2.0"),
                 ("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007", "GPL-3.0"),
                 ("Redistribution and use in source and binary forms ... Neither the name", "BSD-3-Clause"),
                 ("All rights reserved.", "License")]
        for text, want in cases:
            with tempfile.TemporaryDirectory() as d:
                write(d, "LICENSE.txt", text)
                self.assertEqual(rc.detect_license(d), want)
        self.assertEqual(rc.detect_license(self.tmp), "")

    def test_readme_first_prose_line(self):
        write(self.tmp, "README.md", "<p align=center><img src=logo.png></p>\n# Title\n\n```sh\nnpm i x\n```\n"
                                     "> quote\n\nA [fast](https://x) `diff` tool for *JSON*.\n")
        self.assertEqual(rc.readme_line(self.tmp), "A fast diff tool for JSON.")

    def test_parse_remote_forms(self):
        self.assertEqual(rc.parse_remote("git@github.com:acme/widget.git"), ("github.com", "acme", "widget"))
        self.assertEqual(rc.parse_remote("https://github.com/acme/widget"), ("github.com", "acme", "widget"))
        self.assertEqual(rc.parse_remote("ssh://git@gitlab.example.com:2222/grp/sub/tool.git"),
                         ("gitlab.example.com", "sub", "tool"))

    def test_gather_from_local_repo(self):
        data, warnings = rc.gather(self.git_repo(), Args())
        self.assertEqual((data["owner"], data["name"]), ("acme", "widget"))
        self.assertEqual(data["tagline"], "Turns YAML into diagrams.")
        self.assertEqual((data["license"], data["tag"]), ("MIT", "v1.0.0"))
        self.assertEqual(data["url"], "https://github.com/acme/widget")
        self.assertIsNone(data["stars"])
        self.assertEqual(warnings, [])


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestGitHub(TempDir):
    def test_fetch_uses_api_fields(self):
        body = {"stargazers_count": 4200, "forks_count": 31, "topics": ["cli"], "description": "From GitHub.",
                "license": {"spdx_id": "NOASSERTION"}, "html_url": "https://github.com/acme/widget"}
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return FakeResp(json.dumps(body).encode())
        data, warnings = rc.gather(self.git_repo(), Args(github="acme/widget"), opener)
        self.assertEqual(seen, ["https://api.github.com/repos/acme/widget"])
        self.assertEqual((data["stars"], data["forks"], data["topics"]), (4200, 31, ["cli"]))
        self.assertEqual(data["tagline"], "From GitHub.")
        self.assertEqual(data["license"], "MIT")  # NOASSERTION falls back to the local LICENSE
        self.assertEqual(warnings, [])

    def test_tagline_flag_beats_github_description(self):
        opener = lambda req, timeout: FakeResp(b'{"description": "From GitHub."}')  # noqa: E731
        data, _ = rc.gather(self.git_repo(), Args(github="acme/widget", tagline="Mine."), opener)
        self.assertEqual(data["tagline"], "Mine.")

    def test_offline_falls_back_to_local_data(self):
        def opener(req, timeout):
            raise urllib.error.URLError("no network")
        data, warnings = rc.gather(self.git_repo(), Args(github="acme/widget"), opener)
        self.assertIsNone(data["stars"])
        self.assertEqual(data["tagline"], "Turns YAML into diagrams.")
        self.assertIn("unreachable", warnings[0])
        out = os.path.join(self.tmp, "card.svg")
        with mock.patch("sys.stdout", io.StringIO()):
            code = rc.main(["-C", os.path.join(self.tmp, "repo"), "--github", "acme/widget", "--out", out,
                            "--no-png", "--json"], opener=opener)
        self.assertEqual(code, 0)
        ET.parse(out)

    def test_not_found_is_a_warning(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        _, warnings = rc.gather(self.git_repo(), Args(github="acme/gone"), opener)
        self.assertIn("not found or private", warnings[0])


class TestConverters(TempDir):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.patches = [mock.patch.object(rc, "CHROME_APPS", ()),
                        mock.patch.object(rc, "cairosvg_available", lambda: False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def tool(self, name, body="pass"):
        path = os.path.join(self.bin, name)
        with open(path, "w") as fh:
            fh.write("#!%s\nimport sys\n%s\n" % (sys.executable, body))
        os.chmod(path, 0o755)

    def test_nothing_found(self):
        self.assertEqual(rc.find_converter(self.bin), (None, None))

    def test_detection_order(self):
        self.tool("chromium")
        self.assertEqual(rc.find_converter(self.bin)[0], "chrome")
        self.tool("convert", "print('Microsoft convert.exe')")  # not ImageMagick: skipped
        self.assertEqual(rc.find_converter(self.bin)[0], "chrome")
        self.tool("magick")
        self.assertEqual(rc.find_converter(self.bin)[0], "magick")
        with mock.patch.object(rc, "cairosvg_available", lambda: True):
            self.assertEqual(rc.find_converter(self.bin)[0], "cairosvg")
        self.tool("rsvg-convert")
        self.assertEqual(rc.find_converter(self.bin), ("rsvg-convert", os.path.join(self.bin, "rsvg-convert")))

    def test_no_converter_prints_manual_command(self):
        svg = os.path.join(self.tmp, "card.svg")
        name, notes = rc.to_png(svg, os.path.join(self.tmp, "card.png"), path=self.bin)
        self.assertIsNone(name)
        self.assertIn("rsvg-convert --width=1280 --height=640 --output=", notes[0])
        self.assertIn("PNG, JPG or GIF", notes[0])

    def test_fake_rsvg_output_is_checked(self):
        writer = ("out = [a for a in sys.argv if a.startswith('--output=')][0][9:]\n"
                  "open(out, 'wb').write(%r)" % png_bytes(1280, 640))
        self.tool("rsvg-convert", writer)
        svg, png = os.path.join(self.tmp, "card.svg"), os.path.join(self.tmp, "card.png")
        self.assertEqual(rc.to_png(svg, png, path=self.bin), ("rsvg-convert", []))
        self.tool("rsvg-convert", writer.replace("%r" % png_bytes(1280, 640), "%r" % png_bytes(640, 320)))
        _, notes = rc.to_png(svg, png, path=self.bin)
        self.assertIn("expected 1280x640", notes[0])

    def test_failing_converter_is_a_warning(self):
        self.tool("rsvg-convert", "sys.stderr.write('boom'); sys.exit(1)")
        name, notes = rc.to_png("card.svg", os.path.join(self.tmp, "card.png"), path=self.bin)
        self.assertEqual(name, "rsvg-convert")
        self.assertIn("boom", notes[0])


class TestCli(TempDir):
    def test_og_html_tags(self):
        html = rc.og_html(dict(DATA, tagline='Says "hi" <now>'), "https://acme.dev/widget",
                          "https://acme.dev/widget/card.png")
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', html)
        self.assertIn('<meta property="og:image:width" content="1280">', html)
        self.assertIn('<meta property="og:image:height" content="640">', html)
        self.assertIn("Says &quot;hi&quot; &lt;now&gt;", html)

    def test_bad_input_exits_2(self):
        repo = self.git_repo()
        out = os.path.join(self.tmp, "c.svg")
        for extra in (["--github", "not a slug"], ["--logo", os.path.join(self.tmp, "missing.png")],
                      ["--og-html", "acme.dev"], ["--out", os.path.join(self.tmp, "nope", "c.svg")]):
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(rc.main(["-C", repo, "--out", out, "--no-png"] + extra), 2, extra)
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(rc.main(["-C", os.path.join(self.tmp, "missing"), "--out", out, "--no-png"]), 2)

    def test_cli_end_to_end_json(self):
        repo = self.git_repo()
        out = os.path.join(self.tmp, "card.svg")
        r = subprocess.run([sys.executable, os.path.join(HERE, "repo_card.py"), "-C", repo, "--out", out,
                            "--no-png", "--template", "terminal", "--theme", "dark", "--json",
                            "--og-html", "https://acme.dev/"], capture_output=True, text=True, env=ENV, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        result = json.loads(r.stdout)
        self.assertEqual(result["data"]["name"], "widget")
        self.assertIsNone(result["png"])
        self.assertIn('content="https://acme.dev/card.png"', result["og_html"])
        self.assertEqual(ET.parse(out).getroot().get("width"), "1280")


if __name__ == "__main__":
    unittest.main()
