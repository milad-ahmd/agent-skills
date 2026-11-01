#!/usr/bin/env python3
"""Lint a UI's styling for consistency: type scale, fonts, palette, spacing, radii, shadows, contrast. Stdlib only.

Usage: python3 ui_lint.py [path ...] [--json] [--fail-below N] [--baseline FILE] [--exclude GLOB ...]
                          [--grid PX] [--near DELTA_E] [--top N] [--include-js]
  path          files or directories to scan (default: .)
  --json        print the full result as JSON (save it and pass it back with --baseline later)
  --fail-below  exit 1 when the score is below N (for CI)
  --baseline    a previous --json output; prints the score and per-category change against it
  --exclude     glob matched against relative paths and file names; repeatable or comma-separated
  --grid        spacing grid in px (default 4). 0, 1px and 2px always count as on-scale
  --near        CIE76 delta-E under which two colours count as near-duplicates (default 3.0)
  --top         examples shown per category (default 5)
  --include-js  also scan .js/.mjs/.cjs/.ts files (class strings, inline style objects, styled/css templates)

What it reads:
  .css .scss .less                         declarations, nested rules, custom properties, $scss and @less variables
                                           (colours: hex, rgb(), hsl(), oklch(), oklab() and common named colours)
  .html .htm .vue .svelte .astro .jsx .tsx  <style> blocks, style="..." and style={{...}} attributes, styled/css
                                           template literals, and Tailwind classes in class/className attributes and
                                           cn/clsx/classnames/twMerge/cva/tv calls
  Skipped: node_modules, dist, build, out, vendor, .next and similar folders, *.min.*, files whose lines average over
  300 characters (minified), and CSS that starts with a /*! license banner (vendored or generated bundles).

Score (0-100) = sum of weight x health, health in 0..1 per category:
  fonts        10  distinct primary font families. 1.0 at <= 2, minus 0.3 per extra family
  type scale   15  distinct font sizes (px/rem/pt normalised, 16px root). 1.0 at <= 8, 0 at 20
  colours      20  distinct colours after merging near-duplicates (CIE76 delta-E <= --near; pure white and black
                   never merge): 1 - (max(0, n-16)/24 + 0.05 x near-duplicate groups + 0.1 x hue groups beyond 4).
                   Box-shadow colours are left to the shadows check. Tailwind shades count by name (blue-600)
  spacing      15  margin/padding/gap values off the grid: 1 - off_share/0.4 (40% off scores 0). Tailwind scale
                   classes (p-4) count as on-scale; arbitrary ones (p-[13px]) are judged by value. em, %, calc() and
                   var() are not judged
  radii/shadow 10  distinct border-radius and box-shadow values, each 1.0 at <= 4 and 0 at 10, averaged
  contrast     10  colour/background pairs declared in the same rule or element below WCAG AA (4.5, or 3.0 when the
                   same rule sets large text). 1 - failures/4
  overrides     8  !important per 100 declarations (0 at 4) and z-index > 1000 or more than 6 distinct z-index values
  arbitrary     7  Tailwind arbitrary values for spacing, colour, font size, radius, shadow and z-index, per utility
                   used: 1 - rate/0.08. Values that reference var(--x) or theme() are token use and not counted
  generic look  5  minus 0.34 per signal: a purple/blue gradient, 4+ gradients, 3+ emoji in markup, nearly all
                   text-align settings centred
  A category with nothing to measure gets full marks and is marked n/a.

Limits: this is regex scanning, not a CSS parser. It does not compute the cascade, inheritance, media queries,
the Tailwind config or the rendered page. Contrast is only checked where colour and background are set together
with literal colours or var()s it can resolve. Treat the score as a consistency signal and look at the page.

Exit codes: 0 ok · 1 score below --fail-below · 2 nothing to scan (bad path, no style files, nothing measurable)
It only reads files. It never modifies or executes them.
"""
from __future__ import annotations

import argparse
import bisect
import colorsys
import fnmatch
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

SCHEMA_VERSION = 1
CSS_EXTS = {".css", ".scss", ".less"}
MARKUP_EXTS = {".html", ".htm", ".vue", ".svelte", ".astro", ".jsx", ".tsx"}
JS_EXTS = {".js", ".mjs", ".cjs", ".ts"}
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "jspm_packages", "dist", "build", "out", "vendor",
    "vendors", "third_party", "third-party", ".next", ".nuxt", ".svelte-kit", ".output", ".astro", ".vercel",
    "coverage", "storybook-static", ".cache", ".parcel-cache", ".turbo", "__pycache__", ".venv", "venv",
    "target", "public/build",
}
MAX_BYTES = 1_500_000
WEIGHTS = [("fonts", 10), ("type_scale", 15), ("colours", 20), ("spacing", 15), ("radii_shadows", 10),
           ("contrast", 10), ("overrides", 8), ("arbitrary", 7), ("generic_look", 5)]
LABELS = {"fonts": "fonts", "type_scale": "type scale", "colours": "colours", "spacing": "spacing",
          "radii_shadows": "radii/shadows", "contrast": "contrast", "overrides": "overrides",
          "arbitrary": "arbitrary values", "generic_look": "generic look"}

NAMED = {  # CSS named colours most often typed by hand (values from the CSS Color spec)
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
    "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "orange": "#ffa500", "yellow": "#ffff00",
    "purple": "#800080", "pink": "#ffc0cb", "navy": "#000080", "teal": "#008080", "maroon": "#800000",
    "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3", "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9",
    "whitesmoke": "#f5f5f5", "gainsboro": "#dcdcdc", "dimgray": "#696969", "dimgrey": "#696969",
}
COLOUR_RX = re.compile(
    r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])"
    r"|\b(?:rgba?|hsla?|oklch|oklab)\([^()]*\)"
    r"|(?<![\w$@-])(?:" + "|".join(sorted(NAMED, key=len, reverse=True)) + r")(?![\w-])", re.I)
GENERIC_FAMILIES = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "math", "emoji", "fangsong"}
SYSTEM_FAMILIES = {"-apple-system", "system-ui", "blinkmacsystemfont", "ui-sans-serif", "segoe ui"}
SKIP_FAMILIES = {"inherit", "initial", "unset", "revert", "revert-layer", "var"}
SPACING_PROPS = re.compile(r"^(margin|padding|gap|row-gap|column-gap|grid-gap|grid-row-gap|grid-column-gap)"
                           r"(-(top|right|bottom|left|block|inline)(-(start|end))?)?$")
RADIUS_PROP = re.compile(r"^border(-(top|bottom|start|end)-(left|right|start|end))?-radius$")
TOKEN_SIZE = re.compile(r"(font-?size|^-*text-|(^|[-$@])fs-|type-?scale|heading|(^|-)h[1-6]$)", re.I)
TOKEN_FONT = re.compile(r"font(?!-?(size|weight|feature|variant|style|smooth))", re.I)
TOKEN_SPACE = re.compile(r"(space|spacing|gap|gutter|padding|margin|inset|(^|[-$@])sp-)", re.I)
TOKEN_RADIUS = re.compile(r"(radius|rounded)", re.I)
TOKEN_SHADOW = re.compile(r"(shadow|elevation)", re.I)
# Emoji and dingbats; regional-indicator letters (flags, U+1F1E6-1F1FF) are content, not icons, so they are left out
EMOJI_RX = re.compile("[\U0001F000-\U0001F1E5\U0001F200-\U0001FAFF\u2600-\u27BF\u2B05-\u2B07\u2B50\u27A1]")
BARE_HSL = re.compile(r"-?\d*\.?\d+(deg)?\s+\d*\.?\d+%\s+\d*\.?\d+%(\s*/\s*\d*\.?\d+%?)?")

# Tailwind
TW_SIZES = {"xs": 12, "sm": 14, "base": 16, "lg": 18, "xl": 20, "2xl": 24, "3xl": 30, "4xl": 36, "5xl": 48,
            "6xl": 60, "7xl": 72, "8xl": 96, "9xl": 128}
TW_SHADES = {"50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"}
TW_NEUTRALS = {"slate", "gray", "zinc", "neutral", "stone", "taupe", "mauve", "mist", "olive"}
TW_HUES = {  # rough hue of each default family's mid shades, used only to group accents
    "red": 0, "rose": 350, "orange": 25, "amber": 40, "yellow": 50, "lime": 85, "green": 140, "emerald": 160,
    "teal": 175, "cyan": 190, "sky": 200, "blue": 220, "indigo": 240, "violet": 260, "purple": 275,
    "fuchsia": 295, "pink": 330,
}
TW_PURPLEISH = {"indigo", "violet", "purple", "fuchsia"}
TW_COOL = {"sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "cyan"}
TW_COLOUR_PREFIX = re.compile(r"^(text|bg|border(-[xytrblse])?|from|via|to|ring|ring-offset|fill|stroke|outline|"
                              r"decoration|divide|accent|caret|placeholder|shadow)-([a-z]+)-(\d{2,3})(/[\w.\[\]]+)?$")
TW_SPACING_PREFIX = re.compile(r"^(p[xytrblse]?|m[xytrblse]?|gap(-[xy])?|space-[xy])-")
TW_UTIL_PREFIX = set(
    "p px py pt pr pb pl ps pe m mx my mt mr mb ml ms me gap space w h min max size text bg border rounded shadow ring "
    "font leading tracking flex grid col row items justify content self place z top right bottom left inset opacity "
    "from via to fill stroke divide outline overflow transition duration ease delay translate scale rotate skew "
    "cursor order basis grow shrink aspect object decoration underline line list break whitespace align select "
    "pointer sr not animate backdrop blur drop".split())
TW_SINGLE = set("flex grid block inline inline-block inline-flex hidden contents table relative absolute fixed sticky "
                "static container truncate italic underline uppercase lowercase capitalize antialiased".split())
TW_ALIGN = {"text-left": "left", "text-center": "center", "text-right": "right", "text-justify": "justify",
            "text-start": "start", "text-end": "end"}
NOT_COLOUR = re.compile(r"(wrap|nowrap|balance|pretty|ellipsis|clip|cover|contain|fixed|local|scroll|repeat|no-repeat|"
                        r"top|bottom|left|right|center|none|auto|origin|blend|gradient|linear|radial|conic|"
                        r"opacity|inherit|current|transparent)(-|$)")
CLASS_CALLS = re.compile(r"\b(?:cn|clsx|classnames|classNames|twMerge|twJoin|cva|tv|cx)\s*\(")
STYLED_RX = re.compile(r"\b(?:styled(?:\.\w+|\([^()]*\))(?:\.attrs\([^`]*?\))?|css|createGlobalStyle|"
                       r"injectGlobal|keyframes)\s*`([^`]*)`", re.S)


# --- small helpers ---------------------------------------------------------------------------
def blank(s):
    """Same length, newlines kept, everything else turned into spaces (keeps offsets and line numbers)."""
    return re.sub(r"[^\n]", " ", s)


class Lines:
    def __init__(self, text):
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def line(self, offset):
        return bisect.bisect_right(self.starts, offset)


def fmt_px(v):
    v = round(v * 2) / 2
    return ("%d" % v if v == int(v) else ("%g" % v)) + "px"


def to_px(token):
    """'14px' -> 14.0, '0.875rem' -> 14.0, '12pt' -> 16.0, '0' -> 0.0. None for em/%/vw/keywords."""
    m = re.fullmatch(r"(-?\d*\.?\d+)(px|rem|pt)?", token.strip().lower())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    if unit is None:
        return 0.0 if n == 0 else None
    return n * {"px": 1.0, "rem": 16.0, "pt": 4.0 / 3.0}[unit]


def split_top(value, sep=","):
    """Split on sep outside parentheses and quotes."""
    out, depth, cur, quote = [], 0, [], None
    for ch in value:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0 or (sep == " " and ch.isspace() and depth == 0):
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return [p.strip() for p in out if p.strip()]


# --- colour maths ------------------------------------------------------------------------------
def _num(tok, scale):
    tok = tok.strip()
    if tok.endswith("%"):
        return float(tok[:-1]) * scale / 100.0
    return float(tok)


def oklab_to_srgb(kind, parts):
    """oklch(L C H) or oklab(L a b) -> sRGB 0-255, clipped (Ottosson's OKLab matrices)."""
    lig = _num(parts[0], 1.0)
    if kind == "oklch":
        chroma, hue = _num(parts[1], 0.4), math.radians(float(parts[2].replace("deg", "")))
        a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    else:
        a, b = _num(parts[1], 0.4), _num(parts[2], 0.4)
    l_ = (lig + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lig - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lig - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lin = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
           -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
           -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)

    def gamma(x):
        x = max(0.0, min(1.0, x))
        return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
    return tuple(gamma(x) * 255 for x in lin)


def parse_colour(raw):
    """'#777', 'rgb(0 0 0 / 50%)', 'hsl(220 90% 56%)', 'oklch(0.7 0.1 180)', 'white' -> (r, g, b, a), r/g/b 0-255.

    None for anything it can't read (var(), calc(), relative colour syntax, color-mix())."""
    s = raw.strip().lower()
    if s in NAMED:
        s = NAMED[s]
    try:
        if s.startswith("#"):
            h = s[1:]
            if len(h) in (3, 4):
                h = "".join(c * 2 for c in h)
            if len(h) not in (6, 8) or not re.fullmatch(r"[0-9a-f]+", h):
                return None
            a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(a, 3))
        m = re.fullmatch(r"(rgba?|hsla?|oklch|oklab)\((.*)\)", s)
        if not m or "var(" in s or "calc(" in s:
            return None
        parts = [p for p in re.split(r"[\s,/]+", m.group(2).strip()) if p]
        if len(parts) not in (3, 4):
            return None
        a = _num(parts[3], 1.0) if len(parts) == 4 else 1.0
        if m.group(1).startswith("ok"):
            r, g, b = oklab_to_srgb(m.group(1), parts[:3])
        elif m.group(1).startswith("rgb"):
            r, g, b = (max(0.0, min(255.0, _num(p, 255.0))) for p in parts[:3])
        else:
            hue = parts[0]
            if hue.endswith("turn"):
                hv = float(hue[:-4]) * 360
            elif hue.endswith("rad"):
                hv = math.degrees(float(hue[:-3]))
            else:
                hv = float(hue.replace("deg", ""))
            sat, lig = _num(parts[1], 1.0), _num(parts[2], 1.0)
            r, g, b = (c * 255 for c in colorsys.hls_to_rgb((hv % 360) / 360.0, lig, sat))
        return (int(round(r)), int(round(g)), int(round(b)), round(max(0.0, min(1.0, a)), 3))
    except ValueError:
        return None


def hex_key(c):
    r, g, b, a = c
    base = "#%02x%02x%02x" % (r, g, b)
    return base if a >= 0.999 else base + "%02x" % int(round(a * 255))


def find_colours(value):
    """All colours in a CSS value, skipping url(...), quoted strings and var() names."""
    v = re.sub(r"url\([^)]*\)|\"[^\"]*\"|'[^']*'", " ", value)
    out = []
    for m in COLOUR_RX.finditer(v):
        c = parse_colour(m.group(0))
        if c:
            out.append((c, m.group(0)))
    return out


def rel_luminance(c):
    def ch(x):
        x = x / 255.0
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast_ratio(fg, bg):
    """WCAG 2 contrast ratio. A translucent foreground is blended over the background first."""
    a = fg[3] if len(fg) > 3 else 1.0
    if a < 1.0:
        fg = tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)
    l1, l2 = rel_luminance(fg), rel_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def to_lab(c):
    def lin(x):
        x = x / 255.0
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = lin(c[0]), lin(c[1]), lin(c[2])
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1.0 / 3) if t > 0.008856 else 7.787 * t + 16.0 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(c1, c2):
    """CIE76 distance; colours whose alpha differs by more than 0.1 are never near-duplicates."""
    if abs((c1[3] if len(c1) > 3 else 1) - (c2[3] if len(c2) > 3 else 1)) > 0.1:
        return 999.0
    l1, l2 = to_lab(c1), to_lab(c2)
    return math.sqrt(sum((p - q) ** 2 for p, q in zip(l1, l2)))


ANCHORS = {"#ffffff", "#000000"}  # pure white and black are deliberate; an off-white next to them is not a typo


def merge_near(counts, threshold):
    """Greedy grouping, most-used colour first. counts: {hex: n}. Returns list of groups [[hex, ...], ...]."""
    groups = []
    for key in sorted(counts, key=lambda k: (-counts[k], k)):
        c = parse_colour(key)
        if key in ANCHORS:
            groups.append([key])
            continue
        for g in groups:
            if g[0] not in ANCHORS and delta_e(c, parse_colour(g[0])) <= threshold:
                g.append(key)
                break
        else:
            groups.append([key])
    return groups


HUE_NAMES = ["red", "orange", "yellow", "chartreuse", "green", "spring green", "cyan", "azure", "blue", "violet",
             "magenta", "rose"]


def hue_group(c):
    """Name of the 30-degree hue bucket for a clearly chromatic colour, else None (greys, near-black/white)."""
    h, l, s = colorsys.rgb_to_hls(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
    if s < 0.3 or l < 0.12 or l > 0.92:
        return None
    return HUE_NAMES[int(((h * 360) + 15) % 360 // 30)]


def hue_of(c):
    h, l, s = colorsys.rgb_to_hls(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
    return None if s < 0.3 or l < 0.12 or l > 0.92 else h * 360


def is_purple_blue(hues):
    """Gradient stops that all sit in the blue-to-pink range with at least one violet/purple stop."""
    hs = [h for h in hues if h is not None]
    return len(hs) >= 2 and all(195 <= h <= 345 for h in hs) and any(245 <= h <= 300 for h in hs) and \
        max(hs) - min(hs) >= 20


# --- collection ---------------------------------------------------------------------------------
class Lint:
    def __init__(self, grid=4.0, near=3.0):
        self.grid, self.near = grid, near
        self.families = defaultdict(list)
        self.sizes = defaultdict(list)
        self.colours = defaultdict(list)          # hex -> locs (palette, shadows excluded)
        self.literal_uses = 0                     # colour literals outside token definitions
        self.spacing = []                         # (key, on_scale, loc)
        self.spacing_unjudged = 0
        self.radii = defaultdict(list)
        self.shadows = defaultdict(list)
        self.zindex = []                          # (value, loc)
        self.important = []
        self.decls = 0
        self.vars = {}                            # --name -> raw value (first definition wins)
        self.dark_tokens = 0
        self.pairs = []                           # pending contrast checks
        self.tw_utils = 0
        self.tw_arbitrary = []                    # (token, kind, loc)
        self.tw_layout_arbitrary = []
        self.tw_families = Counter()
        self.gradients = []                       # (loc, purple_blue)
        self.emoji = []
        self.align = Counter()
        self.center_locs = []
        self.files = Counter()
        self.skipped = Counter()

    # -- declarations --------------------------------------------------------------------------
    def decl(self, prop, value, loc, frame=None, in_font_face=False, in_dark=False):
        prop = prop.strip()
        value = value.strip()
        if not prop or not value:
            return
        self.decls += 1
        if re.search(r"!\s*important\b", value, re.I):
            self.important.append(loc)
            value = re.sub(r"!\s*important\b", "", value, flags=re.I).strip()
        value = re.sub(r"!\s*(default|global)\b", "", value).strip()
        p = prop.lower()
        if p.startswith(("--", "$", "@")):
            self._token(prop, value, loc, in_dark)
            return
        if frame is not None:
            frame[p] = (value, loc)
        if p == "font-family":
            if not in_font_face:
                self._family(value, loc)
        elif p == "font":
            self._font_shorthand(value, loc)
        elif p == "font-size":
            self._size(value, loc)
        elif SPACING_PROPS.match(p):
            self._spacing(value, loc)
        elif RADIUS_PROP.match(p):
            self._radius(value, loc)
        elif p == "box-shadow":
            self._shadow(value, loc)
        elif p == "z-index":
            self._z(value, loc)
        elif p == "text-align":
            self._align(value.lower(), loc)
        if re.search(r"(linear|radial|conic)-gradient\(", value, re.I):
            hues = [hue_of(c) for c, _ in find_colours(value)]
            self.gradients.append((loc, is_purple_blue(hues)))
        elif p not in ("box-shadow", "text-shadow") and not p.startswith("font") and not in_dark:
            for c, _ in find_colours(value):
                self.colours[hex_key(c)].append(loc)
                self.literal_uses += 1

    def _token(self, name, value, loc, in_dark=False):
        """A custom property, $scss or @less variable: the design tokens."""
        bare = name.lstrip("-$@")
        if in_dark:                             # dark-theme overrides: not part of the light palette
            self.dark_tokens += 1
            return
        if name.startswith("--"):
            self.vars.setdefault(name, value)   # first definition wins (usually :root, the light theme)
        if "var(" in value or "$" in value:
            return
        if BARE_HSL.fullmatch(value):          # shadcn-style channel tokens: --primary: 222 47% 11%;
            value = "hsl(%s)" % value
        if re.search(r"(linear|radial|conic)-gradient\(", value, re.I):
            self.gradients.append((loc, is_purple_blue([hue_of(c) for c, _ in find_colours(value)])))
            return
        for c, _ in find_colours(value):
            if not TOKEN_SHADOW.search(bare):
                self.colours[hex_key(c)].append(loc)
        if TOKEN_SHADOW.search(bare):
            self._shadow(value, loc)
        elif TOKEN_RADIUS.search(bare):
            self._radius(value, loc)
        elif TOKEN_SIZE.search(name) and to_px(value.split()[0] if value.split() else "") is not None:
            self._size(value, loc)
        elif TOKEN_FONT.search(bare) and not find_colours(value) and re.search(r"[A-Za-z]", value) \
                and to_px(value) is None and not re.fullmatch(r"\d+", value):
            self._family(value, loc)
        elif TOKEN_SPACE.search(bare):
            self._spacing(value, loc)
        elif re.search(r"(^|-)z(-|index|$)", bare, re.I):
            self._z(value, loc)

    def _family(self, value, loc):
        first = split_top(value, ",")
        if not first:
            return
        name = first[0].strip().strip("\"'").strip().lower()
        if not name or name.startswith(("var(", "$", "@", "theme(")) or name in SKIP_FAMILIES:
            return
        if name in SYSTEM_FAMILIES:
            name = "system-ui stack"
        elif name in ("monospace", "ui-monospace"):
            name = "monospace stack"
        self.families[name].append(loc)

    def _font_shorthand(self, value, loc):
        m = re.search(r"(?:^|\s)(\d*\.?\d+(?:px|rem|em|pt|%)|(?:xx?-)?(?:small|large)|medium|smaller|larger)"
                      r"(?:\s*/\s*\S+)?\s+(.+)$", value)
        if m:
            self._size(m.group(1), loc)
            self._family(m.group(2), loc)

    def _size(self, value, loc):
        v = value.strip().lower()
        if not v or "(" in v or v.startswith(("$", "@")) or v in ("inherit", "initial", "unset", "revert"):
            return
        v = v.split()[0]
        px = to_px(v)
        self.sizes[fmt_px(px) if px is not None else v].append(loc)

    def on_scale(self, px):
        px = abs(px)
        if px in (0, 1, 2):
            return True
        return abs(px / self.grid - round(px / self.grid)) < 0.01

    def _spacing(self, value, loc):
        if "(" in value or "$" in value or "@" in value:
            self.spacing_unjudged += 1
            return
        for tok in value.split():
            t = tok.lower()
            if t in ("auto", "inherit", "initial", "unset", "revert"):
                continue
            px = to_px(t)
            if px is None:
                self.spacing_unjudged += 1
                continue
            self.spacing.append((fmt_px(abs(px)), self.on_scale(px), loc))

    def _radius(self, value, loc):
        if "(" in value or "$" in value:
            return
        parts = []
        for tok in value.replace("/", " ").split():
            t = tok.lower()
            px = to_px(t)
            if t in ("50%", "100%") or (px is not None and px >= 999):
                parts.append("full")
            elif px is not None:
                parts.append(fmt_px(px))
            else:
                parts.append(t)
        if not parts or all(p == "0px" for p in parts):
            return
        if len(set(parts)) == 1:
            parts = parts[:1]
        self.radii[" ".join(parts)].append(loc)

    def _shadow(self, value, loc):
        v = value.strip().lower()
        if v in ("none", "inherit", "initial", "unset") or "var(" in v or v.startswith("$"):
            return
        v = COLOUR_RX.sub(lambda m: hex_key(parse_colour(m.group(0)) or (0, 0, 0, 1)), v)
        v = re.sub(r"\b0(px|rem)\b", "0", re.sub(r"\s+", " ", v)).strip()
        self.shadows[v].append(loc)

    def _z(self, value, loc):
        m = re.fullmatch(r"\s*(-?\d+)\s*", value)
        if m:
            self.zindex.append((int(m.group(1)), loc))

    def _align(self, value, loc):
        if value in ("center", "left", "right", "justify", "start", "end"):
            self.align[value] += 1
            if value == "center":
                self.center_locs.append(loc)

    def rule_done(self, frame, loc):
        """Queue a contrast check for a rule or element that sets both colour and background."""
        if not frame or "color" not in frame:
            return
        bg = frame.get("background-color") or frame.get("background")
        if not bg:
            return
        size = frame.get("font-size", (None,))[0]
        weight = frame.get("font-weight", ("",))[0]
        self.pairs.append({"fg": frame["color"][0], "bg": bg[0], "loc": frame["color"][1], "size": size,
                           "weight": weight})

    # -- Tailwind ----------------------------------------------------------------------------------
    def tw_string(self, s, line, path, ctx=None):
        """One class string. Returns nothing; feeds every metric. line = line where the string starts."""
        ctx = {} if ctx is None else ctx
        stops = []
        has_gradient = False
        offset_line = line
        for m in re.finditer(r"\S+", s):
            tok = m.group(0)
            loc = "%s:%d" % (path, offset_line + s.count("\n", 0, m.start()))
            if "${" in tok or "{" in tok or "}" in tok:
                continue
            kind = self.tw_token(tok, loc, ctx)
            if kind and kind[0] == "stop":
                stops.append(kind[1])
            elif kind and kind[0] == "gradient":
                has_gradient = True
        if has_gradient:
            hues = [TW_HUES.get(f) for f in stops if f in TW_HUES]
            pb = len(stops) >= 2 and all(f in TW_COOL for f in stops) and any(f in TW_PURPLEISH for f in stops) \
                and len(set(stops)) >= 2
            if not pb and hues:
                pb = is_purple_blue(hues)
            self.gradients.append(("%s:%d" % (path, line), pb))
        if "text" in ctx and "bg" in ctx:
            self.pairs.append({"fg": ctx["text"][0], "bg": ctx["bg"][0], "loc": ctx["text"][1], "size": None,
                               "weight": ""})

    def tw_token(self, tok, loc, ctx):
        # strip variants (md:hover:...) outside brackets
        depth, cut = 0, -1
        for i, ch in enumerate(tok):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            elif ch == ":" and depth == 0:
                cut = i
        base = tok[cut + 1:]
        variant = cut >= 0
        important = base.startswith("!") or base.endswith("!")
        base = base.strip("!")
        if base.startswith("-"):
            base = base[1:]
        if not base or not re.match(r"^[a-z\[]", base):
            return None
        head = base.split("-")[0].split("[")[0]
        if not (head in TW_UTIL_PREFIX or base in TW_SINGLE or base.startswith("[")):
            return None
        self.tw_utils += 1
        self.decls += 1
        if important:
            self.important.append(loc)
        cm = re.fullmatch(r"(text|bg)-([a-z][a-z0-9-]*?)(/\d+)?", base)
        if cm and not variant and not cm.group(3) and cm.group(2) not in TW_SIZES and base not in TW_ALIGN \
                and not NOT_COLOUR.match(cm.group(2)):  # kept for a contrast check if a matching --var exists;
            # an opacity modifier (bg-x/10) means the real colour depends on what is underneath, so it is skipped
            name = cm.group(2)
            ctx[cm.group(1)] = ({"white": "#ffffff", "black": "#000000"}.get(name, "var(--%s)" % name), loc)
        # arbitrary property: [color:#333]
        m = re.fullmatch(r"\[([a-z-]+):(.+)\]", base)
        if m:
            val = m.group(2).replace("_", " ")
            if "var(" not in val and not m.group(1).startswith("--"):
                self.tw_arbitrary.append((tok, "property", loc))
                self.decl(m.group(1), val, loc)
                self.decls -= 1
            return ("util",)
        m = re.fullmatch(r"([a-z][a-z0-9-]*?)-\[(.+)\]", base)
        if m:
            return self._tw_arbitrary(tok, m.group(1), m.group(2).replace("_", " "), loc, ctx, variant)
        if re.fullmatch(r"[a-z][a-z0-9-]*-\(.+\)", base):
            return ("util",)                       # v4 shorthand bg-(--brand): token use
        if base in TW_ALIGN:
            self._align(TW_ALIGN[base], loc)
            return ("util",)
        m = re.fullmatch(r"text-(xs|sm|base|lg|xl|[2-9]xl)(/[\w.]+)?", base)
        if m:
            self.sizes[fmt_px(TW_SIZES[m.group(1)])].append(loc)
            return ("util",)
        if re.fullmatch(r"bg-(gradient-to|linear-to)-[a-z]{1,2}|bg-linear-\d+|bg-radial(-.+)?|bg-conic(-.+)?", base):
            return ("gradient",)
        m = TW_COLOUR_PREFIX.fullmatch(base)
        if m and m.group(4) in TW_SHADES:
            fam = m.group(3)
            self.colours["tw:%s-%s" % (fam, m.group(4))].append(loc)
            self.tw_families[fam] += 1
            if m.group(1) in ("from", "via", "to"):
                return ("stop", fam)
            return ("util",)
        m = re.fullmatch(r"(text|bg|border|from|via|to|ring|fill|stroke)-(white|black)(/[\w.]+)?", base)
        if m:
            self.colours["#ffffff" if m.group(2) == "white" else "#000000"].append(loc)
            return ("util",)
        m = re.fullmatch(r"rounded(-[trblse]{1,2})?(-(none|xs|sm|md|lg|xl|2xl|3xl|4xl|full))?", base)
        if m:
            size = m.group(3) or "DEFAULT"
            if size != "none":
                self.radii["tw:rounded-" + size if size != "DEFAULT" else "tw:rounded"].append(loc)
            return ("util",)
        m = re.fullmatch(r"shadow(-(2xs|xs|sm|md|lg|xl|2xl|inner))?", base)
        if m:
            self.shadows["tw:" + base].append(loc)
            return ("util",)
        m = re.fullmatch(r"z-(\d+)", base)
        if m:
            self.zindex.append((int(m.group(1)), loc))
            return ("util",)
        m = re.fullmatch(r"font-(sans|serif|mono)", base)
        if m:
            self.families["tailwind font-" + m.group(1)].append(loc)
            return ("util",)
        if TW_SPACING_PREFIX.match(base):
            self.spacing.append(("tw-scale", True, loc))
            return ("util",)
        return ("util",)

    def _tw_arbitrary(self, tok, prefix, val, loc, ctx, variant=False):
        val = re.sub(r"^(length|color|size|number|percentage|family-name|shadow):", "", val)
        if "var(" in val or val.startswith("--") or "theme(" in val:
            return ("util",)
        colours = find_colours(val)
        kind = None
        if re.fullmatch(r"p[xytrblse]?|m[xytrblse]?|gap(-[xy])?|space-[xy]", prefix):
            kind = "spacing"
            self._spacing(val, loc)
        elif prefix.startswith("rounded"):
            kind = "radius"
            self._radius(val, loc)
        elif prefix == "shadow" and not (len(colours) == 1 and colours[0][1] == val.strip()):
            kind = "shadow"
            self._shadow(val, loc)
        elif prefix == "z":
            kind = "z-index"
            self._z(val, loc)
        elif prefix == "font" and not colours:
            kind = "font"
            self._family(val, loc)
        elif colours and len(colours) == 1 and colours[0][1].strip() == val.strip():
            kind = "colour"
            c = colours[0][0]
            self.colours[hex_key(c)].append(loc)
            self.literal_uses += 1
            if prefix in ("text", "bg") and not variant:
                ctx[prefix] = (val, loc)
            if prefix in ("from", "via", "to"):
                self.tw_arbitrary.append((tok, kind, loc))
                h = hue_of(c)
                return ("stop", next((f for f, hv in TW_HUES.items() if h is not None and abs(hv - h) < 1), "#"))
        elif prefix == "text" and to_px(val) is not None:
            kind = "font size"
            self._size(val, loc)
        if kind:
            self.tw_arbitrary.append((tok, kind, loc))
        else:
            self.tw_layout_arbitrary.append((tok, loc))
        return ("util",)

    # -- results -------------------------------------------------------------------------------------
    def resolve(self, raw, depth=0):
        raw = raw.strip()
        if BARE_HSL.fullmatch(raw):
            raw = "hsl(%s)" % raw
        m = re.fullmatch(r"(hsla?)\(\s*var\(\s*(--[\w-]+)\s*\)\s*(/\s*[\d.]+%?)?\s*\)", raw)
        if m and m.group(2) in self.vars and BARE_HSL.fullmatch(self.vars[m.group(2)].strip()):
            raw = "hsl(%s %s)" % (self.vars[m.group(2)].strip(), m.group(3) or "")
        m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)", raw)
        if m and depth < 5:
            for name in (m.group(1), "--color-" + m.group(1)[2:]):
                if name in self.vars:
                    return self.resolve(self.vars[name], depth + 1)
            return self.resolve(m.group(2), depth + 1) if m.group(2) else None
        if "gradient" in raw or "url(" in raw:
            return None
        found = find_colours(raw)
        return found[0][0] if len(found) == 1 else None

    def contrast_results(self):
        checked, fails = 0, []
        for p in self.pairs:
            fg, bg = self.resolve(p["fg"]), self.resolve(p["bg"])
            if not fg or not bg or bg[3] < 1.0 or fg[:3] == bg[:3]:
                continue                        # translucent background, or a solid colour block (color = background)
            checked += 1
            ratio = contrast_ratio(fg, bg)
            px = to_px((p["size"] or "").split()[0]) if p["size"] else None
            bold = str(p["weight"]).strip() in ("bold", "bolder", "700", "800", "900")
            large = px is not None and (px >= 24 or (bold and px >= 18.66))
            need = 3.0 if large else 4.5
            if ratio < need:
                fails.append({"loc": p["loc"], "fg": hex_key(fg), "bg": hex_key(bg), "ratio": round(ratio, 2),
                              "needs": need})
        return checked, fails

    def report(self, top=5):
        cats = {}

        def rarest(d, n=top):
            return [{"value": k, "uses": len(v), "at": v[0]} for k, v in
                    sorted(d.items(), key=lambda kv: (len(kv[1]), kv[0]))[:n]]

        # fonts
        nf = len(self.families)
        h = 1.0 if nf <= 2 else max(0.0, 1 - 0.3 * (nf - 2))
        cats["fonts"] = {"health": h, "measured": nf > 0, "distinct": nf,
                         "summary": "%d famil%s (target <= 2)" % (nf, "y" if nf == 1 else "ies"),
                         "examples": [{"value": k, "uses": len(v), "at": v[0]} for k, v in
                                      sorted(self.families.items(), key=lambda kv: -len(kv[1]))][:top]}
        # type scale
        ns = len(self.sizes)
        h = 1.0 if ns <= 8 else max(0.0, 1 - (ns - 8) / 12.0)
        px_sizes = sorted(float(k[:-2]) for k in self.sizes if re.fullmatch(r"[\d.]+px", k))
        cats["type_scale"] = {"health": h, "measured": ns > 0, "distinct": ns,
                              "scale": [fmt_px(v) for v in px_sizes],
                              "other_units": sorted(k for k in self.sizes if not re.fullmatch(r"[\d.]+px", k)),
                              "summary": "%d distinct sizes (target <= 8)" % ns,
                              "examples": rarest(self.sizes) if ns > 8 else []}
        # colours
        lit = {k: len(v) for k, v in self.colours.items() if not k.startswith("tw:")}
        groups = merge_near(lit, self.near)
        near_groups = [g for g in groups if len(g) > 1]
        neutrals = sorted(f for f in self.tw_families if f in TW_NEUTRALS)
        extra_neutral = max(0, len(neutrals) - 1)
        palette = len(groups) + len([k for k in self.colours if k.startswith("tw:")])
        hues = set()
        for g in groups:
            hg = hue_group(parse_colour(g[0]))
            if hg:
                hues.add(hg)
        for fam in self.tw_families:
            if fam in TW_HUES:
                hues.add(HUE_NAMES[int((TW_HUES[fam] + 15) % 360 // 30)])
        pen = max(0, palette - 16) / 24.0 + 0.05 * (len(near_groups) + extra_neutral) + 0.1 * max(0, len(hues) - 4)
        ex = []
        for g in sorted(near_groups, key=lambda g: -sum(lit[k] for k in g))[:top]:
            ex.append({"near_duplicates": [{"value": k, "uses": lit[k], "at": self.colours[k][0]} for k in g],
                       "max_delta_e": round(max(delta_e(parse_colour(g[0]), parse_colour(k)) for k in g), 2)})
        if extra_neutral:
            ex.append({"mixed_neutral_families": neutrals})
        cats["colours"] = {"health": max(0.0, 1 - pen), "measured": bool(self.colours), "distinct": palette,
                           "near_duplicate_groups": len(near_groups) + extra_neutral,
                           "hue_groups": sorted(hues), "literal_uses_outside_tokens": self.literal_uses,
                           "summary": "%d distinct, %d near-duplicate group%s, %d hue group%s%s" % (
                               palette, len(near_groups) + extra_neutral,
                               "" if len(near_groups) + extra_neutral == 1 else "s", len(hues),
                               "" if len(hues) == 1 else "s",
                               (" (%s)" % ", ".join(sorted(hues))) if hues else ""),
                           "examples": ex}
        # spacing
        judged = len(self.spacing)
        off = [s for s in self.spacing if not s[1]]
        share = len(off) / float(judged) if judged else 0.0
        by_val = defaultdict(list)
        for k, _, loc in off:
            by_val[k].append(loc)
        cats["spacing"] = {"health": max(0.0, 1 - share / 0.4), "measured": judged > 0, "judged": judged,
                           "off_scale": len(off), "unjudged": self.spacing_unjudged,
                           "summary": "%d of %d values off the %gpx grid (%d%%)" % (
                               len(off), judged, self.grid, round(share * 100)),
                           "examples": [{"value": k, "uses": len(v), "at": v[0]} for k, v in
                                        sorted(by_val.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]]}
        # radii / shadows
        nr, nsh = len(self.radii), len(self.shadows)
        hr = 1.0 if nr <= 4 else max(0.0, 1 - (nr - 4) / 6.0)
        hs = 1.0 if nsh <= 4 else max(0.0, 1 - (nsh - 4) / 6.0)
        ex = []
        if nr > 4:
            ex += [dict(e, kind="radius") for e in rarest(self.radii, max(1, top // 2 + 1))]
        if nsh > 4:
            ex += [dict(e, kind="shadow") for e in rarest(self.shadows, max(1, top // 2))]
        cats["radii_shadows"] = {"health": (hr + hs) / 2, "measured": bool(nr or nsh), "radii": nr, "shadows": nsh,
                                 "summary": "%d radii, %d shadows (target <= 4 each)" % (nr, nsh),
                                 "examples": ex}
        # contrast
        checked, fails = self.contrast_results()
        cats["contrast"] = {"health": max(0.0, 1 - len(fails) / 4.0), "measured": checked > 0, "checked": checked,
                            "failing": len(fails),
                            "summary": ("%d of %d same-rule pairs below WCAG AA" % (len(fails), checked))
                            if checked else "no colour/background pairs in the same rule to check",
                            "examples": sorted(fails, key=lambda f: f["ratio"])[:top]}
        # overrides
        rate = len(self.important) * 100.0 / self.decls if self.decls else 0.0
        hi = 1 - min(1.0, rate / 4.0)
        big = [(v, l) for v, l in self.zindex if v > 1000]
        zd = len(set(v for v, _ in self.zindex))
        hz = max(0.0, 1 - 0.25 * len(big) - 0.1 * max(0, zd - 6))
        ex = [{"value": "z-index: %d" % v, "at": l} for v, l in sorted(big, key=lambda x: -x[0])[:top]]
        ex += [{"value": "!important", "at": l} for l in self.important[:max(0, top - len(ex))]]
        cats["overrides"] = {"health": (hi + hz) / 2, "measured": self.decls > 0, "important": len(self.important),
                             "important_per_100": round(rate, 1), "z_over_1000": len(big), "z_distinct": zd,
                             "summary": "%d !important (%.1f per 100 declarations), %d z-index > 1000, "
                                        "%d distinct z-index" % (len(self.important), rate, len(big), zd),
                             "examples": ex}
        # arbitrary
        na = len(self.tw_arbitrary)
        arate = na / float(max(self.tw_utils, 50))
        cats["arbitrary"] = {"health": 1 - min(1.0, arate / 0.08), "measured": self.tw_utils > 0, "count": na,
                             "utilities": self.tw_utils, "layout_arbitrary_not_scored": len(self.tw_layout_arbitrary),
                             "summary": ("%d design-token arbitrary values in %d utilities" % (na, self.tw_utils))
                             if self.tw_utils else "no Tailwind classes found",
                             "examples": [{"value": t, "kind": k, "at": l} for t, k, l in self.tw_arbitrary[:top]]}
        # generic look
        signals, ex = [], []
        pb = [l for l, flag in self.gradients if flag]
        if pb:
            signals.append("purple/blue gradient")
            ex += [{"value": "purple/blue gradient", "at": l} for l in pb[:2]]
        if len(self.gradients) >= 4:
            signals.append("%d gradients" % len(self.gradients))
            ex += [{"value": "gradient", "at": l} for l, flag in self.gradients if not flag][:2]
        if len(self.emoji) >= 3:
            signals.append("%d emoji in markup" % len(self.emoji))
            ex += [{"value": "emoji %s" % ch, "at": l} for ch, l in self.emoji[:2]]
        total_align = sum(self.align.values())
        if self.align["center"] >= 6 and self.align["center"] >= 0.75 * total_align:
            signals.append("%d of %d text-align settings centred" % (self.align["center"], total_align))
            ex += [{"value": "centred text", "at": l} for l in self.center_locs[:1]]
        cats["generic_look"] = {"health": max(0.0, 1 - 0.34 * len(signals)), "measured": True, "signals": signals,
                                "summary": ", ".join(signals) if signals else "no signals",
                                "examples": ex[:top]}
        score = 0.0
        for name, w in WEIGHTS:
            c = cats[name]
            c["max"] = w
            c["points"] = round(w * c["health"], 1)
            c["health"] = round(c["health"], 3)
            score += w * c["health"]
        return int(round(score)), cats


# --- file parsing -----------------------------------------------------------------------------
def mask_css(text, syntax):
    """Returns (orig, masked): comments blanked in both; braces/semicolons inside strings, url() and #{} masked."""
    def blank_m(m):
        return blank(m.group(0))
    text = re.sub(r"/\*.*?\*/", blank_m, text, flags=re.S)
    if syntax in ("scss", "less"):
        text = re.sub(r"(?<![:\w\"'/])//[^\n]*", blank_m, text)
    masked = re.sub(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|url\([^)]*\)|#\{[^}]*\}",
                    lambda m: re.sub(r"[;{}]", " ", m.group(0)), text)
    return text, masked


DARK_SEL = re.compile(r"(^|[\s.,:(\[])dark\b|theme\s*=\s*[\"']?dark|prefers-color-scheme\s*:\s*dark")
DECL_RX = re.compile(r"\s*([$@]?-{0,2}[A-Za-z_][\w-]*)\s*:(?!:)(.*)$", re.S)


def parse_css(text, path, lint, syntax="css"):
    orig, masked = mask_css(text, syntax)
    lines = Lines(orig)
    stack = []
    start = 0
    for m in re.finditer(r"[{};]", masked):
        seg = orig[start:m.start()]
        seg_off = start + (len(seg) - len(seg.lstrip()))
        ch = m.group(0)
        if ch == "{":
            sel = seg.strip()
            stack.append({"_sel": sel.lower(), "_frame": {}})
        else:
            s = seg.strip()
            if s and not (s.startswith("@") and not re.match(r"@[\w-]+\s*:", s)):
                dm = DECL_RX.match(seg)
                if dm and not re.search(r"[{}]", dm.group(2)):
                    in_ff = any(f["_sel"].startswith("@font-face") for f in stack)
                    dark = any(DARK_SEL.search(f["_sel"]) for f in stack)
                    frame = stack[-1]["_frame"] if stack else None
                    lint.decl(dm.group(1), dm.group(2), "%s:%d" % (path, lines.line(seg_off)), frame, in_ff, dark)
            if ch == "}" and stack:
                f = stack.pop()
                lint.rule_done(f["_frame"], "%s:%d" % (path, lines.line(m.start())))
        start = m.end()


def parse_style_attr(body, path, line, lint):
    frame = {}
    for part in body.split(";"):
        if ":" in part:
            prop, val = part.split(":", 1)
            lint.decl(prop, val, "%s:%d" % (path, line), frame)
    lint.rule_done(frame, "%s:%d" % (path, line))


UNITLESS = {"z-index", "font-weight", "line-height", "opacity", "flex", "flex-grow", "flex-shrink", "order"}


def parse_style_object(body, path, line, lint):
    frame = {}
    for m in re.finditer(r"([A-Za-z]+|'[\w-]+'|\"[\w-]+\")\s*:\s*('[^']*'|\"[^\"]*\"|`[^`]*`|-?\d*\.?\d+)", body):
        key = m.group(1).strip("'\"")
        prop = re.sub(r"([A-Z])", lambda x: "-" + x.group(1).lower(), key)
        val = m.group(2)
        if val[0] in "'\"`":
            val = val[1:-1]
            if "${" in val:
                continue
        elif prop not in UNITLESS and val not in ("0",):
            val += "px"
        lint.decl(prop, val, "%s:%d" % (path, line + body.count("\n", 0, m.start())), frame)
    lint.rule_done(frame, "%s:%d" % (path, line))


def balanced(text, i, open_ch, close_ch, limit=4000):
    """Index just past the bracket matching text[i] (which must be open_ch), or -1."""
    depth = 0
    for j in range(i, min(len(text), i + limit)):
        if text[j] == open_ch:
            depth += 1
        elif text[j] == close_ch:
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


STRING_RX = re.compile(r"\"([^\"\n]*)\"|'([^'\n]*)'|`([^`]*)`")


def parse_markup(text, path, lint, ext):
    text = re.sub(r"<!--.*?-->", lambda m: blank(m.group(0)), text, flags=re.S)
    lines = Lines(text)
    # <style> blocks: parsed as CSS with everything else blanked, so line numbers stay right
    for m in re.finditer(r"<style\b([^>]*)>(.*?)</style\s*>", text, re.S | re.I):
        syntax = "scss" if re.search(r"lang\s*=\s*[\"']?(scss|less)", m.group(1), re.I) else "css"
        parse_css(blank(text[:m.start(2)]) + m.group(2), path, lint, syntax)
    text = re.sub(r"(<style\b[^>]*>)(.*?)(</style\s*>)", lambda m: m.group(1) + blank(m.group(2)) + m.group(3),
                  text, flags=re.S | re.I)
    # styled-components / emotion template literals
    for m in STYLED_RX.finditer(text):
        body = re.sub(r"\$\{[^}]*\}", lambda x: blank(x.group(0)), m.group(1))
        css_text = blank(text[:m.start(1)]) + body
        parse_css(css_text, path, lint, "scss")
    text = STYLED_RX.sub(lambda m: blank(m.group(0)), text)
    # inline styles
    for m in re.finditer(r"(?<![\w:.-])(?::|v-bind:)?style\s*=\s*(\"([^\"]*)\"|'([^']*)')", text):
        body = m.group(2) if m.group(2) is not None else m.group(3)
        if body.strip().startswith("{"):
            parse_style_object(body, path, lines.line(m.start()), lint)
        else:
            parse_style_attr(body, path, lines.line(m.start()), lint)
    for m in re.finditer(r"(?<![\w-])style\s*=\s*\{", text):
        end = balanced(text, m.end() - 1, "{", "}")
        if end > 0:
            parse_style_object(text[m.end():end - 1], path, lines.line(m.start()), lint)
    # Tailwind classes: class/className attributes, class: directives and class-merging calls
    spans = []
    for m in re.finditer(r"(?<![\w-])(:class|v-bind:class|class|className|class:list)\s*=\s*", text):
        i = m.end()
        if i < len(text) and text[i] in "\"'":
            j = text.find(text[i], i + 1)
            if j > 0:
                inner = text[i + 1:j]
                if m.group(1) in (":class", "v-bind:class"):
                    for sm in STRING_RX.finditer(inner):
                        spans.append((i + 1 + sm.start(), next(g for g in sm.groups() if g is not None)))
                else:
                    spans.append((i + 1, inner))
        elif i < len(text) and text[i] == "{":
            end = balanced(text, i, "{", "}")
            if end > 0:
                for sm in STRING_RX.finditer(text, i, end):
                    spans.append((sm.start(), next(g for g in sm.groups() if g is not None)))
    for m in CLASS_CALLS.finditer(text):
        end = balanced(text, m.end() - 1, "(", ")")
        if end > 0:
            for sm in STRING_RX.finditer(text, m.end(), end):
                spans.append((sm.start(), next(g for g in sm.groups() if g is not None)))
    for m in re.finditer(r"\bclass:([^\s=>/]+)", text):
        spans.append((m.start(1), m.group(1)))
    seen = set()
    for off, s in sorted(spans):
        if off in seen:
            continue
        seen.add(off)
        lint.tw_string(s, lines.line(off), path)
    # emoji in markup (not in JS-only files)
    if ext in MARKUP_EXTS:
        for m in EMOJI_RX.finditer(text):
            lint.emoji.append((m.group(0), "%s:%d" % (path, lines.line(m.start()))))


# --- walking ----------------------------------------------------------------------------------
def is_minified(text, name):
    if ".min." in name:
        return True
    lines = text.count("\n") + 1
    return len(text) > 2000 and len(text) / float(lines) > 300


def is_vendored_css(text):
    head = text.lstrip()[:400]
    return head.startswith("/*!") and re.search(r"licen[cs]e|\bv?\d+\.\d+|tailwindcss|bootstrap|normalize", head, re.I)


def walk(paths, excludes, include_js):
    exts = CSS_EXTS | MARKUP_EXTS | (JS_EXTS if include_js else set())
    out, skipped = [], Counter()

    def excluded(rel, name):
        return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g) for g in excludes)

    for root_arg in paths:
        if os.path.isfile(root_arg):
            out.append((root_arg, os.path.basename(root_arg)))
            continue
        for root, dirs, files in os.walk(root_arg):
            relroot = os.path.relpath(root, root_arg)
            keep = []
            for d in dirs:
                rel = os.path.normpath(os.path.join(relroot, d))
                if d in SKIP_DIRS or d.startswith(".") and d not in (".storybook",) or excluded(rel, d):
                    skipped["ignored folder"] += 1
                else:
                    keep.append(d)
            dirs[:] = sorted(keep)
            for f in sorted(files):
                ext = os.path.splitext(f)[1].lower()
                if ext not in exts:
                    continue
                rel = os.path.normpath(os.path.join(relroot, f))
                if excluded(rel, f):
                    skipped["--exclude"] += 1
                    continue
                if ".min." in f:
                    skipped["minified"] += 1
                    continue
                out.append((os.path.join(root, f), rel))
    return out, skipped


def lint_paths(paths, excludes=(), grid=4.0, near=3.0, include_js=False):
    """Returns a Lint with everything collected, or None when there was nothing to scan."""
    lint = Lint(grid=grid, near=near)
    files, skipped = walk(paths, excludes, include_js)
    lint.skipped.update(skipped)
    base = paths[0] if len(paths) == 1 and os.path.isdir(paths[0]) else None
    for full, rel in files:
        name = os.path.basename(full)
        ext = os.path.splitext(name)[1].lower()
        try:
            if os.path.getsize(full) > MAX_BYTES:
                lint.skipped["too large"] += 1
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            lint.skipped["unreadable"] += 1
            continue
        if is_minified(text, name):
            lint.skipped["minified"] += 1
            continue
        shown = rel if base else full
        if ext in CSS_EXTS:
            if is_vendored_css(text):
                lint.skipped["vendored (license banner)"] += 1
                continue
            parse_css(text, shown, lint, ext[1:])
            lint.files["css"] += 1
        else:
            parse_markup(text, shown, lint, ext)
            lint.files["markup" if ext in MARKUP_EXTS else "js"] += 1
    if not lint.files:
        return None
    return lint


# --- output -----------------------------------------------------------------------------------
def fmt_example(e):
    if "near_duplicates" in e:
        parts = ["%s (%d)" % (d["value"], d["uses"]) for d in e["near_duplicates"]]
        return "near-duplicates, likely meant to be the same: %s  [dE <= %s]  %s" % (
            " ~ ".join(parts), e["max_delta_e"], ", ".join(d["at"] for d in e["near_duplicates"][:3]))
    if "mixed_neutral_families" in e:
        return "mixed neutral families: %s (pick one grey)" % ", ".join(e["mixed_neutral_families"])
    if "ratio" in e:
        return "%s on %s = %.2f:1 (needs %.1f)  %s" % (e["fg"], e["bg"], e["ratio"], e["needs"], e["loc"])
    label = e.get("kind", "")
    uses = (" x%d" % e["uses"]) if "uses" in e else ""
    return "%s%s%s  %s" % ((label + " ") if label else "", e["value"], uses, e["at"])


def render_text(score, cats, lint, baseline=None):
    out = []
    files = ", ".join("%d %s" % (n, k) for k, n in sorted(lint.files.items()))
    out.append("ui-lint: %s files, %d declarations, %d Tailwind utilities" % (files, lint.decls - lint.tw_utils,
                                                                             lint.tw_utils))
    if lint.skipped:
        out.append("skipped: " + ", ".join("%d %s" % (n, k) for k, n in sorted(lint.skipped.items())))
    head = "Score: %d/100" % score
    if baseline:
        head += "   (was %d, %+d)" % (baseline["score"], score - baseline["score"])
    out.append("")
    out.append(head)
    out.append("")
    for name, w in WEIGHTS:
        c = cats[name]
        delta = ""
        if baseline and name in baseline.get("categories", {}):
            d = c["points"] - baseline["categories"][name]["points"]
            delta = "  (%+.1f)" % d if abs(d) >= 0.05 else ""
        na = "" if c["measured"] else "  n/a"
        out.append("  %-16s %5.1f/%-3d %s%s%s" % (LABELS[name], c["points"], w, c["summary"], na, delta))
    issues = [(name, c) for name, c in sorted(cats.items(), key=lambda kv: kv[1]["points"] - kv[1]["max"])
              if c["examples"] and c["health"] < 1]
    if issues:
        out.append("")
        out.append("Top issues (biggest point loss first)")
        for name, c in issues:
            out.append("  %s (-%.1f)" % (LABELS[name], c["max"] - c["points"]))
            for e in c["examples"]:
                out.append("    " + fmt_example(e))
    if cats["type_scale"]["scale"]:
        out.append("")
        out.append("Type sizes in use: " + " ".join(cats["type_scale"]["scale"] + cats["type_scale"]["other_units"]))
    out.append("")
    out.append("Regex scan, not a renderer: check the page itself before and after changes.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="\n".join(__doc__.split("\n")[1:]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter, usage=argparse.SUPPRESS)
    ap.add_argument("paths", nargs="*", default=["."])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-below", type=float, default=None)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--grid", type=float, default=4.0)
    ap.add_argument("--near", type=float, default=3.0)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--include-js", action="store_true")
    a = ap.parse_args(argv)
    excludes = [g.strip() for e in a.exclude for g in e.split(",") if g.strip()]
    missing = [p for p in a.paths if not os.path.exists(p)]
    if missing:
        print("ui-lint: no such path: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    if a.grid <= 0:
        print("ui-lint: --grid must be positive", file=sys.stderr)
        return 2
    lint = lint_paths(a.paths, excludes, a.grid, a.near, a.include_js)
    if lint is None or (lint.decls == 0 and not lint.emoji):
        print("ui-lint: nothing to scan (no CSS/SCSS/Less, markup with inline styles, or Tailwind classes found)",
              file=sys.stderr)
        return 2
    score, cats = lint.report(top=a.top)
    baseline = None
    if a.baseline:
        try:
            with open(a.baseline, encoding="utf-8") as fh:
                baseline = json.load(fh)
            if not isinstance(baseline, dict) or "score" not in baseline:
                raise ValueError("not a ui_lint --json file")
        except (OSError, ValueError) as e:
            print("ui-lint: cannot read --baseline: %s" % e, file=sys.stderr)
            return 2
    if a.json:
        result = {"schema": SCHEMA_VERSION, "score": score, "categories": cats, "files": dict(lint.files),
                  "skipped": dict(lint.skipped), "declarations": lint.decls - lint.tw_utils,
                  "tailwind_utilities": lint.tw_utils, "grid": a.grid, "near_delta_e": a.near}
        if baseline:
            result["baseline"] = {"score": baseline["score"], "delta": score - baseline["score"]}
        print(json.dumps(result, indent=2))
    else:
        print(render_text(score, cats, lint, baseline))
    if a.fail_below is not None and score < a.fail_below:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
