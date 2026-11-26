#!/usr/bin/env python3
"""Tests for check_names.py (stdlib unittest). No network: urlopen and getaddrinfo are mocked.

Run: python3 -m unittest discover -s skills/name-checker/scripts -p 'test_*.py'
"""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.error import HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_names as cn  # noqa: E402

BOOTSTRAP = {
    "version": "1.0", "publication": "2026-11-20T23:00:03Z",
    "services": [
        [["com", "net"], ["http://rdap.example-com.test/v1/", "https://rdap.example-com.test/v1/"]],
        [["dev", "app"], ["https://rdap.example-google.test/rdap"]],
        [["example.com"], ["https://rdap.sub.test/"]],
    ],
}


class Resp:
    def __init__(self, status=200, body=b""):
        self.status, self.body = status, body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code):
    return HTTPError(url, code, "x", {}, None)


class FakeNet:
    """Routes mocked requests: `routes` maps a URL substring to a status code, a body or an exception."""

    def __init__(self, routes, resolves=()):
        self.routes, self.resolves, self.requests = routes, set(resolves), []

    def open(self, req, timeout):
        self.requests.append(req)
        url = req.full_url
        if url == cn.BOOTSTRAP_URL:
            return Resp(200, json.dumps(BOOTSTRAP).encode())
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                if isinstance(value, bytes):
                    return Resp(200, value)
                if value == 200:
                    return Resp(200)
                raise http_error(url, value)
        raise http_error(url, 404)

    def resolve(self, host):
        if host in self.resolves:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0))]
        raise socket.gaierror(8, "nodename nor servname provided")

    def __enter__(self):
        self._patches = [mock.patch.object(cn, "_open", self.open), mock.patch.object(cn, "_resolve", self.resolve)]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self._interval = (cn.DEFAULT_INTERVAL, dict(cn.MIN_INTERVAL))
        cn.DEFAULT_INTERVAL, cn.MIN_INTERVAL = 0.0, {}
        cn.LIMITER = cn.RateLimiter()
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        os.environ.pop("GITHUB_TOKEN", None)
        self.addCleanup(env.stop)

    def tearDown(self):
        cn.DEFAULT_INTERVAL, cn.MIN_INTERVAL = self._interval[0], self._interval[1]


class BootstrapTests(Base):
    def test_parse_prefers_https_and_adds_trailing_slash(self):
        services = cn.parse_bootstrap(BOOTSTRAP)
        self.assertEqual(services["com"], "https://rdap.example-com.test/v1/")
        self.assertEqual(services["net"], "https://rdap.example-com.test/v1/")
        self.assertEqual(services["app"], "https://rdap.example-google.test/rdap/")

    def test_longest_label_match(self):
        services = cn.parse_bootstrap(BOOTSTRAP)
        self.assertEqual(cn.rdap_base("a.example.com", services), "https://rdap.sub.test/")
        self.assertEqual(cn.rdap_base("goodexample.com", services), "https://rdap.example-com.test/v1/")
        self.assertIsNone(cn.rdap_base("foo.io", services))

    def test_malformed_entries_are_skipped(self):
        self.assertEqual(cn.parse_bootstrap({"services": [["com"], [["dev"], []], "x"]}), {})


class DomainTests(Base):
    def test_dns_hit_means_taken_without_rdap(self):
        with FakeNet({}, resolves={"quillo.com"}) as net:
            res = cn.check_domain("quillo", "com", cn.Bootstrap())
        self.assertEqual(res["status"], "taken")
        self.assertEqual(net.requests, [])

    def test_rdap_404_is_likely_free(self):
        with FakeNet({"domain/quillo.dev": 404}) as net:
            res = cn.check_domain("quillo", "dev", cn.Bootstrap())
        self.assertEqual(res["status"], "free")
        last = net.requests[-1]
        self.assertEqual(last.full_url, "https://rdap.example-google.test/rdap/domain/quillo.dev")
        self.assertEqual(last.get_header("Accept"), "application/rdap+json")

    def test_rdap_200_is_taken(self):
        with FakeNet({"domain/quillo.com": 200}):
            self.assertEqual(cn.check_domain("quillo", "com", cn.Bootstrap())["status"], "taken")

    def test_tld_without_rdap_server_is_unknown(self):
        with FakeNet({}):
            res = cn.check_domain("quillo", "io", cn.Bootstrap())
        self.assertEqual(res["status"], "unknown")
        self.assertIn("no RDAP server", res["note"])

    def test_timeout_is_unknown(self):
        with FakeNet({"domain/": socket.timeout("timed out")}):
            self.assertEqual(cn.check_domain("quillo", "com", cn.Bootstrap())["status"], "unknown")

    def test_bootstrap_failure_is_unknown(self):
        with FakeNet({}) as net:
            net.open = mock.Mock(side_effect=OSError("down"))
            with mock.patch.object(cn, "_open", net.open):
                res = cn.check_domain("quillo", "com", cn.Bootstrap())
        self.assertEqual(res["status"], "unknown")

    def test_invalid_label(self):
        self.assertEqual(cn.check_domain("my_tool", "com", cn.Bootstrap())["status"], "invalid")


class RegistryTests(Base):
    def test_npm_lowercases_and_404_is_free(self):
        with FakeNet({}) as net:
            res = cn.check_platform("npm", "Quillo")
        self.assertEqual((res["status"], res["as"]), ("free", "quillo"))
        self.assertEqual(net.requests[0].full_url, "https://registry.npmjs.org/quillo")

    def test_pypi_uses_pep503_name(self):
        with FakeNet({"pypi.org": 200}) as net:
            res = cn.check_platform("pypi", "My.Cool__Tool")
        self.assertEqual((res["status"], res["as"]), ("taken", "my-cool-tool"))
        self.assertEqual(net.requests[0].full_url, "https://pypi.org/pypi/my-cool-tool/json")

    def test_crates_sends_descriptive_user_agent_without_email(self):
        with FakeNet({}) as net:
            cn.check_platform("crates", "quillo")
        ua = net.requests[0].get_header("User-agent")
        self.assertTrue(ua.startswith("name-checker/"))
        self.assertNotIn("@", ua)

    def test_brew_checks_formula_and_cask(self):
        with FakeNet({"cask/quillo": 200}):
            self.assertEqual(cn.check_platform("brew", "quillo")["status"], "taken")
        with FakeNet({}):
            self.assertEqual(cn.check_platform("brew", "quillo")["status"], "free")
        with FakeNet({"formula/quillo": 500}):
            self.assertEqual(cn.check_platform("brew", "quillo")["status"], "unknown")

    def test_docker_org_redirect_is_taken(self):
        with FakeNet({"hub.docker.com/v2/users/sift": 308}):
            self.assertEqual(cn.check_platform("docker", "sift")["status"], "taken")
        with FakeNet({}):
            self.assertEqual(cn.check_platform("docker", "lumora")["status"], "free")

    def test_rate_limit_is_unknown_not_free(self):
        with FakeNet({"api.github.com/users": 429}):
            res = cn.check_platform("github", "quillo")
        self.assertEqual(res["status"], "unknown")
        self.assertIn("rate limited", res["note"])

    def test_invalid_names_per_registry(self):
        self.assertIsNotNone(cn.registry_name("github", "my_tool")[1])
        self.assertIsNotNone(cn.registry_name("github", "a--b")[1])
        self.assertIsNotNone(cn.registry_name("crates", "9lives")[1])
        self.assertIsNone(cn.registry_name("crates", "my_tool")[1])
        self.assertIsNotNone(cn.registry_name("npm", "_private")[1])
        self.assertIsNotNone(cn.registry_name("npm", "http")[1])
        self.assertIsNotNone(cn.registry_name("docker", "abc")[1])
        self.assertIsNotNone(cn.registry_name("docker", "my-tool")[1])
        self.assertIsNotNone(cn.registry_name("pypi", "tool-")[1])
        with FakeNet({}) as net:
            self.assertEqual(cn.check_platform("github", "my_tool")["status"], "invalid")
        self.assertEqual(net.requests, [])

    def test_github_token_only_goes_to_github(self):
        os.environ["GITHUB_TOKEN"] = "test-token"
        with FakeNet({}) as net:
            cn.check_platform("github", "quillo")
            cn.check_platform("npm", "quillo")
        gh, npm = net.requests
        self.assertEqual(gh.get_header("Authorization"), "Bearer test-token")
        self.assertIsNone(npm.get_header("Authorization"))

    def test_repo_count(self):
        with FakeNet({"search/repositories": b'{"total_count": 281}'}):
            self.assertEqual(cn.github_repo_count("quillo"), 281)
        with FakeNet({"search/repositories": b"not json"}):
            self.assertIsNone(cn.github_repo_count("quillo"))


class QualityTests(unittest.TestCase):
    def flags(self, name):
        return " | ".join(cn.quality(name)[1])

    def test_clean_name_scores_full(self):
        self.assertEqual(cn.quality("lumora"), (100, []))

    def test_famous_names(self):
        self.assertIn("is the famous name 'stripe'", self.flags("stripe"))
        self.assertIn("close to the famous name 'stripe'", self.flags("stripee"))
        self.assertIn("contains the famous name 'react'", self.flags("reactify"))

    def test_generic_and_common_words(self):
        self.assertIn("data+sync", self.flags("datasync"))
        self.assertIn("not legal advice", self.flags("datasync"))
        self.assertIn("common word", self.flags("river"))

    def test_pronounceability_and_spelling(self):
        self.assertIn("no vowels", self.flags("xkcd"))
        self.assertIn("4+ consonants", self.flags("tumblr"))
        self.assertIn("dropped-vowel", self.flags("tumblr"))
        self.assertIn("digit", self.flags("app2go"))
        self.assertIn("long", self.flags("superlongproductname"))
        self.assertLess(cn.quality("xkcd")[0], cn.quality("lumora")[0])

    def test_edit_distance(self):
        self.assertEqual(cn.edit_distance("kitten", "sitting"), 3)
        self.assertEqual(cn.edit_distance("", "abc"), 3)


class RankingAndCliTests(Base):
    def test_free_name_outranks_taken_name(self):
        routes = {"lumora": 404, "datasync": 200}
        with FakeNet(routes, resolves={"datasync.com"}):
            results = cn.check_names(["datasync", "lumora"], ["com"], ["npm", "github"])
        self.assertEqual([r["name"] for r in results], ["lumora", "datasync"])
        self.assertEqual([r["rank"] for r in results], [1, 2])
        self.assertEqual(results[0]["domains"]["com"]["status"], "free")
        self.assertEqual(results[1]["platforms"]["npm"]["status"], "taken")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cn.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_offline_json_touches_no_network(self):
        with mock.patch.object(cn, "_open", side_effect=AssertionError("network")), \
                mock.patch.object(cn, "_resolve", side_effect=AssertionError("dns")):
            code, out, _ = self.run_cli("lumora", "my_tool", "--offline", "--json", "--tlds", ".com,dev")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["tlds"], ["com", "dev"])
        self.assertEqual(data["results"][0]["name"], "lumora")
        by_name = {r["name"]: r for r in data["results"]}
        self.assertEqual(by_name["my_tool"]["platforms"]["github"]["status"], "invalid")
        self.assertEqual(by_name["lumora"]["platforms"]["npm"]["status"], "unknown")

    def test_text_output_falls_back_to_ascii(self):
        code, out, _ = self.run_cli("lumora", "--offline", "--platforms", "npm")
        self.assertEqual(code, 0)
        self.assertIn("+ likely free", out)
        self.assertIn("trademark", out)

    def test_bad_input_exits_2(self):
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("foo bar", "--offline")[0], 2)
        self.assertEqual(self.run_cli("lumora", "--platforms", "myspace", "--offline")[0], 2)
        self.assertEqual(self.run_cli("lumora", "--tlds", "c_m", "--offline")[0], 2)
        self.assertEqual(self.run_cli("--file", "/nonexistent/names.txt")[0], 2)

    def test_file_input_with_comments_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "names.txt")
            with open(path, "w") as fh:
                fh.write("# ideas\nlumora\nLumora  # dup\n\nquillo\n")
            code, out, _ = self.run_cli("--file", path, "--offline", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(r["name"] for r in json.loads(out)["results"]), ["lumora", "quillo"])


class RateLimiterTests(unittest.TestCase):
    def test_same_host_is_spaced_out(self):
        saved = cn.MIN_INTERVAL
        cn.MIN_INTERVAL = {"crates.io": 0.05}
        try:
            limiter = cn.RateLimiter()
            start = time.monotonic()
            limiter.wait("crates.io")
            limiter.wait("crates.io")
            self.assertGreaterEqual(time.monotonic() - start, 0.045)
        finally:
            cn.MIN_INTERVAL = saved


if __name__ == "__main__":
    unittest.main()
