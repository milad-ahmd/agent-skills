#!/usr/bin/env python3
"""Evaluate slop_scan.py on the labeled benchmark in ../bench. Stdlib only.

Usage:
  bench.py [--split test|dev|all] [--threshold N] [-v] [--json]

Reports ROC-AUC (probability that a random LLM-written sample outscores a random human one;
ties count half), and precision / recall / accuracy for "flag as AI-sounding when score >= threshold".
The default split is test: weights and the threshold were chosen on dev only.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slop_scan  # noqa: E402

BENCH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench")


def load(split: str) -> list[dict]:
    with open(os.path.join(BENCH, "labels.csv"), newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if split == "all" or r["split"] == split]
    for r in rows:
        with open(os.path.join(BENCH, r["label"], r["id"] + ".txt"), encoding="utf-8") as fh:
            r["text"] = fh.read()
        r["hard"] = r["hard"] == "1"
    return rows


def auc(pos: list[float], neg: list[float]) -> float:
    """Mann-Whitney estimate of ROC-AUC."""
    if not pos or not neg:
        return float("nan")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def confusion(rows: list[dict], key: str, thr: float) -> dict:
    tp = sum(1 for r in rows if r["label"] == "ai" and r[key] >= thr)
    fp = sum(1 for r in rows if r["label"] == "human" and r[key] >= thr)
    fn = sum(1 for r in rows if r["label"] == "ai" and r[key] < thr)
    tn = sum(1 for r in rows if r["label"] == "human" and r[key] < thr)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec,
            "accuracy": (tp + tn) / len(rows) if rows else float("nan")}


def evaluate(rows: list[dict], thr: float) -> dict:
    patterns = slop_scan.load_patterns()
    for r in rows:
        res = slop_scan.analyze(r["text"], patterns)
        r["score"], r["words"], r["hits"] = res["score"], res["words"], len(res["hits"])
        r["signals"] = {k: v["signal"] for k, v in res["components"].items()}
    ai = [r["score"] for r in rows if r["label"] == "ai"]
    hu = [r["score"] for r in rows if r["label"] == "human"]
    out = {"n_ai": len(ai), "n_human": len(hu), "threshold": thr, "auc": auc(ai, hu), **confusion(rows, "score", thr)}
    out["component_auc"] = {k: auc([r["signals"][k] for r in rows if r["label"] == "ai"],
                                   [r["signals"][k] for r in rows if r["label"] == "human"]) for k in slop_scan.WEIGHTS}
    hard = [r for r in rows if r["hard"]]
    out["hard"] = {"human_flagged": sum(r["score"] >= thr for r in hard if r["label"] == "human"),
                   "human_n": sum(r["label"] == "human" for r in hard),
                   "ai_flagged": sum(r["score"] >= thr for r in hard if r["label"] == "ai"),
                   "ai_n": sum(r["label"] == "ai" for r in hard)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=["test", "dev", "all"], default="test")
    ap.add_argument("--threshold", type=float, default=slop_scan.THRESHOLD)
    ap.add_argument("-v", "--verbose", action="store_true", help="per-sample scores")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = load(a.split)
    res = evaluate(rows, a.threshold)
    if a.json:
        res["samples"] = [{k: r[k] for k in ("id", "label", "split", "hard", "genre", "words", "score", "hits")} for r in rows]
        print(json.dumps(res, indent=2))
        return 0
    if a.verbose:
        print(f"{'id':<5} {'label':<6} {'hard':<5} {'words':>5} {'hits':>4} {'score':>5}  genre / topic")
        for r in sorted(rows, key=lambda r: (r["label"], -r["score"])):
            flag = " <- flagged" if r["score"] >= a.threshold else ""
            print(f"{r['id']:<5} {r['label']:<6} {str(r['hard']):<5} {r['words']:>5} {r['hits']:>4} {r['score']:>5}  "
                  f"{r['genre']} / {r['topic']}{flag}")
        print()
    print(f"split={a.split}  n={res['n_ai']} LLM-written + {res['n_human']} human  threshold={a.threshold:g}")
    print(f"ROC-AUC            {res['auc']:.3f}")
    print(f"precision          {res['precision']:.3f}  ({res['tp']} of {res['tp'] + res['fp']} flagged texts are LLM-written)")
    print(f"recall             {res['recall']:.3f}  ({res['tp']} of {res['n_ai']} LLM-written texts flagged)")
    print(f"accuracy           {res['accuracy']:.3f}")
    print(f"false positives    {res['fp']} of {res['n_human']} human texts flagged")
    h = res["hard"]
    print(f"hard cases         {h['human_flagged']}/{h['human_n']} formal/corporate human texts flagged, "
          f"{h['ai_flagged']}/{h['ai_n']} casual-prompted LLM texts flagged")
    print("component AUCs     " + ", ".join(f"{k} {v:.2f}" for k, v in res["component_auc"].items()))
    print("\nSmall, single-author benchmark: read bench/README.md before quoting these numbers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
