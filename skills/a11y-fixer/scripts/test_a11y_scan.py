#!/usr/bin/env python3
"""Tests for a11y_scan.py. Run: python3 -m unittest discover -s scripts -p 'test_*.py'"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a11y_scan as a  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def scan(self, name, text):
        """Findings for one file as (rule, confidence, line) tuples."""
        path = self.write(name, text)
        _, _, findings, skipped = a.scan([path])
        self.skipped = skipped
        return [(f["rule"], f["confidence"], f["line"]) for f in findings]

    def rules(self, name, text):
        return [(r, c) for r, c, _ in self.scan(name, text)]

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = a.main(list(args))
        return code, out.getvalue(), err.getvalue()


class TestHtml(Base):
    def test_images(self):
        got = self.scan("p.html", '<img src="a.png">\n<img src="b.png" alt="">\n<img src="c.png" alt="c.png">\n'
                                  '<img src="d.png" aria-hidden="true">\n<img src="IMG_2034.jpg" alt="IMG_2034">\n'
                                  '<img src="e.png" alt="spacer">\n<input type="image" src="go.png">\n'
                                  '<map><area href="/x" shape="rect" coords="0,0,1,1"></map>')
        self.assertEqual(got, [("img-alt", "high", 1), ("alt-not-alternative", "high", 3),
                               ("alt-not-alternative", "medium", 5), ("alt-not-alternative", "medium", 6),
                               ("input-image-alt", "high", 7), ("area-alt", "high", 8)])

    def test_form_labels(self):
        got = self.rules("f.html", '<label for="e">Email</label><input id="e" type="email">\n'
                                   '<label>Name <input type="text"></label>\n'
                                   '<input type="search" placeholder="Search">\n<input type="hidden" name="t">\n'
                                   '<input type="submit">\n<select aria-label="Size"></select>\n')
        self.assertEqual(got, [])
        # a label whose for= points at nothing does not label the input
        got = self.scan("g.html", '<label for="to">To</label>\n<input name="to">\n<textarea>a < b</textarea>')
        self.assertEqual(got, [("control-label", "high", 2), ("control-label", "high", 3)])

    def test_button_and_link_names(self):
        got = self.scan("b.html", '<button></button>\n<button><svg aria-hidden="true"><path d=""/></svg></button>\n'
                                  '<button><span class="sr-only">Close</span></button>\n<button aria-label="Menu"></button>\n'
                                  '<button><svg><title>Close</title></svg></button>\n<input type="button">\n'
                                  '<a href="/x"><img src="i.png" alt=""></a>\n<a href="/y">Home</a>\n<a name="top"></a>\n'
                                  '<a href="/z"><img src="h.png" alt="Home"></a>')
        self.assertEqual(got, [("button-name", "high", 1), ("button-name", "high", 2), ("button-name", "high", 6),
                               ("link-name", "high", 7)])

    def test_page_level_rules(self):
        got = self.scan("x.html", '<!doctype html>\n<html>\n<body>\n<iframe src="v"></iframe>\n'
                                  '<iframe src="w" title="Map"></iframe>\n<div tabindex="2">x</div><div tabindex="0">y</div>\n'
                                  '<video autoplay src="v.mp4"></video>\n<video autoplay muted src="v.mp4"></video>\n'
                                  '<audio autoplay controls src="a.mp3"></audio>\n</body></html>')
        self.assertEqual(got, [("html-lang", "high", 2), ("iframe-title", "high", 4), ("tabindex-positive", "medium", 6),
                               ("media-autoplay", "medium", 7)])
        self.assertEqual(self.rules("y.html", '<html lang="en"><body></body></html>'), [])

    def test_aria_hidden_and_roles(self):
        got = self.scan("r.html", '<div aria-hidden="true"><a href="/z">z</a></div>\n'
                                  '<a href="/q" aria-hidden="true" tabindex="-1">q</a>\n'
                                  '<div aria-hidden="true"><button disabled>b</button></div>\n'
                                  '<button aria-hidden="true">x</button>\n'
                                  '<div role="buton">x</div>\n<div role="foo button">x</div>\n<div role="widget">x</div>\n'
                                  '<nav role="doc-toc"></nav>\n<span role="image"></span>\n<svg role="graphics-symbol"></svg>')
        self.assertEqual(got, [("aria-hidden-focus", "high", 1), ("aria-hidden-focus", "high", 4),
                               ("role-invalid", "medium", 5), ("role-invalid", "medium", 7)])

    def test_click_handlers_and_headings(self):
        got = self.scan("c.html", '<h1>T</h1>\n<h3>S</h3>\n<h4>U</h4>\n<div onclick="go()">Go</div>\n'
                                  '<div onclick="go()" onkeydown="k()" role="button" tabindex="0">Go</div>\n'
                                  '<span role="button" onclick="go()">x</span>\n<li onclick="pick()"><a href="#">One</a></li>')
        self.assertEqual(got, [("heading-skip", "low", 2), ("click-no-keyboard", "high", 4), ("click-no-keyboard", "low", 7)])

    def test_template_syntax_counts_as_dynamic(self):
        # Jinja/Angular/Alpine values are present-but-unknown, never "missing"
        got = self.rules("t.html", '<img src="a.png" alt="{{ caption }}">\n<img [alt]="desc" src="z.png">\n'
                                   '<button (click)="go()">{{ label }}</button>\n<div @click="open = true">x</div>\n'
                                   '<div (click)="go()">y</div>')
        self.assertEqual(got, [("click-no-keyboard", "high"), ("click-no-keyboard", "high")])


    def test_omitted_end_tags_close_siblings(self):
        # a hidden <li> must not swallow the next <li>; 1500 open <li> must not hit the recursion limit
        got = self.rules("o.html", '<ul><li aria-hidden="true">x<li><a href="/a">a</a></ul>\n<p onclick="f()">t<p><a href="/b">b</a>')
        self.assertEqual(got, [("click-no-keyboard", "high")])
        self.assertEqual(self.rules("deep.html", "<ul>" + "<li>item" * 1500 + "</ul>"), [])


class TestJsx(Base):
    def test_generics_comparisons_and_regex_are_not_tags(self):
        src = ('const re = /<div>/g;\nfunction f<T>(x: T): Array<T> { return [x]; }\n'
               'const [o, setO] = useState<boolean>(false);\nif (a < b && c<d) {}\nconst g = <T,>(x: T) => x;\n'
               'const s = "<img src=x>";\n')
        root, note = a.parse(src, "jsx")
        self.assertEqual(note, "")
        self.assertEqual(list(a.walk(root)), [])

    def test_arrow_functions_in_attributes_keep_parsing(self):
        got = self.scan("n.tsx", 'export const N = () => (\n  <nav>\n'
                                 '    <button onClick={() => setOpen(x => x > 1)} aria-label="Open">x</button>\n'
                                 '    <button onClick={() => { if (a > b) go(); }}><MenuIcon /></button>\n'
                                 '    {/* <button></button> in a comment */}\n'
                                 '    // eslint-disable-next-line\n'
                                 '    <img src={logo} />\n  </nav>\n);\n')
        self.assertEqual(got, [("button-name", "medium", 4), ("img-alt", "high", 7)])

    def test_comment_between_paren_and_jsx(self):
        got = self.scan("m.tsx", 'const C = {\n  img: (p) => (\n    // eslint-disable-next-line\n    <img src={p.src} />\n  ),\n};\n')
        self.assertEqual(got, [("img-alt", "high", 4)])

    def test_spreads_wrappers_and_dynamic_content(self):
        got = self.rules("w.tsx", 'export const B = ({...props}) => <button {...props} />;\n'
                                  'export const L = ({label}) => <button>{label}</button>;\n'
                                  'export const S = () => <button>{busy ? "Saving" : <Spinner />}</button>;\n'
                                  'export const T = () => <button><Trans>Save</Trans></button>;\n'
                                  'export const H = () => <div dangerouslySetInnerHTML={{__html: x}} />;\n'
                                  'export const F = () => <input {...register("email")} />;\n'
                                  'export const P = () => <button><Avatar user={u} /></button>;\n')
        self.assertEqual(got, [("control-label", "low"), ("button-name", "low")])

    def test_labels_in_components(self):
        got = self.rules("l.tsx", 'export const A = () => (<form>\n<input id="n" /><label htmlFor="n">N</label>\n'
                                  '<Label htmlFor="e">Email</Label><input id="e" type="email" />\n'
                                  '<FormField label="Age"><input type="number" /></FormField>\n'
                                  '<input id={uid} /><label htmlFor={uid}>U</label>\n'
                                  '<input type="text" value={v} onChange={(e) => set(e.target.value)} />\n</form>);\n')
        self.assertEqual(got, [("control-label", "low"), ("control-label", "medium")])

    def test_click_handlers_and_link_components(self):
        got = self.rules("k.jsx", 'export const K = () => (<ul>\n'
                                  '<div onClick={() => close()} className="overlay" />\n'
                                  '<div onClick={go} onKeyDown={key} role="button" tabIndex={0}>ok</div>\n'
                                  '<i className="ion-trash" onClick={del}></i>\n'
                                  '{items.map((i) => <li key={i.id} onClick={() => pick(i)}>{i.name}</li>)}\n'
                                  '<Link href="/home"><HomeIcon /></Link>\n<Link href="/about">About</Link>\n'
                                  '<video autoPlay muted loop src="a.mp4" />\n<div tabIndex={3}>x</div>\n'
                                  '<span aria-hidden><button>x</button></span>\n</ul>);\n')
        self.assertEqual(got, [("click-no-keyboard", "high"), ("click-no-keyboard", "high"), ("click-no-keyboard", "high"),
                               ("link-name", "low"), ("tabindex-positive", "medium"), ("aria-hidden-focus", "medium")])

    def test_js_files_only_with_flag(self):
        self.write("app/App.js", "export default () => <img src={logo} />;\n")
        code, out, _ = self.run_cli(self.dir)
        self.assertEqual(code, 2)
        code, out, _ = self.run_cli(self.dir, "--js")
        self.assertEqual(code, 1)
        self.assertIn("img-alt", out)


class TestVueSvelte(Base):
    def test_vue_template_only(self):
        got = self.scan("C.vue", '<template>\n  <div>\n    <img :src="logo">\n    <img src="a.png" :alt="t(\'a\')">\n'
                                 '    <button @click="open = !open"><icon-menu /></button>\n'
                                 '    <div @click.stop="close">x</div>\n    <input v-bind="$attrs">\n    <img :[attr]="v">\n'
                                 '    <router-link to="/"></router-link>\n'
                                 '    <template v-if="x"><span v-html="raw"></span></template>\n  </div>\n</template>\n'
                                 '<script setup lang="ts">\nconst a = 1 < 2; const s = "<img>";\n</script>\n')
        self.assertEqual(got, [("img-alt", "high", 3), ("button-name", "medium", 5), ("click-no-keyboard", "high", 6),
                               ("control-label", "low", 7), ("img-alt", "low", 8), ("link-name", "medium", 9)])

    def test_vue_pug_is_skipped(self):
        self.assertEqual(self.scan("P.vue", '<template lang="pug">\ndiv\n  img(src="a.png")\n</template>\n'), [])
        self.assertEqual(self.skipped[0]["reason"], "template lang=pug is not HTML")

    def test_svelte(self):
        got = self.scan("S.svelte", '<script lang="ts">\n  let open: boolean = false;\n  const x = 1 < 2 ? "<img>" : "";\n</script>\n'
                                    '<div on:click={() => (open = !open)}>toggle</div>\n'
                                    '<button on:click|preventDefault={go}><svg><title>Close</title></svg></button>\n'
                                    '<img src={logo}>\n<img {alt} src="x.png">\n{#if open}<a href="/x"></a>{/if}\n'
                                    '<input bind:value={name} placeholder="Name">\n<div {...$$restProps} onclick={go}>x</div>\n'
                                    '<p>{@html body}</p>\n<style>div > img { color: red }</style>\n')
        self.assertEqual(got, [("click-no-keyboard", "high", 5), ("img-alt", "high", 7), ("link-name", "high", 9),
                               ("click-no-keyboard", "low", 11)])


    def test_unparseable_file_is_skipped_not_fatal(self):
        self.assertEqual(self.scan("U.svelte", '<img src="a.png">\n<p>{#if open</p>\n'), [])
        self.assertTrue(self.skipped[0]["reason"].startswith("could not parse"))


class TestCli(Base):
    def test_exit_codes_and_fail_on(self):
        self.write("a.html", '<html lang="en"><div tabindex="1">x</div></html>')
        self.assertEqual(self.run_cli(self.dir)[0], 0)                       # only a medium finding
        self.assertEqual(self.run_cli(self.dir, "--fail-on", "medium")[0], 1)
        self.write("b.html", '<img src="x.png">')
        self.assertEqual(self.run_cli(self.dir)[0], 1)
        self.assertEqual(self.run_cli(self.dir, "--fail-on", "never")[0], 0)

    def test_nothing_to_scan_and_missing_path(self):
        self.write("readme.md", "# hi")
        code, _, err = self.run_cli(self.dir)
        self.assertEqual(code, 2)
        self.assertIn("no .html", err)
        self.assertEqual(self.run_cli(os.path.join(self.dir, "nope"))[0], 2)

    def test_skips_vendor_and_test_files(self):
        self.write("node_modules/x/index.html", '<img src="x.png">')
        self.write("src/Button.test.tsx", "export const t = <img src={x} />;")
        self.write("src/ok.html", '<html lang="en"></html>')
        code, out, _ = self.run_cli(self.dir)
        self.assertEqual(code, 0)
        self.assertIn("in 0 of 1 files", out)

    def test_json_report(self):
        self.write("a.html", '<button></button>')
        code, out, _ = self.run_cli(self.dir, "--json")
        data = json.loads(out)
        self.assertEqual(code, 1)
        f = data["findings"][0]
        self.assertEqual((f["rule"], f["line"], f["confidence"], f["element"]), ("button-name", 1, "high", "<button>"))
        self.assertEqual(f["wcag"][0], {"sc": "4.1.2", "name": "Name, Role, Value", "level": "A",
                                        "url": "https://www.w3.org/TR/WCAG22/#name-role-value"})
        self.assertIn("colour contrast", data["not_checked"])

    def test_text_report_groups_by_file(self):
        self.write("b.html", '<img src="x.png">')
        self.write("a.html", '<button></button>')
        _, out, _ = self.run_cli(self.dir)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("a11y_scan: 2 findings in 2 of 2 files"))
        self.assertLess(out.index("a.html"), out.index("b.html"))
        self.assertIn("Not checked", out)


class TestTables(unittest.TestCase):
    def test_rules_map_to_known_criteria(self):
        for rule, (crit, fix) in a.RULES.items():
            self.assertTrue(crit and fix, rule)
            for c in crit:
                self.assertIn(c, a.SC, rule)
        self.assertFalse(a.ROLES & a.ABSTRACT_ROLES)
        self.assertEqual(len([r for r in a.ROLES if r.startswith("doc-")]), 41)


if __name__ == "__main__":
    unittest.main()
