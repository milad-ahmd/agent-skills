#!/usr/bin/env python3
"""Render a terminal-style animated GIF from real captured command output.

Usage:
  python3 make_gif.py out.gif "<title>" "<command 1>" output1.txt ["<command 2>" output2.txt ...]
Requires Pillow. Typing is animated; output lines appear progressively.
"""
import sys
from PIL import Image, ImageDraw, ImageFont

W, PAD, LINE_H, FONT_SIZE = 960, 24, 24, 16
BG, BAR, FG, PROMPT, DIM, GREEN, RED, YELLOW = (24, 26, 33), (40, 42, 54), (230, 232, 238), (139, 233, 253), (130, 135, 150), (80, 250, 123), (255, 85, 85), (241, 250, 140)
MAX_LINES = 22


def load_font():
    for path in ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        try:
            return ImageFont.truetype(path, FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default()


FONT = load_font()
H = 70 + MAX_LINES * LINE_H + PAD


WRAP = 86


EMOJI = {"👤": "@", "👥": "»", "📦": "■", "🧹": "~", "🪦": "†", "🗣": "»", "🏆": "★", "✍️": "✎", "✍": "✎",
         "📜": "§", "🌱": "→", "📍": "→", "📈": "↑", "🎭": "»", "💥": "!", "👻": "†", "🌙": "…", "🌪": "↺",
         "🔐": "▶", "🔴": "✗", "🟠": "!", "🟡": "•", "🟢": "✓", "➡️": "→", "➡": "→", "✅": "✓", "❌": "✗",
         "⚠️": "!", "⚠": "!", "🔀": "⇄", "⭐": "*", "📊": "#"}


def clean(text):
    for k, v in EMOJI.items():
        text = text.replace(k, v)
    return "".join(ch for ch in text if ord(ch) < 0x1F000 and ch != "\ufe0f")


def wrap(text, width=WRAP):
    if not text:
        return [""]
    return [text[i:i + width] for i in range(0, len(text), width)]


def color_for(line):
    s = line.lstrip()
    if s.startswith(("✓", "pushed", "TOP")):
        return GREEN
    if s.startswith("✗") or " ✗ " in s[:6]:
        return RED
    if s.startswith("!"):
        return YELLOW
    if s.startswith(("✓",)):
        return GREEN
    if s.startswith(("✗", "FAILED")):
        return RED
    if s.startswith(("⭐", "BOTTOM", "Installed")):
        return YELLOW
    return FG


def frame(title, lines, cursor=True):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 40], fill=BAR)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([16 + i * 22, 13, 30 + i * 22, 27], fill=c)
    tw = d.textlength(title, font=FONT)
    d.text(((W - tw) / 2, 11), title, font=FONT, fill=DIM)
    visible = lines[-MAX_LINES:]
    y = 56
    for kind, text in visible:
        x = PAD
        if kind == "cont":
            d.text((x + d.textlength("$ ", font=FONT), y), text, font=FONT, fill=FG)
        elif kind == "cmd":
            d.text((x, y), "$", font=FONT, fill=PROMPT)
            x += d.textlength("$ ", font=FONT)
            d.text((x, y), text, font=FONT, fill=FG)
        else:
            d.text((x, y), text, font=FONT, fill=color_for(text))
        y += LINE_H
    if cursor and visible:
        kind, text = visible[-1]
        cx = PAD + d.textlength(("$ " if kind in ("cmd", "cont") else "") + text, font=FONT) + 2
        d.rectangle([cx, y - LINE_H + 3, cx + 9, y - 3], fill=FG)
    return img


def main():
    if len(sys.argv) < 5 or (len(sys.argv) - 3) % 2:
        sys.exit(__doc__)
    out, title, pairs = sys.argv[1], sys.argv[2], sys.argv[3:]
    frames, durations, lines = [], [], []
    for i in range(0, len(pairs), 2):
        cmd, outfile = pairs[i], pairs[i + 1]
        with open(outfile, encoding="utf-8") as f:
            output = []
            for l in f:
                l = clean(l.rstrip("\n"))
                output += wrap(l)
        lines.append(("cmd", ""))
        frames.append(frame(title, lines)); durations.append(500)
        step = max(1, len(cmd) // 25)
        base = len(lines) - 1
        for n in range(step, len(cmd) + step, step):
            parts = wrap(cmd[:n], WRAP - 2)
            del lines[base:]
            lines.append(("cmd", parts[0]))
            lines.extend(("cont", p) for p in parts[1:])
            frames.append(frame(title, lines)); durations.append(45)
        frames.append(frame(title, lines)); durations.append(400)
        for l in output:
            lines.append(("out", l))
            frames.append(frame(title, lines, cursor=False)); durations.append(110)
        frames.append(frame(title, lines, cursor=False)); durations.append(1200)
    durations[-1] = 3500
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=32) for f in frames]
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True)
    print(f"wrote {out} ({len(frames)} frames)")


if __name__ == "__main__":
    main()
