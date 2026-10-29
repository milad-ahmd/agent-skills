#!/usr/bin/env python3
"""Plan and script real demo recordings: VHS tapes for CLIs, Playwright videos for web apps. Stdlib only.

Usage:
  make_demo.py check [--os mac|linux|windows] [--json]
  make_demo.py tape [STEPS] [--cmd CMD ...] [-o demo.tape] [--out demo.gif ...] [--sh demo.sh] [options]
  make_demo.py lint TAPE
  make_demo.py web STEPS --url URL [-o demo.web.js] [--video-dir DIR] [--viewport 1280x720] [options]
  make_demo.py optimize INPUT [--to gif|mp4|webm] [--width 1000] [--fps 12] [-o OUT] [--run]

check     lists the recording tools on PATH, which routes work and how to install the rest. Never installs.
tape      writes a VHS .tape from a steps file (one shell command per line, plus # directives) or --cmd args.
          --sh also writes a bash script that types and runs the same steps, for `asciinema rec -c`.
lint      checks an existing .tape against the VHS command grammar.
web       writes a Node script using the `playwright` library that records the steps as .webm, and prints
          the ffmpeg commands to turn it into a GIF and an MP4.
optimize  prints (or with --run, executes) ffmpeg/gifsicle commands that shrink a GIF or video.

Steps file directives (tape): # sleep 2s · # hide · # show · # type-speed 40ms|default · # wait [/regex/]
  · # key Ctrl+C|Down 3|Enter · any other line starting with # is a comment.
Steps file (web): goto URL · click SEL · hover SEL · fill SEL TEXT · type SEL TEXT · press KEY · wait MS
  · wait-for SEL · scroll PX. Quote a selector that contains spaces: fill "input[name=q]" hello world

Exit codes: 0 ok · 1 check: no route works / optimize: tool missing or command failed / lint: problems found
            · 2 bad input (unreadable file, unquotable command, bad step)
"""
import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urljoin

# --- tool detection -------------------------------------------------------------------
TOOLS = ["vhs", "ttyd", "ffmpeg", "gifsicle", "asciinema", "agg", "node", "npx", "playwright"]

INSTALL = {
    "vhs": {"mac": "brew install vhs", "linux": "go install github.com/charmbracelet/vhs@latest",
            "windows": "winget install charmbracelet.vhs"},
    "ttyd": {"mac": "brew install ttyd", "windows": "scoop install ttyd",
             "linux": "sudo apt install ttyd  # if too old for VHS, use the binary from github.com/tsl0922/ttyd/releases"},
    "ffmpeg": {"mac": "brew install ffmpeg", "linux": "sudo apt install ffmpeg", "windows": "winget install Gyan.FFmpeg"},
    "gifsicle": {"mac": "brew install gifsicle", "linux": "sudo apt install gifsicle", "windows": "scoop install gifsicle"},
    "asciinema": {"mac": "brew install asciinema", "linux": "sudo apt install asciinema", "windows": "(not supported; use WSL)"},
    "agg": {"mac": "brew install agg", "linux": "cargo install --git https://github.com/asciinema/agg",
            "windows": "cargo install --git https://github.com/asciinema/agg"},
    "node": {"mac": "brew install node", "linux": "sudo apt install nodejs npm", "windows": "winget install OpenJS.NodeJS.LTS"},
    "npx": {"mac": "brew install node", "linux": "sudo apt install nodejs npm", "windows": "winget install OpenJS.NodeJS.LTS"},
    "playwright": {k: "npm install --save-dev playwright && npx playwright install chromium" for k in ("mac", "linux", "windows")},
}

ROUTES = [
    ("terminal-vhs", "CLI demo from a .tape (best quality, scriptable)", ["vhs", "ttyd", "ffmpeg"]),
    ("terminal-asciinema", "CLI demo via asciinema rec + agg (fallback)", ["asciinema", "agg"]),
    ("browser-playwright", "web app demo via a Playwright script (.webm; ffmpeg for GIF/MP4)", ["node", "playwright"]),
]


def host_os():
    s = platform.system()
    return "mac" if s == "Darwin" else "windows" if s == "Windows" else "linux"


def playwright_importable():
    """True if `require('playwright')` resolves from the current directory (a local devDependency)."""
    node = shutil.which("node")
    if not node:
        return False
    try:
        r = subprocess.run([node, "-e", "require.resolve('playwright')"], capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect():
    found = {t: bool(shutil.which(t)) for t in TOOLS}
    if not found["playwright"]:
        found["playwright"] = playwright_importable()
    return found


def cmd_check(args):
    osname = args.os or host_os()
    found = detect()
    routes = [{"route": name, "what": what, "needs": needs, "ok": all(found[n] for n in needs),
               "missing": [n for n in needs if not found[n]]} for name, what, needs in ROUTES]
    missing = [t for t in TOOLS if not found[t]]
    install = {t: INSTALL[t][osname] for t in missing}
    ok = any(r["ok"] for r in routes)
    if args.json:
        print(json.dumps({"os": osname, "tools": found, "routes": routes, "install": install, "ok": ok}, indent=2))
        return 0 if ok else 1
    print("Tools (%s):" % osname)
    for t in TOOLS:
        print("  %-11s %s" % (t, "found" if found[t] else "missing"))
    print("\nRoutes:")
    for r in routes:
        state = "ready" if r["ok"] else "needs " + ", ".join(r["missing"])
        print("  %-19s %s  [%s]" % (r["route"], r["what"], state))
    if found["playwright"] and not found["ffmpeg"]:
        print("  note: without ffmpeg the browser route stops at .webm")
    if not found["gifsicle"]:
        print("  note: gifsicle is optional; it usually shrinks GIFs further after ffmpeg")
    if install:
        print("\nTo install (not run, ask the user first):")
        seen = set()
        for t, c in install.items():
            if c not in seen:
                seen.add(c)
                print("  %-11s %s" % (t, c))
        if osname == "mac" and "vhs" in install:
            print("  (brew install vhs also pulls in ttyd and ffmpeg)")
    print("\n" + ("At least one route works." if ok else "No route works yet. Install the tools for one route above."))
    return 0 if ok else 1


# --- VHS tape -------------------------------------------------------------------------
DUR = r"\d+(?:\.\d+)?(?:ms|s|m)?"
DUR_RX = re.compile(r"^%s$" % DUR)
KEYS = ("Enter", "Backspace", "Delete", "Insert", "Tab", "Space", "Up", "Down", "Left", "Right",
        "PageUp", "PageDown", "Escape")
KEY_RX = re.compile(r"^(?:%s|(?:Ctrl|Alt|Shift)(?:\+(?:Ctrl|Alt|Shift))*\+\S+)(?:@%s)?(?:\s+\d+)?$" % ("|".join(KEYS), DUR))
OUT_EXT = (".gif", ".mp4", ".webm")


class StepError(ValueError):
    pass


def vhs_string(s):
    """Quote s for a VHS Type line. VHS strings have no escapes, so pick a delimiter s doesn't contain."""
    if "\n" in s or "\r" in s:
        raise StepError("command contains a newline; put one command per line")
    for d in ('"', "'", "`"):
        if d not in s:
            return d + s + d
    raise StepError("command contains \", ' and ` so VHS cannot type it in one string. "
                    "Move it into a small script (e.g. demo/step.sh) and type `./demo/step.sh` instead: %s" % s)


def parse_tape_steps(lines):
    """Turn steps-file lines into ('cmd'|'sleep'|'hide'|'show'|'speed'|'wait'|'key'|'comment', value) tuples."""
    steps = []
    for n, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        s = line.strip()
        if not s:
            continue
        if not s.startswith("#"):
            vhs_string(line.strip())  # fail early on unquotable lines
            steps.append(("cmd", line.strip()))
            continue
        body = s.lstrip("#").strip()
        word, _, rest = body.partition(" ")
        word, rest = word.lower(), rest.strip()
        if word == "sleep":
            if not DUR_RX.match(rest):
                raise StepError("line %d: '# sleep' needs a duration like 2s or 500ms" % n)
            steps.append(("sleep", rest))
        elif word in ("hide", "show") and not rest:
            steps.append((word, ""))
        elif word == "type-speed":
            if rest != "default" and not DUR_RX.match(rest):
                raise StepError("line %d: '# type-speed' needs a duration like 40ms, or 'default'" % n)
            steps.append(("speed", "" if rest == "default" else rest))
        elif word == "wait":
            if rest and not re.match(r"^/.+/$", rest):
                raise StepError("line %d: '# wait' takes an optional /regex/" % n)
            steps.append(("wait", rest))
        elif word == "key":
            if not KEY_RX.match(rest):
                raise StepError("line %d: '# key' needs a VHS key such as Enter, Down 3, Ctrl+C" % n)
            steps.append(("key", rest))
        else:
            steps.append(("comment", body))
    return steps


def build_tape(steps, opts):
    for o in opts.out:
        if not o.endswith(OUT_EXT) or re.search(r"[\s\"'`]", o):
            raise StepError("output %r must end in .gif/.mp4/.webm and contain no spaces or quotes" % o)
    t = ["# Record: vhs %s" % (opts.tape_name or "demo.tape"),
         "# Edit the steps below and re-run vhs after the tool changes.", ""]
    t += ["Output %s" % o for o in opts.out]
    t += ["Require %s" % r for r in opts.require]
    t += ["", "Set Shell %s" % vhs_string(opts.shell), "Set FontSize %d" % opts.font_size,
          "Set Width %d" % opts.width, "Set Height %d" % opts.height, "Set Padding %d" % opts.padding,
          "Set Framerate %d" % opts.framerate, "Set TypingSpeed %s" % opts.typing_speed]
    if opts.theme:
        t.append("Set Theme %s" % vhs_string(opts.theme))
    if opts.prompt and opts.wait:
        t.append("Set WaitPattern /%s$/" % re.escape(opts.prompt.strip()).replace("/", "\\/"))
    t.append("")
    if opts.fake_home or opts.prompt:
        t.append("Hide")
        if opts.fake_home:
            t += ["Type %s" % vhs_string('export HOME="$(mktemp -d)"'), "Enter"]
        if opts.prompt:
            t += ["Type %s" % vhs_string("export PS1=%s" % shlex.quote(opts.prompt)), "Enter"]
        t += ["Type \"clear\"", "Enter", "Sleep 200ms", "Show", ""]
    hidden, speed, hidden_ran = False, "", False
    for kind, val in steps:
        if kind == "cmd":
            typ = "Type@%s" % speed if speed and not hidden else "Type"
            t += ["%s %s" % (typ, vhs_string(val))]
            if hidden:
                t += ["Enter", "Wait" if opts.wait else "Sleep 500ms"]
                hidden_ran = True
            else:
                t += ["Sleep 400ms", "Enter"] + (["Wait"] if opts.wait else []) + ["Sleep %s" % opts.pause, ""]
        elif kind == "sleep":
            t.append("Sleep %s" % val)
        elif kind == "hide" and not hidden:
            hidden = True
            t.append("Hide")
        elif kind == "show" and hidden:
            hidden = False
            if hidden_ran:
                t += ["Type \"clear\"", "Enter", "Sleep 200ms"]
            t += ["Show", ""]
            hidden_ran = False
        elif kind == "speed":
            speed = val
        elif kind == "wait":
            t.append("Wait" + (" " + val if val else ""))
        elif kind == "key":
            t.append(val)
        elif kind == "comment":
            t.append("# " + val if val else "#")
    if hidden:
        t.append("Show")
    if t[-1] != "":
        t.append("")
    t.append("Sleep %s" % opts.end_pause)
    return "\n".join(t) + "\n"


SETTINGS = {
    "Shell": "str", "FontFamily": "str", "Theme": "theme", "MarginFill": "str", "WaitPattern": "regex",
    "FontSize": "num", "Framerate": "num", "Height": "num", "Width": "num", "Padding": "num", "Margin": "num",
    "LetterSpacing": "num", "LineHeight": "num", "PlaybackSpeed": "num", "WindowBarSize": "num",
    "BorderRadius": "num", "LoopOffset": "num", "TypingSpeed": "dur", "WaitTimeout": "dur",
    "CursorBlink": "bool", "WindowBar": "word",
}
STR = r"(?:\"[^\"]*\"|'[^']*'|`[^`]*`)"
VALUE_RX = {
    "str": re.compile(r"^(?:%s|\S+)$" % STR), "theme": re.compile(r"^(?:%s|\S+|\{.*\})$" % STR),
    "regex": re.compile(r"^/.*/$"), "num": re.compile(r"^\d+(?:\.\d+)?%?$"), "dur": DUR_RX,
    "bool": re.compile(r"^(?:true|false)$"), "word": re.compile(r"^\w+$"),
}
LINE_RX = [
    re.compile(r"^Output\s+(?:\S+\.(?:gif|mp4|webm|ascii|txt)|\S+/)$"),
    re.compile(r"^Require\s+\S+$"),
    re.compile(r"^Type(?:@%s)?\s+%s$" % (DUR, STR)),
    re.compile(r"^Sleep\s+%s$" % DUR),
    re.compile(r"^(?:Hide|Show|Copy|Paste)$"),
    re.compile(r"^Wait(?:\+(?:Screen|Line))?(?:@%s)?(?:\s+/.+/)?$" % DUR),
    re.compile(r"^Screenshot\s+\S+\.png$"),
    re.compile(r"^Source\s+\S+\.tape$"),
    re.compile(r"^Env\s+\w+\s+%s$" % STR),
    KEY_RX,
]


def lint_tape(text):
    """Return a list of 'line N: problem' strings; empty means the tape looks valid."""
    problems, outputs, started = [], 0, False
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("Set "):
            m = re.match(r"^Set\s+(\w+)\s+(.+)$", s)
            if not m or m.group(1) not in SETTINGS:
                problems.append("line %d: unknown setting: %s" % (n, s))
            elif not VALUE_RX[SETTINGS[m.group(1)]].match(m.group(2).strip()):
                problems.append("line %d: bad value for %s: %s" % (n, m.group(1), m.group(2)))
            elif started and m.group(1) != "TypingSpeed":
                problems.append("line %d: VHS ignores Set %s after the first command; move it to the top" % (n, m.group(1)))
            continue
        if not any(rx.match(s) for rx in LINE_RX):
            problems.append("line %d: not a VHS command (check quoting): %s" % (n, s))
            continue
        if s.startswith("Output"):
            outputs += 1
        elif not s.startswith("Require"):
            started = True
    if not outputs:
        problems.append("no Output line: VHS would record to out.gif")
    return problems


def sh_seconds(d):
    m = re.match(r"^(\d+(?:\.\d+)?)(ms|s|m)?$", d)
    v, unit = float(m.group(1)), m.group(2) or "s"
    v = v / 1000 if unit == "ms" else v * 60 if unit == "m" else v
    return ("%.3f" % v).rstrip("0").rstrip(".")


def build_sh(steps, opts):
    """Bash script that types each command, then runs it for real (for asciinema rec -c)."""
    delay = sh_seconds(opts.typing_speed)
    q = shlex.quote
    out = ["#!/usr/bin/env bash",
           "# Record: asciinema rec demo.cast --overwrite -c 'bash %s'" % (opts.sh_name or "demo.sh"),
           "# Render: agg --font-size %d demo.cast demo.gif" % opts.font_size,
           "PROMPT=%s" % q(opts.prompt or "$ "), "DELAY=%s" % delay,
           'show() { local s="$1" d="${2:-$DELAY}" i; printf \'%s\' "$PROMPT"; '
           'for ((i=0; i<${#s}; i++)); do printf \'%s\' "${s:i:1}"; sleep "$d"; done; sleep 0.4; printf \'\\n\'; }']
    if opts.fake_home:
        out.append('export HOME="$(mktemp -d)"')
    out.append("clear")
    hidden, speed = False, ""
    for kind, val in steps:
        if kind == "cmd":
            if hidden:
                out.append("eval %s >/dev/null 2>&1" % q(val))
            else:
                out += ["show %s%s" % (q(val), " " + sh_seconds(speed) if speed else ""),
                        "eval %s" % q(val), "sleep %s" % sh_seconds(opts.pause)]
        elif kind == "sleep":
            out.append("sleep %s" % sh_seconds(val))
        elif kind == "hide":
            hidden = True
        elif kind == "show":
            if hidden:
                out.append("clear")
            hidden = False
        elif kind == "speed":
            speed = val
        elif kind in ("key", "wait"):
            out.append("# %s %s: not supported outside VHS" % (kind, val))
        elif kind == "comment":
            out.append("# " + val)
    out += ['printf \'%s\' "$PROMPT"', "sleep %s" % sh_seconds(opts.end_pause)]
    return "\n".join(out) + "\n"


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError as e:
        raise StepError("cannot read %s: %s" % (path, e))


def write(path, text, mode=0o644):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, mode)


MEDIA_EXT = (".gif", ".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg")


def check_script_path(path, kind, hint):
    # -o is the tape/script, not the recording; writing text over demo.gif would destroy the real demo
    if path and path.lower().endswith(MEDIA_EXT):
        raise StepError("-o %s is where the %s goes, not the recording; %s" % (path, kind, hint))


def cmd_tape(args):
    check_script_path(args.output, "tape", "use --out %s for the recording and -o demo.tape for the tape" % args.output)
    lines = read_lines(args.steps) if args.steps else []
    lines += args.cmd or []
    steps = parse_tape_steps(lines)
    if not any(k == "cmd" for k, _ in steps):
        raise StepError("no commands: pass a steps file or --cmd")
    for d in (args.typing_speed, args.pause, args.end_pause):
        if not DUR_RX.match(d):
            raise StepError("bad duration %r (use 2s or 500ms)" % d)
    args.out = args.out or ["demo.gif"]
    args.tape_name = args.output
    tape = build_tape(steps, args)
    problems = lint_tape(tape)
    if problems:
        raise StepError("generated tape failed validation:\n  " + "\n  ".join(problems))
    if args.output:
        write(args.output, tape)
        print("wrote %s -> record with: vhs %s" % (args.output, args.output), file=sys.stderr)
    else:
        sys.stdout.write(tape)
    if args.sh:
        args.sh_name = args.sh
        write(args.sh, build_sh(steps, args), 0o755)
        print("wrote %s -> asciinema rec demo.cast --overwrite -c 'bash %s' && agg demo.cast demo.gif"
              % (args.sh, args.sh), file=sys.stderr)
    return 0


def cmd_lint(args):
    problems = lint_tape("\n".join(read_lines(args.tape)))
    for p in problems:
        print(p)
    if not problems:
        print("%s: ok" % args.tape)
    return 1 if problems else 0


# --- Playwright web demo --------------------------------------------------------------
SEL_RX = re.compile(r"""^("(?:[^"\\]|\\.)*"|'[^']*'|\S+)(?:\s+(.*))?$""")
WEB_VERBS = {"goto": "url", "click": "sel", "hover": "sel", "wait-for": "sel", "fill": "sel+text",
             "type": "sel+text", "press": "key", "wait": "int", "scroll": "int"}


def unquote(s):
    """Strip one pair of surrounding quotes; anything else (e.g. "a" and "b") stays literal."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        parts = shlex.split(s)
        if len(parts) == 1:
            return parts[0]
    return s


def parse_web_steps(lines):
    steps = []
    for n, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        verb, _, rest = s.partition(" ")
        verb, rest = verb.lower(), rest.strip()
        kind = WEB_VERBS.get(verb)
        if not kind:
            raise StepError("line %d: unknown step %r (use %s)" % (n, verb, ", ".join(sorted(WEB_VERBS))))
        if not rest:
            raise StepError("line %d: %s needs an argument" % (n, verb))
        if kind == "int":
            if not re.match(r"^-?\d+$", rest):
                raise StepError("line %d: %s needs a whole number, got %r" % (n, verb, rest))
            steps.append((verb, int(rest)))
        elif kind == "sel+text":
            m = SEL_RX.match(rest)
            try:
                sel = unquote(m.group(1))
            except ValueError:
                raise StepError("line %d: unbalanced quotes in selector" % n)
            if m.group(2) is None:
                raise StepError("line %d: %s needs a selector and text" % (n, verb))
            steps.append((verb, sel, unquote(m.group(2).strip())))
        elif kind == "sel":
            steps.append((verb, unquote(rest)))
        else:
            steps.append((verb, rest))
    return steps


CURSOR_JS = r"""
  // Headless video has no mouse pointer, so draw one that follows the mouse.
  await context.addInitScript(() => {
    const draw = () => {
      if (document.getElementById('__demo_cursor')) return;
      const c = document.createElement('div');
      c.id = '__demo_cursor';
      c.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;width:18px;height:18px;' +
        'margin:-9px 0 0 -9px;border-radius:50%;background:rgba(255,80,80,.55);border:2px solid #fff;' +
        'left:-40px;top:-40px;transition:transform .1s';
      document.documentElement.appendChild(c);
      addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
      addEventListener('mousedown', () => { c.style.transform = 'scale(.7)'; }, true);
      addEventListener('mouseup', () => { c.style.transform = ''; }, true);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', draw); else draw();
  });
"""


def js(v):
    """A JS literal for v: json.dumps escapes quotes, backslashes, control chars and U+2028/9 (ASCII-only)."""
    return json.dumps(v)


def build_web(steps, opts):
    w, h = opts.vw, opts.vh
    pause = opts.pause
    body = []
    for st in steps:
        verb = st[0]
        body.append("    // %s" % js(" ".join(str(x) for x in st)))
        if verb == "goto":
            body.append("    await page.goto(%s, { waitUntil: 'load' });" % js(urljoin(opts.url, st[1])))
        elif verb in ("click", "hover"):
            body.append("    await moveTo(%s);" % js(st[1]))
            body.append("    await page.%s(%s);" % (verb, js(st[1])))
        elif verb == "wait-for":
            body.append("    await page.waitForSelector(%s, { state: 'visible' });" % js(st[1]))
        elif verb == "fill":
            body.append("    await moveTo(%s);" % js(st[1]))
            body.append("    await page.fill(%s, %s);" % (js(st[1]), js(st[2])))
        elif verb == "type":
            body.append("    await moveTo(%s);" % js(st[1]))
            body.append("    await page.click(%s);" % js(st[1]))
            body.append("    await page.keyboard.type(%s, { delay: %d });" % (js(st[2]), opts.type_delay))
        elif verb == "press":
            body.append("    await page.keyboard.press(%s);" % js(st[1]))
        elif verb == "wait":
            body.append("    await page.waitForTimeout(%d);" % st[1])
            continue
        elif verb == "scroll":
            body.append("    await page.mouse.wheel(0, %d);" % st[1])
        body.append("    await page.waitForTimeout(PAUSE);")
    move = ("""  const moveTo = async (sel) => {
    const box = await page.locator(sel).first().boundingBox();
    if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 });
  };""" if opts.cursor else "  const moveTo = async () => {};")
    return """// Records a demo of %(url)s. Generated by make_demo.py web; edit the steps and re-run.
// Run: node %(name)s   (needs: npm install --save-dev playwright && npx playwright install chromium)
const { chromium } = require('playwright');
const path = require('path');

const VIDEO_DIR = %(video_dir)s;
const OUT = path.join(VIDEO_DIR, %(video_name)s);
const VIEWPORT = { width: %(w)d, height: %(h)d };
const PAUSE = %(pause)d;

(async () => {
  const browser = await chromium.launch({ headless: %(headless)s });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: %(scheme)s,
    recordVideo: { dir: VIDEO_DIR, size: VIEWPORT },
  });
%(cursor)s  const page = await context.newPage();
%(move)s
  try {
    await page.goto(%(start)s, { waitUntil: 'load' });
    await page.waitForTimeout(1000);
%(body)s
    await page.waitForTimeout(1500);
  } finally {
    const video = page.video();
    await page.close();
    if (video) {
      await video.saveAs(OUT);
      await video.delete();
    }
    await context.close();
    await browser.close();
  }
  console.log(OUT);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
""" % {"url": opts.url.replace("\n", " "), "name": opts.output or "demo.web.js", "video_dir": js(opts.video_dir),
       "video_name": js(opts.video_name), "w": w, "h": h, "pause": pause,
       "headless": "false" if opts.headed else "true", "scheme": js(opts.color_scheme),
       "cursor": CURSOR_JS if opts.cursor else "", "move": move, "start": js(opts.url), "body": "\n".join(body)}


def cmd_web(args):
    check_script_path(args.output, "Playwright script", "use -o demo.web.js; the video goes to --video-dir")
    m = re.match(r"^(\d{2,5})x(\d{2,5})$", args.viewport)
    if not m:
        raise StepError("--viewport must look like 1280x720")
    args.vw, args.vh = int(m.group(1)), int(m.group(2))
    if not re.match(r"^https?://", args.url):
        raise StepError("--url must start with http:// or https://")
    steps = parse_web_steps(read_lines(args.steps))
    script = build_web(steps, args)
    if args.output:
        write(args.output, script)
        print("wrote %s -> run: node %s" % (args.output, args.output))
    else:
        sys.stdout.write(script)
    webm = os.path.join(args.video_dir, args.video_name)
    stem = os.path.splitext(webm)[0]
    out = sys.stderr if not args.output else sys.stdout
    print("\nThen convert (keep the .webm until you are happy with the result):", file=out)
    for c in plan_optimize(webm, "gif", args.gif_width, args.fps, stem + ".gif", palette=stem + ".palette.png"):
        print("  " + shlex.join(c), file=out)
    print("  rm " + shlex.quote(stem + ".palette.png"), file=out)
    for c in plan_optimize(webm, "mp4", args.vw, 30, stem + ".mp4"):
        print("  " + shlex.join(c), file=out)
    return 0


# --- optimize -------------------------------------------------------------------------
def gif_filters(width, fps):
    return "fps=%d,scale=%d:-1:flags=lanczos" % (fps, width)


def plan_optimize(src, to, width, fps, out, palette=None, lossy=80, colors=128, have=None):
    """Return a list of argv lists. `have` limits the plan to available tools (None = assume all)."""
    have = have if have is not None else {"ffmpeg": True, "gifsicle": True}
    cmds = []
    if to == "gif":
        if have.get("ffmpeg"):
            palette = palette or os.path.splitext(out)[0] + ".palette.png"
            f = gif_filters(width, fps)
            cmds.append(["ffmpeg", "-y", "-i", src, "-vf", f + ",palettegen=max_colors=%d:stats_mode=diff" % colors, palette])
            cmds.append(["ffmpeg", "-y", "-i", src, "-i", palette, "-lavfi",
                         f + "[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", out])
            if have.get("gifsicle"):
                cmds.append(["gifsicle", "-O3", "--lossy=%d" % lossy, "--colors", str(colors), "-b", out])
        elif have.get("gifsicle") and src.lower().endswith(".gif"):
            cmds.append(["gifsicle", "-O3", "--lossy=%d" % lossy, "--colors", str(colors),
                         "--resize-width", str(width), src, "-o", out])
    elif to == "mp4" and have.get("ffmpeg"):
        cmds.append(["ffmpeg", "-y", "-i", src, "-vf", "fps=%d,scale=%d:-2:flags=lanczos" % (fps, width),
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28", "-preset", "slow",
                     "-movflags", "+faststart", "-an", out])
    elif to == "webm" and have.get("ffmpeg"):
        cmds.append(["ffmpeg", "-y", "-i", src, "-vf", "fps=%d,scale=%d:-2:flags=lanczos" % (fps, width),
                     "-c:v", "libvpx-vp9", "-crf", "40", "-b:v", "0", "-an", out])
    return cmds


def human(n):
    return "%.2f MB" % (n / 1e6) if n >= 1e5 else "%d KB" % (n // 1000)


def cmd_optimize(args):
    src = args.input
    if not os.path.isfile(src):
        raise StepError("no such file: %s" % src)
    ext = os.path.splitext(src)[1].lower().lstrip(".")
    to = args.to or (ext if ext in ("gif", "mp4", "webm") else "gif")
    out = args.output or "%s.min.%s" % (os.path.splitext(src)[0], to)
    if os.path.abspath(out) == os.path.abspath(src):
        raise StepError("output must differ from input")
    have = {"ffmpeg": bool(shutil.which("ffmpeg")), "gifsicle": bool(shutil.which("gifsicle"))}
    plan_have = have if args.run else None
    tmp = tempfile.mkdtemp(prefix="demo-palette-") if args.run else None
    palette = os.path.join(tmp, "palette.png") if tmp else None
    cmds = plan_optimize(src, to, args.width, args.fps, out, palette=palette, lossy=args.lossy,
                         colors=args.colors, have=plan_have)
    if not cmds:
        print("cannot make a .%s here: needs ffmpeg%s" % (to, " (or gifsicle for GIF input)" if to == "gif" else ""))
        return 1
    if not args.run:
        for c in cmds:
            print(shlex.join(c))
        lacking = sorted({c[0] for c in cmds if not have.get(c[0])})
        if lacking:
            print("# not installed here: %s (see: make_demo.py check)" % ", ".join(lacking))
        if to == "gif":
            print("# the palette PNG is temporary; delete it afterwards")
        return 0
    if to == "gif" and not have["ffmpeg"]:
        print("ffmpeg missing: only resizing with gifsicle, frame rate unchanged", file=sys.stderr)
    try:
        for c in cmds:
            print("$ " + shlex.join(c), file=sys.stderr)
            r = subprocess.run(c, capture_output=True, text=True)
            if r.returncode:
                print(r.stderr[-2000:], file=sys.stderr)
                return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    before, after = os.path.getsize(src), os.path.getsize(out)
    print("%s: %s -> %s: %s (%d%% of original)" % (src, human(before), out, human(after), 100 * after // max(before, 1)))
    if after > 10e6:
        print("still over 10 MB: GitHub will not accept it as an image upload. Lower --width/--fps or shorten the demo.")
    elif after > args.max_mb * 1e6:
        print("over %g MB: try --fps 10, --width 900, --lossy 120, or ship an MP4 instead." % args.max_mb)
    return 0


# --- CLI ------------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description="Script real demo recordings (VHS, asciinema, Playwright).")
    sub = p.add_subparsers(dest="command")
    sub.required = True

    c = sub.add_parser("check", help="list recording tools and working routes")
    c.add_argument("--os", choices=["mac", "linux", "windows"], help="install commands for this OS (default: this host)")
    c.add_argument("--json", action="store_true")

    t = sub.add_parser("tape", help="write a VHS .tape from steps")
    t.add_argument("steps", nargs="?", help="steps file: one command per line, # directives")
    t.add_argument("--cmd", action="append", help="a command (repeatable; appended after the steps file)")
    t.add_argument("-o", "--output", help="write the tape here (default: stdout)")
    t.add_argument("--out", action="append", help="recording path(s) for VHS: .gif/.mp4/.webm (repeatable; default demo.gif)")
    t.add_argument("--sh", help="also write a bash script of the same steps for asciinema rec -c")
    t.add_argument("--width", type=int, default=1200)
    t.add_argument("--height", type=int, default=600)
    t.add_argument("--font-size", type=int, default=22)
    t.add_argument("--theme", help='VHS theme name, e.g. "Catppuccin Mocha" (list with: vhs themes)')
    t.add_argument("--padding", type=int, default=20)
    t.add_argument("--framerate", type=int, default=24)
    t.add_argument("--typing-speed", default="50ms")
    t.add_argument("--pause", default="2s", help="pause after each command's output (default 2s)")
    t.add_argument("--end-pause", default="3s", help="hold the last frame (default 3s)")
    t.add_argument("--shell", default="bash")
    t.add_argument("--require", action="append", default=[], help="program VHS must find before recording (repeatable)")
    t.add_argument("--no-wait", dest="wait", action="store_false",
                   help="don't emit Wait after commands (for VHS older than 0.9; fixed sleeps only)")
    t.add_argument("--fake-home", action="store_true", help="hidden setup: point HOME at an empty temp dir")
    t.add_argument("--prompt", help="hidden setup: export PS1 to this (VHS already starts bash with '> ')")

    li = sub.add_parser("lint", help="check a .tape against the VHS grammar")
    li.add_argument("tape")

    w = sub.add_parser("web", help="write a Playwright recording script")
    w.add_argument("steps")
    w.add_argument("--url", required=True, help="start URL; relative goto steps resolve against it")
    w.add_argument("-o", "--output", help="write the script here (default: stdout)")
    w.add_argument("--video-dir", default="demo-video")
    w.add_argument("--video-name", default="demo.webm")
    w.add_argument("--viewport", default="1280x720")
    w.add_argument("--pause", type=int, default=800, help="ms to hold after each step")
    w.add_argument("--type-delay", type=int, default=60, help="ms between keystrokes for type steps")
    w.add_argument("--color-scheme", choices=["light", "dark"], default="light")
    w.add_argument("--headed", action="store_true")
    w.add_argument("--no-cursor", dest="cursor", action="store_false", help="don't draw a visible mouse pointer")
    w.add_argument("--gif-width", type=int, default=1000)
    w.add_argument("--fps", type=int, default=12)

    o = sub.add_parser("optimize", help="shrink a GIF or video")
    o.add_argument("input")
    o.add_argument("--to", choices=["gif", "mp4", "webm"])
    o.add_argument("-o", "--output")
    o.add_argument("--width", type=int, default=1000)
    o.add_argument("--fps", type=int, default=12)
    o.add_argument("--lossy", type=int, default=80, help="gifsicle --lossy level (30-200; higher = smaller)")
    o.add_argument("--colors", type=int, default=128)
    o.add_argument("--max-mb", type=float, default=5.0, help="warn when the result is bigger (default 5)")
    o.add_argument("--run", action="store_true", help="execute the commands instead of printing them")

    args = p.parse_args(argv)
    handler = {"check": cmd_check, "tape": cmd_tape, "lint": cmd_lint, "web": cmd_web, "optimize": cmd_optimize}
    try:
        return handler[args.command](args)
    except StepError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
