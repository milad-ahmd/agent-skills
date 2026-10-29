#!/usr/bin/env python3
"""Tests for make_demo.py (stdlib unittest). Needs none of vhs, ffmpeg, gifsicle or playwright.

Run: python3 -m unittest discover -s skills/demo-recorder/scripts -p 'test_*.py'
One test runs `node --check` on a generated script and is skipped when node is not installed.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_demo as md  # noqa: E402

FX = os.path.join(HERE, "fixtures")
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = md.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def touch(path, data):
    with open(path, "wb") as f:
        f.write(data)


def tape_lines(text):
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


class Quoting(unittest.TestCase):
    def test_picks_a_delimiter_the_command_does_not_use(self):
        self.assertEqual(md.vhs_string("ls -la"), '"ls -la"')
        self.assertEqual(md.vhs_string('echo "hi"'), "'echo \"hi\"'")
        self.assertEqual(md.vhs_string("echo \"it's\""), "`echo \"it's\"`")
        self.assertEqual(md.vhs_string("echo `date` it's"), '"echo `date` it\'s"')
        self.assertEqual(md.vhs_string("grep -E '$x' \"$y\""), "`grep -E '$x' \"$y\"`")

    def test_all_three_quote_chars_fail_clearly(self):
        with self.assertRaises(md.StepError) as ctx:
            md.vhs_string("echo \"a\" 'b' `c`")
        self.assertIn("script", str(ctx.exception))
        code, _, err = run("tape", os.path.join(FX, "unquotable.steps"))
        self.assertEqual(code, 2)
        self.assertIn("cannot type it", err)

    def test_o_with_a_media_path_is_refused_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as t:
            gif = os.path.join(t, "demo.gif")
            touch(gif, b"GIF89a real demo")
            code, _, err = run("tape", os.path.join(FX, "cli.steps"), "-o", gif)
            self.assertEqual(code, 2)
            self.assertIn("--out", err)
            self.assertEqual(slurp(gif), "GIF89a real demo")
            code, _, err = run("web", os.path.join(FX, "web.steps"), "--url", "http://localhost:3000",
                               "-o", os.path.join(t, "demo.webm"))
            self.assertEqual(code, 2)
            self.assertFalse(os.path.exists(os.path.join(t, "demo.webm")))

    def test_newline_rejected(self):
        with self.assertRaises(md.StepError):
            md.vhs_string("echo a\necho b")


class Tape(unittest.TestCase):
    def setUp(self):
        code, self.tape, err = run("tape", os.path.join(FX, "cli.steps"), "--out", "demo.gif",
                                   "--out", "demo.webm", "--theme", "Catppuccin Mocha")
        self.assertEqual(code, 0, err)
        self.lines = tape_lines(self.tape)

    def test_outputs_and_settings_come_first(self):
        self.assertEqual(self.lines[:2], ["Output demo.gif", "Output demo.webm"])
        self.assertIn('Set Theme "Catppuccin Mocha"', self.lines)
        first_cmd = next(i for i, ln in enumerate(self.lines) if not ln.startswith(("Output", "Set", "Require")))
        self.assertTrue(all(not ln.startswith("Set ") for ln in self.lines[first_cmd:]))

    def test_hidden_setup_is_cleared_before_show(self):
        i, j = self.lines.index("Hide"), self.lines.index("Show")
        block = self.lines[i:j]
        self.assertIn("Type 'cd \"$(mktemp -d)\"'", block)
        self.assertIn('Type "clear"', block)
        self.assertFalse(any(ln.startswith("Type@") for ln in block))  # speed directive comes after show

    def test_type_speed_sleep_wait_key_and_quoting(self):
        self.assertIn('Type@40ms "ls -la"', self.lines)
        self.assertIn("Type@40ms `echo \"it's done\"`", self.lines)
        self.assertIn("Type@40ms `echo 'say \"hi\"'`", self.lines)
        self.assertIn('Type "grep -c ok `which python3` || true"', self.lines)  # after '# type-speed default'
        self.assertIn("Sleep 1s", self.lines)
        self.assertIn("Ctrl+L", self.lines)
        self.assertIn("Wait /ready/", self.lines)
        self.assertIn("# Comment about the next step", self.tape)

    def test_every_command_is_entered_and_waited(self):
        n_typed = sum(1 for ln in self.lines if ln.startswith("Type") and ln != 'Type "clear"')
        self.assertEqual(n_typed, 6)
        self.assertGreaterEqual(self.lines.count("Enter"), n_typed)
        self.assertGreaterEqual(self.lines.count("Wait"), n_typed)
        self.assertEqual(md.lint_tape(self.tape), [])

    def test_cmd_args_and_options(self):
        code, tape, _ = run("tape", "--cmd", "mytool --help", "--cmd", "echo 'done'", "--no-wait",
                            "--width", "1000", "--height", "500", "--font-size", "18", "--padding", "10",
                            "--framerate", "15", "--typing-speed", "30ms", "--require", "mytool",
                            "--out", "out/demo.mp4", "--fake-home", "--prompt", "$ ")
        self.assertEqual(code, 0)
        lines = tape_lines(tape)
        for want in ["Output out/demo.mp4", "Require mytool", "Set Width 1000", "Set Height 500",
                     "Set FontSize 18", "Set Padding 10", "Set Framerate 15", "Set TypingSpeed 30ms",
                     "Type 'export HOME=\"$(mktemp -d)\"'", "Type \"export PS1='$ '\"", 'Type "mytool --help"',
                     "Type \"echo 'done'\""]:
            self.assertIn(want, lines)
        self.assertNotIn("Wait", lines)
        self.assertFalse(any(ln.startswith("Set WaitPattern") for ln in lines))
        self.assertEqual(md.lint_tape(tape), [])

    def test_custom_prompt_sets_wait_pattern(self):
        code, tape, _ = run("tape", "--cmd", "ls", "--prompt", "demo$ ")
        self.assertEqual(code, 0)
        self.assertIn("Set WaitPattern /demo\\$$/", tape)

    def test_bad_inputs(self):
        self.assertEqual(run("tape", "--cmd", "ls", "--out", "demo.png")[0], 2)
        self.assertEqual(run("tape", "--cmd", "ls", "--out", "my demo.gif")[0], 2)
        self.assertEqual(run("tape", "--cmd", "ls", "--pause", "soon")[0], 2)
        self.assertEqual(run("tape", "--cmd", "# hide")[0], 2)  # no commands
        with self.assertRaises(md.StepError):
            md.parse_tape_steps(["# sleep forever"])
        with self.assertRaises(md.StepError):
            md.parse_tape_steps(["# key Ctrl"])
        with self.assertRaises(md.StepError):
            md.parse_tape_steps(["# wait ready"])

    def test_writes_tape_and_asciinema_script(self):
        with tempfile.TemporaryDirectory() as t:
            tp, sh = os.path.join(t, "assets", "demo.tape"), os.path.join(t, "demo.sh")
            code, _, err = run("tape", os.path.join(FX, "cli.steps"), "-o", tp, "--sh", sh)
            self.assertEqual(code, 0, err)
            self.assertEqual(run("lint", tp)[0], 0)
            script = slurp(sh)
            self.assertIn("show 'ls -la' 0.04", script)
            self.assertIn("eval 'ls -la'", script)
            self.assertIn("eval 'git init -q demo && cd demo' >/dev/null 2>&1", script)
            self.assertIn("sleep 1\n", script)
            self.assertNotIn("show 'git init", script)  # hidden steps run but are not typed
            if shutil.which("bash"):
                self.assertEqual(subprocess.run(["bash", "-n", sh]).returncode, 0)


class Lint(unittest.TestCase):
    def test_example_tape_is_valid(self):
        self.assertEqual(run("lint", os.path.join(EXAMPLES, "demo.tape"))[0], 0)

    def test_catches_problems(self):
        bad = "\n".join([
            "Output demo.gif",
            "Set FontSize 20",
            'Type "ls"',
            "Set Width 900",          # setting after a command
            "Set TypingSpeed 20ms",   # allowed mid-tape
            'Type "it"s"',            # broken quoting
            "Set Colour red",         # unknown setting
            "Sleep later",
            "Enter 3",
            "Ctrl+C",
        ])
        problems = md.lint_tape(bad)
        self.assertEqual(len(problems), 4, problems)
        self.assertTrue(any("line 4" in p and "Width" in p for p in problems))
        self.assertTrue(any("line 6" in p for p in problems))
        self.assertTrue(any("Colour" in p for p in problems))
        self.assertTrue(any("line 8" in p for p in problems))
        self.assertIn("no Output", md.lint_tape('Type "ls"\n')[0])


class Web(unittest.TestCase):
    def test_parse_steps(self):
        steps = md.parse_web_steps(md.read_lines(os.path.join(FX, "web.steps")))
        self.assertEqual(steps, [
            ("goto", "/login"),
            ("fill", "#email", "demo@example.com"),
            ("type", "input[name=password]", "correct horse"),
            ("click", "button[type=submit]"),
            ("wait-for", "text=Dashboard"),
            ("scroll", 600),
            ("wait", 500),
            ("press", "Enter"),
            ("hover", ".card:first-child"),
        ])

    def test_bad_steps(self):
        for line in ["jump #x", "wait soon", "fill #only-selector", "click", "scroll 1.5"]:
            with self.assertRaises(md.StepError, msg=line):
                md.parse_web_steps([line])

    def gen(self, steps_file, *extra):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "demo.web.js")
            code, stdout, err = run("web", steps_file, "--url", "http://localhost:3000/app/", "-o", out, *extra)
            self.assertEqual(code, 0, err)
            return slurp(out), stdout

    def test_generated_script(self):
        script, stdout = self.gen(os.path.join(FX, "web.steps"), "--viewport", "1024x640")
        self.assertIn("require('playwright')", script)
        self.assertNotIn("@playwright/test", script)
        self.assertIn("const VIEWPORT = { width: 1024, height: 640 };", script)
        self.assertIn("recordVideo: { dir: VIDEO_DIR, size: VIEWPORT }", script)
        self.assertIn('await page.goto("http://localhost:3000/login"', script)
        self.assertIn('await page.fill("#email", "demo@example.com");', script)
        self.assertIn('await page.keyboard.type("correct horse", { delay: 60 });', script)
        self.assertIn("await page.mouse.wheel(0, 600);", script)
        self.assertIn('await page.keyboard.press("Enter");', script)
        self.assertIn("video.saveAs(OUT)", script)
        self.assertIn("__demo_cursor", script)
        self.assertIn("palettegen", stdout)
        self.assertIn("paletteuse", stdout)
        self.assertIn("libx264", stdout)
        self.assertIn("scale=1024:-2", stdout)  # MP4 keeps the viewport width

    def test_no_cursor(self):
        script, _ = self.gen(os.path.join(FX, "web.steps"), "--no-cursor")
        self.assertNotIn("__demo_cursor", script)

    def test_step_text_cannot_inject_js(self):
        script, _ = self.gen(os.path.join(FX, "web_injection.steps"))
        sel = "); require('child_process').execSync('touch /tmp/pwned'); ("
        self.assertIn("await page.click(%s);" % json.dumps(sel), script)
        text = '`${process.exit(1)}` </script> "quoted" \\back\\slash'
        self.assertIn("await page.fill(\"#q\", %s);" % json.dumps(text), script)
        # the payload only ever appears inside a JSON string literal or a // comment
        for ln in script.splitlines():
            if "execSync" in ln:
                self.assertTrue(ln.strip().startswith(("//", "await page.click(\"", "await moveTo(\"")), ln)

    def test_rejects_bad_url_and_viewport(self):
        steps = os.path.join(FX, "web.steps")
        self.assertEqual(run("web", steps, "--url", "javascript:alert(1)")[0], 2)
        self.assertEqual(run("web", steps, "--url", "http://x", "--viewport", "big")[0], 2)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_generated_js_parses(self):
        for f in ("web.steps", "web_injection.steps"):
            with tempfile.TemporaryDirectory() as t:
                out = os.path.join(t, "demo.web.js")
                self.assertEqual(run("web", os.path.join(FX, f), "--url", "http://localhost:3000", "-o", out)[0], 0)
                r = subprocess.run(["node", "--check", out], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


class Check(unittest.TestCase):
    def fake(self, present, importable=False):
        return (mock.patch.object(md.shutil, "which", lambda t: "/usr/bin/" + t if t in present else None),
                mock.patch.object(md, "playwright_importable", lambda: importable))

    def check(self, present, *argv, importable=False):
        a, b = self.fake(present, importable)
        with a, b:
            return run("check", *argv)

    def test_nothing_installed(self):
        code, out, _ = self.check(set(), "--os", "linux")
        self.assertEqual(code, 1)
        self.assertIn("No route works", out)
        self.assertIn("go install github.com/charmbracelet/vhs@latest", out)
        self.assertIn("sudo apt install ffmpeg", out)

    def test_vhs_route(self):
        code, out, _ = self.check({"vhs", "ttyd", "ffmpeg"}, "--os", "mac", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        ok = {r["route"]: r["ok"] for r in data["routes"]}
        self.assertEqual(ok, {"terminal-vhs": True, "terminal-asciinema": False, "browser-playwright": False})
        self.assertEqual(data["install"]["gifsicle"], "brew install gifsicle")
        self.assertNotIn("vhs", data["install"])

    def test_asciinema_route_and_partial_vhs(self):
        code, out, _ = self.check({"vhs", "asciinema", "agg"}, "--os", "mac")
        self.assertEqual(code, 0)
        self.assertIn("needs ttyd, ffmpeg", out)
        self.assertIn("terminal-asciinema", out)

    def test_playwright_as_local_package(self):
        code, out, _ = self.check({"node", "npx"}, "--json", importable=True)
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(data["tools"]["playwright"])
        self.assertTrue(next(r for r in data["routes"] if r["route"] == "browser-playwright")["ok"])

    def test_never_runs_installers(self):
        a, b = self.fake(set())
        with a, b, mock.patch.object(md.subprocess, "run") as sp:
            run("check", "--os", "windows")
        sp.assert_not_called()


class Optimize(unittest.TestCase):
    def test_gif_plan_two_pass_then_gifsicle(self):
        cmds = md.plan_optimize("in.gif", "gif", 900, 10, "out.gif", palette="p.png", lossy=100, colors=64)
        self.assertEqual([c[0] for c in cmds], ["ffmpeg", "ffmpeg", "gifsicle"])
        self.assertIn("fps=10,scale=900:-1:flags=lanczos,palettegen=max_colors=64:stats_mode=diff", cmds[0])
        self.assertEqual(cmds[0][-1], "p.png")
        self.assertEqual(cmds[1][:6], ["ffmpeg", "-y", "-i", "in.gif", "-i", "p.png"])
        self.assertTrue(any("paletteuse" in x for x in cmds[1]))
        self.assertEqual(cmds[2], ["gifsicle", "-O3", "--lossy=100", "--colors", "64", "-b", "out.gif"])

    def test_tool_fallbacks(self):
        only_gifsicle = md.plan_optimize("in.gif", "gif", 800, 12, "o.gif", have={"gifsicle": True})
        self.assertEqual(only_gifsicle, [["gifsicle", "-O3", "--lossy=80", "--colors", "128",
                                          "--resize-width", "800", "in.gif", "-o", "o.gif"]])
        self.assertEqual(md.plan_optimize("in.webm", "gif", 800, 12, "o.gif", have={"gifsicle": True}), [])
        no_gifsicle = md.plan_optimize("in.webm", "gif", 800, 12, "o.gif", have={"ffmpeg": True})
        self.assertEqual([c[0] for c in no_gifsicle], ["ffmpeg", "ffmpeg"])

    def test_video_targets(self):
        mp4 = md.plan_optimize("in.webm", "mp4", 1200, 30, "o.mp4")[0]
        for want in ["libx264", "yuv420p", "+faststart", "fps=30,scale=1200:-2:flags=lanczos"]:
            self.assertIn(want, mp4)
        webm = md.plan_optimize("in.mp4", "webm", 1200, 30, "o.webm")[0]
        self.assertIn("libvpx-vp9", webm)

    def test_cli_prints_without_running(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "demo.gif")
            touch(src, b"GIF89a")
            with mock.patch.object(md.subprocess, "run") as sp, \
                    mock.patch.object(md.shutil, "which", lambda x: None):
                code, out, _ = run("optimize", src, "--width", "800", "--fps", "10")
            sp.assert_not_called()
            self.assertEqual(code, 0)
            self.assertIn("demo.min.gif", out)
            self.assertIn("not installed here: ffmpeg, gifsicle", out)
            code, out, _ = run("optimize", src, "--to", "mp4")
            self.assertIn("demo.min.mp4", out)
            self.assertEqual(run("optimize", src, "-o", src)[0], 2)
            self.assertEqual(run("optimize", os.path.join(t, "missing.gif"))[0], 2)

    def test_run_without_tools_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "demo.webm")
            touch(src, b"\x1aE\xdf\xa3")
            with mock.patch.object(md.shutil, "which", lambda x: None):
                code, out, _ = run("optimize", src, "--to", "gif", "--run")
            self.assertEqual(code, 1)
            self.assertIn("needs ffmpeg", out)


if __name__ == "__main__":
    unittest.main()
