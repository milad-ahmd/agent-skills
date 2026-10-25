"""Minimal Markdown → HTML renderer for SKILL.md files (stdlib only)."""
import html
import re

E = html.escape


def md_inline(t):
    t = E(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', t)
    return t


def md_to_html(md):
    out, lines, i = [], md.splitlines(), 0
    block_start = re.compile(r"^(#|```|\||\s*([-*]|\d+\.) |>)")
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append("<pre><code>" + E("\n".join(buf)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,4}) (.*)", l)
        if m:
            lvl = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{lvl}>{md_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if l.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|?$", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            if rows:
                h = "".join(f"<th>{md_inline(c)}</th>" for c in rows[0])
                b = "".join("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
                out.append(f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>")
            continue
        if re.match(r"^\s*([-*]|\d+\.) ", l):
            tag = "ol" if re.match(r"^\s*\d+\.", l) else "ul"
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.) ", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.) ", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{md_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if l.startswith(">"):
            out.append(f"<blockquote>{md_inline(l.lstrip('> '))}</blockquote>")
            i += 1
            continue
        if l.strip():
            buf = [l]
            i += 1
            while i < len(lines) and lines[i].strip() and not block_start.match(lines[i]):
                buf.append(lines[i])
                i += 1
            out.append("<p>" + md_inline(" ".join(buf)) + "</p>")
            continue
        i += 1
    return "\n".join(out)
