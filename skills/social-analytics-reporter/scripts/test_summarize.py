#!/usr/bin/env python3
"""Tests for summarize.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/social-analytics-reporter/scripts -p 'test_*.py'
Each test writes a small synthetic export (YouTube Studio, TikTok, Meta Business Suite, LinkedIn, X,
Meta Ads) to a temp dir and checks number parsing, column detection, the
total/mean/median/max table, ranking, and clean failures on empty, broken or missing files.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import summarize as sm  # noqa: E402

SCRIPT = os.path.join(HERE, "summarize.py")

YOUTUBE = """Content,Video title,Video publish time,Duration,Views,Watch time (hours),Subscribers,Impressions,Impressions click-through rate (%)
Total,,,,"6,000",300.5,40,"60,000",5.5
vid001,How we cut build time in half,"Mar 3, 2026",612,"3,000",150.25,25,"30,000",6.1
vid002,Five terminal tricks,"Mar 10, 2026",305,"2,000",100.25,10,"20,000",5.2
vid003,Live Q&A recap,"Mar 17, 2026",1800,"1,000",50,5,"10,000",4.9
"""
TIKTOK = """Video title,Video link,Post time,Total likes,Total comments,Total shares,Total views
Desk setup tour,https://example.com/v/1,2026-03-01 18:00,1.2K,40,15,10K
Morning routine,https://example.com/v/2,2026-03-02 18:00,300,12,3,2.5K
Cable management fail,https://example.com/v/3,2026-03-04 18:00,4.5k,210,90,1.1M
"""
META = "﻿" + """Post ID,Account name,Description,Publish time,Post type,Reach,Likes,Comments,Shares,Saves
1001_2001,Example Bakery,New sourdough drop,03/01/2026 09:00,Reel,"12,400",820,41,33,90
1001_2002,Example Bakery,Behind the oven,03/05/2026 09:00,Photo,"3,100",150,9,2,12
1001_2003,Example Bakery,Weekend hours,03/08/2026 09:00,Photo,"1,900",60,,1,
"""
LINKEDIN = """Post title,Post link,Created date,Impressions,Clicks,Click through rate (CTR),Likes,Comments,Reposts,Engagement rate
Hiring a backend engineer,https://example.com/p/1,03/02/2026,"8,000",240,3.00%,120,14,6,4.75%
Our Q1 roadmap,https://example.com/p/2,03/09/2026,"2,000",20,1.00%,30,2,0,2.60%
"""
X_EXPORT = """Tweet id,Tweet permalink,Tweet text,time,impressions,engagements,engagement rate,retweets,replies,likes
1800000000000000001,https://example.com/s/1,Shipping day,2026-03-01 10:00 +0000,5000,250,0.05,10,5,80
1800000000000000002,https://example.com/s/2,Small fix,2026-03-02 10:00 +0000,1000,10,0.01,0,1,4
"""
META_ADS = """Campaign name,Reach,Impressions,Link clicks,Amount spent (USD),CTR (link click-through rate)
Spring promo,"20,000","45,000",900,250.40,2.00
Retargeting,"5,000","15,000",600,99.60,4.00
"""


def table(out):
    """Parse the metric table into {metric: (total, mean, median, max)}."""
    lines = out.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("metric")) + 1
    rows = {}
    for line in lines[start:]:
        if not line.strip():
            break
        rows[line[:30].strip()] = tuple(float(x.replace(",", "")) for x in line[30:].split())
    return rows


def ranked(out, which):
    lines = out.splitlines()
    i = next(i for i, l in enumerate(lines) if l.startswith(which + " ")) + 1
    got = []
    while i < len(lines) and lines[i].startswith("  "):
        value, _, label = lines[i].strip().partition("  ")
        got.append((float(value.replace(",", "")), label.strip()))
        i += 1
    return got


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)

    def write(self, text, name="export.csv", mode="w"):
        path = os.path.join(self._td.name, name)
        with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8", "newline": ""})) as fh:
            fh.write(text)
        return path

    def run_main(self, text, *args):
        """Return (exit message or None, stdout)."""
        out = io.StringIO()
        path = text if os.path.isabs(text) else self.write(text)
        with mock.patch.object(sys, "argv", ["summarize.py", path, *args]), redirect_stdout(out):
            try:
                sm.main()
            except SystemExit as e:
                return str(e.code), out.getvalue()
        return None, out.getvalue()


class ToNumber(unittest.TestCase):
    def test_plain_and_formatted(self):
        cases = {"42": 42, " 3.5 ": 3.5, "1,234": 1234, "12.5%": 12.5, "-7": -7, "0": 0,
                 "1.2K": 1200, "4.5k": 4500, "2M": 2_000_000, "1.1m": 1_100_000, 17: 17, 2.5: 2.5}
        for raw, want in cases.items():
            self.assertAlmostEqual(sm.to_number(raw), want, msg=repr(raw))

    def test_placeholders_and_junk(self):
        for raw in (None, "", "  ", "-", "--", "N/A", "n/a", "K", "abc", "2026-03-01", "Mar 3, 2026", "1001_2001"):
            self.assertIsNone(sm.to_number(raw), repr(raw))


class KpiMaths(Base):
    def test_total_mean_median_max(self):
        err, out = self.run_main("post,views,likes\na,10,1\nb,20,\nc,30,5\nd,100,6\n")
        self.assertIsNone(err)
        t = table(out)
        self.assertEqual(t["views"], (160, 40.0, 25.0, 100))  # even count: median of 20 and 30
        self.assertEqual(t["likes"], (12, 4.0, 5.0, 6))  # the blank is skipped, not counted as 0
        self.assertIn("Rows: 4  |  Columns: 3", out)

    def test_numeric_threshold_is_half_the_rows(self):
        csv_text = "name,half,few\n" + "".join("r%d,%s,%s\n" % (i, i if i < 2 else "x", 1 if i == 0 else "x")
                                               for i in range(5))
        t = table(self.run_main(csv_text)[1])
        self.assertIn("half", t)  # 2 of 5 numeric (5 // 2 = 2)
        self.assertNotIn("few", t)  # 1 of 5 numeric

    def test_single_row(self):
        t = table(self.run_main("title,views\nonly one,7\n")[1])
        self.assertEqual(t["views"], (7, 7.0, 7.0, 7))


class Formats(Base):
    def test_youtube_total_row_not_double_counted(self):
        err, out = self.run_main(YOUTUBE, "--sort", "views", "--top", "2")
        self.assertIsNone(err)
        t = table(out)
        self.assertIn("Rows: 3", out)
        self.assertEqual(t["Views"], (6000, 2000.0, 2000.0, 3000))
        self.assertEqual(t["Impressions click-through rat"][1:3], (5.4, 5.2))  # name cut to 29 chars
        self.assertNotIn("Video publish time", t)
        self.assertEqual(ranked(out, "TOP"), [(3000, "How we cut build time in half"), (2000, "Five terminal tricks")])

    def test_tiktok_k_and_m_suffixes(self):
        err, out = self.run_main(TIKTOK, "--sort", "Total views", "--top", "1")
        t = table(out)
        self.assertEqual(t["Total likes"], (6000, 2000.0, 1200.0, 4500))
        self.assertEqual(t["Total views"][0], 1_112_500)
        self.assertEqual(ranked(out, "TOP"), [(1_100_000, "Cable management fail")])
        self.assertEqual(ranked(out, "BOTTOM"), [(2500, "Morning routine")])

    def test_meta_business_suite_bom_and_blanks(self):
        t = table(self.run_main(META)[1])
        self.assertIn("Reach", t)  # BOM stripped from the first header, so columns line up
        self.assertEqual(t["Reach"], (17400, 5800.0, 3100.0, 12400))
        self.assertEqual(t["Comments"], (50, 25.0, 25.0, 41))
        self.assertEqual(t["Saves"], (102, 51.0, 51.0, 90))
        self.assertNotIn("Post ID", t)  # page_post IDs are not a metric

    def test_linkedin_percent_columns(self):
        err, out = self.run_main(LINKEDIN, "--sort", "engagement rate", "--top", "1")
        t = table(out)
        self.assertEqual(t["Impressions"], (10000, 5000.0, 5000.0, 8000))
        self.assertEqual(t["Engagement rate"][1:3], (3.7, 3.7))  # mean of rates, not a sum
        self.assertEqual([label for _, label in ranked(out, "TOP")], ["Hiring a backend engineer"])

    def test_x_export(self):
        err, out = self.run_main(X_EXPORT, "--sort", "impressions")
        t = table(out)
        self.assertEqual(t["impressions"], (6000, 3000.0, 3000.0, 5000))
        self.assertEqual(ranked(out, "TOP")[0][0], 5000)
        self.assertTrue(all(len(v) == 4 for v in t.values()), t)  # 19-digit sums must not run columns together

    def test_meta_ads(self):
        t = table(self.run_main(META_ADS)[1])
        self.assertEqual(t["Amount spent (USD)"][:3], (350, 175.0, 175.0))
        self.assertEqual(t["Link clicks"], (1500, 750.0, 750.0, 900))


class Ranking(Base):
    DATA = "Caption,Views\nalpha,10\nbeta,40\ngamma,\ndelta,30\nepsilon,20\n"

    def test_top_and_bottom_case_insensitive(self):
        err, out = self.run_main(self.DATA, "--sort", "VIEWS", "--top", "2")
        self.assertIsNone(err)
        self.assertIn("TOP 2 by Views:", out)
        self.assertEqual(ranked(out, "TOP"), [(40, "beta"), (30, "delta")])
        self.assertEqual(ranked(out, "BOTTOM"), [(10, "alpha"), (20, "epsilon")])  # blank row left out

    def test_default_top_five_and_more_than_rows(self):
        out = self.run_main(self.DATA, "--sort", "views", "--top", "10")[1]
        self.assertEqual(len(ranked(out, "TOP")), 4)
        self.assertEqual(ranked(out, "BOTTOM")[0], (10, "alpha"))

    def test_top_zero_lists_nothing(self):
        out = self.run_main(self.DATA, "--sort", "views", "--top", "0")[1]
        self.assertEqual((ranked(out, "TOP"), ranked(out, "BOTTOM")), ([], []))

    def test_sort_unknown_or_text_column(self):
        for col in ("shares", "caption"):
            err, _ = self.run_main(self.DATA, "--sort", col)
            self.assertIn("not found or not numeric", err)
            self.assertIn("Numeric columns: Views", err)

    def test_no_text_column(self):
        out = self.run_main("views,likes\n5,1\n9,2\n", "--sort", "likes")[1]
        self.assertEqual(ranked(out, "TOP"), [(2, ""), (1, "")])


class BrokenInput(Base):
    def test_empty_file(self):
        self.assertEqual(self.run_main(self.write(""))[0], "No rows found.")

    def test_header_only(self):
        self.assertEqual(self.run_main("title,views\n")[0], "No rows found.")

    def test_only_a_total_row(self):
        self.assertEqual(self.run_main("Content,Views\nTotal,10\n")[0], "No rows found.")

    def test_short_rows_missing_columns(self):
        err, out = self.run_main("title,views,likes\na,10,1\nb,20\nc\n")
        self.assertIsNone(err)
        self.assertEqual(table(out)["views"], (30, 15.0, 15.0, 20))
        self.assertEqual(table(out)["likes"], (1, 1.0, 1.0, 1))

    def test_extra_cells_in_first_row(self):
        err, out = self.run_main("title,views\na,10,EXTRA\nb,20\n")
        self.assertIsNone(err)
        self.assertIn("Columns: 2", out)
        self.assertEqual(table(out)["views"], (30, 15.0, 15.0, 20))

    def test_missing_and_binary_files(self):
        err, _ = self.run_main(os.path.join(self._td.name, "nope.csv"))
        self.assertIn("Cannot read", err)
        err, _ = self.run_main(self.write(b"PK\x03\x04\xff\xfe binary xlsx", "export.xlsx", "wb"))
        self.assertIn("Cannot read", err)


class Cli(Base):
    def test_subprocess_run(self):
        r = subprocess.run([sys.executable, SCRIPT, self.write(TIKTOK), "--sort", "total likes"], capture_output=True,
                           text=True, encoding="utf-8", timeout=60, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("TOP 5 by Total likes:", r.stdout)

    def test_subprocess_failure_has_no_traceback(self):
        r = subprocess.run([sys.executable, SCRIPT, self.write("")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.strip(), "No rows found.")


if __name__ == "__main__":
    unittest.main()
