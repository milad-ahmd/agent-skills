#!/usr/bin/env python3
"""Find the calls that break durable replay in Python agent workflows (stdlib only).

A durable workflow (rewindable, and the same idea in Temporal or DBOS) is re-run from
the top on resume, and completed steps come back from a journal. Anything the body does
outside a step runs again on every resume: model calls are paid twice, emails go out
twice, and time.time() or uuid4() return new values, so the replay can take a different
path than the journal recorded.

Two modes, chosen per file:

  rewindable  The file imports rewindable or has functions that take a ctx and call
              ctx.step / ctx.llm / ctx.parallel / ... . The body of every workflow is
              checked for side effects and nondeterminism outside a ctx.step / ctx.llm
              callable. Lambdas and functions handed to ctx.step are fine. Also checks
              @tool functions: irreversible ones without requires_approval=True, and
              steps or tools that pay, email or POST without an idempotency key.
  plain       No rewindable yet. Lists every side effect and source of nondeterminism
              in the file, each of which needs its own step when converting.

Exit codes: 0 nothing found, 1 findings, 2 nothing to scan (bad path, no .py files,
or no file could be parsed).
"""
import argparse
import ast
import json
import os
import re
import sys

LIMITS = """\
static analysis limits:
  It reads code, it doesn't run it. It resolves names through imports and simple
  assignments (client = OpenAI(), s3 = boto3.client("s3"), cur = conn.cursor()) and
  follows helper functions defined in the scanned files, a few calls deep. It cannot
  see through dynamic dispatch, getattr, objects passed in as arguments, methods on
  classes it can't resolve, or libraries it doesn't know. So a clean result means
  "nothing known was found", not "this workflow replays correctly". Run the workflow,
  kill it mid-way and resume it: that is the real test.

  Silence a line you have checked with a trailing comment:  # durable: ok
"""

SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__", "site-packages",
             "build", "dist", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".rewindable", ".eggs"}
CTX_METHODS = {"step", "llm", "parallel", "approve", "wait_for", "sleep", "now", "uuid", "budget_check"}
STRONG_CTX_METHODS = {"step", "llm", "parallel", "wait_for"}  # not approve: Engine has one too
SUPPRESS = re.compile(r"#\s*durable:\s*ok\b", re.I)


def _expand(spec):
    """'requests.get|post' -> {'requests.get', 'requests.post'}"""
    head, _, tail = spec.rpartition(".")
    return {f"{head}.{t}" if head else t for t in tail.split("|")}


def _table(rows):
    out = {}
    for spec, cat in rows:
        for name in _expand(spec):
            out[name] = cat
    return out


# Calls that are side effects or nondeterministic on their own.
EFFECTS = _table([
    ("requests.get|post|put|patch|delete|head|options|request", "network"),
    ("httpx.get|post|put|patch|delete|head|options|request|stream", "network"),
    ("urllib.request.urlopen|urlretrieve", "network"),
    ("urllib3.request", "network"),
    ("aiohttp.request", "network"),
    ("socket.create_connection|getaddrinfo", "network"),
    ("litellm.completion|acompletion|embedding|text_completion", "llm"),
    ("subprocess.run|call|check_call|check_output|Popen|getoutput|getstatusoutput", "process"),
    ("os.system|popen|execv|execve|execvp|execl|execlp|spawnv|spawnl|kill|startfile", "process"),
    ("os.remove|unlink|rename|replace|renames|makedirs|mkdir|rmdir|removedirs|truncate|chmod|chown|symlink|link",
     "file"),
    ("shutil.copy|copy2|copyfile|copytree|move|rmtree|make_archive|unpack_archive|chown", "file"),
    ("time.time|time_ns|localtime|gmtime|ctime|asctime", "time"),
    ("datetime.datetime.now|utcnow|today", "time"),
    ("datetime.date.today", "time"),
    ("time.sleep", "sleep"),
    ("asyncio.sleep", "sleep"),
    ("random.random|randint|randrange|choice|choices|shuffle|sample|uniform|gauss|getrandbits|randbytes|"
     "triangular|betavariate|expovariate|normalvariate|seed", "random"),
    ("secrets.token_hex|token_bytes|token_urlsafe|choice|randbelow|randbits", "random"),
    ("os.urandom|getrandom", "random"),
    ("uuid.uuid1|uuid4", "uuid"),
    ("input", "input"),
    ("getpass.getpass", "input"),
])
# Module prefixes where any call is an external effect (SDKs with module-level functions).
EFFECT_PREFIXES = [
    ("numpy.random.", "random"),
    ("stripe.", "payment"),
    ("openai.", "llm"),
    ("anthropic.", "llm"),
    ("google.generativeai.", "llm"),
    ("ollama.", "llm"),
]
# Constructors whose result is a client: method calls on it are effects; building it is not.
CLIENTS = _table([
    ("requests.Session|session", "network"),
    ("httpx.Client|AsyncClient", "network"),
    ("aiohttp.ClientSession", "network"),
    ("urllib3.PoolManager", "network"),
    ("http.client.HTTPConnection|HTTPSConnection", "network"),
    ("github.Github", "network"),
    ("openai.OpenAI|AsyncOpenAI|AzureOpenAI|AsyncAzureOpenAI", "llm"),
    ("anthropic.Anthropic|AsyncAnthropic|AnthropicBedrock|AnthropicVertex", "llm"),
    ("google.generativeai.GenerativeModel", "llm"),
    ("google.genai.Client", "llm"),
    ("groq.Groq|AsyncGroq", "llm"),
    ("mistralai.Mistral", "llm"),
    ("cohere.Client|ClientV2", "llm"),
    ("ollama.Client", "llm"),
    ("langchain_openai.ChatOpenAI", "llm"),
    ("langchain_anthropic.ChatAnthropic", "llm"),
    ("rewindable.AnthropicModel|OpenAICompatModel", "llm"),
    ("boto3.client|resource", "cloud"),
    ("boto3.session.Session", "cloud"),
    ("smtplib.SMTP|SMTP_SSL|LMTP", "email"),
    ("sendgrid.SendGridAPIClient", "email"),
    ("twilio.rest.Client", "message"),
    ("slack_sdk.WebClient", "message"),
    ("stripe.StripeClient", "payment"),
    ("sqlite3.connect", "database"),
    ("psycopg.connect", "database"),
    ("psycopg2.connect", "database"),
    ("pymysql.connect", "database"),
    ("mysql.connector.connect", "database"),
    ("sqlalchemy.create_engine", "database"),
    ("pymongo.MongoClient", "database"),
    ("redis.Redis|StrictRedis|from_url", "database"),
])
# On rewindable model objects only .complete() is a model call (cost() and name are local).
CLIENT_ONLY_METHODS = {"rewindable.AnthropicModel": {"complete"}, "rewindable.OpenAICompatModel": {"complete"}}
HARMLESS_METHODS = {"close", "headers", "cookies", "mount", "auth", "params", "__enter__", "__exit__", "cost", "name"}
# Method names that mean the same thing whatever the receiver is.
METHOD_EFFECTS = {"write_text": "file", "write_bytes": "file", "sendmail": "email", "send_message": "email"}
HTTP_WRITE = {"post", "put", "patch", "delete"}
# Calls on a client that return another client of the same kind (cur = conn.cursor()).
DERIVE_METHODS = {"cursor", "Table", "Bucket", "Object", "Queue", "start_chat", "get_database", "get_collection",
                  "pipeline", "client", "resource", "Session"}
IRREVERSIBLE_CATS = {"payment", "email", "message"}

WHY = {
    "network": "runs again on every resume, so the request repeats and its answer can differ from the one later "
               "steps were built on",
    "llm": "a model call outside ctx.llm is paid for again on every resume, the Budget never sees it, and a replay "
           "can get a different answer",
    "cloud": "the cloud API call runs again on every resume",
    "database": "the query runs again on every resume, and writes repeat",
    "email": "sends again on every resume: duplicate emails",
    "message": "sends again on every resume: duplicate messages",
    "payment": "charges again on every resume",
    "process": "the command runs again on every resume",
    "file": "writes again on every resume, and a crash between the write and the next step leaves a state the "
            "journal knows nothing about",
    "time": "a replay gets a new value, so code that branches on it can take a different path than the journal "
            "recorded (NonDeterminismError, or the wrong cached result)",
    "random": "a replay gets a new value, so code that branches on it can take a different path than the journal "
              "recorded",
    "uuid": "a replay gets a new id, so anything keyed on it no longer matches what was recorded",
    "sleep": "sleeps again on every replay and keeps a process alive for the whole wait; a crash loses the timer",
    "input": "blocks a process on a terminal and the answer is not journaled, so a resume asks again",
}
LABEL = {
    "network": "network call", "llm": "model call", "cloud": "cloud API call", "database": "database call",
    "email": "email send", "message": "message send", "payment": "payment API call", "process": "subprocess",
    "file": "file write", "time": "wall-clock read", "random": "randomness", "uuid": "random id",
    "sleep": "blocking sleep", "input": "terminal input",
}
IRREVERSIBLE_SEGMENTS = {"refund", "pay", "payment", "payout", "charge", "transfer", "wire", "deploy", "email", "mail",
                         "sms", "tweet", "publish", "purchase", "withdraw", "invoice", "delete", "drop", "rollout"}
IRREVERSIBLE_FIRST = {"send", "post", "delete", "remove", "cancel", "push", "merge", "release", "buy", "place",
                      "submit", "execute", "notify", "approve", "book", "transfer", "destroy", "terminate", "revoke"}


def dotted(node):
    """['a', 'b', 'c'] for a.b.c, with a Call at the root kept as the first element."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append(node)
    else:
        return None
    return list(reversed(parts))


def src_of(text, node, limit=72):
    seg = ast.get_source_segment(text, node) or ""
    seg = " ".join(seg.split())
    return seg if len(seg) <= limit else seg[: limit - 3] + "..."


def first_param(fn):
    args = list(fn.args.posonlyargs) + list(fn.args.args)
    if args and args[0].arg in ("self", "cls") and not isinstance(fn, ast.Lambda):
        args = args[1:]
    return args[0] if args else None


def mentions(node, *words):
    blob = ast.dump(node)
    return any(w in blob for w in words)


class Module:
    """One parsed file: imports, client variables, functions, tools and workflows."""

    def __init__(self, path, text, tree):
        self.path, self.text, self.tree = path, text, tree
        self.lines = text.splitlines()
        self.stem = os.path.splitext(os.path.basename(path))[0]
        self.alias = {}       # local name -> dotted module path
        self.from_imports = {}  # local name -> (module, name)
        self.uses_rewindable = False
        self.funcs = {}       # name -> FunctionDef (first definition wins; methods included)
        self.tools = {}       # name -> (FunctionDef, requires_approval)
        self._collect()
        self.clients = {}     # 'name' or 'self.attr' -> (category, constructor)
        self._collect_clients(tree, self.clients)

    def _collect(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.alias[a.asname] = a.name
                    else:
                        self.alias[a.name.split(".")[0]] = a.name.split(".")[0]
                    if a.name.split(".")[0] == "rewindable":
                        self.uses_rewindable = True
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] == "rewindable":
                    self.uses_rewindable = True
                for a in node.names:
                    local = a.asname or a.name
                    self.alias[local] = f"{mod}.{a.name}" if mod else a.name
                    self.from_imports[local] = (mod, a.name, node.level)
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs.setdefault(node.name, node)
                approval = self._tool_decorator(node)
                if approval is not None:
                    self.tools[node.name] = (node, approval)

    def _tool_decorator(self, fn):
        """None if fn is not a @tool; else whether requires_approval=True."""
        for dec in fn.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            parts = dotted(target)
            if not parts or not isinstance(parts[-1], str) or parts[-1] != "tool":
                continue
            full = self.resolve(parts)
            if not (full.startswith("rewindable") or self.uses_rewindable):
                continue
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "requires_approval" and isinstance(kw.value, ast.Constant):
                        return bool(kw.value.value)
            return False
        return None

    def resolve(self, parts):
        """Dotted name with the root expanded through imports; a call at the root becomes `name()`."""
        root = parts[0]
        if isinstance(root, ast.Call):
            inner = dotted(root.func)
            head = (self.resolve(inner) + "()") if inner else "?()"
        else:
            head = self.alias.get(root, root)
        return ".".join([head] + [p for p in parts[1:] if isinstance(p, str)])

    def client_of_call(self, call, scope_clients):
        """(category, constructor) if call builds a client, or derives one from a client (conn.cursor())."""
        parts = dotted(call.func)
        if not parts:
            return None
        full = self.resolve(parts)
        if full in CLIENTS:
            return CLIENTS[full], full
        if len(parts) > 1 and parts[-1] in DERIVE_METHODS:
            hit, _used = self.client_var(parts, scope_clients)
            if hit:
                return hit
        return None

    def client_var(self, parts, scope_clients):
        """((category, constructor), parts consumed by the receiver) if parts start at a client."""
        if isinstance(parts[0], ast.Call):
            return self.client_of_call(parts[0], scope_clients), 1
        candidates = []
        if len(parts) > 2:
            candidates.append((f"{parts[0]}.{parts[1]}", 2))  # self.client.messages.create
        candidates.append((parts[0], 1))
        for key, used in candidates:
            for table in (scope_clients, self.clients):
                if key in table:
                    return table[key], used
        return None, 0

    def _collect_clients(self, root, table, local=False):
        """Assignments and with-targets that hold clients. Module level unless local=True."""
        nodes = ast.walk(root) if local else self._module_and_methods(root)
        for node in nodes:
            pairs = []
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                pairs = [(node.target, node.value)]
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                pairs = [(i.optional_vars, i.context_expr) for i in node.items if i.optional_vars is not None]
            for target, value in pairs:
                if isinstance(value, ast.Await):
                    value = value.value
                if not isinstance(value, ast.Call):
                    continue
                hit = self.client_of_call(value, table)
                if not hit:
                    continue
                name = self._target_name(target)
                if name:
                    table[name] = hit

    def _module_and_methods(self, root):
        """Module-level statements plus self.x assignments anywhere (clients built in __init__)."""
        for node in ast.walk(root):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.With, ast.AsyncWith)):
                targets = node.targets if isinstance(node, ast.Assign) else (
                    [node.target] if isinstance(node, ast.AnnAssign) else [i.optional_vars for i in node.items])
                if any(isinstance(t, ast.Attribute) for t in targets if t is not None) or node in self.tree.body:
                    yield node

    @staticmethod
    def _target_name(t):
        if isinstance(t, ast.Name):
            return t.id
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
            return f"{t.value.id}.{t.attr}"
        return None

    def suppressed(self, line):
        return 0 < line <= len(self.lines) and bool(SUPPRESS.search(self.lines[line - 1]))


class Project:
    """All scanned modules, so helpers imported from another scanned file can be followed."""

    def __init__(self, modules):
        self.modules = modules
        self.by_stem = {}
        for m in modules:
            self.by_stem.setdefault(m.stem, []).append(m)
        self._effects_cache = {}

    def lookup_function(self, mod, name):
        """(module, FunctionDef) for a bare name called in mod, following `from x import name`."""
        if name in mod.funcs:
            return mod, mod.funcs[name]
        if name in mod.from_imports:
            src_mod, orig, _level = mod.from_imports[name]
            stem = (src_mod or "").split(".")[-1]
            cands = self.by_stem.get(stem, [])
            if len(cands) > 1:
                tail = os.sep.join((src_mod or "").split(".")) + ".py"
                cands = [c for c in cands if c.path.endswith(tail)] or cands
            for c in cands:
                if orig in c.funcs:
                    return c, c.funcs[orig]
        return None, None

    def effects_of(self, mod, fn, depth=0, seen=None):
        """Known effects inside a helper's body, following same-project helpers a few levels deep."""
        key = (mod.path, fn.name, fn.lineno)
        if key in self._effects_cache:
            return self._effects_cache[key]
        seen = set() if seen is None else seen
        if key in seen or depth > 3:
            return []
        seen.add(key)
        scope = {}
        mod._collect_clients(fn, scope, local=True)
        found = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or mod.suppressed(node.lineno):
                continue
            hit = classify(mod, node, scope)
            if hit:
                found.append((hit[0], hit[1], node.lineno, mod))
                continue
            parts = dotted(node.func)
            if parts and len(parts) == 1 and isinstance(parts[0], str):
                m2, f2 = self.lookup_function(mod, parts[0])
                if f2 is not None and f2 is not fn:
                    for cat, what, line, m3 in self.effects_of(m2, f2, depth + 1, seen):
                        found.append((cat, f"{parts[0]}() -> {what}", line, m3))
        self._effects_cache[key] = found
        return found


def classify(mod, call, scope_clients):
    """(category, readable callee) if this call is a known effect, else None."""
    parts = dotted(call.func)
    if not parts:
        return None
    full = mod.resolve(parts)
    if full in CLIENTS:
        return None  # building a client is not the effect; using it is
    if full in EFFECTS:
        return EFFECTS[full], full
    if full == "open" or full.endswith(".open"):
        if _write_mode(call, method=full != "open"):
            return "file", full
        return None
    for prefix, cat in EFFECT_PREFIXES:
        if full.startswith(prefix):
            return cat, full
    if len(parts) > 1:
        hit, used = mod.client_var(parts, scope_clients)
        if hit:
            cat, ctor = hit
            methods = [p for p in parts[used:] if isinstance(p, str)]
            if not methods or any(p in HARMLESS_METHODS or p in DERIVE_METHODS for p in methods):
                return None
            only = CLIENT_ONLY_METHODS.get(ctor)
            if only is not None and methods[-1] not in only:
                return None
            root = ".".join(str(p) for p in parts[:used]) if isinstance(parts[0], str) else \
                ctor.rsplit(".", 1)[-1] + "()"
            return cat, ".".join([root] + methods)
        last = parts[-1]
        if isinstance(last, str) and last in METHOD_EFFECTS:
            return METHOD_EFFECTS[last], full
    return None


def _write_mode(call, method):
    mode = None
    pos = 0 if method else 1
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
        mode = call.args[pos].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and any(c in mode for c in "wax+")


def is_http_write(mod, call, scope):
    hit = classify(mod, call, scope)
    if not hit:
        return False
    cat, what = hit
    if cat in IRREVERSIBLE_CATS:
        return True
    if cat == "network":
        method = what.rsplit(".", 1)[-1]
        if method in HTTP_WRITE:
            return True
        if method == "request" and call.args and isinstance(call.args[0], ast.Constant):
            return str(call.args[0].value).lower() in HTTP_WRITE
    return False


def irreversible_name(name):
    segs = [s for s in name.lower().split("_") if s]
    return bool(segs) and (segs[0] in IRREVERSIBLE_FIRST or any(s in IRREVERSIBLE_SEGMENTS for s in segs))


def ctx_call(call, ctxname):
    """The ctx method name if call is ctxname.<method>(...)."""
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == ctxname:
        return f.attr
    return None


def passes_name(call, name):
    return any(isinstance(a, ast.Name) and a.id == name for a in list(call.args) + [k.value for k in call.keywords])


def is_engine_ref(mod, node, engines):
    parts = dotted(node)
    if not parts:
        return False
    if isinstance(parts[0], ast.Call):
        inner = dotted(parts[0].func)
        return bool(inner) and mod.resolve(inner).endswith("Engine")
    return parts[0] in engines


def find_workflows(mod, project):
    """{FunctionDef or Lambda: ctx param name} for every workflow body in the module."""
    wfs = {}
    engines = set()
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            parts = dotted(node.value.func)
            if parts and mod.resolve(parts).endswith("Engine"):
                engines.update(t.id for t in node.targets if isinstance(t, ast.Name))
    for fn in list(mod.funcs.values()) + [n for n in ast.walk(mod.tree)
                                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        p = first_param(fn)
        if p is None or fn in wfs:
            continue
        used = {ctx_call(c, p.arg) for c in ast.walk(fn) if isinstance(c, ast.Call)} - {None}
        annotated = isinstance(p.annotation, (ast.Name, ast.Attribute)) and \
            (getattr(p.annotation, "id", None) or getattr(p.annotation, "attr", None)) == "Context"
        other_type = isinstance(p.annotation, (ast.Name, ast.Attribute)) and not annotated
        if other_type:
            continue  # def cmd(engine: Engine, ...) is not a workflow even if it calls engine.approve
        named_ctx = p.arg in ("ctx", "context")
        if used & STRONG_CTX_METHODS or annotated or (named_ctx and mod.uses_rewindable):
            wfs[fn] = p.arg
    # Functions handed to Engine.run / submit / rewind as the workflow.
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr in ("run", "submit", "resume", "rewind") and is_engine_ref(mod, node.func.value, engines):
            for a in node.args[:2] + [k.value for k in node.keywords if k.arg == "workflow"]:
                if isinstance(a, ast.Name) and a.id in mod.funcs:
                    fn = mod.funcs[a.id]
                    p = first_param(fn)
                    if p is not None:
                        wfs.setdefault(fn, p.arg)
    # Fixpoint: helpers that receive the ctx, and named parallel branches.
    changed = True
    while changed:
        changed = False
        for body, ctxname in list(wfs.items()):
            for c in ast.walk(body):
                if not isinstance(c, ast.Call):
                    continue
                targets = []
                if ctx_call(c, ctxname) == "parallel" and c.args:
                    seq = c.args[0]
                    elts = seq.elts if isinstance(seq, (ast.List, ast.Tuple)) else \
                        [seq.elt] if isinstance(seq, (ast.ListComp, ast.GeneratorExp)) else []
                    for e in elts:
                        if isinstance(e, ast.Lambda):
                            p = first_param(e)
                            if p is not None and e not in wfs:
                                wfs[e] = p.arg
                                changed = True
                        elif isinstance(e, ast.Name):
                            targets.append(e.id)
                parts = dotted(c.func)
                if parts and len(parts) == 1 and isinstance(parts[0], str) and c.args and \
                        isinstance(c.args[0], ast.Name) and c.args[0].id == ctxname:
                    targets.append(parts[0])
                for name in targets:
                    fn = mod.funcs.get(name)
                    if fn is not None and fn not in wfs:
                        p = first_param(fn)
                        if p is not None:
                            wfs[fn] = p.arg
                            changed = True
    return wfs


def fix_for(cat, src):
    lam = f"lambda: {src}" if len(src) <= 60 else "lambda: ..."
    if cat == "llm":
        return ("use ctx.llm(model, messages=[...]) or an Agent so the call is journaled, counted and budgeted; "
                f"or at least ctx.step(\"call model\", {lam})")
    if cat == "time":
        if "datetime" in src or "date." in src:
            return "datetime.fromtimestamp(ctx.now(), tz=timezone.utc)"
        return "ctx.now()"
    if cat == "uuid":
        return "ctx.uuid()"
    if cat == "random":
        return f"ctx.step(\"pick\", {lam}) (or ctx.uuid() for an id)"
    if cat == "sleep":
        return "ctx.sleep(seconds): short waits sleep in-process, long ones suspend the run for `rewindable worker`"
    if cat == "input":
        return "ctx.approve(\"question?\") for yes/no, ctx.wait_for(\"signal\", \"prompt\") for a value"
    if cat == "file":
        return "move the write into a function and call it with ctx.step(\"save ...\", save, data)"
    if cat in IRREVERSIBLE_CATS:
        return (f"ctx.step(\"{LABEL[cat]}\", ...) with current_step().idempotency_key passed to the provider, "
                "behind ctx.approve(...) or a @tool(requires_approval=True)")
    return f"ctx.step(\"{LABEL.get(cat, cat)}\", {lam})"


class Scanner:
    def __init__(self, project):
        self.project = project
        self.findings = []

    def add(self, mod, node, kind, mode, function, call, cat, why, fix):
        line = getattr(node, "lineno", 0)
        if mod.suppressed(line):
            return
        key = (mod.path, line, kind, call)
        if any((f["file"], f["line"], f["kind"], f["call"]) == key for f in self.findings):
            return
        self.findings.append({"file": mod.path, "line": line, "col": getattr(node, "col_offset", 0) + 1,
                              "kind": kind, "mode": mode, "function": function, "call": call,
                              "category": cat, "why": why, "fix": fix})

    # -- rewindable mode -------------------------------------------------------------------
    def scan_workflow(self, mod, body, ctxname, wfs):
        name = getattr(body, "name", "<parallel branch>")
        scope = {}
        mod._collect_clients(body, scope, local=True)
        nodes = [body.body] if isinstance(body, ast.Lambda) else list(body.body)
        for n in nodes:
            self._visit(mod, n, ctxname, name, scope, wfs)

    def _visit(self, mod, node, ctxname, wf, scope, wfs):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return  # deferred code: runs only if something calls it (ctx.step, a parallel branch, a helper)
        if isinstance(node, ast.Call):
            method = ctx_call(node, ctxname)
            if method:
                self._ctx_call(mod, node, method, ctxname, wf, scope, wfs)
                return
            self._call(mod, node, ctxname, wf, scope, wfs)
        for child in ast.iter_child_nodes(node):
            self._visit(mod, child, ctxname, wf, scope, wfs)

    def _ctx_call(self, mod, node, method, ctxname, wf, scope, wfs):
        if method == "step":
            fn_arg = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "fn"), None)
            self._check_step_idempotency(mod, node, fn_arg, wf, scope)
            if isinstance(fn_arg, ast.Call):
                # ctx.step("x", fetch(url)) runs fetch now, in the body, and hands the step its result
                src = src_of(mod.text, fn_arg, 50)
                parts = dotted(fn_arg.func)
                fname = ".".join(p for p in parts if isinstance(p, str)) if parts else "fn"
                args = ", ".join(src_of(mod.text, a, 30) for a in fn_arg.args)
                self.add(mod, fn_arg, "replay", "rewindable", wf, f"{fname}()", "early-call",
                         f"ctx.step in `{wf}` was given the result of `{src}`, not a function, so it runs in "
                         "the workflow body on every resume and the step then tries to call its return value",
                         f"pass the function and its arguments: ctx.step(\"...\", {fname}{', ' + args if args else ''})")
                for a in list(fn_arg.args) + [k.value for k in fn_arg.keywords]:
                    self._visit(mod, a, ctxname, wf, scope, wfs)
            rest = [a for a in node.args if a is not fn_arg] + [k.value for k in node.keywords if k.value is not fn_arg]
            for a in rest:  # the step name and extra args are evaluated in the workflow body
                self._visit(mod, a, ctxname, wf, scope, wfs)
            return
        if method == "parallel":
            return  # lambda branches are scanned as workflows of their own; named ones via the fixpoint
        for a in list(node.args) + [k.value for k in node.keywords]:
            self._visit(mod, a, ctxname, wf, scope, wfs)

    def _call(self, mod, node, ctxname, wf, scope, wfs):
        hit = classify(mod, node, scope)
        if hit:
            cat, what = hit
            src = src_of(mod.text, node)
            self.add(mod, node, "replay", "rewindable", wf, what, cat,
                     f"{LABEL[cat]} in workflow `{wf}` outside a step: {WHY[cat]}", fix_for(cat, src))
            return
        parts = dotted(node.func)
        if not parts or isinstance(parts[0], ast.Call):
            return
        # tool.fn(...) or tool(...) called straight from the body
        tool_name = parts[0] if len(parts) == 1 or (len(parts) == 2 and parts[1] == "fn") else None
        if tool_name and tool_name in mod.tools:
            fn = mod.tools[tool_name][0]
            effects = self.project.effects_of(mod, fn)
            if effects or irreversible_name(tool_name):
                detail = f"; it calls {effects[0][1]} (line {effects[0][2]})" if effects else ""
                self.add(mod, node, "replay", "rewindable", wf, f"{tool_name}()", effects[0][0] if effects else "tool",
                         f"tool `{tool_name}` called directly in workflow `{wf}`, so it is not a step and runs again "
                         f"on every resume{detail}",
                         f"ctx.step(\"{tool_name}\", {tool_name}.fn, ...), or let an Agent call it")
            return
        if len(parts) != 1:
            return
        if passes_name(node, ctxname):
            return  # a helper that gets the ctx journals its own steps (scanned as a workflow)
        m2, fn = self.project.lookup_function(mod, parts[0])
        if fn is None or fn in wfs:
            return
        effects = self.project.effects_of(m2, fn)
        if not effects:
            return
        cat, what, line, where = effects[0]
        loc = f"line {line}" if where is mod else f"{os.path.basename(where.path)}:{line}"
        more = f" and {len(effects) - 1} more" if len(effects) > 1 else ""
        self.add(mod, node, "replay", "rewindable", wf, f"{parts[0]}()", cat,
                 f"`{parts[0]}()` runs in workflow `{wf}` outside a step and calls {what} ({loc}){more}: "
                 f"{WHY[cat]}",
                 f"ctx.step(\"{parts[0].replace('_', ' ')}\", {parts[0]}, ...args)")

    def _check_step_idempotency(self, mod, node, fn_arg, wf, scope):
        if fn_arg is None:
            return
        target, owner = None, mod
        if isinstance(fn_arg, ast.Lambda):
            target = fn_arg
        else:
            parts = dotted(fn_arg)
            if parts and isinstance(parts[0], str) and (len(parts) == 1 or parts[1:] == ["fn"]):
                owner, target = self.project.lookup_function(mod, parts[0])
        if target is None or mentions(target, "idempotency_key", "current_step", "Idempotency-Key"):
            return
        local = dict(scope)
        owner._collect_clients(target, local, local=True)
        for c in ast.walk(target):
            if isinstance(c, ast.Call) and is_http_write(owner, c, local) and not owner.suppressed(c.lineno):
                what = classify(owner, c, local)[1]
                self.add(mod, node, "idempotency", "rewindable", wf, what, "idempotency",
                         f"step in `{wf}` calls {what} without an idempotency key: steps are exactly-once in the "
                         "journal but at-least-once in the world, so a crash after the call and before the result "
                         "is saved repeats it on resume",
                         "pass current_step().idempotency_key to the provider (Stripe idempotency_key=, an "
                         "Idempotency-Key header, a fixed Message-ID)")
                return

    def scan_tools(self, mod):
        for name, (fn, approval) in mod.tools.items():
            local = {}
            mod._collect_clients(fn, local, local=True)
            effects = self.project.effects_of(mod, fn)
            irreversible = [e for e in effects if e[0] in IRREVERSIBLE_CATS or e[0] == "process"]
            writes = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and is_http_write(mod, c, local)]
            if not approval and (irreversible or writes or irreversible_name(name)):
                reason = (f"it calls {irreversible[0][1]}" if irreversible else
                          f"it calls {classify(mod, writes[0], local)[1]}" if writes else "its name says it acts")
                self.add(mod, fn, "approval", "rewindable", name, f"@tool {name}", "approval",
                         f"tool `{name}` looks irreversible ({reason}) but has no human gate, so the agent can "
                         "call it on its own",
                         f"@tool(requires_approval=True) on `{name}`, and set `rewindable approval-code set` so "
                         "the agent can't approve itself")
            if writes and not mentions(fn, "idempotency_key", "current_step", "Idempotency-Key"):
                what = classify(mod, writes[0], local)[1]
                self.add(mod, writes[0], "idempotency", "rewindable", name, what, "idempotency",
                         f"tool `{name}` calls {what} without an idempotency key, so a crash after the call and "
                         "before the step is saved repeats it on resume",
                         "pass current_step().idempotency_key to the provider")

    # -- plain mode -----------------------------------------------------------------------
    def scan_plain(self, mod):
        func_of = {}
        for fn in ast.walk(mod.tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(fn):
                    func_of.setdefault(id(n), fn.name)
        scopes = {}
        for fn in ast.walk(mod.tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = {}
                mod._collect_clients(fn, scope, local=True)
                scopes[fn.name] = scope
        for node in ast.walk(mod.tree):
            if not isinstance(node, ast.Call):
                continue
            where = func_of.get(id(node), "<module>")
            hit = classify(mod, node, scopes.get(where, {}))
            if not hit:
                continue
            cat, what = hit
            note = ""
            if cat in IRREVERSIBLE_CATS or cat == "process" or is_http_write(mod, node, scopes.get(where, {})):
                note = " This one is irreversible: put a human approval in front of it."
            self.add(mod, node, "needs-step", "plain", where, what, cat,
                     f"{LABEL[cat]}: {WHY[cat]}.{note}", fix_for(cat, src_of(mod.text, node)))


def iter_files(paths, include_tests):
    files, missing = [], []
    for p in paths:
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")
                                 and (include_tests or d not in ("tests", "test")))
                for n in sorted(names):
                    if not n.endswith(".py"):
                        continue
                    if not include_tests and (n.startswith("test_") or n.endswith("_test.py") or n == "conftest.py"):
                        continue
                    files.append(os.path.join(root, n))
        else:
            missing.append(p)
    return files, missing


def audit(paths, include_tests=False):
    files, missing = iter_files(paths, include_tests)
    modules, skipped = [], [{"file": m, "error": "no such file or directory"} for m in missing]
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            modules.append(Module(f, text, ast.parse(text, filename=f)))
        except (SyntaxError, ValueError) as exc:
            skipped.append({"file": f, "error": f"could not parse: {exc}"})
    project = Project(modules)
    scanner = Scanner(project)
    workflows, modes = [], {}
    any_rewindable = False
    found = {}
    for mod in modules:
        wfs = find_workflows(mod, project)
        found[mod.path] = wfs
        if mod.uses_rewindable or wfs:
            any_rewindable = True
    imported_by_durable = set()
    if any_rewindable:
        for mod in modules:
            if not (mod.uses_rewindable or found[mod.path]):
                continue
            for local, (src_mod, _orig, _lvl) in mod.from_imports.items():
                for c in project.by_stem.get((src_mod or "").split(".")[-1], []):
                    imported_by_durable.add(c.path)
            for alias in mod.alias.values():
                for c in project.by_stem.get(alias.split(".")[-1], []):
                    imported_by_durable.add(c.path)
    for mod in modules:
        wfs = found[mod.path]
        if mod.uses_rewindable or wfs:
            modes[mod.path] = "rewindable"
            for body, ctxname in wfs.items():
                if not isinstance(body, ast.Lambda):
                    workflows.append({"file": mod.path, "function": body.name, "line": body.lineno, "ctx": ctxname})
                scanner.scan_workflow(mod, body, ctxname, wfs)
            scanner.scan_tools(mod)
        elif mod.path in imported_by_durable:
            modes[mod.path] = "helper"  # followed from the workflows that import it
        else:
            modes[mod.path] = "plain"
            scanner.scan_plain(mod)
    findings = sorted(scanner.findings, key=lambda f: (f["file"], f["line"], f["col"]))
    return {"files_scanned": len(modules), "modes": modes, "workflows": workflows,
            "findings": findings, "skipped": skipped}


def render_text(report):
    out = []
    for f in report["findings"]:
        tag = {"replay": "replay", "approval": "approval", "idempotency": "idempotency",
               "needs-step": "needs a step"}[f["kind"]]
        out.append(f"{f['file']}:{f['line']}  [{tag}] {f['call']}  (in {f['function']})")
        out.append(f"    why: {f['why']}")
        out.append(f"    fix: {f['fix']}")
    for s in report["skipped"]:
        out.append(f"skipped {s['file']}: {s['error']}")
    modes = report["modes"]
    n_rw = sum(1 for m in modes.values() if m == "rewindable")
    n_plain = sum(1 for m in modes.values() if m == "plain")
    n_help = sum(1 for m in modes.values() if m == "helper")
    by_kind = {}
    for f in report["findings"]:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    if out:
        out.append("")
    kinds = ", ".join(f"{v} {k}" for k, v in sorted(by_kind.items())) or "no findings"
    nw = len(report["workflows"])
    out.append(f"{report['files_scanned']} file(s): {n_rw} with rewindable workflows "
               f"({nw} workflow function{'' if nw == 1 else 's'}), {n_plain} plain, {n_help} helper. {kinds}.")
    if n_plain and any(f["kind"] == "needs-step" for f in report["findings"]):
        out.append("Plain files have no durable workflow yet: each line above is a call that needs its own step.")
    out.append("Static check only: a clean result is not proof. Kill a real run mid-way and resume it.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="durability_audit.py",
        description="Report calls that break durable replay in Python agent workflows (rewindable), "
                    "or list the side effects a plain script would need to turn into steps.",
        epilog=LIMITS + "\nexit codes: 0 clean, 1 findings, 2 nothing to scan",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="Python files or folders")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--include-tests", action="store_true",
                    help="also scan test_*.py, *_test.py and tests/ folders when walking a directory")
    a = ap.parse_args(argv)
    report = audit(a.paths, include_tests=a.include_tests)
    if report["files_scanned"] == 0:
        if a.json:
            print(json.dumps(report, indent=2))
        else:
            for s in report["skipped"]:
                print(f"skipped {s['file']}: {s['error']}", file=sys.stderr)
            print("nothing to scan: no readable .py files under " + ", ".join(a.paths), file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
