---
name: demo-recorder
description: Records a short demo of a project actually running, for a README, launch post or pull request. Writes a VHS .tape (or an asciinema script) for CLI tools and a Playwright script for web apps, runs the commands for real so the GIF or MP4 shows real output, then shrinks it with ffmpeg and gifsicle and keeps the script in the repo for re-recording. Use when the user asks to record, make or update a demo GIF, terminal recording, screen recording, screencast or demo video of their CLI or web app (sign-up flow, onboarding, a feature), or wants a GIF for the README that shows the tool in action.
---

# Demo Recorder

Record the real tool doing one useful thing in under 25 seconds, small enough to load on a README, and leave the script behind so the next release can re-record it with one command.

## 1. Pick the story

One task, 10 to 25 seconds, ending on the result that makes someone want to try it (the "aha" moment). Read the README and `--help` to find it. Good: `mytool scan .` finds three issues, `--fix` fixes them, a second scan is clean. Bad: a tour of every flag. Write the steps down and show them to the user before recording.

## 2. Check the tools

```bash
python3 scripts/make_demo.py check          # what is installed, which routes work, install commands for this OS
```

| Route | Needs | Use for |
|---|---|---|
| `terminal-vhs` | vhs, ttyd, ffmpeg | CLIs and TUIs. Scripted, repeatable, best result |
| `terminal-asciinema` | asciinema, agg | CLIs when VHS won't install (not on Windows) |
| `browser-playwright` | node, `playwright` package (ffmpeg for GIF/MP4) | web apps |

`check` only prints install commands (exit 0 if a route works, 1 if none). Ask the user before installing anything, and prefer a project devDependency (`npm install --save-dev playwright`) over a global install.

## 3. Write the script

**CLI.** Put one command per line in a steps file, with directives as comment lines:

```text
# hide
export HOME="$(mktemp -d)"
cd examples/sample-project
# show
mytool scan .
# sleep 1s
mytool scan . --fix
```

Directives: `# hide` / `# show` (setup the viewer doesn't see, cleared before showing), `# sleep 2s`, `# type-speed 40ms` (or `default`), `# wait [/regex/]`, `# key Ctrl+C` (or `Down 3`, `Enter`). Other `#` lines are comments.

```bash
python3 scripts/make_demo.py tape demo.steps -o assets/demo.tape --out assets/demo.gif
python3 scripts/make_demo.py tape --cmd "mytool --version" --cmd "mytool scan ." --out demo.gif --out demo.mp4
python3 scripts/make_demo.py tape demo.steps --sh assets/demo.sh   # also writes a script for asciinema rec -c
python3 scripts/make_demo.py lint assets/demo.tape                 # check a hand-edited tape
```

Options: `--width 1200 --height 600 --font-size 22 --theme "Catppuccin Mocha" --padding 20 --framerate 24 --typing-speed 50ms --pause 2s --require mytool --fake-home --prompt '$ '`. After each command the tape waits for the prompt (`Wait`, VHS 0.9+; `--no-wait` for older VHS) so slow commands are not cut off. VHS strings have no escapes, so each `Type` line uses whichever of `"`, `'` or `` ` `` the command doesn't contain; a command with all three is rejected, so move it into a small script and type that. Exit codes: `0` written, `2` bad step or unquotable command.

**Web app.** Steps: `goto URL`, `click SEL`, `hover SEL`, `fill SEL TEXT`, `type SEL TEXT` (visible keystrokes), `press KEY`, `wait MS`, `wait-for SEL`, `scroll PX`. Quote selectors with spaces: `type "input[name=q]" hello world`.

```bash
python3 scripts/make_demo.py web demo.web.steps --url http://localhost:3000 -o assets/demo.web.js --viewport 1280x720
```

The script uses the plain `playwright` library (not `@playwright/test`), records `.webm` into `demo-video/`, and draws a pointer so clicks are visible (`--no-cursor` to skip). Step text goes into the script as JSON string literals, so a selector can't inject code. The command prints the ffmpeg lines for the GIF and MP4.

See `examples/` for a steps file, the tape it produces and a web steps file.

## 4. Dry-run for real

Run every command in the steps file yourself first, in the same clean setup, and read the output. Fix anything that errors, prompts, prints a path under the real home directory or takes longer than a few seconds. The recording types the commands into a real shell, so what you see now is what the GIF shows. For web demos, start the app with seed or demo data and check each selector exists.

## 5. Record

```bash
vhs assets/demo.tape
asciinema rec demo.cast --overwrite -c 'bash assets/demo.sh' && agg --font-size 22 demo.cast demo.gif
node assets/demo.web.js
```

Watch the result once from start to end. Re-record rather than editing frames.

## 6. Shrink it

GitHub caps image uploads at 10 MB; aim for under 3 to 5 MB so the README loads quickly. 1000 to 1200 px wide at 10 to 15 fps is usually enough.

```bash
python3 scripts/make_demo.py optimize assets/demo.gif --width 1000 --fps 12          # print the commands
python3 scripts/make_demo.py optimize assets/demo.gif --width 1000 --fps 12 --run    # run them, report before/after size
python3 scripts/make_demo.py optimize demo-video/demo.webm --to mp4 --width 1280 --run
```

GIFs go through a two-pass ffmpeg palette (`palettegen` / `paletteuse`), then `gifsicle -O3 --lossy`. Without ffmpeg it falls back to gifsicle resizing only. `optimize --run` exits 1 when a needed tool is missing or a command fails. If it is still too big, cut the story, lower fps, or ship MP4: GitHub plays it inline only when the file is uploaded through the web editor (a `user-attachments` URL), while a repo-relative `.mp4` shows as a link.

## 7. Embed and keep the source

```markdown
![mytool scanning a sample project, fixing three issues and re-scanning clean](assets/demo.gif)
```

Alt text says what happens, not "demo". Commit the steps file, the `.tape` or `.web.js` and the output together (for example `assets/demo.tape` next to `assets/demo.gif`), and add a line to the contributing docs: "re-record with `vhs assets/demo.tape`". Delete `demo-video/` and palette files.

## Rules

- Never fake output. No typed-out results, no edited frames, no mock server presented as the product. If the real command fails, fix the setup or tell the user.
- Never record secrets, tokens, real customer data, real email addresses or a personal home directory. Use a fresh temp `HOME`, a demo account with seed data, and check the final GIF frame by frame for anything private.
- Ask before installing tools, and don't commit, push or upload the result without the user's OK.
- Keep the demo short. If it needs a voice-over or more than one task, it is a video script, not a README GIF.
