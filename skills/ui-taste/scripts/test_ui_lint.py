#!/usr/bin/env python3
"""Tests for ui_lint.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/ui-taste/scripts -p 'test_*.py'
Each test writes a small project into a temp dir, or uses the messy/clean projects in fixtures/.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ui_lint  # noqa: E402
from ui_lint import (Lint, contrast_ratio, delta_e, find_colours, hex_key, lint_paths, merge_near,  # noqa: E402
                     parse_colour, to_px)

SCRIPT = os.path.join(HERE, "ui_lint.py")
MESSY = os.path.join(HERE, "fixtures", "messy")
CLEAN = os.path.join(HERE, "fixtures", "clean")


TEMP_DIRS = []


def tearDownModule():
    for d in TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


def project(files):
    """Write {relpath: text} into a fresh temp dir (removed after the run) and return its path."""
    d = tempfile.mkdtemp(prefix="ui-lint-test-")
    TEMP_DIRS.append(d)
    for rel, text in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    return d


def run(files, **kw):
    lint = lint_paths([project(files)], **kw)
    score, cats = lint.report()
    return lint, score, cats


def main_exit(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ui_lint.main(list(args))
    return code, out.getvalue(), err.getvalue()


class ColourTests(unittest.TestCase):
    def test_hex_forms_normalise_to_the_same_key(self):
        self.assertEqual(hex_key(parse_colour("#FFF")), "#ffffff")
        self.assertEqual(hex_key(parse_colour("#3B82F6")), "#3b82f6")
        self.assertEqual(hex_key(parse_colour("#3b82f6ff")), "#3b82f6")
        self.assertEqual(hex_key(parse_colour("#0008")), "#00000088")

    def test_rgb_comma_space_and_percent_syntax(self):
        a = parse_colour("rgb(59, 130, 246)")
        self.assertEqual(a, parse_colour("rgb(59 130 246)"))
        self.assertEqual(hex_key(a), "#3b82f6")
        self.assertEqual(parse_colour("rgba(0,0,0,.5)"), parse_colour("rgb(0 0 0 / 50%)"))
        self.assertEqual(hex_key(parse_colour("rgb(100% 0% 0%)")), "#ff0000")

    def test_hsl_and_named(self):
        self.assertEqual(hex_key(parse_colour("hsl(120, 100%, 25%)")), "#008000")
        self.assertEqual(hex_key(parse_colour("hsl(0deg 100% 50%)")), "#ff0000")
        self.assertEqual(hex_key(parse_colour("hsla(240, 100%, 50%, 1)")), "#0000ff")
        self.assertEqual(hex_key(parse_colour("white")), "#ffffff")
        self.assertEqual(parse_colour("grey"), parse_colour("gray"))
        self.assertIsNone(parse_colour("rgb(var(--r), 0, 0)"))

    def test_oklch_and_oklab(self):
        self.assertEqual(hex_key(parse_colour("oklab(0.627955 0.224863 0.125846)")), "#ff0000")
        self.assertEqual(hex_key(parse_colour("oklch(62.8% 0.2577 29.23)")), "#ff0000")
        self.assertEqual(hex_key(parse_colour("oklch(1 0 0)")), "#ffffff")
        self.assertEqual(hex_key(parse_colour("oklch(0 0 0 / 50%)")), "#00000080")

    def test_find_colours_skips_urls_strings_and_variable_names(self):
        found = [hex_key(c) for c, _ in find_colours('url("#fff") var(--white) "black" 1px solid #e5e5e5')]
        self.assertEqual(found, ["#e5e5e5"])

    def test_near_duplicates_merge_and_distinct_colours_do_not(self):
        groups = merge_near({"#333333": 5, "#343434": 1, "#323232": 2, "#3b82f6": 4}, 3.0)
        self.assertIn(["#333333", "#323232", "#343434"], groups)
        self.assertIn(["#3b82f6"], groups)
        self.assertEqual(len(groups), 2)

    def test_pure_white_and_alpha_never_merge(self):
        self.assertEqual(len(merge_near({"#ffffff": 3, "#fdfdfd": 1}, 3.0)), 2)
        self.assertGreater(delta_e(parse_colour("#000000"), parse_colour("#00000080")), 100)


class ContrastTests(unittest.TestCase):
    def test_known_wcag_values(self):
        white = parse_colour("#fff")
        self.assertAlmostEqual(contrast_ratio(parse_colour("#777"), white), 4.48, places=2)
        self.assertAlmostEqual(contrast_ratio(parse_colour("#767676"), white), 4.54, places=2)
        self.assertAlmostEqual(contrast_ratio(parse_colour("#000"), white), 21.0, places=2)
        self.assertAlmostEqual(contrast_ratio(white, white), 1.0, places=3)

    def test_same_rule_pair_flagged_and_large_text_uses_3(self):
        lint, _, cats = run({"a.css": ".x { color: #777; background-color: #fff; }\n"
                                      ".big { color: #777; background: #fff; font-size: 24px; }\n"})
        c = cats["contrast"]
        self.assertEqual((c["checked"], c["failing"]), (2, 1))
        self.assertEqual(c["examples"][0]["needs"], 4.5)
        self.assertTrue(c["examples"][0]["loc"].endswith("a.css:1"))

    def test_var_and_shadcn_hsl_tokens_resolve(self):
        css = (":root { --fg: #999; --bg: #ffffff; --muted: 0 0% 60%; }\n"
               ".a { color: var(--fg); background-color: var(--bg); }\n"
               ".b { color: hsl(var(--muted)); background: var(--bg); }\n")
        _, _, cats = run({"a.css": css})
        self.assertEqual(cats["contrast"]["failing"], 2)

    def test_tailwind_semantic_pair_resolves_and_opacity_is_skipped(self):
        css = ":root { --foreground: 0 0% 60%; --background: 0 0% 100%; }\n"
        jsx = ('export const A = () => <div className="bg-background text-foreground text-sm">x</div>;\n'
               'export const B = () => <div className="bg-background/10 text-foreground">y</div>;\n')
        _, _, cats = run({"t.css": css, "A.jsx": jsx})
        self.assertEqual((cats["contrast"]["checked"], cats["contrast"]["failing"]), (1, 1))


class SizeAndSpacingTests(unittest.TestCase):
    def test_to_px(self):
        self.assertEqual(to_px("14px"), 14.0)
        self.assertEqual(to_px("0.875rem"), 14.0)
        self.assertEqual(to_px("12pt"), 16.0)
        self.assertEqual(to_px("0"), 0.0)
        self.assertIsNone(to_px("1.2em"))
        self.assertIsNone(to_px("50%"))

    def test_sizes_normalise_across_units_and_shorthand(self):
        lint, _, _ = run({"a.css": "a { font-size: 1rem; } b { font-size: 16px; } "
                                   "c { font: 600 12pt/1.4 'Inter', sans-serif; } d { font-size: clamp(1rem, 2vw, 2rem); }"})
        self.assertEqual(sorted(lint.sizes), ["16px"])
        self.assertIn("inter", lint.families)

    def test_spacing_scale(self):
        lint = Lint(grid=4)
        for px in (0, 1, 2, 4, 8, 12, 16, 24, 64):
            self.assertTrue(lint.on_scale(px), px)
        for px in (3, 6, 10, 13, 18):
            self.assertFalse(lint.on_scale(px), px)
        self.assertFalse(Lint(grid=8).on_scale(12))

    def test_spacing_values_judged_per_token(self):
        lint, _, cats = run({"a.scss": ".a { margin: 7px 0 18px; padding: calc(1rem + 2px); gap: 0.5rem; }"})
        self.assertEqual((cats["spacing"]["judged"], cats["spacing"]["off_scale"]), (4, 2))
        self.assertEqual(lint.spacing_unjudged, 1)


class TailwindTests(unittest.TestCase):
    def test_arbitrary_values_detected_by_kind(self):
        jsx = ('export const A = () => <div className="p-[13px] text-[#3b82f6] text-[13px] md:hover:mt-[7px] '
               'w-[calc(100%-2rem)] bg-[var(--brand)] bg-(--brand) rounded-[10px] z-[9999] [color:#333]">x</div>;')
        lint, _, cats = run({"A.jsx": jsx})
        kinds = sorted(k for _, k, _ in lint.tw_arbitrary)
        self.assertEqual(kinds, ["colour", "font size", "property", "radius", "spacing", "spacing", "z-index"])
        self.assertEqual(len(lint.tw_layout_arbitrary), 1)
        self.assertIn("13px", lint.sizes)
        self.assertIn("#3b82f6", lint.colours)
        self.assertEqual(cats["overrides"]["z_over_1000"], 1)

    def test_scale_classes_count_as_on_scale_and_sizes_map(self):
        lint, _, cats = run({"A.tsx": 'const a = <p className={cn("p-4 mt-2.5 gap-6", ok && "text-sm")}>x</p>;'})
        self.assertEqual(cats["spacing"]["off_scale"], 0)
        self.assertEqual(cats["spacing"]["judged"], 3)
        self.assertIn("14px", lint.sizes)

    def test_important_modifier_both_positions(self):
        lint, _, _ = run({"a.html": '<div class="!p-4 font-bold! p-2">x</div>'})
        self.assertEqual(len(lint.important), 2)

    def test_mixed_neutral_families_reported(self):
        _, _, cats = run({"a.html": '<p class="text-gray-700 bg-slate-50">x</p>'})
        self.assertIn({"mixed_neutral_families": ["gray", "slate"]}, cats["colours"]["examples"])


class ParsingTests(unittest.TestCase):
    def test_scss_comments_interpolation_and_nesting(self):
        scss = ("// a comment with color: #ff0000;\n.card {\n  $w: 10px;\n  &__title { width: #{$w}; color: #111; }\n"
                "  margin: 13px; /* margin: 99px; */\n}\n")
        lint, _, cats = run({"a.scss": scss})
        self.assertNotIn("#ff0000", lint.colours)
        self.assertIn("#111111", lint.colours)
        self.assertEqual(cats["spacing"]["examples"][0]["value"], "13px")
        self.assertTrue(cats["spacing"]["examples"][0]["at"].endswith("a.scss:5"))

    def test_style_block_line_numbers_and_inline_styles(self):
        html = ("<html>\n<head>\n<style>\n.a { padding: 13px; }\n</style>\n</head>\n"
                "<body><p style=\"margin: 7px\">x</p></body>\n</html>\n")
        _, _, cats = run({"i.html": html})
        locs = sorted(e["at"] for e in cats["spacing"]["examples"])
        self.assertEqual(locs, ["i.html:4", "i.html:7"])

    def test_jsx_style_object_numbers_are_px(self):
        lint, _, cats = run({"A.jsx": "const a = <p style={{ fontSize: 13, padding: 5, zIndex: 2 }}>x</p>;"})
        self.assertIn("13px", lint.sizes)
        self.assertEqual(cats["spacing"]["off_scale"], 1)
        self.assertEqual(lint.zindex[0][0], 2)

    def test_font_face_and_dark_theme_tokens_not_counted(self):
        css = ("@font-face { font-family: 'Brand'; src: url(a.woff2); }\n"
               ":root { --bg: #ffffff; }\n.dark { --bg: #0b0b0b; }\n"
               "@media (prefers-color-scheme: dark) { :root { --bg: #111111; } }\n")
        lint, _, _ = run({"a.css": css})
        self.assertNotIn("brand", lint.families)
        self.assertEqual(sorted(lint.colours), ["#ffffff"])
        self.assertEqual(lint.dark_tokens, 2)

    def test_generic_look_signals(self):
        html = ('<section class="bg-gradient-to-r from-purple-600 to-blue-500">'
                '<h1>\U0001F680 Fast</h1><p>✨ Easy</p><p>\U0001F48E Pro</p><p>\U0001F1E9\U0001F1EA</p></section>\n'
                '<style>.a { background: linear-gradient(90deg, #6a11cb, #2575fc); }</style>')
        lint, _, cats = run({"a.html": html})
        self.assertEqual(len(lint.emoji), 3)               # the flag pair is content, not an icon
        self.assertEqual([flag for _, flag in lint.gradients], [True, True])
        self.assertIn("purple/blue gradient", cats["generic_look"]["signals"])


class WalkTests(unittest.TestCase):
    def test_ignores_node_modules_dist_minified_and_vendored(self):
        long_line = ".x{color:#123456}" * 300
        d = project({"src/a.css": ".a { color: #111111; }",
                     "node_modules/lib/b.css": ".b { color: #222222; }",
                     "dist/c.css": ".c { color: #333333; }",
                     "src/d.min.css": ".d { color: #444444; }",
                     "src/bundle.css": long_line,
                     "src/vendor-ish.css": "/*! normalize.css v8.0.1 | MIT License */\n.e { color: #555555; }"})
        lint = lint_paths([d])
        self.assertEqual(sorted(lint.colours), ["#111111"])
        self.assertEqual(lint.files["css"], 1)
        self.assertGreaterEqual(lint.skipped["minified"], 2)

    def test_exclude_glob(self):
        d = project({"src/a.css": ".a { color: #111111; }", "src/legacy/b.css": ".b { color: #222222; }"})
        lint = lint_paths([d], excludes=["src/legacy/*"])
        self.assertEqual(sorted(lint.colours), ["#111111"])


class ScoreAndCliTests(unittest.TestCase):
    def test_messy_fixture_scores_lower_than_clean(self):
        _, messy, _ = run_dir(MESSY)
        _, clean, _ = run_dir(CLEAN)
        self.assertLess(messy, 40)
        self.assertGreater(clean, 90)

    def test_adding_inconsistency_never_raises_the_score(self):
        base = {"a.css": ":root { --c: #111111; }\n.a { color: #111111; padding: 8px; font-size: 16px; }\n"}
        _, s0, _ = run(base)
        worse = dict(base, **{"b.css": ".b { color: #121212; padding: 13px; font-size: 15px; z-index: 99999; "
                                       "border-radius: 7px; margin: 3px !important; }\n"})
        _, s1, c1 = run(worse)
        self.assertLess(s1, s0)
        for name, cat in run(base)[2].items():
            self.assertLessEqual(c1[name]["points"], cat["points"], name)

    def test_exit_codes_json_and_baseline(self):
        code, _, err = main_exit(os.path.join(HERE, "does-not-exist"))
        self.assertEqual(code, 2)
        code, _, err = main_exit(project({"README.md": "# nothing"}))
        self.assertEqual(code, 2)
        self.assertIn("nothing to scan", err)
        code, out, _ = main_exit(MESSY, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(set(data["categories"]), {n for n, _ in ui_lint.WEIGHTS})
        self.assertEqual(sum(c["max"] for c in data["categories"].values()), 100)
        self.assertEqual(main_exit(MESSY, "--fail-below", "60")[0], 1)
        base = os.path.join(project({}), "before.json")
        with open(base, "w") as fh:
            fh.write(out)
        code, out2, _ = main_exit(CLEAN, "--baseline", base)
        self.assertEqual(code, 0)
        self.assertRegex(out2, r"\(was %d, \+\d+\)" % data["score"])

    def test_cli_runs_as_a_script(self):
        r = subprocess.run([sys.executable, SCRIPT, CLEAN, "--fail-below", "90"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Score:", r.stdout)


def run_dir(path):
    lint = lint_paths([path])
    score, cats = lint.report()
    return lint, score, cats


if __name__ == "__main__":
    unittest.main()
