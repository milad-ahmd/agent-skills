#!/usr/bin/env python3
"""Static accessibility scan of HTML and component markup. Stdlib only, read-only, offline.

Usage: python3 a11y_scan.py [PATH ...] [--json] [--fail-on high|medium|low|never] [--js]
  PATH            files or directories to scan (default: current directory)
  --json          machine-readable report on stdout
  --js            also read .js files as JSX (Create React App and older Next.js projects)
  --fail-on LVL   exit 1 when a finding of this confidence or higher exists (default: high)

Files: .html .htm (html.parser), .jsx .tsx (JSX tokenizer), .vue (the <template> block), .svelte
(markup outside <script> and <style>). Directories are walked in sorted order; node_modules, dist,
build, .next, .nuxt, .svelte-kit, coverage, vendor, minified files and test/spec files are skipped.

Rules (WCAG 2.2 success criterion in brackets, all Level A):
  img-alt              img without alt, aria-label, aria-labelledby or title           [1.1.1]
  alt-not-alternative  alt that is a filename, "image"/"spacer", or a numbered reference [1.1.1]
  input-image-alt      input type=image without a text alternative                     [1.1.1]
  area-alt             area with href and no alt                                       [1.1.1 2.4.4]
  control-label        input/select/textarea with no label, aria-label(ledby), title
                       or placeholder (wrapping <label> or literal for/id counts)      [4.1.2 3.3.2]
  button-name          button with no text, no labelled image and no aria-label        [4.1.2]
  link-name            a[href] (or a Link/RouterLink/NuxtLink component) with no name  [2.4.4 4.1.2]
  html-lang            <html> without a non-empty lang                                [3.1.1]
  iframe-title         iframe without title, aria-label or aria-labelledby             [4.1.2]
  tabindex-positive    tabindex greater than 0                                         [2.4.3]
  media-autoplay       audio/video with autoplay, no muted and no controls             [1.4.2]
  click-no-keyboard    div/span/li/... with a click handler, no role, no key handler   [2.1.1 4.1.2]
  aria-hidden-focus    aria-hidden="true" on or around a focusable element             [4.1.2]
  role-invalid         role with no valid, non-abstract WAI-ARIA role token            [4.1.2]
  heading-skip         heading level jumps by more than one within a file (advisory)   [1.3.1]

Confidence: high = the markup proves it; medium = very likely, but something outside the file could
make it fine (an icon component, a positive tabindex that keeps a sensible order); low = the scan
could not see everything (spread props, dynamic ids, component children) or the rule is advisory.

What it cannot see (limits):
  - Component files are tokenised with a hand-written scanner, not a JS/TS parser. Unusual syntax
    can hide an element; an element it cannot parse is skipped, never guessed.
  - Values computed at runtime: {expr}, :attr, v-bind, [attr], {{ }} and {% %} count as present
    with an unknown value. Spread props ({...props}, v-bind="obj") lower confidence to low.
  - Components (<Button>, <my-widget>) are never checked themselves; as children their content is
    unknown. Labels passed down from a parent component are invisible.
  - Handlers added in script (addEventListener, refs), CSS (colour contrast, focus outlines,
    display:none, target size), the rendered DOM, and anything that needs a browser. Colour
    contrast from classes or stylesheets is out of scope; use axe or Lighthouse on a running page.

Exit codes: 0 no finding at or above --fail-on
            1 at least one finding at or above --fail-on
            2 bad arguments, a path that does not exist, or no supported files found
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
from html.parser import HTMLParser

EXTS = {".html": "html", ".htm": "html", ".jsx": "jsx", ".tsx": "jsx", ".vue": "vue", ".svelte": "svelte"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "out", ".next", ".nuxt", ".output", ".svelte-kit",
             "coverage", "vendor", "bower_components", "__tests__", "__mocks__", "__snapshots__", ".cache"}
TEST_FILE = re.compile(r"\.(?:test|spec)\.[^.]+$|\.min\.[^.]+$")
MAX_BYTES = 2000000
LEVELS = {"high": 3, "medium": 2, "low": 1}

SC = {  # number: (name, level, anchor in https://www.w3.org/TR/WCAG22/)
    "1.1.1": ("Non-text Content", "A", "non-text-content"),
    "1.3.1": ("Info and Relationships", "A", "info-and-relationships"),
    "1.4.2": ("Audio Control", "A", "audio-control"),
    "2.1.1": ("Keyboard", "A", "keyboard"),
    "2.4.3": ("Focus Order", "A", "focus-order"),
    "2.4.4": ("Link Purpose (In Context)", "A", "link-purpose-in-context"),
    "3.1.1": ("Language of Page", "A", "language-of-page"),
    "3.3.2": ("Labels or Instructions", "A", "labels-or-instructions"),
    "4.1.2": ("Name, Role, Value", "A", "name-role-value"),
}
RULES = {  # rule: (criteria, fix hint)
    "img-alt": (["1.1.1"], 'add alt that says what the image shows, or alt="" if it is decorative'),
    "alt-not-alternative": (["1.1.1"], "replace the alt with what the image shows or does; use alt=\"\" for decoration"),
    "input-image-alt": (["1.1.1"], "add alt naming the action, e.g. alt=\"Search\""),
    "area-alt": (["1.1.1", "2.4.4"], "add alt naming the link destination"),
    "control-label": (["4.1.2", "3.3.2"], "add a <label for=...> (or wrap it in <label>), or aria-label if no visible label fits"),
    "button-name": (["4.1.2"], "add visible text, or aria-label on the button and aria-hidden=\"true\" on the icon"),
    "link-name": (["2.4.4", "4.1.2"], "add link text, or aria-label naming the destination"),
    "html-lang": (["3.1.1"], 'add lang to <html>, e.g. <html lang="en">'),
    "iframe-title": (["4.1.2"], "add a title that names the frame's content"),
    "tabindex-positive": (["2.4.3"], 'use tabindex="0" (or none) and fix the order in the DOM'),
    "media-autoplay": (["1.4.2"], "remove autoplay, add muted, or add controls so users can stop the sound"),
    "click-no-keyboard": (["2.1.1", "4.1.2"], "use a <button> (or <a href>); otherwise add role, tabindex=\"0\" and Enter/Space key handling"),
    "aria-hidden-focus": (["4.1.2"], 'remove aria-hidden, or take the elements out of the tab order (tabindex="-1")'),
    "role-invalid": (["4.1.2"], "use a role from the WAI-ARIA spec, or drop the attribute and use the native element"),
    "heading-skip": (["1.3.1"], "use the next heading level down, and style it with CSS if it must look smaller"),
}

# WAI-ARIA 1.2 concrete roles, the ARIA 1.3 draft additions, DPUB-ARIA 1.1 and Graphics-ARIA 1.0 (see references/sources.md)
ROLES = set("""alert alertdialog application article banner blockquote button caption cell checkbox code columnheader
combobox complementary contentinfo definition deletion dialog directory document emphasis feed figure form generic grid
gridcell group heading img insertion link list listbox listitem log main marquee math menu menubar menuitem
menuitemcheckbox menuitemradio meter navigation none note option paragraph presentation progressbar radio radiogroup
region row rowgroup rowheader scrollbar search searchbox separator slider spinbutton status strong subscript superscript
switch tab table tablist tabpanel term textbox time timer toolbar tooltip tree treegrid treeitem
comment image mark sectionfooter sectionheader suggestion
graphics-document graphics-object graphics-symbol""".split())
ROLES |= {"doc-" + r for r in """abstract acknowledgments afterword appendix backlink biblioentry bibliography biblioref
chapter colophon conclusion cover credit credits dedication endnote endnotes epigraph epilogue errata example footnote
foreword glossary glossref index introduction noteref notice pagebreak pagefooter pageheader pagelist part preface prologue
pullquote qna subtitle tip toc""".split()}
ABSTRACT_ROLES = set("command composite input landmark range roletype section sectionhead select structure widget window".split())

VOID = set("area base br col embed hr img input link meta param source track wbr".split())
RAW = {"script", "style", "textarea"}  # contents are text, never markup
KNOWN = VOID | RAW | set("""a abbr address article aside audio b bdi bdo blockquote body button canvas caption center cite
code colgroup data datalist dd del details dfn dialog div dl dt em fieldset figcaption figure font footer form h1 h2 h3 h4
h5 h6 head header hgroup html i iframe ins kbd label legend li main map mark menu meter nav noscript object ol optgroup
option output p picture pre progress q rp rt ruby s samp search section select slot small span strong sub summary sup
table tbody td template textarea tfoot th thead time title tr u ul var video svg path g circle rect line polyline polygon
ellipse defs use symbol text tspan desc mask clippath lineargradient radialgradient stop image foreignobject marker
pattern filter""".split())
CLICKABLE_STATIC = set("div span li p i img svg td tr section article header footer".split())
# HTML end tags the parser may omit: a new <li> closes an open <li>, a block element closes an open <p>
IMPLIED_END = {"li": {"li"}, "option": {"option"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "tr": {"tr", "td", "th"},
               "td": {"td", "th"}, "th": {"td", "th"}, "p": {"p"}}
CLOSES_P = set("address article aside blockquote details div dl fieldset figure footer form h1 h2 h3 h4 h5 h6 header hr "
               "main menu nav ol p pre section table ul".split())
LINKISH = {"link", "navlink", "routerlink", "router-link", "nuxtlink", "nuxt-link"}
ICONISH = re.compile(r"(?i:icon|^svg|svg$)|^(?:Fa|Md|Hi|Lu|Io|Ri|Bi|Ai|Bs|Fi|Tb|Gi|Si)[A-Z]")
INTERP = re.compile(r"\{\{|\{%|<%|\$\{|\[\[")
FILE_ALT = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|avif|bmp|ico|tiff?)$", re.I)
PLACEHOLDER_ALT = {"image", "img", "picture", "photo", "graphic", "spacer", "icon", "placeholder"}
NUMBERED_ALT = re.compile(r"^(?:image|img|picture|photo|pic|dsc|screenshot)?[\s_#-]*\d+$", re.I)


class Dyn(object):
    """An attribute value only known at runtime."""

    def __repr__(self):
        return "<dynamic>"


DYN = Dyn()
ABSENT = object()


class Expr(object):
    def __init__(self, text):
        self.text = text


class ParseError(Exception):
    pass


class Node(object):
    __slots__ = ("tag", "comp", "attrs", "events", "spread", "dyncontent", "line", "snippet", "children", "parent", "text")

    def __init__(self, tag, line=0, comp=False, text=""):
        self.tag, self.line, self.comp, self.text = tag, line, comp, text
        self.attrs, self.events, self.children = {}, {}, []
        self.spread = self.dyncontent = False
        self.snippet, self.parent = "", None

    def get(self, name, default=None):
        return self.attrs.get(name, default)


def squash(s, limit=90):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 3] + "..."


def is_component(raw, flavor):
    if flavor == "jsx":
        return raw[:1].isupper() or "." in raw or "-" in raw or ":" in raw
    return "-" in raw or ":" in raw or raw[:1].isupper() or raw.lower() not in KNOWN


def literal(text):
    """Value of a JS expression if it is a plain literal, else DYN (ABSENT for false/null/undefined)."""
    e = text.strip()
    m = re.fullmatch(r"'([^'\\]*)'|\"([^\"\\]*)\"|`([^`\\$]*)`", e)
    if m:
        return next(g for g in m.groups() if g is not None)
    if re.fullmatch(r"-?\d+", e):
        return e
    if e in ("false", "null", "undefined"):
        return ABSENT
    return "true" if e == "true" else DYN


def add_attr(node, name, value, flavor):
    """Normalise one attribute across JSX, Vue, Svelte, Alpine and Angular-in-HTML syntax."""
    if flavor == "jsx":
        low = name.lower()
        if low.startswith("on") and name[2:3].isupper():
            node.events[low[2:]] = value.text if isinstance(value, Expr) else (value or "")
            return
        if low == "dangerouslysetinnerhtml":
            node.dyncontent = True
            return
        low = {"classname": "class", "htmlfor": "for"}.get(low, low)
        v = "true" if value is None else literal(value.text) if isinstance(value, Expr) else value
        if v is not ABSENT:
            node.attrs[low] = v
        return
    n = name.lower()
    if n in ("v-bind", "v-on", "x-bind", "x-on") or re.match(r"(?:v-bind:|v-on:|:|@)\[", n):  # object or dynamic-name binding
        node.spread = True
        return
    if n in ("v-html", "v-text", "x-html", "x-text", "[innerhtml]", "[innertext]", "[textcontent]"):
        node.dyncontent = True
        return
    text = value.text if isinstance(value, Expr) else (value or "")
    m = re.match(r"(?:v-on:|x-on:|@|on:|on-)([\w-]+)|\(([\w-]+)", n)
    if m:
        node.events[m.group(1) or m.group(2)] = text
        return
    m = re.match(r"(?:v-bind:|x-bind:|:|bind:|bind-)([\w-]+)|\[(?:attr\.)?([\w-]+)\]", n)
    if m:
        node.attrs[m.group(1) or m.group(2)] = DYN
        return
    if n.startswith("on") and len(n) > 3 and n[2:].isalpha():
        node.events[n[2:]] = text
        return
    if isinstance(value, Expr):
        v = literal(value.text)
        if v is not ABSENT:
            node.attrs[n] = v
    elif value is None:
        node.attrs[n] = ""
    elif INTERP.search(value) or (flavor == "svelte" and "{" in value):
        node.attrs[n] = DYN
    else:
        node.attrs[n] = value


def link(parent, kids):
    for k in kids:
        k.parent = parent
        parent.children.append(k)


# ---------------------------------------------------------------- HTML (html.parser)

class HtmlTree(HTMLParser):
    CDATA_CONTENT_ELEMENTS = ("script", "style", "textarea")

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = self.cur = Node("#root")

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, True)

    def _start(self, tag, attrs, selfclose):
        node = Node(tag, self.getpos()[0], is_component(tag, "html"))
        node.snippet = squash(self.get_starttag_text() or "<%s>" % tag)
        while self.cur.parent is not None and (self.cur.tag in IMPLIED_END.get(tag, ()) or
                                               (self.cur.tag == "p" and tag in CLOSES_P)):
            self.cur = self.cur.parent
        for k, v in attrs:
            add_attr(node, k, v, "html")
        link(self.cur, [node])
        if not selfclose and tag not in VOID:
            self.cur = node

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if self.cur.tag in RAW or not data.strip():
            return
        link(self.cur, [Node("#expr" if INTERP.search(data) else "#text", self.getpos()[0], text=data)])


# ---------------------------------------------------------------- JSX / Vue / Svelte tokenizer

NAME = re.compile(r"[A-Za-z][\w.\-:]*")
ATTR_NAME = {"jsx": re.compile(r"[A-Za-z_$][\w$\-:]*"), "vue": re.compile(r"[^\s\"'>/=]+"),
             "svelte": re.compile(r"[^\s\"'>/={}]+")}
CLOSE = re.compile(r"</\s*([A-Za-z][\w.\-:]*)?\s*>")
WS = re.compile(r"\s*")
WORD = re.compile(r"[\w$]+")
OPERATOR_BEFORE = set("([{,;:=?!&|>+-*%~^")
KEYWORD_BEFORE = {"return", "yield", "default", "case", "await", "else", "do", "in", "of", "typeof", "void"}
REGEX_BEFORE = set("([{,;:=?!&|")


class Markup(object):
    """Hand-written tokenizer: tracks quotes, template literals, comments and brace depth, keeps case.
    shortcut: not a JS parser; anything it cannot parse is skipped, so recall drops on odd syntax."""

    def __init__(self, text, flavor):
        self.s, self.n, self.flavor = text, len(text), flavor
        self.nl = [i for i, c in enumerate(text) if c == "\n"]

    def line(self, i):
        return bisect.bisect_left(self.nl, i) + 1

    def ws(self, j):
        return WS.match(self.s, j).end()

    def jsx_start(self, i, p):
        """True when '<' at i opens JSX: p is the previous token (comments skipped), so `a < b` and `f<T>(` are not tags."""
        if self.flavor != "jsx" or i + 1 >= self.n or not (self.s[i + 1].isalpha() or self.s[i + 1] == ">"):
            return False
        return p == "" or p in OPERATOR_BEFORE or p in KEYWORD_BEFORE

    def skip_string(self, i):
        q, j, s = self.s[i], i + 1, self.s
        while j < self.n:
            if s[j] == "\\":
                j += 2
            elif s[j] == q:
                return j + 1
            elif s[j] == "\n":
                return j
            else:
                j += 1
        return self.n

    def skip_template(self, i, nodes):
        j, s = i + 1, self.s
        while j < self.n:
            if s[j] == "\\":
                j += 2
            elif s[j] == "`":
                return j + 1
            elif s.startswith("${", j):
                j, sub = self.code(j + 2, True)
                nodes.extend(sub)
            else:
                j += 1
        return self.n

    def skip_regex(self, i):
        j, s, cls = i + 1, self.s, False
        while j < self.n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n":
                return i + 1
            if c == "[":
                cls = True
            elif c == "]":
                cls = False
            elif c == "/" and not cls:
                j += 1
                while j < self.n and s[j].isalpha():
                    j += 1
                return j
            j += 1
        return i + 1

    def code(self, i, close):
        """Scan JS/TS code from i. With close=True stop after the matching '}'. Returns (end, elements)."""
        s, nodes, depth, p = self.s, [], 0, ""  # p: previous significant token
        while i < self.n:
            c = s[i]
            if c.isspace():
                i += 1
                continue
            if s.startswith("//", i):
                e = s.find("\n", i)
                i = self.n if e < 0 else e
                continue
            if s.startswith("/*", i):
                e = s.find("*/", i + 2)
                i = self.n if e < 0 else e + 2
                continue
            if c in "\"'":
                i, p = self.skip_string(i), "x"
                continue
            if c == "`":
                i, p = self.skip_template(i, nodes), "x"
                continue
            if c == "/" and (p == "" or p in REGEX_BEFORE or p in ("return", "typeof")):
                i, p = self.skip_regex(i), "x"
                continue
            m = WORD.match(s, i)
            if m:
                i, p = m.end(), m.group(0)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0 and close:
                    return i + 1, nodes
                depth = max(depth - 1, 0)
            elif c == "<" and self.jsx_start(i, p):
                try:
                    node, i = self.element(i, ())
                    nodes.append(node)
                    p = ")"
                    continue
                except ParseError:
                    pass
            i, p = i + 1, c
        if close:
            raise ParseError("unclosed {")
        return self.n, nodes

    def element(self, i, ancestors):
        s, flavor = self.s, self.flavor
        j = i + 1
        if flavor == "jsx" and s.startswith(">", j):
            node = Node("#fragment", self.line(i))
            kids, j = self.children(j + 1, "", ancestors)
            link(node, kids)
            return node, j
        m = NAME.match(s, j)
        if not m:
            raise ParseError("no tag name")
        raw, j = m.group(0), m.end()
        comp = is_component(raw, flavor)
        node = Node(raw if comp else raw.lower(), self.line(i), comp)
        nested = []
        while True:
            j = self.ws(j)
            if j >= self.n:
                raise ParseError("unclosed tag")
            if s.startswith("/>", j):
                j, selfclose = j + 2, True
                break
            if s[j] == ">":
                j, selfclose = j + 1, False
                break
            if s[j] == "{":
                end, sub = self.code(j + 1, True)
                inner = s[j + 1: end - 1].strip()
                nested.extend(sub)
                if inner.startswith("..."):
                    node.spread = True
                elif flavor == "svelte" and re.fullmatch(r"[A-Za-z_$][\w$]*", inner):
                    add_attr(node, inner, Expr(inner), flavor)
                elif inner and not inner.startswith("/*"):
                    raise ParseError("expression in tag")
                j = end
                continue
            m = ATTR_NAME[flavor].match(s, j)
            if not m:
                raise ParseError("bad attribute")
            name, j = m.group(0), m.end()
            k = self.ws(j)
            value = None
            if k < self.n and s[k] == "=":
                k = self.ws(k + 1)
                q = s[k: k + 1]
                if q in ("'", '"'):
                    e = s.find(q, k + 1)
                    if e < 0:
                        raise ParseError("unclosed quote")
                    value, j = s[k + 1: e], e + 1
                elif q == "{":
                    j, sub = self.code(k + 1, True)
                    value = Expr(s[k + 1: j - 1])
                    nested.extend(sub)
                elif flavor == "jsx":
                    raise ParseError("bad attribute value")
                else:
                    mv = re.compile(r"[^\s>]+").match(s, k)
                    if not mv:
                        raise ParseError("bad attribute value")
                    value, j = mv.group(0), mv.end()
            add_attr(node, name, value, flavor)
        node.snippet = squash(s[i:j])
        low = raw.lower()
        if not selfclose and not (flavor != "jsx" and low in VOID):
            if flavor != "jsx" and low in RAW:
                e = re.compile(r"</\s*%s\s*>" % low, re.I).search(s, j)
                j = self.n if e is None else e.end()
            else:
                kids, j = self.children(j, raw, ancestors + (raw,))
                nested.extend(kids)
        link(node, nested)
        return node, j

    def children(self, j, closename, ancestors):
        s, flavor, out = self.s, self.flavor, []
        while j < self.n:
            c = s[j]
            if c == "<":
                if s.startswith("</", j):
                    m = CLOSE.match(s, j)
                    if m:
                        name = m.group(1) or ""
                        if name == closename:
                            return out, m.end()
                        if flavor == "jsx":
                            raise ParseError("mismatched close tag")
                        if name in ancestors:
                            return out, j
                        j = m.end()
                        continue
                elif s.startswith("<!--", j):
                    e = s.find("-->", j)
                    j = self.n if e < 0 else e + 3
                    continue
                elif j + 1 < self.n and (s[j + 1].isalpha() or (flavor == "jsx" and s[j + 1] == ">")):
                    if flavor == "jsx":
                        node, j = self.element(j, ancestors)
                        out.append(node)
                        continue
                    try:
                        node, j = self.element(j, ancestors)
                        out.append(node)
                        continue
                    except ParseError:
                        pass
            elif c == "{" and flavor in ("jsx", "svelte"):
                end, sub = self.code(j + 1, True)
                inner = s[j + 1: end - 1].strip()
                block = flavor == "svelte" and (inner[:1] in ("#", ":", "/") or
                                                (inner[:1] == "@" and not inner.startswith(("@html", "@render"))))
                if inner and not inner.startswith("/*") and not block:
                    without = s[j + 1: end - 1]
                    for k in sub:  # an expression made only of elements adds no text of its own
                        without = without.replace(k.snippet, "")
                    if not sub or re.search(r"['\"`]", without):
                        out.append(Node("#expr", self.line(j)))
                out.extend(sub)
                j = end
                continue
            elif flavor == "vue" and s.startswith("{{", j):
                e = s.find("}}", j)
                out.append(Node("#expr", self.line(j)))
                j = self.n if e < 0 else e + 2
                continue
            m = re.compile(r"[^<{]+").match(s, j)
            text = m.group(0) if m else c
            if re.sub(r"&nbsp;|&#160;", "", text).strip():
                out.append(Node("#text", self.line(j), text=text))
            j += len(text)
        if flavor == "jsx":
            raise ParseError("unclosed element")
        return out, self.n


def parse(text, flavor):
    """Return (root node, note). note is a reason the file was skipped, else ''."""
    root = Node("#root")
    if flavor == "html":
        p = HtmlTree()
        try:
            p.feed(text)
            p.close()
        except Exception as e:  # html.parser is lenient; this is a last resort
            return root, "html.parser failed: %s" % e
        return p.root, ""
    mk = Markup(text, flavor)
    if flavor == "jsx":
        link(root, mk.code(0, False)[1])
    elif flavor == "svelte":
        link(root, mk.children(0, None, ())[0])
    else:
        m = re.search(r"^<template(?=[\s>])", text, re.M)
        if not m:
            return root, "no <template> block"
        try:
            node, _ = mk.element(m.start(), ())
        except ParseError as e:
            return root, "could not parse <template>: %s" % e
        lang = node.get("lang")
        if lang not in (None, "", "html"):
            return root, "template lang=%s is not HTML" % lang
        link(root, [node])
    return root, ""


# ---------------------------------------------------------------- rules

def walk(node):
    """Element descendants in document order (iterative: deep markup must not hit the recursion limit)."""
    stack = list(reversed(node.children))
    while stack:
        k = stack.pop()
        if not k.tag.startswith("#") or k.tag == "#fragment":
            yield k
            stack.extend(reversed(k.children))


def present(v):
    return v is DYN or (isinstance(v, str) and v.strip() != "")


def hidden(n):
    return n.get("aria-hidden") == "true" or "hidden" in n.attrs and n.get("hidden") is not DYN


def own_name(n):
    return present(n.get("aria-label")) or "aria-labelledby" in n.attrs or present(n.get("title"))


RANK = {"named": 5, "dynamic": 4, "component": 3, "icon": 2, "empty": 1}


def content_name(node):
    """Best evidence of a text name inside node: named > dynamic > component > icon > empty."""
    best = "empty"
    for k in node.children:
        if k.tag == "#text":
            return "named"
        if k.tag == "#expr":
            st = "dynamic"
        elif k.comp:
            props = any(present(k.get(a)) for a in ("aria-label", "title", "label", "alt", "text"))
            st = "dynamic" if props or k.children and content_name(k) != "empty" else (
                "icon" if ICONISH.search(k.tag) else "component")
        elif hidden(k):
            continue
        elif own_name(k) or (k.tag == "img" and present(k.get("alt"))):
            return "named"
        elif k.dyncontent or (k.tag == "img" and k.get("alt") is DYN) or k.spread:
            st = "dynamic"
        elif k.tag == "slot":
            st = "component"
        else:
            st = content_name(k)
        if RANK[st] > RANK[best]:
            best = st
        if best == "named":
            break
    return best


def focusable(n):
    if n.comp:
        return False
    tab = n.get("tabindex")
    if isinstance(tab, str) and tab.strip():
        return not tab.strip().startswith("-")
    if n.tag in ("button", "select", "textarea") or (n.tag == "input" and n.get("type") != "hidden"):
        return "disabled" not in n.attrs
    if n.tag in ("a", "area"):
        return "href" in n.attrs
    if n.tag in ("iframe", "summary"):
        return True
    if n.tag in ("audio", "video"):
        return "controls" in n.attrs
    return n.get("contenteditable") in ("", "true")


def lower(conf):
    return "low" if conf in ("high", "medium") else conf


def check_file(root, flavor):
    """Yield (node, rule, confidence, message)."""
    comp_file = flavor != "html"
    nodes = list(walk(root))
    labels = [n for n in nodes if n.tag == "label" or n.comp and "label" in n.tag.lower()]
    label_for = {n.get("for") for n in labels if isinstance(n.get("for"), str)}
    dynamic_for = any(n.get("for") is DYN for n in labels)
    last_heading = 0
    for n in nodes:
        a, t = n.attrs, n.tag
        if n.comp or t == "#fragment":
            if t.lower() in LINKISH and ("href" in a or "to" in a) and not own_name(n) and not n.dyncontent:
                st = content_name(n)
                conf = {"empty": "medium", "icon": "low", "component": "low"}.get(st)
                if conf and not (n.spread and st == "empty"):
                    yield n, "link-name", lower(conf) if n.spread else conf, "link component with no text (%s)" % _what(st)
            continue
        conf_missing = "low" if n.spread else "high"
        if t == "img" and "alt" not in a and not hidden(n) and a.get("role") not in ("none", "presentation") and not own_name(n):
            yield n, "img-alt", conf_missing, "image has no alt attribute"
        if t in ("img", "area") or (t == "input" and a.get("type") == "image"):
            alt = a.get("alt")
            if isinstance(alt, str) and alt.strip():
                bad = _alt_problem(alt.strip(), a.get("src"))
                if bad:
                    yield n, "alt-not-alternative", bad[0], 'alt="%s" %s' % (squash(alt, 40), bad[1])
        if t == "input" and a.get("type") == "image" and not present(a.get("alt")) and not own_name(n):
            yield n, "input-image-alt", conf_missing, "image button has no text alternative"
        if t == "area" and "href" in a and "alt" not in a and not own_name(n):
            yield n, "area-alt", conf_missing, "image-map area has no alt"
        if t in ("input", "select", "textarea"):
            r = _label_problem(n, label_for, dynamic_for, comp_file)
            if r:
                yield (n, "control-label") + r
        if t == "button" or (t == "input" and a.get("type") in ("button", "submit", "reset")):
            r = _button_problem(n)
            if r:
                yield (n, "button-name") + r
        if t == "a" and "href" in a and not own_name(n) and not n.dyncontent and not hidden(n):
            st = content_name(n)
            conf = {"empty": "high", "icon": "medium", "component": "low"}.get(st)
            if conf and not (n.spread and st == "empty"):
                yield n, "link-name", lower(conf) if n.spread else conf, "link has no text (%s)" % _what(st)
        if t == "html" and not present(a.get("lang")) and not n.spread:
            yield n, "html-lang", "high", "page language is not set"
        if t == "iframe" and not own_name(n) and not hidden(n):
            yield n, "iframe-title", conf_missing, "frame has no title"
        tab = a.get("tabindex")
        if isinstance(tab, str) and re.fullmatch(r"\s*\+?\d+\s*", tab) and int(tab) > 0:
            yield n, "tabindex-positive", "medium", "tabindex=%s changes the tab order" % tab.strip()
        if t in ("audio", "video") and "autoplay" in a and a["autoplay"] is not DYN and "muted" not in a and "controls" not in a:
            yield n, "media-autoplay", "low" if n.spread else "medium", "plays automatically with sound and no controls"
        if t in CLICKABLE_STATIC and "click" in n.events and "role" not in a and not (
                {"keydown", "keyup", "keypress"} & set(n.events)):
            inner = next((d for d in walk(n) if focusable(d)), None)
            soft = n.spread or "stopPropagation" in n.events["click"]
            if inner is not None:  # clicks (and Enter) on the focusable child bubble up here
                yield n, "click-no-keyboard", "low", "<%s> click handler wraps a focusable <%s>; check it is not the only way in" % (t, inner.tag)
            else:
                yield n, "click-no-keyboard", "low" if soft else "high", "<%s> has a click handler but no role or key handler" % t
        if a.get("aria-hidden") == "true":
            if focusable(n):
                yield n, "aria-hidden-focus", "high", "hidden from screen readers but still focusable"
            else:
                inner = next((d for d in walk(n) if focusable(d)), None)
                if inner is not None:
                    yield n, "aria-hidden-focus", "medium" if comp_file else "high", \
                        "contains focusable <%s> (line %d) hidden from screen readers" % (inner.tag, inner.line)
        role = a.get("role")
        if isinstance(role, str) and role.strip():
            tokens = role.lower().split()
            if not any(r in ROLES for r in tokens):
                kind = "abstract" if any(r in ABSTRACT_ROLES for r in tokens) else "unknown"
                yield n, "role-invalid", "medium", 'role="%s" is an %s role' % (role.strip(), kind)
        if re.fullmatch(r"h[1-6]", t):
            level = int(t[1])
            if last_heading and level > last_heading + 1:
                yield n, "heading-skip", "low", "h%d follows h%d" % (level, last_heading)
            last_heading = level


def _ancestors(node, stop):
    n = node
    while n is not None and n is not stop:
        yield n
        n = n.parent


def _what(st):
    return {"empty": "empty", "icon": "only an icon component", "component": "only a component; check what it renders"}[st]


def _alt_problem(alt, src):
    if FILE_ALT.search(alt):
        return "high", "is a filename"
    if alt.lower() in PLACEHOLDER_ALT:
        return "medium", "is placeholder text, not a description"
    if NUMBERED_ALT.match(alt):
        return "medium", "is a numbered reference, not a description"
    if isinstance(src, str):
        stem = os.path.splitext(os.path.basename(src.split("?")[0]))[0]
        if stem and alt.lower() == stem.lower() and re.search(r"[-_\d]", stem):
            return "medium", "repeats the file name"
    return None


def _label_problem(n, label_for, dynamic_for, comp_file):
    a = n.attrs
    typ = a.get("type")
    if n.tag == "input" and isinstance(typ, str) and typ.lower() in ("hidden", "submit", "reset", "button", "image"):
        return None
    if hidden(n) or own_name(n) or present(a.get("placeholder")):
        return None
    anc = list(_ancestors(n.parent, None))
    if any(x.tag == "label" for x in anc):
        return None
    if any(x.comp and ("label" in x.attrs or re.search(r"label|field|formitem|formcontrol", x.tag, re.I)) for x in anc):
        return None
    ident = a.get("id")
    if isinstance(ident, str) and ident in label_for:
        return None
    what = "<%s>" % n.tag if n.tag != "input" else "input" + (' type="%s"' % typ if isinstance(typ, str) else "")
    if n.spread or typ is DYN or ident is DYN or (ident is not None and dynamic_for):
        return "low", "%s: no label found, but spread props or dynamic ids may supply one" % what
    return ("medium" if comp_file else "high"), "%s has no label, aria-label or title" % what


def _button_problem(n):
    a = n.attrs
    if hidden(n) or own_name(n) or n.dyncontent:
        return None
    if n.tag == "input":
        if a.get("type") in ("submit", "reset") or present(a.get("value")):
            return None
        return ("low" if n.spread else "high"), "input button has no value or aria-label"
    st = content_name(n)
    conf = {"empty": "high", "icon": "medium", "component": "low"}.get(st)
    if not conf or (n.spread and st == "empty"):  # a wrapper: children arrive through the spread props
        return None
    return (lower(conf) if n.spread else conf), "button has no text (%s)" % _what(st)


# ---------------------------------------------------------------- driver

def find_files(paths, exts=EXTS):
    out, missing = [], []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
        elif os.path.isdir(p):
            for d, dirs, files in os.walk(p):
                dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in exts and not TEST_FILE.search(f):
                        out.append(os.path.join(d, f))
        else:
            missing.append(p)
    return [f for f in out if os.path.splitext(f)[1].lower() in exts], missing


def scan(paths, js=False):
    exts = dict(EXTS, **{".js": "jsx"}) if js else EXTS
    files, missing = find_files(paths, exts)
    findings, skipped = [], []
    for f in files:
        rel = os.path.relpath(f)
        if os.path.getsize(f) > MAX_BYTES:
            skipped.append({"file": rel, "reason": "larger than %d bytes" % MAX_BYTES})
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        flavor = exts[os.path.splitext(f)[1].lower()]
        try:
            root, note = parse(text, flavor)
            hits = [] if note else list(check_file(root, flavor))
        except (ParseError, RecursionError) as e:  # never let one odd file turn into exit 1
            root, note, hits = None, "could not parse: %s" % (e or type(e).__name__), []
        if note:
            skipped.append({"file": rel, "reason": note})
            continue
        for node, rule, conf, msg in hits:
            crit, fix = RULES[rule]
            findings.append({
                "file": rel, "line": node.line, "rule": rule, "confidence": conf, "element": node.snippet,
                "message": msg, "fix": fix,
                "wcag": [{"sc": c, "name": SC[c][0], "level": SC[c][1],
                          "url": "https://www.w3.org/TR/WCAG22/#" + SC[c][2]} for c in crit],
            })
    findings.sort(key=lambda x: (x["file"], x["line"], -LEVELS[x["confidence"]], x["rule"]))
    return files, missing, findings, skipped


def render_text(files, findings, skipped):
    counts = {k: sum(1 for f in findings if f["confidence"] == k) for k in LEVELS}
    nfiles = len({f["file"] for f in findings})
    out = ["a11y_scan: %d findings in %d of %d files (high %d, medium %d, low %d)" % (
        len(findings), nfiles, len(files), counts["high"], counts["medium"], counts["low"])]
    current = None
    for f in findings:
        if f["file"] != current:
            current = f["file"]
            out += ["", current]
        sc = " ".join(w["sc"] for w in f["wcag"])
        out.append("  %4d  %-6s  %-19s  [%s]  %s" % (f["line"], f["confidence"], f["rule"], sc, f["element"]))
        out.append("        %s -> fix: %s" % (f["message"], f["fix"]))
    if skipped:
        out += ["", "Skipped:"] + ["  %s: %s" % (s["file"], s["reason"]) for s in skipped]
    out += ["", "Not checked (needs a browser or judgement): colour contrast, focus visibility, keyboard traps,",
            "focus management in dialogs, target size, motion, captions, and anything rendered at runtime."]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog=__doc__.split("\n", 2)[2], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=["."], metavar="PATH")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    ap.add_argument("--fail-on", choices=["high", "medium", "low", "never"], default="high",
                    help="exit 1 when a finding of this confidence or higher exists (default: high)")
    ap.add_argument("--js", action="store_true", help="also read .js files as JSX")
    args = ap.parse_args(argv)
    files, missing, findings, skipped = scan(args.paths, args.js)
    if missing:
        print("a11y_scan: no such file or directory: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    if not files:
        print("a11y_scan: no .html, .htm, .jsx, .tsx, .vue or .svelte files found", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"files_scanned": len(files), "findings": findings, "skipped": skipped,
                          "not_checked": ["colour contrast", "focus visibility", "keyboard traps", "focus management",
                                          "target size", "motion", "captions", "runtime-rendered content"]}, indent=2))
    else:
        print(render_text(files, findings, skipped))
    floor = LEVELS.get(args.fail_on)
    return 1 if floor and any(LEVELS[f["confidence"]] >= floor for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
