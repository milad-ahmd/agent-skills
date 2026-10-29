#!/usr/bin/env python3
"""Lint a feature spec (SPEC.md) for testability and traceability. Stdlib only, Python 3.9+.

Usage: python3 spec_lint.py [SPEC.md] [--json] [--fail-on error|warning] [--require KEYS]
  SPEC.md    the spec to check (default: ./SPEC.md)
  --require  comma-separated section keys that must exist and be non-empty. Default: problem,goals,
             non-goals,actors,requirements,acceptance,edge-cases,data,rollout,risks,open-questions,
             test-plan. "decisions" is also accepted.
  --fail-on  lowest severity that makes the exit code 1 (default: error)
  --json     machine-readable output

Expected shape (see ../references/spec-template.md): sections are markdown headings. Requirements
are list items or subheadings starting R1, R2... (a plain numbered list counts as R1, R2...).
Acceptance criteria start AC1, AC2..., cite the requirement IDs they check and read
Given ... when ... then .... The test plan names every AC ID. Open questions carry "owner: name"
or an @handle (or sit in a table with an Owner column). HTML comments and fenced code are ignored.

Exit codes: 0 nothing at or above --fail-on · 1 findings at or above it · 2 file missing, unreadable
or not a markdown spec (no headings)
"""
import argparse
import json
import os
import re
import sys

SEVERITIES = ["error", "warning"]
KEYS = ["problem", "goals", "non-goals", "actors", "requirements", "acceptance", "edge-cases", "data",
        "rollout", "risks", "open-questions", "test-plan", "decisions"]
DEFAULT_REQUIRE = [k for k in KEYS if k != "decisions"]
TITLE = {"problem": "Problem", "goals": "Goals", "non-goals": "Non-goals", "actors": "Users and actors",
         "requirements": "Requirements", "acceptance": "Acceptance criteria", "edge-cases": "Edge cases and error states",
         "data": "Data and API changes", "rollout": "Rollout, migration and flags", "risks": "Risks",
         "open-questions": "Open questions", "test-plan": "Test plan", "decisions": "Decisions"}
# Matched with re.search against a normalised heading; one heading may fill several keys.
SECTION_RX = {
    "problem": r"\b(?:problem|background|context|motivation|why)\b",
    "goals": r"(?<!non )\b(?:goals?|objectives?)\b",
    "non-goals": r"\bnon goals?\b|\bout of scope\b|\bnot in scope\b",
    "actors": r"\b(?:users?|actors?|personas?|roles?|who)\b",
    "requirements": r"\b(?:requirements?|behaviou?rs?)\b",
    "acceptance": r"\bacceptance\b",
    "edge-cases": r"\bedge cases?\b|\berror states?\b|\bfailure modes?\b|\berrors?\b",
    "data": r"\b(?:data|api|apis|schema|interfaces?)\b",
    "rollout": r"\b(?:rollout|roll out|migrations?|release|launch|deploy(?:ment)?|flags?)\b",
    "risks": r"\brisks?\b",
    "open-questions": r"\bquestions?\b",
    "test-plan": r"^(?:test|tests|testing|verification)\b",
    "decisions": r"\bdecisions?\b",
}
SECTION_RX = {k: re.compile(v) for k, v in SECTION_RX.items()}

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}")
NON_GOALS_LABEL = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?(?:non[- ]goals?|out of scope)(?:\*\*|__)?\s*:(.*)$", re.I)
PLACEHOLDER = re.compile(r"<([A-Za-z][^<>\n]*)>")
HTML_TAGS = {"a", "abbr", "b", "br", "code", "details", "div", "em", "hr", "i", "img", "kbd", "li", "ol", "p", "pre",
             "s", "small", "span", "strong", "sub", "summary", "sup", "table", "td", "th", "tr", "u", "ul"}
OWNER = re.compile(r"\bowners?\s*[:=]\s*[^\s|)]|@[A-Za-z0-9_][\w.-]*", re.I)
NONE_ITEM = re.compile(r"^(?:none|n/?a|nothing|no open questions)\b", re.I)
GWT = re.compile(r"\bgiven\b.*\bwhen\b.*\bthen\b", re.I | re.S)
REQ_REF = re.compile(r"\bR-?(\d+)\b")
AC_REF = re.compile(r"\bAC-?(\d+)\b")
WEASEL = re.compile(r"\b(should|could|might)\b", re.I)
CONTROL = re.compile(r"\d|encrypt|tls|hash|role|permission|auth|csrf|owasp|token|sign|rate.?limit|audit", re.I)

# (pattern, kind, rule, fix). kind: "always" flags every hit; "measurable" skips items that contain
# a number; "secure" skips items that name a number or a concrete control.
VAGUE = [
    (r"\b(?:fast|faster|quick|quickly|slow|speedy|snappy|instant(?:ly)?|real[- ]time|responsive|performant|efficient(?:ly)?)\b",
     "measurable", "give the number: p95 latency, time to complete, throughput"),
    (r"\bscal(?:able|ably|es well)\b", "measurable", "state the load it must handle: requests/s, rows, concurrent users"),
    (r"\b(?:optimal|optimi[sz]ed?|minimi[sz]ed?|maximi[sz]ed?)\b", "measurable", "state the target value"),
    (r"\b(?:robust|reliabl[ey]|resilient)\b", "measurable", "name the failure it must survive and how a test shows it"),
    (r"\bsecure(?:ly)?\b", "secure", "name the control: who may do what, which check, which encryption"),
    (r"\b(?:user[- ]friendly|intuitive|easy to use|easy|simple|seamless(?:ly)?|smooth(?:ly)?)\b", "always",
     "describe what the user sees or does, or a task outcome you can measure"),
    (r"\betc\b\.?|\band so on\b|\band the like\b", "always", "list every case; an open-ended list can't be tested"),
    (r"\bas (?:needed|necessary|appropriate|required)\b|\bwhere (?:possible|appropriate|applicable)\b|\bif (?:needed|necessary)\b"
     r"|\bappropriate(?:ly)?\b|\bproper(?:ly)?\b|\bsufficient(?:ly)?\b|\breasonabl[ey]\b", "always", "state the exact condition or value"),
    (r"\b(?:some|various|several|many|few|a number of|lots of)\b", "always", "give the number or list the cases"),
    (r"\bhandles? (?:all )?errors? gracefully\b|\bgraceful(?:ly)?\b", "always", "name each error and what the user sees or the system does"),
    (r"\bshould probably\b|\bprobably\b|\bmaybe\b|\bperhaps\b|\bideally\b|\bhopefully\b|\bif possible\b", "always",
     "decide: make it a 'must', move it to non-goals, or make it an open question with an owner"),
]
VAGUE = [(re.compile(p, re.I), kind, fix) for p, kind, fix in VAGUE]
UNRESOLVED = re.compile(r"\b(?:TBD|TBC|TODO|FIXME)\b|\?\?\?")


class Unparseable(Exception):
    pass


def norm(title):
    t = title.lower().replace("*", "").replace("`", "")
    t = re.sub(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[ivx]+\.)\s+", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[-_/&,:;()'\".]+", " ", t)).strip()


def strip_code_spans(s):
    return re.sub(r"`[^`]*`", " ", s)


class Spec:
    def __init__(self, text):
        text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
        self.lines, fence = [], None  # (lineno, text, in_code)
        for i, ln in enumerate(text.split("\n"), 1):
            m = FENCE.match(ln)
            if fence:
                self.lines.append((i, ln, True))
                if m and m.group(1) == fence:
                    fence = None
            else:
                if m:
                    fence = m.group(1)
                self.lines.append((i, ln, bool(m)))
        heads = []
        for idx, (no, ln, code) in enumerate(self.lines):
            m = None if code else HEADING.match(ln)
            if m:
                heads.append({"level": len(m.group(1)), "title": m.group(2), "line": no, "idx": idx})
        if not heads:
            raise Unparseable("no markdown headings found; is this a spec?")
        for i, h in enumerate(heads):
            h["end"] = next((x["idx"] for x in heads[i + 1:] if x["level"] <= h["level"]), len(self.lines))
        self.heads = heads
        skip_h1 = any(h["level"] > 1 for h in heads)
        self.sections = {}
        for h in heads:
            if skip_h1 and h["level"] == 1:
                continue
            n = norm(h["title"])
            for key, rx in SECTION_RX.items():
                if key not in self.sections and rx.search(n):
                    self.sections[key] = h

    def body(self, h):
        return self.lines[h["idx"] + 1:h["end"]]

    def has_content(self, h):
        return any(ln.strip() for _, ln, _ in self.body(h))

    def items(self, h, prefix):
        """Top-level list items, subheadings or table rows of a section.

        Returns dicts: id (or None), explicit (ID written in the text), numbered (list number), line,
        text, heading (a subheading without an ID: a group label, not an item), cells (table rows).
        """
        body = [(no, ln) for no, ln, code in self.body(h) if not code]
        indents = [len(m.group(1).expandtabs(4)) for _, ln in body for m in [ITEM.match(ln)] if m]
        base = min(indents) if indents else 0
        id_rx = re.compile(r"^(?:\*\*|__|\[|`)?\s*" + prefix + r"-?(\d+)\b")
        out, cur = [], None

        def start(no, text, numbered=None, heading=False):
            m = id_rx.match(text)
            it = {"id": prefix + m.group(1) if m else None, "explicit": bool(m), "numbered": numbered,
                  "line": no, "text": text, "heading": heading and not m, "subheading": heading, "cells": None}
            out.append(it)
            return it

        for no, ln in body:
            hm = HEADING.match(ln)
            im = ITEM.match(ln)
            if hm:
                cur = start(no, hm.group(2), heading=True)
            elif im and len(im.group(1).expandtabs(4)) <= base + 1 and not (cur and cur["subheading"] and cur["explicit"]):
                # a list under "### AC1" (e.g. - Given / - When / - Then) belongs to AC1
                marker = im.group(2)
                cur = start(no, im.group(3), numbered=int(marker[:-1]) if marker[0].isdigit() else None)
            elif cur is not None:
                cur["text"] += "\n" + ln
        if not any(not it["heading"] for it in out):
            rows = [(no, ln) for no, ln in body if ln.strip().startswith("|")]
            if len(rows) >= 2 and TABLE_SEP.match(rows[1][1]):
                header = [c.strip().lower() for c in rows[0][1].strip().strip("|").split("|")]
                out = []
                for no, ln in rows[2:]:
                    cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                    it = start(no, " | ".join(cells))
                    it["cells"] = dict(zip(header, cells))
        return out


def lint(text, require=None):
    spec = Spec(text)
    require = DEFAULT_REQUIRE if require is None else require
    findings = []

    def add(rule, sev, line, msg, fix="", ident=None):
        findings.append({"rule": rule, "severity": sev, "line": line, "id": ident, "message": msg, "fix": fix})

    # --- sections ---------------------------------------------------------------------------
    for key in require:
        h = spec.sections.get(key)
        if key == "non-goals":
            check_non_goals(spec, add)
            continue
        if h is None:
            add("missing-section", "error", 1, f"no '{TITLE[key]}' section",
                f"add '## {TITLE[key]}' (write 'None: <reason>' if it really doesn't apply)")
        elif not spec.has_content(h):
            add("empty-section", "error", h["line"], f"'{h['title']}' is empty", "fill it in, or write 'None:' and the reason")
    for no, ln, code in spec.lines:
        if not code and not HEADING.match(ln):
            for m in PLACEHOLDER.finditer(strip_code_spans(ln)):
                inner = m.group(1)
                if "=" in inner or "://" in inner or re.fullmatch(r"[\w.+-]+@[\w.-]+", inner) \
                        or inner.split()[0].lower().rstrip("/") in HTML_TAGS:
                    continue
                add("placeholder", "error", no, f"template placeholder left in: {m.group(0)}", "replace it with the real content")

    # --- requirements -----------------------------------------------------------------------
    reqs, crits = [], []
    weasels = 0
    if "requirements" in spec.sections:
        reqs = number_items(spec.items(spec.sections["requirements"], "R"), "R", "requirement", add)
        if not reqs:
            add("no-requirements", "error", spec.sections["requirements"]["line"], "the requirements section has no items",
                "list them as 'R1 The system must ...'")
        for r in reqs:
            text = strip_code_spans(r["text"])
            scan_vague(text, r, add)
            words = [w.lower() for w in WEASEL.findall(re.sub(r"\bshould probably\b", " ", text, flags=re.I))]
            weasels += len(words)
            if words:
                add("weasel-modal", "warning", r["line"], f"{r['id']} says '{', '.join(sorted(set(words)))}'",
                    "a requirement is an obligation: use 'must', or move it to non-goals or open questions", r["id"])

    # --- acceptance criteria ----------------------------------------------------------------
    if "acceptance" in spec.sections:
        raw, group_refs = [], set()
        for it in spec.items(spec.sections["acceptance"], "AC"):
            if it["heading"]:
                group_refs = set(REQ_REF.findall(it["text"]))
                continue
            it["refs"] = {"R" + n for n in REQ_REF.findall(it["text"])} or {"R" + n for n in group_refs}
            raw.append(it)
        crits = number_items(raw, "AC", "criterion", add)
        if not crits:
            add("no-criteria", "error", spec.sections["acceptance"]["line"], "the acceptance criteria section has no items",
                "write 'AC1 (R1) Given ... when ... then ...'")
        req_ids = {r["id"] for r in reqs}
        for c in crits:
            if not GWT.search(c["text"]):
                add("not-gwt", "error", c["line"], f"{c['id']} is not written as Given / When / Then",
                    "state the starting state, the action and the observable result", c["id"])
            scan_vague(strip_code_spans(c["text"]), c, add)
            if not c["refs"]:
                add("orphan-criterion", "error", c["line"], f"{c['id']} doesn't cite a requirement",
                    "add the requirement ID it checks, e.g. '(R2)', or drop it", c["id"])
            elif reqs:
                for ref in sorted(c["refs"] - req_ids, key=natural):
                    add("unknown-reference", "error", c["line"], f"{c['id']} cites {ref}, which isn't a requirement",
                        "fix the ID or add the requirement", c["id"])
        covered = set().union(*[c["refs"] for c in crits]) if crits else set()
        for r in reqs:
            if r["id"] not in covered:
                add("uncovered-requirement", "error", r["line"], f"{r['id']} has no acceptance criterion",
                    f"add 'ACn ({r['id']}) Given ... when ... then ...'", r["id"])

    # --- test plan --------------------------------------------------------------------------
    if "test-plan" in spec.sections and crits:
        h = spec.sections["test-plan"]
        tested = {"AC" + n for _, ln, _ in spec.body(h) for n in AC_REF.findall(ln)}
        crit_ids = {c["id"] for c in crits}
        for c in crits:
            if c["id"] not in tested:
                add("untested-criterion", "error", c["line"], f"{c['id']} is not in the test plan",
                    f"add a row mapping {c['id']} to a test (name, level, file)", c["id"])
        for ref in sorted(tested - crit_ids, key=natural):
            add("unknown-reference", "error", h["line"], f"the test plan cites {ref}, which isn't a criterion",
                "fix the ID or remove the row", ref)

    # --- open questions ---------------------------------------------------------------------
    questions = []
    if "open-questions" in spec.sections:
        seen = {}
        for q in spec.items(spec.sections["open-questions"], "Q"):
            first = re.sub(r"^[*_\s]+", "", q["text"].strip())
            if q["heading"] or NONE_ITEM.match(first):
                continue
            questions.append(q)
            if q["id"]:
                if q["id"] in seen:
                    add("duplicate-id", "error", q["line"], f"{q['id']} is used twice (first on line {seen[q['id']]})",
                        "renumber one of them", q["id"])
                seen.setdefault(q["id"], q["line"])
            if q["cells"] is not None and any("owner" in k for k in q["cells"]):
                cell = next(v for k, v in q["cells"].items() if "owner" in k)
                has_owner = bool(cell) and cell.lower() not in {"-", "?", "tbd", "none", "n/a"}
            else:
                has_owner = bool(OWNER.search(q["text"]))
            if not has_owner:
                add("question-without-owner", "error", q["line"], f"open question has no owner: {first.splitlines()[0][:70]}",
                    "add 'owner: name' (or an @handle) and, if you can, a date it must be answered by", q["id"])

    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order[f["severity"]], f["line"], f["rule"]))
    stats = {"requirements": len(reqs), "criteria": len(crits), "open_questions": len(questions), "weasel_modals": weasels,
             "sections": sorted(spec.sections, key=KEYS.index)}
    return findings, stats


def natural(s):
    return (re.sub(r"\d+", "", s), int(re.sub(r"\D", "", s) or 0))


def check_non_goals(spec, add):
    h = spec.sections.get("non-goals")
    if h is not None:
        combined = bool(SECTION_RX["goals"].search(norm(h["title"])))
        if not combined:
            if not spec.has_content(h):
                add("empty-section", "error", h["line"], f"'{h['title']}' is empty", "list what this change will not do")
            return
        # "Goals and non-goals" heading: it must still say which ones are non-goals.
        body = spec.body(h)
        if any(not code and (NON_GOALS_LABEL.match(ln) or (HEADING.match(ln) and SECTION_RX["non-goals"].search(norm(HEADING.match(ln).group(2)))))
               for _, ln, code in body):
            return
        add("missing-section", "error", h["line"], f"'{h['title']}' lists no non-goals",
            "add a 'Non-goals:' list: what this change will not do")
        return
    lines = spec.lines
    for i, (no, ln, code) in enumerate(lines):
        m = None if code else NON_GOALS_LABEL.match(ln)
        if m:
            nxt = next((x for _, x, c in lines[i + 1:] if x.strip()), "")
            if m.group(1).strip() or ITEM.match(nxt):
                return
            add("empty-section", "error", no, "the 'Non-goals:' label has nothing under it", "list what this change will not do")
            return
    add("missing-section", "error", 1, "no 'Non-goals' section",
        "add '## Non-goals': what this change will not do (it stops scope creep later)")


def number_items(items, prefix, noun, add):
    """Give unnumbered bullets an ID, report missing and duplicate IDs; drop group headings."""
    out, seen = [], {}
    for it in items:
        if it["heading"]:
            continue
        if it["id"] is None:
            it["id"] = f"{prefix}{it['numbered'] if it['numbered'] is not None else len(out) + 1}"
            if it["numbered"] is None:
                add("missing-id", "warning", it["line"], f"{noun} without an ID (treated as {it['id']})",
                    f"start it with '{it['id']}' so criteria and tests can cite it", it["id"])
        if it["id"] in seen:
            add("duplicate-id", "error", it["line"], f"{it['id']} is used twice (first on line {seen[it['id']]})",
                "renumber one of them; IDs must be unique", it["id"])
        else:
            seen[it["id"]] = it["line"]
        out.append(it)
    return out


def scan_vague(text, item, add):
    hits = {}
    bare = re.sub(r"\b(?:AC|R|Q)-?\d+\b", " ", text)  # IDs are not measures
    for rx, kind, fix in VAGUE:
        for m in rx.finditer(text):
            if kind == "measurable" and re.search(r"\d", bare):
                continue
            if kind == "secure" and CONTROL.search(bare):
                continue
            hits.setdefault(m.group(0).lower(), fix)
    for word, fix in hits.items():
        add("vague-term", "warning", item["line"], f"{item['id']}: '{word}' can't be tested as written", fix, item["id"])
    for m in sorted(set(UNRESOLVED.findall(text))):
        add("unresolved", "error", item["line"], f"{item['id']} still says '{m}'",
            "decide it now, or move it to Open questions with an owner", item["id"])


def main():
    ap = argparse.ArgumentParser(description="Lint a feature spec for testability and traceability.")
    ap.add_argument("path", nargs="?", default="SPEC.md")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on", default="error", choices=SEVERITIES)
    ap.add_argument("--require", help="comma-separated section keys (default: all except decisions)")
    a = ap.parse_args()
    require = None
    if a.require is not None:
        require = [k.strip() for k in a.require.split(",") if k.strip()]
        bad = [k for k in require if k not in KEYS]
        if bad:
            ap.error(f"unknown section key(s): {', '.join(bad)}. Known: {', '.join(KEYS)}")
    try:
        with open(a.path, encoding="utf-8") as f:
            text = f.read()
        findings, stats = lint(text, require)
    except (OSError, UnicodeDecodeError, Unparseable) as e:
        print(f"spec_lint: {a.path}: {e}", file=sys.stderr)
        sys.exit(2)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    if a.json:
        print(json.dumps({"file": a.path, "counts": counts, "stats": stats, "findings": findings}, indent=2))
    else:
        for f in findings:
            print(f"{os.path.basename(a.path)}:{f['line']}: {f['severity']:<7} {f['rule']:<22} {f['message']}")
            if f["fix"]:
                print(f"{'':>8}fix: {f['fix']}")
        summary = (f"{stats['requirements']} requirements, {stats['criteria']} criteria, "
                   f"{stats['open_questions']} open questions, {stats['weasel_modals']} should/could/might in requirements")
        status = ", ".join(f"{v} {k}{'s' if v != 1 else ''}" for k, v in counts.items() if v) or "clean"
        print(f"\n{a.path}: {status} ({summary})")
    worst = SEVERITIES.index(a.fail_on)
    sys.exit(1 if any(SEVERITIES.index(f["severity"]) <= worst for f in findings) else 0)


if __name__ == "__main__":
    main()
