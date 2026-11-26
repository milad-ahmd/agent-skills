---
name: name-checker
description: Brainstorms names for a product, startup, open-source library or CLI tool, then checks every candidate in seconds and ranks them. A read-only script looks up domains (.com, .dev, .io, .app or any TLD) through DNS and the registry's RDAP server, the GitHub user or org name and how many repos already use it, and the npm, PyPI, crates.io, Homebrew and optionally Docker Hub names, applying each registry's own naming and normalisation rules. It also flags names that are too long, hard to pronounce or spell, a plain dictionary word, too close to a famous brand, or a generic descriptive term that makes a weak trademark. Results are likely-free / taken / unknown, never a guarantee, and nothing is registered. Use when the user asks to name or rename a project, package, library, CLI or app, wants name ideas, asks "is this name taken", whether a domain, npm package, PyPI name or GitHub org is available, or wants to compare a few candidate names.
---

# Name Checker

A good name is easy to say, easy to spell after hearing it once, and free where the user will publish. Brainstorm wide, check fast, then shortlist with reasons.

## 1. Ask two things first

Ask what the thing is and who uses it (a Python library, a Rust CLI, a SaaS app, a VS Code extension), because that decides which platforms matter. Then ask for the vibe they want, playful or serious or technical, and any names they already like or hate. Skip the questions if the user already said both.

## 2. Brainstorm 15-25 names, using several strategies

| Strategy | Example for a log search CLI |
|---|---|
| Descriptive | logscan, greplog |
| Metaphor | lantern, sieve |
| Compound | tracehound, logwhisk |
| Invented | lumora, quillo |
| Verb / action | sift, unearth |
| Short and punchy (4-7 letters) | lork, kib |

Mix strategies so the shortlist is not five variations of one idea. Use lowercase letters and digits with at most one hyphen, so the same name works as a domain and a package name. Skip names that are obviously a famous brand.

## 3. Check them

```bash
python3 scripts/check_names.py lumora quillo tracehound sift logwhisk
python3 scripts/check_names.py --file names.txt --tlds com,dev,sh --platforms github,pypi
python3 scripts/check_names.py lumora quillo --json          # machine-readable
python3 scripts/check_names.py lumora --offline              # quality signals only, no network
```

Default TLDs are `com,dev,io,app`; default platforms are `github,npm,pypi,crates,brew` (add `docker` if they ship images). Pick platforms that match step 1: a Python library needs `pypi`, a Rust CLI needs `crates` and `brew`. Set `GITHUB_TOKEN` if checking many names, since the anonymous GitHub API allows 60 requests an hour.

How to read the table: `✓` likely free, `✗` taken, `?` unknown (rate limit, timeout, or no RDAP server for that TLD, such as .io), `-` the name is not valid on that platform. A domain is taken if it resolves in DNS or its RDAP server returns a record; RDAP 404 means likely free. Registries are checked under their normalised name: PyPI per PEP 503 (`My_Tool` becomes `my-tool`), npm lowercased, crates.io treats `-` and `_` as the same.

Exit codes: `0` report printed · `2` bad input (no names, a malformed name, unknown TLD or platform).

## 4. Shortlist 3

Rank by the script's score, then use judgement: the score cannot tell whether a name fits the vibe. For each of the three, say why it fits what the product does, where it is free and where it is not ("npm and PyPI free, .com taken, .dev likely free"), and which quality flags matter.

If nothing good is free, brainstorm a second round in the direction of the best near-miss: a prefix or suffix (`get-`, `-hq`, `-dev`), another TLD, or a sibling word.

## 5. Remind the user to check by hand

The script cannot answer the legal question. Tell the user to search their national trademark office and the WIPO Global Brand Database in the classes they will trade in; the "generic" and "famous name" flags are heuristics, not legal advice. Social handles (X, Bluesky, Instagram, YouTube, Discord) are not checked either.

Before buying a domain, confirm it at a registrar: a free RDAP result can still be a premium or reserved name. npm and PyPI may refuse a name that looks free but is too similar to an existing package, and PyPI also refuses standard-library module names.

## Output

The ranked table from the script (or a trimmed version for long lists), then the shortlist of 3 with reasons, then the manual checks above as a short list.

## Do not

- Say a name is "available" without "likely", or say it is legally safe or trademark-free.
- Register, buy, reserve or publish anything: no domain purchases, no placeholder npm or PyPI packages, no GitHub org creation. Tell the user how, and let them do it.
- Drop a name only because one minor TLD is taken; .com and the user's main registry matter most.
- Run the script on more than about 30 names at once without a `GITHUB_TOKEN`; shortlist first.
