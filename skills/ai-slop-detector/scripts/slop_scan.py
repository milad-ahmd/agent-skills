#!/usr/bin/env python3
"""Score text for AI-slop *style signals*. It does not detect authorship. Stdlib only, Python 3.10+.

Usage:
  slop_scan.py [options] PATH|GLOB|- [...]

  PATH may be a file, a directory (scans *.md, *.markdown, *.txt, *.rst inside) or a glob
  such as 'docs/**/*.md' (quote it). '-' reads stdin.

Options:
  --format text|json|github   output style (default text); --json is short for --format json
  --fail-above N              exit 1 if any scanned text scores above N (for CI)
  --min-words N               skip texts shorter than N words (default 50)
  --exclude GLOB              skip matching paths (repeatable)
  --stdin-name NAME           display name for '-' (default <stdin>)
  --top N                     hits listed per file in text output (default 15)
  --max-annotations N         warning annotations in github output (default 10)
  --patterns FILE             alternative pattern catalog (default: patterns.json next to this script)
  --list-patterns             print the catalog and exit

Score (0-100) = 100 * sum(weight_c * signal_c), each signal in [0, 1]:
  patterns    1 - exp(-D / 25), D = 1000 * (sum of matched pattern weights) / max(words, 150)
  rhythm      sentence-length coefficient of variation: 1 at CV <= 0.30, 0 at CV >= 0.70 (needs 6+ sentences)
  paragraphs  paragraph-length CV: 1 at CV <= 0.20, 0 at CV >= 0.60 (needs 4+ prose paragraphs)
  em-dashes   per 1000 words, saturating at 12
  triads      'X, Y, and Z' lists per 1000 words, saturating at 20
  lists       share of non-blank lines that are list items or headings, saturating at 60%
  weights are in WEIGHTS below. Bands: low < 20 <= moderate < THRESHOLD <= high.

Exit codes: 0 ok, 1 --fail-above exceeded, 2 usage error.
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import math
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATTERNS = os.path.join(HERE, "patterns.json")

# Component weights (sum to 1), set by hand after looking at per-signal AUCs on the benchmark's dev split
# (never the test split); see bench/README.md. Pattern hits carry most of the signal.
WEIGHTS = {"patterns": 0.70, "rhythm": 0.04, "paragraphs": 0.06, "dashes": 0.04, "triads": 0.06, "lists": 0.10}
LEXICAL_SCALE = 25.0   # D at which the pattern signal reaches 1 - 1/e
MIN_DENOM_WORDS = 150  # short texts are scored as if they had 150 words, so one cliché in a tweet doesn't max out
THRESHOLD = 30         # default "high" cut-off; the benchmark reports precision/recall here
LOW_BAND = 20

TEXT_EXT = (".md", ".markdown", ".txt", ".rst", ".mdx")
DEFAULT_EXCLUDES = ["**/node_modules/**", "**/.git/**", "**/vendor/**", "**/.venv/**"]

LIST_RX = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
HEADING_RX = re.compile(r"^\s{0,3}#{1,6}\s")
TABLE_RX = re.compile(r"^\s*\|")
WORD_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
SENT_SPLIT = re.compile(r"(?<=[.!?])[\"'”’)]*\s+")
DASH_RX = re.compile(r"—|(?<=\w)--(?=\w)|\s--\s|\s–\s")
TRIAD_RX = re.compile(r"\b[\w'’-]+(?: [\w'’-]+){0,2}, [\w'’-]+(?: [\w'’-]+){0,2},? (?:and|or) [\w'’-]+", re.I)


class Pattern:
    __slots__ = ("id", "category", "weight", "why", "instead", "rx", "regex")

    def __init__(self, d):
        self.id, self.category, self.weight = d["id"], d["category"], float(d["weight"])
        self.why, self.instead, self.regex = d.get("why", ""), d.get("instead", ""), d["regex"]
        self.rx = re.compile(d["regex"], 0 if d.get("case_sensitive") else re.I)


def load_patterns(path: str | None = None) -> list[Pattern]:
    with open(path or DEFAULT_PATTERNS, encoding="utf-8") as fh:
        data = json.load(fh)
    return [Pattern(d) for d in data["patterns"]]


def _blank(m: re.Match) -> str:
    return " " * len(m.group(0))


def clean_text(text: str) -> str:
    """Remove code, URLs, front matter and HTML, keeping line count and (mostly) columns."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out, fence, i = [], None, 0
    if lines and lines[0].strip() == "---":  # YAML front matter
        for j in range(1, min(len(lines), 200)):
            if lines[j].strip() in ("---", "..."):
                out = [""] * (j + 1)
                i = j + 1
                break
    for line in lines[i:]:
        s = line.lstrip()
        if fence:
            out.append("")
            if s.startswith(fence):
                fence = None
            continue
        if s.startswith("```") or s.startswith("~~~"):
            fence = s[:3]
            out.append("")
            continue
        if line.startswith("    ") and not LIST_RX.match(line) and (not out or not out[-1].strip()):
            out.append("")  # indented code block (only after a blank line)
            continue
        out.append(line)
    t = "\n".join(out)
    t = re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), t, flags=re.S)
    t = re.sub(r"`[^`\n]+`", _blank, t)
    t = re.sub(r"!\[[^\]\n]*\]\([^)\n]*\)", _blank, t)
    t = re.sub(r"\[([^\]\n]+)\]\(([^)\n]*)\)", lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1))), t)
    t = re.sub(r"https?://\S+|www\.\S+", _blank, t)
    t = re.sub(r"</?[A-Za-z][^>\n]*>", _blank, t)
    return t


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ramp_down(x: float, full: float, zero: float) -> float:
    """1 when x <= full, 0 when x >= zero, linear in between."""
    return _clamp((zero - x) / (zero - full))


def find_hits(lines: list[str], patterns: list[Pattern]) -> list[dict]:
    hits = []
    for ln, line in enumerate(lines, 1):
        if not line.strip():
            continue
        cands = []
        for p in patterns:
            for m in p.rx.finditer(line):
                if m.end() > m.start():
                    cands.append((m.start(), m.end(), p, m.group(0)))
        kept = []
        for c in sorted(cands, key=lambda c: (-c[2].weight, c[0])):
            if all(c[1] <= k[0] or c[0] >= k[1] for k in kept):
                kept.append(c)
        for s, e, p, txt in sorted(kept, key=lambda c: c[0]):
            hits.append({"line": ln, "col": s + 1, "end_col": e + 1, "id": p.id, "category": p.category,
                         "weight": p.weight, "text": txt.strip(), "why": p.why, "instead": p.instead})
    return hits


def structure(lines: list[str]) -> dict:
    """Split cleaned lines into prose paragraphs, list items and sentences."""
    paragraphs, units, cur = [], [], []
    nonblank = listish = 0
    for line in lines + [""]:
        s = line.strip()
        if s:
            nonblank += 1
        is_list, is_head = bool(LIST_RX.match(line)), bool(HEADING_RX.match(line))
        if is_list or is_head:
            listish += 1
        if not s or is_list or is_head or TABLE_RX.match(line):
            if cur:
                paragraphs.append(" ".join(cur))
                cur = []
            if is_list:
                units.append(LIST_RX.sub("", line).strip())
            continue
        cur.append(s)
    units = paragraphs + units
    sentences = [s for u in units for s in SENT_SPLIT.split(u) if WORD_RX.search(s)]
    return {"paragraphs": paragraphs, "sentences": sentences, "nonblank": nonblank, "listish": listish}


def analyze(text: str, patterns: list[Pattern] | None = None) -> dict:
    patterns = patterns if patterns is not None else load_patterns()
    cleaned = clean_text(text)
    lines = cleaned.split("\n")
    words = len(WORD_RX.findall(cleaned))
    hits = find_hits(lines, patterns)
    st = structure(lines)

    weighted = sum(h["weight"] for h in hits)
    d = 1000 * weighted / max(words, MIN_DENOM_WORDS)
    lens = [len(WORD_RX.findall(s)) for s in st["sentences"]]
    cv = statistics.pstdev(lens) / statistics.mean(lens) if len(lens) >= 6 and statistics.mean(lens) else None
    plens = [len(WORD_RX.findall(p)) for p in st["paragraphs"] if len(WORD_RX.findall(p)) >= 8]
    pcv = statistics.pstdev(plens) / statistics.mean(plens) if len(plens) >= 4 else None
    per1k = (lambda n: 1000 * n / words) if words else (lambda n: 0.0)
    dashes = len(DASH_RX.findall(cleaned))
    triads = sum(len(TRIAD_RX.findall(u)) for u in st["paragraphs"]) + sum(
        len(TRIAD_RX.findall(LIST_RX.sub("", ln))) for ln in lines if LIST_RX.match(ln))
    list_share = st["listish"] / st["nonblank"] if st["nonblank"] else 0.0

    comps = {
        "patterns": {"value": round(d, 1), "unit": "weighted hits per 1k words", "signal": 1 - math.exp(-d / LEXICAL_SCALE)},
        "rhythm": {"value": None if cv is None else round(cv, 2), "unit": "sentence-length CV",
                   "signal": 0.0 if cv is None else _ramp_down(cv, 0.30, 0.70)},
        "paragraphs": {"value": None if pcv is None else round(pcv, 2), "unit": "paragraph-length CV",
                       "signal": 0.0 if pcv is None else _ramp_down(pcv, 0.20, 0.60)},
        "dashes": {"value": round(per1k(dashes), 1), "unit": "em-dashes per 1k words", "signal": _clamp(per1k(dashes) / 12)},
        "triads": {"value": round(per1k(triads), 1), "unit": "'X, Y, and Z' per 1k words", "signal": _clamp(per1k(triads) / 20)},
        "lists": {"value": round(100 * list_share), "unit": "% lines that are list items/headings", "signal": _clamp(list_share / 0.6)},
    }
    for c in comps.values():
        c["signal"] = round(c["signal"], 3)
    score = round(100 * sum(WEIGHTS[k] * comps[k]["signal"] for k in WEIGHTS))
    return {"words": words, "sentences": len(lens), "score": score, "band": band(score),
            "components": comps, "hits": hits}


def band(score: int) -> str:
    return "high" if score >= THRESHOLD else "moderate" if score >= LOW_BAND else "low"


# ---------------------------------------------------------------- inputs

def _excluded(path: str, excludes: list[str]) -> bool:
    rel = os.path.relpath(path).replace(os.sep, "/")
    for pat in excludes:
        pat = pat.strip().rstrip("/")
        if not pat:
            continue
        cands = {pat, pat[3:] if pat.startswith("**/") else pat}
        for c in cands:
            if fnmatch.fnmatch(rel, c) or rel == c or rel.startswith(c + "/"):
                return True
    return False


def collect_inputs(args: list[str], excludes: list[str]) -> tuple[list[str], list[str]]:
    """Return (paths, errors). '-' stays as '-'."""
    files, errors, seen = [], [], set()

    def add(p):
        key = os.path.normpath(p)
        if key not in seen and not _excluded(p, excludes):
            seen.add(key)
            files.append(p)

    for a in args:
        if a == "-":
            files.append("-")
        elif os.path.isdir(a):
            for root, dirs, fs in os.walk(a):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "node_modules")
                for f in sorted(fs):
                    if f.lower().endswith(TEXT_EXT):
                        add(os.path.join(root, f))
        elif os.path.isfile(a):
            add(a)
        elif any(ch in a for ch in "*?["):
            for p in sorted(glob.glob(a, recursive=True)):
                if os.path.isfile(p):
                    add(p)
        else:
            errors.append(f"no such file: {a}")
    return files, errors


def read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    if os.path.getsize(path) > 2_000_000:
        raise ValueError("file larger than 2 MB")
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------- output

def _bar(sig: float) -> str:
    return f"{sig:.2f}"


def fmt_text(results: list[dict], top: int) -> str:
    out = []
    for r in results:
        if r.get("skipped"):
            out.append(f"{r['file']}: skipped ({r['skipped']})")
            continue
        out.append(f"{r['file']}: slop-signal {r['score']}/100 ({r['band']}), {r['words']} words, {r['sentences']} sentences")
        for k, c in r["components"].items():
            v = "n/a" if c["value"] is None else c["value"]
            out.append(f"  {k:<11} {str(v):>6}  {c['unit']:<38} signal {_bar(c['signal'])}")
        hits = r["hits"]
        if hits:
            out.append(f"  {len(hits)} pattern hits" + (f" (showing {top})" if len(hits) > top else "") + ":")
            for h in sorted(hits, key=lambda h: (-h["weight"], h["line"]))[:top]:
                out.append(f"    line {h['line']:>4}  [{h['id']}] \"{h['text']}\" -> {h['instead']}")
        out.append("")
    out.append("Style signals only. A score is not evidence that a person used AI.")
    return "\n".join(out)


def _esc_data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _esc_prop(s: str) -> str:
    return _esc_data(s).replace(":", "%3A").replace(",", "%2C")


def fmt_github(results: list[dict], fail_above: float | None, max_ann: int) -> str:
    out, scored = [], [r for r in results if not r.get("skipped")]
    all_hits = [(r, h) for r in scored for h in r["hits"]]
    all_hits.sort(key=lambda rh: (-rh[1]["weight"], -rh[0]["score"], rh[0]["file"], rh[1]["line"]))
    for r, h in all_hits[:max_ann]:
        props = [f"title={_esc_prop('AI-sounding: ' + h['id'])}"]
        if not r.get("stdin"):
            props = [f"file={_esc_prop(r['file'])}", f"line={h['line']}", f"col={h['col']}", f"endColumn={h['end_col']}"] + props
        msg = f"\"{h['text']}\" ({h['category']}): {h['why']} Try: {h['instead']}."
        if r.get("stdin"):
            msg = f"[{r['file']} line {h['line']}] " + msg
        out.append(f"::warning {','.join(props)}::{_esc_data(msg)}")
    if len(all_hits) > max_ann:
        out.append(f"({len(all_hits) - max_ann} more pattern hits not annotated; run slop_scan.py locally for the full list)")
    for r in scored:
        failed = fail_above is not None and r["score"] > fail_above
        level = "error" if failed else "notice"
        c = r["components"]
        msg = (f"{len(r['hits'])} pattern hits; patterns {c['patterns']['signal']:.2f}, rhythm {c['rhythm']['signal']:.2f}, "
               f"paragraphs {c['paragraphs']['signal']:.2f}, em-dashes {c['dashes']['signal']:.2f}, triads {c['triads']['signal']:.2f}, "
               f"lists {c['lists']['signal']:.2f}. Style signals only, not proof of AI authorship.")
        title = f"Slop-signal {r['score']}/100 ({r['band']})" + (f" is above {fail_above:g}" if failed else "")
        props = [f"title={_esc_prop(title)}"]
        if not r.get("stdin"):
            props = [f"file={_esc_prop(r['file'])}", "line=1"] + props
        else:
            msg = f"[{r['file']}] " + msg
        out.append(f"::{level} {','.join(props)}::{_esc_data(msg)}")
    for r in results:  # plain log lines; escaped so a crafted file name can't start a workflow command
        out.append(_esc_data(f"{r['file']}: " + (f"skipped ({r['skipped']})" if r.get("skipped") else f"{r['score']}/100 {r['band']}")))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write("### AI slop detector\n\n| Text | Score | Band | Pattern hits |\n|---|---:|---|---:|\n")
                for r in results:
                    name = re.sub(r"[\r\n|]", " ", r["file"])
                    if r.get("skipped"):
                        fh.write(f"| {name} | - | skipped: {r['skipped']} | - |\n")
                    else:
                        fh.write(f"| {name} | {r['score']} | {r['band']} | {len(r['hits'])} |\n")
                fh.write("\nStyle signals only; a score is not evidence that anyone used AI.\n")
        except OSError:
            pass
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score text for AI-slop style signals (not authorship).",
                                 epilog="Exit codes: 0 ok, 1 --fail-above exceeded, 2 usage error.")
    ap.add_argument("inputs", nargs="*", help="files, directories, globs, or - for stdin")
    ap.add_argument("--format", choices=["text", "json", "github"], default="text")
    ap.add_argument("--json", action="store_true", help="same as --format json")
    ap.add_argument("--fail-above", type=float, default=None, metavar="N")
    ap.add_argument("--min-words", type=int, default=50, metavar="N")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    ap.add_argument("--stdin-name", default="<stdin>")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--max-annotations", type=int, default=10)
    ap.add_argument("--patterns", default=None, metavar="FILE")
    ap.add_argument("--list-patterns", action="store_true")
    a = ap.parse_args(argv)
    fmt = "json" if a.json else a.format

    try:
        patterns = load_patterns(a.patterns)
    except (OSError, ValueError, KeyError, re.error) as e:
        print(f"error: cannot load patterns: {e}", file=sys.stderr)
        return 2
    if a.list_patterns:
        for p in patterns:
            print(f"{p.id:<26} {p.category:<12} {p.weight:>4}  {p.why}")
        return 0
    if not a.inputs:
        ap.print_usage(sys.stderr)
        print("error: give at least one file, glob, directory or - for stdin", file=sys.stderr)
        return 2

    files, errors = collect_inputs(a.inputs, a.exclude + DEFAULT_EXCLUDES)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    if errors:
        return 2
    if not files:
        print("no files matched", file=sys.stderr)
        if fmt == "github":
            print(f"::notice title=AI Slop Detector::{_esc_data('No files matched: ' + ' '.join(a.inputs))}")

    results = []
    for f in files:
        name = a.stdin_name if f == "-" else os.path.relpath(f)
        r = {"file": name}
        if f == "-":
            r["stdin"] = True
        try:
            text = read_input(f)
        except (OSError, ValueError) as e:
            r["skipped"] = str(e)
            results.append(r)
            continue
        res = analyze(text, patterns)
        if res["words"] < a.min_words:
            r.update(words=res["words"], skipped=f"{res['words']} words < --min-words {a.min_words}")
        else:
            r.update(res)
        results.append(r)

    if fmt == "json":
        print(json.dumps({"tool": "ai-slop-detector", "threshold": THRESHOLD, "fail_above": a.fail_above,
                          "note": "Style signals only, not proof of AI authorship.", "results": results}, indent=2))
    elif fmt == "github":
        text = fmt_github(results, a.fail_above, a.max_annotations)
        if text:
            print(text)
    else:
        print(fmt_text(results, a.top))
    if a.fail_above is not None and any(not r.get("skipped") and r["score"] > a.fail_above for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
