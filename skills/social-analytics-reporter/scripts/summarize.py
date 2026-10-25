#!/usr/bin/env python3
"""Summarize a social media analytics CSV export (stdlib only).

Usage:
  python3 summarize.py export.csv [--sort COLUMN] [--top N]
"""
import argparse
import csv
import statistics
import sys


def to_number(value):
    if value is None:
        return None
    v = str(value).strip().replace(",", "").replace("%", "")
    if v in ("", "-", "--", "N/A", "n/a"):
        return None
    mult = 1
    if v[-1:].upper() in ("K", "M"):
        mult = 1_000 if v[-1].upper() == "K" else 1_000_000
        v = v[:-1]
    try:
        return float(v) * mult
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_file")
    ap.add_argument("--sort", help="column to rank rows by (case-insensitive)")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("No rows found.")

    cols = list(rows[0].keys())
    numeric = [c for c in cols if sum(to_number(r[c]) is not None for r in rows) >= max(1, len(rows) // 2)]
    text_cols = [c for c in cols if c not in numeric]
    label_col = next((c for c in text_cols if any(k in c.lower() for k in ("title", "caption", "post", "name", "description"))),
                     text_cols[0] if text_cols else None)

    print(f"Rows: {len(rows)}  |  Columns: {len(cols)}")
    print(f"\n{'metric':<30}{'total':>14}{'mean':>12}{'median':>12}{'max':>14}")
    for c in numeric:
        vals = [n for n in (to_number(r[c]) for r in rows) if n is not None]
        print(f"{c[:29]:<30}{sum(vals):>14,.0f}{statistics.mean(vals):>12,.1f}{statistics.median(vals):>12,.1f}{max(vals):>14,.0f}")

    if args.sort:
        key = next((c for c in cols if c.lower() == args.sort.lower()), None)
        if key is None or key not in numeric:
            sys.exit(f"\nColumn '{args.sort}' not found or not numeric. Numeric columns: {', '.join(numeric)}")
        ranked = sorted((r for r in rows if to_number(r[key]) is not None), key=lambda r: to_number(r[key]), reverse=True)
        for title, part in (("TOP", ranked[: args.top]), ("BOTTOM", ranked[-args.top:][::-1])):
            print(f"\n{title} {args.top} by {key}:")
            for r in part:
                label = (r.get(label_col) or "")[:60] if label_col else ""
                print(f"  {to_number(r[key]):>12,.0f}  {label}")


if __name__ == "__main__":
    main()
