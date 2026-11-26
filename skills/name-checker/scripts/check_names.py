#!/usr/bin/env python3
"""Check candidate names for a product, library or CLI: domains, package registries, GitHub, plus
cheap quality signals, then rank them. Stdlib only. Read-only: it never registers or reserves anything.

Usage: python3 check_names.py NAME [NAME ...] [--file FILE] [--tlds com,dev,io,app]
                              [--platforms github,npm,pypi,crates,brew] [--json] [--offline] [--timeout S]
  NAME          candidate names (letters, digits, '-', '_', '.'; up to 63 chars). Duplicates are dropped.
  --file FILE   read more names from FILE, one per line ('-' = stdin, '#' starts a comment)
  --tlds        comma-separated TLDs to check (default com,dev,io,app; '' = none)
  --platforms   comma-separated, from github,npm,pypi,crates,brew,docker (default: all but docker; '' = none)
  --json        machine-readable output on stdout
  --offline     no network at all: every availability is '?', only the quality signals are computed
  --timeout S   per-request timeout in seconds (default 8)

How availability is decided (every "free" means "likely free": confirm at the registrar or registry):
  domains   the name resolves in DNS -> taken. Otherwise the TLD's RDAP server is looked up in the IANA
            bootstrap file (https://data.iana.org/rdap/dns.json, RFC 9224) and queried for the domain:
            HTTP 200 -> taken, 404 -> likely free, anything else -> unknown. A TLD with no RDAP server in
            the bootstrap (for example .io at the time of writing) and no DNS record is unknown.
  github    api.github.com/users/NAME (users and orgs): 404 -> free. Also counts repos matching the name.
            Set GITHUB_TOKEN to raise the API rate limit; the token is only sent to api.github.com.
  npm       registry.npmjs.org/NAME, lowercased: 404 -> free. npm may still refuse names it considers
            too similar to an existing package.
  pypi      pypi.org/pypi/NAME/json, PEP 503-normalised: 404 -> free. PyPI also refuses stdlib module
            names and names too similar to existing projects.
  crates    crates.io/api/v1/crates/NAME: 404 -> free. crates.io treats '-' and '_' as the same name.
  brew      formulae.brew.sh formula and cask JSON (homebrew-core and homebrew-cask only, not taps).
  docker    hub.docker.com/v2/users/NAME: 200 (user) or a redirect to /orgs/ -> taken, 404 -> free.
Rate limits, timeouts and network errors give '?' (unknown), never '✓'.

Ranking: score = 0.6 * availability + 0.4 * quality (both 0-100). Availability weights .com x3 and
GitHub x2; free = 1, unknown = 0.5, taken or invalid = 0. The quality flags (length, pronounceability,
ambiguous spelling, common word, closeness to a famous name, generic/descriptive term) are heuristics,
not legal advice: search trademark databases before you commit to a name.

Exit codes: 0 report printed (whatever the availability)
            2 bad input: no names, a malformed name, an unknown TLD or platform, an unreadable --file
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

USER_AGENT = "name-checker/1.0 (+https://github.com/milad-ahmd/agent-skills)"
BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
DEFAULT_TLDS = ["com", "dev", "io", "app"]
ALL_PLATFORMS = ["github", "npm", "pypi", "crates", "brew", "docker"]
DEFAULT_PLATFORMS = ["github", "npm", "pypi", "crates", "brew"]
TIMEOUT = 8.0
# seconds between requests to the same host; crates.io asks crawlers for at most 1 request per second
MIN_INTERVAL = {"crates.io": 1.0}
DEFAULT_INTERVAL = 0.2
WEIGHTS = {"com": 3.0, "github": 2.0}
SYMBOLS = {"free": "✓", "taken": "✗", "unknown": "?", "invalid": "-"}
ASCII_SYMBOLS = {"free": "+", "taken": "x", "unknown": "?", "invalid": "-"}

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
TLD_RE = re.compile(r"^[a-z0-9-]{2,63}$")
GITHUB_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}$")  # signup form rule, max 39
PEP508_RE = re.compile(r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", re.IGNORECASE)
CRATE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
DOCKER_RE = re.compile(r"^[a-z0-9]{4,30}$")
NPM_RESERVED = {"node_modules", "favicon.ico"} | set(
    "assert buffer child_process cluster console constants crypto dgram dns domain events fs http http2 "
    "https inspector module net os path perf_hooks process punycode querystring readline repl stream "
    "string_decoder sys timers tls trace_events tty url util v8 vm wasi worker_threads zlib".split())

FAMOUS = ("google github gitlab docker kubernetes react angular svelte vercel netlify stripe slack notion "
          "figma discord twitter facebook instagram linkedin amazon apple microsoft openai anthropic claude "
          "spotify netflix uber airbnb shopify dropbox zoom paypal tesla nvidia oracle firebase supabase "
          "heroku cloudflare redis postgres mongodb mysql python rust golang node deno bun vite webpack "
          "tailwind nextjs flutter android chrome firefox safari linux ubuntu windows jira trello asana "
          "zapier hubspot salesforce twilio sentry datadog grafana terraform ansible jenkins npm pypi "
          "homebrew youtube tiktok whatsapp telegram reddit wordpress").split()
COMMON = set(("apple river stone cloud light spark bridge pulse wave orbit anchor forge hive nest beacon "
              "echo atlas canvas compass harbor lantern summit vertex prism ember maple cedar falcon fox "
              "otter raven tiger lion bear wolf panda koala mango lemon pepper honey sugar coffee tea "
              "rocket comet star moon sun planet galaxy ocean lake island garden field meadow forest "
              "storm thunder rain snow frost flame fire iron steel silver gold copper paper pencil ink "
              "book page note letter word story signal switch button lever gear engine motor pilot "
              "captain scout ranger guide keeper home house door window key lock box bag cup glass "
              "table chair bench road path trail track line point dot circle square block brick tile "
              "seed root leaf tree branch flower bloom simple happy quick bright clear smart").split())
GENERIC = set(("app apps data db sql cloud sync fast quick easy simple smart api apis web net dev code "
               "test tests log logs hub kit lab labs tool tools stack flow base box io ai bot bots dash "
               "board cli lib core pro plus max auto file files doc docs task tasks job jobs deploy ops "
               "build run cache store queue stream mail chat pay shop form forms auth key keys secure safe "
               "track scan check list lists sheet note notes link links media image video audio search "
               "find map maps time date calendar plan go js py rs ui ux site page view admin user users "
               "team work one my get "
               "manager monitor server service services config git repo deploy online digital tech labs "
               "soft ware hq").split())
AMBIGUOUS = [
    (re.compile(r"([a-z])\1"), "double letter: people will ask how many"),
    (re.compile(r"ph"), "'ph' can be heard as 'f'"),
    (re.compile(r"[bcdfgklmnpstvz]r$"), "dropped-vowel ending: people may type an extra 'e' (flickr/flicker)"),
    (re.compile(r"q(?!u)"), "'q' without 'u' is hard to spell from hearing it"),
    (re.compile(r"[kc]s$|x$"), "-x/-ks/-cs endings sound alike"),
]


# ---------- networking (all of it goes through _open and _resolve so tests can mock them) ----------

class RateLimiter:
    """Serialises requests per host with a minimum interval between them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._next = {}
        self._host_locks = {}

    def wait(self, host):
        with self._lock:
            hlock = self._host_locks.setdefault(host, threading.Lock())
        with hlock:
            now = time.monotonic()
            start = max(now, self._next.get(host, 0.0))
            if start > now:
                time.sleep(start - now)
            self._next[host] = start + MIN_INTERVAL.get(host, DEFAULT_INTERVAL)


LIMITER = RateLimiter()


def _open(req, timeout):
    return urlopen(req, timeout=timeout)


def _resolve(host):
    return socket.getaddrinfo(host, None)


def fetch(url, accept="application/json", read=False):
    """Return (status, body or None). status is None on a network error or timeout."""
    host = urlparse(url).hostname or ""
    LIMITER.wait(host)
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    token = os.environ.get("GITHUB_TOKEN")
    if token and host == "api.github.com":
        headers["Authorization"] = "Bearer " + token
    try:
        with _open(Request(url, headers=headers), TIMEOUT) as resp:
            # the body is skipped unless needed: an npm packument can be megabytes
            return resp.status, (resp.read() if read else None)
    except HTTPError as e:
        return e.code, None
    except (URLError, socket.timeout, TimeoutError, OSError, ValueError):
        return None, None


def status_of(code, what="record"):
    if code == 200:
        return "taken", "%s exists" % what
    if code == 404:
        return "free", "no %s found" % what
    if code is None:
        return "unknown", "network error or timeout"
    if code in (403, 429):
        return "unknown", "HTTP %d (rate limited or blocked)" % code
    return "unknown", "HTTP %d" % code


def parse_bootstrap(data):
    """IANA RDAP DNS bootstrap (RFC 9224) -> {label sequence: base URL ending in '/'}, HTTPS preferred."""
    out = {}
    for entry in data.get("services", []):
        if not isinstance(entry, list) or len(entry) != 2:
            continue
        labels, urls = entry
        urls = sorted((u for u in urls if isinstance(u, str)), key=lambda u: not u.startswith("https://"))
        if not urls:
            continue
        base = urls[0] if urls[0].endswith("/") else urls[0] + "/"
        for label in labels:
            out[label.lower()] = base
    return out


def rdap_base(domain, services):
    """Longest label-wise suffix match (RFC 9224 section 4)."""
    labels = domain.lower().split(".")
    for i in range(1, len(labels)):
        base = services.get(".".join(labels[i:]))
        if base:
            return base
    return None


class Bootstrap:
    def __init__(self):
        self._lock = threading.Lock()
        self._services = None
        self._loaded = False

    def services(self):
        with self._lock:
            if not self._loaded:
                self._loaded = True
                code, body = fetch(BOOTSTRAP_URL, read=True)
                if code == 200 and body:
                    try:
                        self._services = parse_bootstrap(json.loads(body.decode("utf-8")))
                    except (ValueError, AttributeError):
                        self._services = None
            return self._services


def check_domain(label, tld, bootstrap):
    domain = "%s.%s" % (label, tld)
    if not DOMAIN_LABEL_RE.match(label):
        return {"status": "invalid", "note": "not a valid domain label (a-z, 0-9, inner hyphens)"}
    try:
        if _resolve(domain):
            return {"status": "taken", "note": "resolves in DNS"}
    except (socket.gaierror, socket.herror, UnicodeError, OSError):
        pass  # no DNS record proves nothing: registered domains can have none
    services = bootstrap.services()
    if services is None:
        return {"status": "unknown", "note": "no DNS record; IANA RDAP bootstrap unavailable"}
    base = rdap_base(domain, services)
    if not base:
        return {"status": "unknown", "note": "no DNS record; .%s has no RDAP server in the IANA bootstrap" % tld}
    code, _ = fetch(base + "domain/" + domain, accept="application/rdap+json")
    status, note = status_of(code, "RDAP registration")
    return {"status": status, "note": note}


# ---------- registries ----------

def pep503(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def registry_name(platform, name):
    """Return (name as the registry sees it, reason it is invalid there or None)."""
    low = name.lower()
    if platform == "github":
        return low, None if GITHUB_RE.match(low) else "GitHub names: letters, digits, single inner hyphens, max 39"
    if platform == "npm":
        if len(low) > 214:
            return low, "npm names are at most 214 characters"
        if low[0] in "._":
            return low, "npm names cannot start with '.' or '_'"
        if low in NPM_RESERVED:
            return low, "reserved on npm (Node.js core module or blocked name)"
        return low, None
    if platform == "pypi":
        return pep503(name), None if PEP508_RE.match(name) else "PyPI names must start and end with a letter or digit"
    if platform == "crates":
        if not CRATE_RE.match(name):
            return name, "crates.io names: ASCII letter first, then letters, digits, '-' or '_', max 64"
        return name, None
    if platform == "brew":
        return low, None
    if platform == "docker":
        return low, None if DOCKER_RE.match(low) else "Docker IDs: 4-30 lowercase letters and digits only"
    raise ValueError(platform)


def check_platform(platform, name):
    as_name, invalid = registry_name(platform, name)
    if invalid:
        return {"status": "invalid", "as": as_name, "note": invalid}
    q = quote(as_name, safe="")
    if platform == "brew":
        codes = [fetch("https://formulae.brew.sh/api/%s/%s.json" % (kind, q))[0] for kind in ("formula", "cask")]
        if 200 in codes:
            status, note = "taken", "Homebrew %s exists" % ("formula" if codes[0] == 200 else "cask")
        elif codes == [404, 404]:
            status, note = "free", "no homebrew-core formula or cask"
        else:
            status, note = status_of(next(c for c in codes if c != 404))
        return {"status": status, "as": as_name, "note": note}
    url = {
        "github": "https://api.github.com/users/%s",
        "npm": "https://registry.npmjs.org/%s",
        "pypi": "https://pypi.org/pypi/%s/json",
        "crates": "https://crates.io/api/v1/crates/%s",
        "docker": "https://hub.docker.com/v2/users/%s",
    }[platform] % q
    code = fetch(url)[0]
    if platform == "docker" and code in (301, 302, 307, 308):
        return {"status": "taken", "as": as_name, "note": "Docker Hub org exists"}  # users/ redirects orgs
    status, note = status_of(code, {"github": "GitHub user or org", "docker": "Docker Hub user"}.get(platform, "package"))
    return {"status": status, "as": as_name, "note": note}


def github_repo_count(name):
    code, body = fetch("https://api.github.com/search/repositories?q=%s+in:name&per_page=1"
                       % quote(name, safe=""), read=True)
    if code != 200 or not body:
        return None
    try:
        return int(json.loads(body.decode("utf-8"))["total_count"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


# ---------- quality heuristics ----------

def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def generic_parts(word):
    """Split word into GENERIC terms if it is made only of them (e.g. 'datasync' -> data+sync), else None."""
    if not word:
        return []
    for i in range(len(word), 1, -1):
        if word[:i] in GENERIC:
            rest = generic_parts(word[i:])
            if rest is not None:
                return [word[:i]] + rest
    return None


def quality(name):
    """Return (score 0-100, flags). Heuristics only."""
    n = name.lower()
    letters = re.sub(r"[^a-z]", "", n)
    flags, score = [], 100

    def hit(points, msg):
        nonlocal score
        score -= points
        flags.append(msg)

    if len(n) < 3:
        hit(30, "very short (%d chars): almost certainly taken everywhere" % len(n))
    elif len(n) == 3:
        hit(10, "3 chars: short names are scarce and often taken")
    elif 10 <= len(n) <= 12:
        hit(10, "long (%d chars)" % len(n))
    elif len(n) > 12:
        hit(25, "long (%d chars): hard to type and remember" % len(n))
    if re.search(r"\d", n):
        hit(10, "contains a digit: 'two' or '2' when said aloud?")
    if re.search(r"[-_.]", n):
        hit(10, "contains '-', '_' or '.': easy to drop when said aloud")
    if letters and not re.search(r"[aeiouy]", letters):
        hit(40, "no vowels: hard to pronounce")
    else:
        if re.search(r"[^aeiouy]{4,}", letters):
            hit(15, "4+ consonants in a row: hard to pronounce")
        if re.search(r"[aeiou]{3,}", letters):
            hit(10, "3+ vowels in a row: unclear how to say it")
    for rx, msg in AMBIGUOUS:
        if rx.search(letters):
            hit(8, "spelling: " + msg)
    if letters in COMMON:
        hit(15, "common word: hard to search for and to own")
    parts = generic_parts(letters)
    if parts:
        hit(15, "generic/descriptive term (%s): weak as a trademark, not legal advice" % "+".join(parts))
    for famous in FAMOUS:
        if letters == famous:
            hit(50, "is the famous name '%s'" % famous)
            break
        limit = 1 if len(famous) <= 5 else 2
        if len(letters) >= 4 and edit_distance(letters, famous) <= limit:
            hit(35, "close to the famous name '%s'" % famous)
            break
        if len(famous) >= 4 and famous in letters:
            hit(20, "contains the famous name '%s'" % famous)
            break
    return max(0, min(100, score)), flags


# ---------- scoring and output ----------

def availability(result):
    value = {"free": 1.0, "unknown": 0.5}
    total = weight = 0.0
    for kind in ("domains", "platforms"):
        for key, res in result[kind].items():
            w = WEIGHTS.get(key, 1.0)
            total += w * value.get(res["status"], 0.0)
            weight += w
    return round(100 * total / weight) if weight else 50


def check_names(names, tlds, platforms, offline=False):
    results = [{"name": n, "domains": {}, "platforms": {}, "github_repos": None} for n in names]
    if offline:
        for r in results:
            for t in tlds:
                r["domains"][t] = {"status": "unknown", "note": "offline"}
            for p in platforms:
                as_name, invalid = registry_name(p, r["name"])
                r["platforms"][p] = ({"status": "invalid", "as": as_name, "note": invalid} if invalid
                                     else {"status": "unknown", "as": as_name, "note": "offline"})
    else:
        bootstrap = Bootstrap()
        jobs = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for r in results:
                label = r["name"].lower()
                for t in tlds:
                    jobs.append((r["domains"], t, pool.submit(check_domain, label, t, bootstrap)))
                for p in platforms:
                    jobs.append((r["platforms"], p, pool.submit(check_platform, p, r["name"])))
                if "github" in platforms:
                    jobs.append((r, "github_repos", pool.submit(github_repo_count, r["name"])))
            for target, key, fut in jobs:
                target[key] = fut.result()
    for r in results:
        q, flags = quality(r["name"])
        if r["github_repos"] is not None and r["github_repos"] >= 500:
            q = max(0, q - 10)
            flags.append("crowded: %d GitHub repos match this name" % r["github_repos"])
        r["quality_score"], r["flags"] = q, flags
        r["availability_score"] = availability(r)
        r["score"] = round(0.6 * r["availability_score"] + 0.4 * q)
    results.sort(key=lambda r: (-r["score"], -r["availability_score"], r["name"].lower()))
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


NOTES = [
    "'free' means no record was found: likely available, confirm at a registrar or the registry itself.",
    "Search trademarks before you commit (your national trademark office, WIPO Global Brand Database).",
    "Quality flags are heuristics, not legal advice. Social handles are not checked.",
]


def symbols():
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(SYMBOLS.values()).encode(enc)
        return SYMBOLS
    except (UnicodeEncodeError, LookupError):
        return ASCII_SYMBOLS


def render(results, tlds, platforms, offline):
    sym = symbols()
    cols = ["." + t for t in tlds] + platforms
    head = ["#", "name", "score"] + cols
    rows = []
    for r in results:
        cells = [sym[r["domains"][t]["status"]] for t in tlds] + [sym[r["platforms"][p]["status"]] for p in platforms]
        rows.append([str(r["rank"]), r["name"], str(r["score"])] + cells)
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(head)]
    out = ["  ".join(c.ljust(w) for c, w in zip(head, widths)).rstrip()]
    out += ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in rows]
    out.append("")
    out.append("%s likely free  %s taken  %s unknown  %s invalid name there%s" % (
        sym["free"], sym["taken"], sym["unknown"], sym["invalid"], "   (offline: nothing was checked)" if offline else ""))
    out.append("")
    for r in results:
        details = list(r["flags"])
        for p in platforms:
            res = r["platforms"][p]
            if res["status"] == "invalid" or (res["status"] == "unknown" and not offline):
                details.append("%s: %s" % (p, res["note"]))
            elif res["as"] != r["name"] and res["status"] != "unknown":
                details.append("%s: checked as '%s'" % (p, res["as"]))
        for t in tlds:
            res = r["domains"][t]
            if res["status"] in ("invalid", "unknown") and not offline:
                details.append(".%s: %s" % (t, res["note"]))
        if r["github_repos"] is not None and r["github_repos"] < 500:
            details.append("%d GitHub repos match the name" % r["github_repos"])
        out.append("%s. %s  (availability %d, quality %d)" % (r["rank"], r["name"], r["availability_score"],
                                                                r["quality_score"]))
        out += ["   - " + d for d in details]
    out.append("")
    out += NOTES
    return "\n".join(out)


def split_list(value):
    items = [v.strip().lower().lstrip(".") for v in value.split(",") if v.strip()]
    return list(dict.fromkeys(items))


def main(argv=None):
    global TIMEOUT
    ap = argparse.ArgumentParser(description="Check and rank candidate names (domains, registries, quality).",
                                 epilog="Exit codes: 0 report printed, 2 bad input.")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--tlds", default=",".join(DEFAULT_TLDS))
    ap.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--timeout", type=float, default=TIMEOUT)
    args = ap.parse_args(argv)

    def bad(msg):
        print("error: " + msg, file=sys.stderr)
        return 2

    names = list(args.names)
    if args.file:
        try:
            fh = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
            with fh:
                names += [ln.split("#", 1)[0].strip() for ln in fh]
        except OSError as e:
            return bad("cannot read %s: %s" % (args.file, e))
    seen, unique = set(), []
    for n in (n.strip() for n in names):
        if n and n.lower() not in seen:
            seen.add(n.lower())
            unique.append(n)
    if not unique:
        return bad("give at least one name")
    if len(unique) > 60:
        return bad("at most 60 names per run (%d given): shortlist first" % len(unique))
    for n in unique:
        if not NAME_RE.match(n):
            return bad("'%s' is not a usable name: letters, digits, '-', '_', '.', max 63 chars" % n)
    tlds = split_list(args.tlds)
    for t in tlds:
        if not TLD_RE.match(t):
            return bad("'%s' is not a TLD" % t)
    platforms = split_list(args.platforms)
    for p in platforms:
        if p not in ALL_PLATFORMS:
            return bad("unknown platform '%s' (choose from %s)" % (p, ",".join(ALL_PLATFORMS)))
    if args.timeout <= 0:
        return bad("--timeout must be positive")
    TIMEOUT = args.timeout

    results = check_names(unique, tlds, platforms, offline=args.offline)
    if args.json:
        print(json.dumps({"offline": args.offline, "tlds": tlds, "platforms": platforms,
                          "results": results, "notes": NOTES}, indent=2, ensure_ascii=False))
    else:
        print(render(results, tlds, platforms, args.offline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
