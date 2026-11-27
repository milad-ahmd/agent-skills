#!/usr/bin/env python3
"""Tests for scan_secrets.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/env-secrets-guard/scripts -p 'test_*.py'
Detector tests call scan_text() directly; CLI tests build throwaway git repos and run the script.
Every fake credential is assembled at runtime from split parts so this file never contains a
literal secret shape (GitHub push protection and skills-doctor --strict would flag it).
test_source_has_no_literal_secrets enforces that.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scan_secrets as ss  # noqa: E402

SCRIPT = os.path.join(HERE, "scan_secrets.py")
BASE_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", TZ="UTC",
                PYTHONIOENCODING="utf-8")
for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
    BASE_ENV.pop(var, None)
WHO = dict(GIT_AUTHOR_NAME="Pat Example", GIT_AUTHOR_EMAIL="pat@example.com", GIT_COMMITTER_NAME="Pat Example",
           GIT_COMMITTER_EMAIL="pat@example.com", GIT_AUTHOR_DATE="2026-11-01T12:00:00Z",
           GIT_COMMITTER_DATE="2026-11-01T12:00:00Z")

FILL = "Hq4Lm9"  # neutral filler: no placeholder words (test/example/xxxx/dummy...) or the scanner skips it
FAKE = {
    "AWS access key": "AK" + "IA" + "Q7RZ" * 4,
    "OpenAI key": "sk" + "-proj-" + FILL * 4,
    "Anthropic key": "sk" + "-ant-" + "api03-" + FILL * 4,
    "GitHub token": "gh" + "p_" + FILL * 6,
    "Slack token": "xo" + "xb-" + "2048" * 3 + "-" + FILL,
    "Google API key": "AI" + "za" + "Sy" + FILL * 5 + "Abc",
    "Stripe live key": "sk" + "_live_" + FILL * 5,
    "Private key": "-----BEGIN " + "OPENSSH" + " PRIVATE KEY-----",
    "DB URL with password": "post" + "gresql://" + "app:" + FILL + "pw" + "@db.internal/app",
    "Hard-coded secret": "pass" + "word" + ' = "' + FILL + "Zr" + '"',
}
GITHUB_PAT = "github" + "_pat_" + "11AB" + FILL * 9


def run_scan(path, *args, cwd=None):
    argv = [sys.executable, SCRIPT] + ([path] if path else []) + list(args)
    return subprocess.run(argv, cwd=cwd, env=BASE_ENV, capture_output=True, encoding="utf-8", timeout=60)


class Repo:
    def __init__(self, root):
        self.path = os.path.join(root, "repo")
        os.makedirs(self.path)
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.path, env=dict(BASE_ENV, **WHO), capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout

    def write(self, rel, data):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as fh:
            fh.write(data)

    def commit(self, files, msg="change"):
        for rel, data in files.items():
            if data is None:
                os.remove(os.path.join(self.path, rel))
            else:
                self.write(rel, data)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "--short", "HEAD").strip()


def scan(text):
    findings = []
    ss.scan_text(text, "f.py", findings, set())
    return findings


class DetectorTests(unittest.TestCase):
    def test_each_detector_fires_alone(self):
        for name, value in FAKE.items():
            with self.subTest(rule=name):
                self.assertEqual([f[0] for f in scan("x = " + value + "\n")], [name])

    def test_detector_alternations(self):
        cases = {GITHUB_PAT: "GitHub token", "gh" + "o_" + FILL * 6: "GitHub token", "gh" + "s_" + FILL * 6: "GitHub token",
                 "AS" + "IA" + "Q7RZ" * 4: "AWS access key", "rk" + "_live_" + FILL * 5: "Stripe live key",
                 "sk" + "-" + FILL * 4: "OpenAI key"}  # the last one: legacy key without proj-
        for value, name in cases.items():
            with self.subTest(value=value[:4]):
                self.assertEqual([f[0] for f in scan(value)], [name])

    def test_hard_coded_secret_variants(self):
        val = FILL + "Zr"
        for line in ("api_key: '%s'", "apikey='%s'", 'access_token = "%s"', 'auth_token: "%s"',
                     'SECRET = "%s"', 'passwd="%s"',
                     # prefixed names and JSON keys, the most common real-world forms
                     'DB_PASSWORD="%s"', 'OPENAI_API_KEY="%s"', 'SECRET_KEY = "%s"', '"password": "%s"'):
            with self.subTest(line=line % "..."):
                self.assertEqual([f[0] for f in scan(line % val)], ["Hard-coded secret"])

    def test_hard_coded_rule_needs_the_whole_name(self):
        val = FILL + "Zr"
        for line in ('password_hash = "%s"', 'mysecretsauce = "%s"', 'api_key_url = "%s"'):
            with self.subTest(line=line % "..."):
                self.assertEqual(scan(line % val), [])

    def test_translation_labels_are_not_secrets(self):
        pw = "pass" + "word"  # split so the hygiene test does not see a literal match in this file
        for line in ('"%s": "Password"' % pw, '"secret": "Geheim' + 'nis"', 'passwd="Contra' + 'se\u00f1a"'):
            with self.subTest(line=line):
                self.assertEqual(scan(line), [])
        found = []
        ss.scan_text('"api_key": "API-' + FILL + '"', "web/locales/de.json", found, set())
        self.assertEqual(found, [])
        # outside a locale folder the same line is still a finding, and lower-case or mixed values still count
        self.assertEqual([f[0] for f in scan('"api_key": "API-' + FILL + '"')], ["Hard-coded secret"])
        self.assertEqual([f[0] for f in scan(pw + ' = "letmein' + 'please"')], ["Hard-coded secret"])

    def test_db_url_variants(self):
        for scheme in ("postgres", "mysql", "mongodb+srv", "redis", "amqp"):
            with self.subTest(scheme=scheme):
                url = scheme + "://svc:" + FILL + "@host:1234/db"
                self.assertEqual([f[0] for f in scan(url)], ["DB URL with password"])

    def test_location_and_redaction(self):
        found = scan("a = 1\nb = 2\nkey = '" + FAKE["AWS access key"] + "'\n")
        self.assertEqual(len(found), 1)
        name, where, shown = found[0]
        self.assertEqual(where, "f.py:3")
        self.assertEqual(shown, "AKIA****(20 chars)")
        self.assertEqual(ss.redact("  abcdefgh "), "abcd****")  # short values: no length hint

    def test_placeholders_are_skipped(self):
        for value in ("AK" + "IA" + "IOSFODNN7" + "EXAMPLE",
                      "sk" + "-proj-" + "your_" + FILL * 4,
                      "gh" + "p_" + "x" * 36,
                      "sk" + "_live_" + "dummy" + FILL * 4,
                      "pass" + "word" + ' = "changeme123"',
                      "pass" + "word" + ' = "<db-password>"',
                      "pass" + "word" + ' = "${DB_PASS}"',
                      "postgres" + "://user:" + "test1234" + "@localhost/db"):
            with self.subTest(value=value[:8]):
                self.assertEqual(scan(value), [])

    def test_ordinary_code_has_no_false_positives(self):
        code = "\n".join([
            "import os",
            "password = os.environ['DB_PASSWORD']",
            "token = get_token(user)",
            "secret = load()",
            'password = "short"',
            "from sklearn import sk_learn_helper",
            "def private_key_path(): return 'keys/id.pub'",
            "DATABASE_URL = 'postgres://localhost:5432/app'",
            "REDIS = 'redis://cache:6379/0'",
            "sha = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'",
            "uuid = '123e4567-e89b-12d3-a456-426614174000'",
            "akia = 'akia" + "q7rz" * 4 + "'",  # lowercase is not an AWS key id
            "risk = '-----BEGIN PUBLIC KEY-----'",
            "# see docs about the passwords_list helper",
        ])
        self.assertEqual(scan(code), [])

    def test_word_boundary_prevents_embedded_matches(self):
        self.assertEqual(scan("ID" + FAKE["AWS access key"] + "9"), [])

    def test_same_value_reported_once_per_rule(self):
        self.assertEqual(len(scan(FAKE["GitHub token"] + "\n" + FAKE["GitHub token"])), 1)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_repo_exits_zero(self):
        self.repo.commit({"app.py": "print('hello')\n", "README.md": "# demo\n"})
        r = run_scan(self.repo.path)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Scanned 2 tracked files", r.stdout)
        self.assertIn("No likely secrets found", r.stdout)

    def test_secret_exits_one_with_location_and_never_full_value(self):
        key = FAKE["Stripe live key"]
        self.repo.commit({"src/pay.py": "import stripe\nstripe.api_base = 1\nKEY = '" + key + "'\n"})
        r = run_scan(self.repo.path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Stripe live key", r.stdout)
        self.assertIn("src/pay.py:3", r.stdout)
        self.assertIn("sk_l****", r.stdout)
        self.assertNotIn(key, r.stdout + r.stderr)
        self.assertIn("1 likely secret(s) (0 only in history), 0 risky file(s).", r.stdout)

    def test_anthropic_key_counted_once(self):
        self.repo.commit({"ai.py": "K = '" + FAKE["Anthropic key"] + "'\n"})
        r = run_scan(self.repo.path)
        self.assertIn("Anthropic key", r.stdout)
        self.assertNotIn("OpenAI key", r.stdout)
        self.assertIn("1 likely secret(s)", r.stdout)

    def test_risky_files_are_flagged(self):
        risky = [".env", "config/.env.production", "id_rsa", "id_ed25519", "certs/server.pem", "store.p12",
                 "android/release.keystore", "app.jks", "credentials.json", "gcp/service-account-prod.json"]
        self.repo.commit({f: "placeholder\n" for f in risky + ["notes.txt"]})
        r = run_scan(self.repo.path)
        self.assertEqual(r.returncode, 1)
        for f in risky:
            self.assertIn("risky file tracked: %s " % f, r.stdout)
        self.assertNotIn("notes.txt", r.stdout)
        self.assertIn("%d risky file(s)" % len(risky), r.stdout)

    def test_env_templates_are_allowed(self):
        self.repo.commit({".env.example": "API_KEY=\n", ".env.sample": "API_KEY=\n", ".env.template": "API_KEY=\n"})
        r = run_scan(self.repo.path)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_env_template_content_is_still_scanned(self):
        self.repo.commit({".env.example": "AWS_KEY_ID=" + FAKE["AWS access key"] + "\n"})
        r = run_scan(self.repo.path)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("risky file tracked", r.stdout)
        self.assertIn(".env.example:1", r.stdout)

    def test_unusual_file_names_are_scanned(self):
        # git ls-files C-quotes these names unless -z is used; the scanner used to skip them silently
        self.repo.commit({"配置.py": "K = '" + FAKE["GitHub token"] + "'\n",
                          'we"ird.py': "K = '" + FAKE["Slack token"] + "'\n"})
        r = run_scan(self.repo.path)
        self.assertIn("配置.py:1", r.stdout)
        self.assertIn('we"ird.py:1', r.stdout)
        self.assertIn("2 likely secret(s)", r.stdout)

    def test_untracked_files_are_not_scanned(self):
        self.repo.commit({"app.py": "x = 1\n"})
        self.repo.write("scratch.py", "K = '" + FAKE["AWS access key"] + "'\n")
        self.assertEqual(run_scan(self.repo.path).returncode, 0)

    def test_binary_files_do_not_crash(self):
        self.repo.commit({"logo.png": b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01", "a.py": "x=1\n"})
        r = run_scan(self.repo.path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")

    def test_defaults_to_current_directory(self):
        self.repo.commit({"a.py": "K = '" + FAKE["Google API key"] + "'\n"})
        r = run_scan(None, cwd=self.repo.path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Google API key", r.stdout)

    def test_not_a_git_repo(self):
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        env_root = os.path.dirname(self.tmp.name)
        r = subprocess.run([sys.executable, SCRIPT, plain], env=dict(BASE_ENV, GIT_CEILING_DIRECTORIES=env_root),
                           capture_output=True, encoding="utf-8", timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Not a git repository", r.stderr)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)
        self.key = FAKE["AWS access key"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_removed_secret_needs_history_flag(self):
        sha = self.repo.commit({"cfg.py": "K = '" + self.key + "'\n"}, "add key")
        self.repo.commit({"cfg.py": "import os\nK = os.environ['K']\n"}, "remove key")
        self.assertEqual(run_scan(self.repo.path).returncode, 0)
        r = run_scan(self.repo.path, "--history")
        self.assertEqual(r.returncode, 1)
        self.assertIn("+ full git history", r.stdout)
        self.assertIn("history@%s" % sha, r.stdout)
        self.assertIn("1 likely secret(s) (1 only in history)", r.stdout)
        self.assertNotIn(self.key, r.stdout)

    def test_history_covers_other_branches(self):
        self.repo.commit({"a.py": "x = 1\n"})
        self.repo.git("checkout", "-q", "-b", "feature")
        sha = self.repo.commit({"b.py": "T = '" + FAKE["GitHub token"] + "'\n"})
        self.repo.git("checkout", "-q", "main")
        r = run_scan(self.repo.path, "--history")
        self.assertIn("history@%s" % sha, r.stdout)
        self.assertEqual(r.returncode, 1)

    def test_secret_in_tree_and_history_is_not_double_counted(self):
        self.repo.commit({"cfg.py": "K = '" + self.key + "'\n"})
        r = run_scan(self.repo.path, "--history")
        self.assertIn("cfg.py:1", r.stdout)
        self.assertNotIn("history@", r.stdout)
        self.assertIn("1 likely secret(s) (0 only in history)", r.stdout)

    def test_removed_lines_are_not_reported_as_new(self):
        # a deletion diff line starts with '-', only '+' lines are scanned
        self.repo.commit({"cfg.py": "K = '" + self.key + "'\n"})
        self.repo.commit({"cfg.py": "x = 1\n"})
        r = run_scan(self.repo.path, "--history")
        self.assertEqual(r.stdout.count("AWS access key"), 1)


class HygieneTests(unittest.TestCase):
    def test_source_has_no_literal_secrets(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            src = fh.read()
        for name, rx in ss.RULES:
            self.assertIsNone(rx.search(src), "literal %s in test source" % name)
        with open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8") as fh:
            grep = re.search(r"git grep -nIE '([^']+)'", fh.read()).group(1)
        self.assertTrue(re.search(grep, FAKE["AWS access key"]))  # the extracted pattern has teeth
        self.assertIsNone(re.search(grep, src), "SKILL.md grep pattern matches test source")


if __name__ == "__main__":
    unittest.main()
