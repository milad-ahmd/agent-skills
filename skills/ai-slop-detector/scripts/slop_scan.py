#!/usr/bin/env python3
"""Score text for AI-slop *style signals* (not proof of authorship). Stdlib only.

Usage: python3 slop_scan.py <file|->
"""
import re
import statistics
import sys

PHRASES = [
    r"\bdelve[sd]?\b", r"\bdelving\b", r"in today'?s (fast[- ]paced|digital|ever[- ]changing)", r"\bever[- ]evolving\b",
    r"\btapestry\b", r"\btestament to\b", r"\bnavigat(e|ing) the (complexities|landscape|world)\b", r"\blandscape\b",
    r"\bseamless(ly)?\b", r"\bleverag(e|ing)\b", r"\bunlock(ing)? (the )?(power|potential)\b", r"\bgame[- ]chang(er|ing)\b",
    r"\belevate\b", r"\brobust\b", r"\bcutting[- ]edge\b", r"\bharness(ing)? the power\b", r"\bin the realm of\b",
    r"\bit'?s (important|worth|crucial) to (note|remember|mention)\b", r"\bplays? a (crucial|pivotal|vital) role\b",
    r"\bin conclusion\b", r"\bultimately,", r"\bmoreover,", r"\bfurthermore,", r"\bembark(ing)? on\b",
    r"\bnot (just|only) (a|an|about)\b.*\b(it'?s|but)\b", r"\bwhether you'?re\b", r"\bi hope this helps\b",
    r"\bfoster(ing)?\b", r"\bmyriad\b", r"\bparamount\b", r"\bbustling\b", r"\bvibrant\b", r"\bresonate[sd]?\b",
]
RX = [re.compile(p, re.I) for p in PHRASES]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    text = sys.stdin.read() if sys.argv[1] == "-" else open(sys.argv[1], encoding="utf-8").read()
    words = re.findall(r"\b\w+\b", text)
    if len(words) < 20:
        sys.exit("Need at least ~20 words to score.")
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for rx in RX:
            for m in rx.finditer(line):
                hits.append((i, m.group(0)))
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.split()) > 2]
    lens = [len(s.split()) for s in sentences]
    cv = statistics.pstdev(lens) / statistics.mean(lens) if len(lens) > 2 else 1
    dashes = text.count("—") + text.count(" -- ")
    per1k = lambda n: n * 1000 / len(words)
    triads = len(re.findall(r"\b\w+, \w+,? and \w+\b", text))
    score = min(100, round(per1k(len(hits)) * 4 + per1k(dashes) * 3 + max(0, 0.45 - cv) * 80 + per1k(triads) * 2))
    label = "🟢 low" if score < 25 else "🟡 medium" if score < 55 else "🔴 high"
    print(f"Slop-signal score: {score}/100  {label}   ({len(words)} words, {len(sentences)} sentences)")
    print(f"  cliché phrases: {len(hits)} ({per1k(len(hits)):.1f}/1k words)")
    print(f"  em-dashes: {dashes} ({per1k(dashes):.1f}/1k words)")
    print(f"  sentence-length variation: {cv:.2f} {'(uniform rhythm ⚠️)' if cv < 0.35 else '(varied ✓)'}")
    print(f"  'X, Y, and Z' triads: {triads}")
    if hits:
        print("\nFlagged:")
        for ln, p in hits[:15]:
            print(f"  line {ln:>3}: \"{p}\"")
    print("\nNote: style signals only, NOT proof of AI authorship.")


if __name__ == "__main__":
    main()
