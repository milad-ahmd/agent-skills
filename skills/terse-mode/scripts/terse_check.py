#!/usr/bin/env python3
"""Measure padding in an agent reply, and check that a shortened version kept what matters. Stdlib only.

Usage:
  python3 terse_check.py [FILE|-] [--json] [--max-pct 0]
  python3 terse_check.py --compare BEFORE AFTER [--json]

Check mode counts the prose words (code blocks and `inline code` are never counted or flagged) and
finds padding: preamble ("Great question!", "Sure, I'll now..."), restating the question, recaps
("In summary, ..."), offers ("Let me know if...", "Hope this helps"), filler phrases ("it's worth
noting that", "in order to") and hedge stacks (two or more hedges in one sentence). It reports each
hit with its line and the words it would save.

Compare mode checks that AFTER still contains everything from BEFORE that must survive shortening:
fenced code blocks, inline code, URLs, file paths, numbers, error lines and warning words
(irreversible, cannot be undone, permanently, data loss, back up, destructive, warning, caution).
It also reports the words saved.

Exit codes: 0 check: padding at or below --max-pct (default 0, so: none found) · compare: nothing lost
            1 check: padding above --max-pct · compare: something that must be kept is missing
            2 usage error or unreadable file
"""
from __future__ import annotations

import argparse
import json
import re
import sys

FENCE = re.compile(r"^\s*(```|~~~)")
INLINE = re.compile(r"`[^`\n]+`")
WORD = re.compile(r"[A-Za-z0-9][\w'’./:-]*")

# Whole sentences that can go. Matched at the start of a sentence, case-insensitive.
SENTENCE_RULES = [
    ("preamble", r"(great|good|excellent|interesting) question\b"),
    ("preamble", r"(sure|certainly|absolutely|of course|okay|ok|got it)\s*[!,.]"),
    ("preamble", r"(sure thing|no problem)\b"),
    ("preamble", r"(i'?d|i would|i'?m|i am) (be )?(happy|glad|delighted) to\b"),
    ("preamble", r"thanks? (you )?for (asking|the question|sharing|reaching out)\b"),
    ("preamble", r"(i'?ll|i will|let me|now i'?ll|i'?m going to) (now )?(take a look|look into|walk you through|"
                 r"explain|go through|break (this|it) down|help you)\b"),
    ("preamble", r"here'?s (what i found|a (quick |brief )?(overview|breakdown|summary))\s*[:.]?\s*$"),
    ("restate", r"(you'?re asking|you asked|you want to know|you'?d like to know|so you want|to answer your question|"
                r"your question is)\b"),
    ("recap", r"(in summary|to summari[sz]e|in conclusion|to recap|to sum up|to wrap up|overall)\b[,:]?"),
    ("recap", r"as (i )?(mentioned|noted|shown|discussed) (above|earlier|before)\b"),
    ("offer", r"(let me know if|feel free to|don'?t hesitate to|(i )?hope (this|that) helps|happy coding|"
              r"if you have any (other |more |further )?questions|is there anything else|"
              r"would you like me to (also )?(explain|elaborate|go into)|i can also (help|explain))\b"),
]
# Phrases inside a sentence: (category, pattern, words saved). None = all the phrase's words.
PHRASE_RULES = [
    ("filler", r"\bit'?s worth (noting|mentioning|pointing out) that\b", None),
    ("filler", r"\bit (is|'s) important to (note|remember|understand) that\b", None),
    ("filler", r"\b(it should be noted|please note|note) that\b", None),
    ("filler", r"\bneedless to say\b", None),
    ("filler", r"\bas a matter of fact\b", None),
    ("filler", r"\bfor all intents and purposes\b", None),
    ("filler", r"\bat the end of the day\b", None),
    ("filler", r"\bin order to\b", 2),
    ("filler", r"\bdue to the fact that\b", 4),
    ("filler", r"\bat this point in time\b", 4),
    ("filler", r"\bin the event that\b", 3),
    ("filler", r"\b(basically|essentially|actually|simply|really|quite|very|literally|definitely)\b", None),
]
HEDGES = r"\b(i think|i believe|i guess|it seems( like)?|it appears|perhaps|possibly|probably|maybe|might|" \
         r"could potentially|potentially|somewhat|kind of|sort of|arguably|in some cases|to some extent)\b"
WARN_WORDS = ["irreversible", "cannot be undone", "can't be undone", "permanently", "data loss", "back up",
              "backup", "destructive", "warning", "caution", "danger", "do not run", "don't run", "never run"]
URL = re.compile(r"https?://[^\s)>\]`'\"]+")
PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2})?/?(?:[\w.-]+/)+[\w.-]+|(?<![\w/.])[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|"
                  r"java|rb|php|json|ya?ml|toml|md|sh|sql|env|lock|cfg|ini|txt|html|css)\b")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)*(?:%|[a-zA-Z]{1,3}\b)?")
ERROR_LINE = re.compile(r"\b[\w.]*(?:Error|Exception|ERR|FATAL|Traceback)\b[^\n]*")


def nwords(text):
    return len(WORD.findall(text))


def prose_lines(text):
    """[(line_no, prose)] with fenced blocks dropped and inline code removed."""
    out, in_code = [], False
    for i, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_code = not in_code
            continue
        if not in_code:
            out.append((i, INLINE.sub(" ", line)))
    return out


BLOCK_START = re.compile(r"^\s*(?:[-*+]|\d+[.)]|#+|>|\|)\s")


def paragraphs(text):
    """[(first_line_no, text)]: soft-wrapped prose lines joined, so a sentence split over lines is one sentence.
    List items, headings, quotes and table rows start a new paragraph."""
    out = []
    prev = None
    for line_no, line in prose_lines(text):
        if not line.strip():
            prev = None
            continue
        if prev is not None and not BLOCK_START.match(line) and prev == line_no - 1:
            first, joined = out[-1]
            out[-1] = (first, joined + " " + line.strip())
        else:
            out.append((line_no, line.strip()))
        prev = line_no
    return out


def sentences(line):
    stripped = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+|^\s*#+\s+|^\s*>\s*", "", line)
    return [s for s in re.split(r"(?<=[.!?])\s+", stripped) if s.strip()]


def check(text):
    hits, total = [], 0
    for line_no, line in paragraphs(text):
        total += nwords(line)
        for sent in sentences(line):
            lead = sent.lstrip("*_ ").lower()
            rule = next((cat for cat, rx in SENTENCE_RULES if re.match(rx, lead)), None)
            if rule:
                hits.append({"category": rule, "line": line_no, "text": sent.strip(), "words": nwords(sent)})
                continue
            for cat, rx, saved in PHRASE_RULES:
                for m in re.finditer(rx, sent, re.I):
                    hits.append({"category": cat, "line": line_no, "text": m.group(0),
                                 "words": saved if saved is not None else nwords(m.group(0))})
            hedges = [m.group(0) for m in re.finditer(HEDGES, sent, re.I)]
            if len(hedges) >= 2:
                hits.append({"category": "hedge stack", "line": line_no, "text": ", ".join(hedges),
                             "words": sum(nwords(h) for h in hedges[1:])})
    saved = sum(h["words"] for h in hits)
    by_cat = {}
    for h in hits:
        by_cat[h["category"]] = by_cat.get(h["category"], 0) + h["words"]
    pct = round(100.0 * saved / total, 1) if total else 0.0
    return {"prose_words": total, "removable_words": saved, "removable_pct": pct, "by_category": by_cat,
            "hits": hits}


def code_blocks(text):
    blocks, cur, in_code = [], [], False
    for line in text.splitlines():
        if FENCE.match(line):
            if in_code:
                blocks.append("\n".join(cur).strip())
                cur = []
            in_code = not in_code
            continue
        if in_code:
            cur.append(line)
    if in_code and cur:
        blocks.append("\n".join(cur).strip())
    return [b for b in blocks if b]


def must_keep(text):
    """{kind: [items]} that a shortened version has to contain verbatim."""
    prose = "\n".join(p for _, p in prose_lines(text))
    trim = lambda xs: [x.rstrip(".,;:!?") for x in xs]  # noqa: E731
    urls = trim(URL.findall(text))
    no_urls = URL.sub(" ", prose)
    items = {
        "code block": code_blocks(text),
        "inline code": [m.group(0)[1:-1] for m in INLINE.finditer(text)],
        "url": urls,
        "path": trim(PATH.findall(no_urls)),
        "number": NUMBER.findall(re.sub(PATH, " ", no_urls)),
        "error line": [m.group(0).strip().rstrip(".") for m in ERROR_LINE.finditer(prose)],
        "warning": [w for w in WARN_WORDS if w in prose.lower()],
    }
    return {k: list(dict.fromkeys(v)) for k, v in items.items() if v}


def squash(s):
    return re.sub(r"\s+", " ", s).strip()


def compare(before, after):
    keep = must_keep(before)
    flat_after = squash(after)
    lower_after = flat_after.lower()
    missing = []
    for kind, items in keep.items():
        for item in items:
            present = (item.lower() in lower_after) if kind == "warning" else (squash(item) in flat_after)
            if not present:
                missing.append({"kind": kind, "item": item})
    bw, aw = nwords(before), nwords(after)
    return {"before_words": bw, "after_words": aw, "saved_words": bw - aw,
            "saved_pct": round(100.0 * (bw - aw) / bw, 1) if bw else 0.0,
            "kept": sum(len(v) for v in keep.values()) - len(missing), "missing": missing}


def read(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="Exit codes: 0 ok · 1 padding above --max-pct, or compare lost something · 2 error",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", default="-", help="reply to check (default: stdin)")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="check AFTER kept what BEFORE must keep")
    ap.add_argument("--max-pct", type=float, default=0.0, help="padding %% allowed before exit 1 (default 0)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    try:
        if args.compare:
            before, after = read(args.compare[0]), read(args.compare[1])
        else:
            text = read(args.file)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.compare:
        r = compare(before, after)
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        else:
            print(f"{r['before_words']} -> {r['after_words']} words ({r['saved_words']} saved, {r['saved_pct']}%)")
            print(f"kept {r['kept']} must-keep item(s)")
            for m in r["missing"]:
                print(f"  MISSING {m['kind']}: {m['item'][:100]}")
        return 1 if r["missing"] else 0

    r = check(text)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        for h in r["hits"]:
            print(f"line {h['line']:>3}  {h['category']:<11}  -{h['words']:<3} {h['text'][:90]}")
        cats = ", ".join(f"{k} {v}" for k, v in sorted(r["by_category"].items(), key=lambda kv: -kv[1]))
        print(f"\n{r['prose_words']} prose words, about {r['removable_words']} removable "
              f"({r['removable_pct']}%){': ' + cats if cats else ''}")
    return 1 if r["removable_pct"] > args.max_pct else 0


if __name__ == "__main__":
    sys.exit(main())
