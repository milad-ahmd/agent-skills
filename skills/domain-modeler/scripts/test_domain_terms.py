#!/usr/bin/env python3
"""Tests for domain_terms.py (stdlib unittest, no network).

Run: python3 -m unittest discover -s skills/domain-modeler/scripts -p 'test_*.py'
fixtures/shop is a small multi-language shop: Customer, ClientAccount, User and Member for one party
concept, Order declared in checkout/ (Python) and fulfilment/ (Go), enums in six languages, a SQL schema.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from domain_terms import analyse, parse_glossary, scan, singular, split_words, term_words  # noqa: E402

SCRIPT = os.path.join(HERE, "domain_terms.py")
SHOP = os.path.join(HERE, "fixtures", "shop")
GLOSSARY = os.path.join(HERE, "fixtures", "GLOSSARY.md")


def run(*args):
    r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def write_tree(root, files):
    for name, text in files.items():
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)


class Splitting(unittest.TestCase):
    def test_camel_case_and_acronyms(self):
        self.assertEqual(split_words("OrderLineItemDTO"), ["order", "line", "item", "dto"])
        self.assertEqual(split_words("HTTPServer"), ["http", "server"])
        self.assertEqual(split_words("IOrderRepository"), ["i", "order", "repository"])

    def test_snake_and_upper_snake(self):
        self.assertEqual(split_words("order_status"), ["order", "status"])
        self.assertEqual(split_words("ORDER_STATUS"), ["order", "status"])
        self.assertEqual(split_words("shipping-address2"), ["shipping", "address", "2"])

    def test_singular(self):
        cases = {"customers": "customer", "categories": "category", "addresses": "address", "statuses": "status",
                 "status": "status", "boxes": "box", "skus": "sku", "analysis": "analysis"}
        for word, want in cases.items():
            self.assertEqual(singular(word), want, word)


class SuffixStripping(unittest.TestCase):
    def test_technical_suffixes_and_prefixes_dropped(self):
        self.assertEqual(term_words("CustomerService"), ["customer"])
        self.assertEqual(term_words("OrderLineDto"), ["order", "line"])
        self.assertEqual(term_words("IOrderRepository"), ["order"])
        self.assertEqual(term_words("AbstractInvoiceHandlerImpl"), ["invoice"])
        self.assertEqual(term_words("PaymentDeclinedError"), ["payment", "declined"])

    def test_only_technical_words_gives_nothing(self):
        for name in ("BaseService", "Meta", "HttpClient", "RedisCache", "JsonPayloadMapper", "Config"):
            self.assertEqual(term_words(name), [], name)

    def test_domain_words_that_look_technical_survive(self):
        self.assertEqual(term_words("OrderType"), ["order", "type"])
        self.assertEqual(term_words("PriceList"), ["price", "list"])
        self.assertEqual(term_words("orders"), ["order"])


class Extraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files, cls.decls = scan(SHOP)

    def find(self, name, kind=None):
        return [d for d in self.decls if d["name"] == name and (kind is None or d["kind"] == kind)]

    def members(self, parent):
        return [d["name"] for d in self.decls if d["parent"] == parent]

    def test_python_enum_members_not_method_locals(self):
        self.assertEqual(self.members("OrderStatus"), ["PENDING", "PAID", "CANCELLED"])
        self.assertEqual(self.find("OrderStatus")[0]["line"], 10)

    def test_java_kotlin_and_ts_enum_members(self):
        self.assertEqual(self.members("InvoiceStatus"), ["DRAFT", "ISSUED", "VOIDED"])
        self.assertEqual(self.members("ParcelSize"), ["SMALL", "LARGE"])
        self.assertEqual(self.members("PaymentState"), ["Pending", "Voided"])  # comma inside a string is not a member

    def test_go_iota_block_and_untyped_const(self):
        self.assertEqual(self.members("ShipmentStatus"), ["ShipmentPending", "ShipmentDelivered"])
        self.assertFalse(self.find("MaxRetries"))
        self.assertEqual(self.find("Order", "struct")[0]["file"], "fulfilment/order.go")

    def test_sql_prisma_and_table_hints(self):
        self.assertEqual({d["name"] for d in self.decls if d["kind"] == "table"}, {"customers", "orders", "invoices"})
        self.assertEqual(self.members("order_status"), ["pending", "paid", "canceled"])
        self.assertEqual(self.members("Role"), ["MEMBER"])  # ADMIN is a technical word
        self.assertTrue(self.find("Member", "model"))

    def test_ruby_namespaced_class_and_test_dirs_skipped(self):
        self.assertTrue(self.find("Adress", "class"))
        self.assertFalse([d for d in self.decls if d["file"].startswith("tests/")])
        self.assertFalse(self.find("HttpClient"))

    def test_table_hints_in_orm_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, {
                "a.py": "class Booking(Base):\n    __tablename__ = 'bookings'\n",
                "b.ts": "@Entity({ name: \"reservations\" })\nexport class Reservation {}\n",
                "C.java": "@Entity\n@Table(name = \"purchases\")\npublic class Purchase {}\n",
                "db/migrate/1.rb": "create_table :line_items do |t|\nend\n",
                "Order.test.ts": "export class ShouldBeSkipped {}\n",
            })
            _, decls = scan(tmp)
        tables = {d["name"] for d in decls if d["kind"] == "table"}
        self.assertEqual(tables, {"bookings", "reservations", "purchases", "line_items"})
        self.assertNotIn("ShouldBeSkipped", {d["name"] for d in decls})


class Analysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = analyse(scan(SHOP)[1])

    def test_party_synonyms_grouped_with_locations(self):
        party = next(g for g in self.res["synonyms"] if g["group"] == "party")
        self.assertTrue({"customer", "client", "user", "member"} <= set(party["words"]))
        self.assertIn("billing/ClientAccount.java:3", party["words"]["client"]["examples"])

    def test_cancel_spellings_grouped(self):
        cancel = next(g for g in self.res["synonyms"] if g["group"] == "cancel")
        self.assertEqual(set(cancel["words"]), {"cancelled", "canceled", "voided"})

    def test_order_in_two_contexts(self):
        multi = {m["term"]: m for m in self.res["multi_defined"]}
        self.assertEqual(multi["order"]["folders"], ["checkout", "fulfilment"])
        self.assertNotIn("order status", multi)  # the SQL enum mirrors the code enum, not a second context
        self.assertFalse(any("OrderService" in e for e in multi["order"]["examples"]))

    def test_near_duplicate_spellings(self):
        pairs = {tuple(n["variants"]) for n in self.res["near_duplicates"]}
        self.assertIn(("address", "adress"), pairs)
        self.assertIn(("canceled", "cancelled"), pairs)
        self.assertNotIn(("order", "orders"), pairs)

    def test_word_split_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, {"a.ts": "export class CheckOut {}\n", "b.py": "class Checkout:\n    pass\n"})
            res = analyse(scan(tmp)[1])
        self.assertIn({"kind": "word split", "variants": ["check out", "checkout"]},
                      [{k: n[k] for k in ("kind", "variants")} for n in res["near_duplicates"]])


class GlossaryCheck(unittest.TestCase):
    def test_parse_formats(self):
        g = parse_glossary("## Customer\n- **Avoid:** `client`, buyers\nAllow: StripeClient\n### Order Line\n* avoid: line item\n")
        self.assertEqual(g["avoid"], {"client": "Customer", "buyer": "Customer", "line item": "Order Line"})
        self.assertEqual(g["allow"], {"StripeClient"})
        self.assertEqual(g["terms"], ["Customer", "Order Line"])

    def test_violations_exit_1(self):
        code, out, _ = run(SHOP, "--glossary", GLOSSARY, "--json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        got = {(v["file"], v["line"], v["avoid"], v["use"]) for v in data["violations"]}
        self.assertIn(("billing/ClientAccount.java", 3, "client", "Customer"), got)
        self.assertIn(("db/001_init.sql", 3, "canceled", "Cancelled"), got)
        self.assertEqual(data["conflicts"], ["order"])
        _, out2, _ = run(SHOP, "--glossary", GLOSSARY)
        self.assertIn("warning: 'order' is both a glossary term", out2)  # 'order' is a heading and an Avoid: word, so not checked
        self.assertFalse([v for v in data["violations"] if v["avoid"] == "order"])

    def test_text_output_and_allow(self):
        with tempfile.TemporaryDirectory() as tmp:
            gl = os.path.join(tmp, "GLOSSARY.md")
            with open(gl, "w") as fh:
                fh.write("## Customer\n- Avoid: client\n- Allow: ClientAccount\n## Order (Checkout)\n- Avoid: purchase\n"
                         "## Cancelled\n- Avoid: order\n")
            code, out, _ = run(SHOP, "--glossary", gl)
        self.assertEqual(code, 0, out)
        self.assertIn("0 glossary violation(s)", out)
        self.assertIn("'order' is both a glossary term", out)  # a context suffix in brackets still names the term

    def test_multiword_avoid_phrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, {"a.py": "class LineItem:\n    pass\nclass Item:\n    pass\n",
                             "G.md": "## Order Line\n- Avoid: line item\n"})
            code, out, _ = run(os.path.join(tmp, "a.py"), "--glossary", os.path.join(tmp, "G.md"))
        self.assertEqual(code, 1)
        self.assertIn("a.py:1: class LineItem uses 'line item'", out)
        self.assertNotIn("class Item ", out)


class Cli(unittest.TestCase):
    def test_report_text_and_json(self):
        code, out, _ = run(SHOP)
        self.assertEqual(code, 0)
        self.assertIn("Possible synonyms", out)
        code, out, _ = run(SHOP, "--json")
        data = json.loads(out)
        self.assertEqual(set(data) >= {"terms", "synonyms", "multi_defined", "near_duplicates", "declarations"}, True)
        self.assertEqual(data["terms"][0]["term"], "order")

    def test_exclude_glob(self):
        _, out, _ = run(SHOP, "--json", "--exclude", "prisma/*")
        self.assertNotIn("Member", {d["name"] for d in json.loads(out)["declarations"]})

    def test_nothing_to_scan_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run(tmp)[0], 2)
            write_tree(tmp, {"notes.md": "# hi\n", "x.py": "import os\n"})
            self.assertEqual(run(tmp)[0], 2)
            self.assertEqual(run(os.path.join(tmp, "missing"))[0], 2)

    def test_glossary_without_avoid_lines_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            gl = os.path.join(tmp, "G.md")
            with open(gl, "w") as fh:
                fh.write("## Customer\nSomeone who pays.\n")
            code, _, err = run(SHOP, "--glossary", gl)
        self.assertEqual(code, 2)
        self.assertIn("Avoid:", err)


if __name__ == "__main__":
    unittest.main()
