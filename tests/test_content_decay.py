"""Content Decay Monitor tests: run with  python -m unittest discover tests  (no network: fetch and the sitemap read are stubbed)."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seo"))
import engine  # noqa: E402

sys.modules["server"] = engine
import contentdecay as cd  # noqa: E402

YEAR = datetime.now(timezone.utc).year
OLD = (datetime.now(timezone.utc) - timedelta(days=900)).date().isoformat()


def page(body, head=""):
    return {"url": "https://acme.test/x", "status": 200, "chain": [], "ttfb": 1, "ms": 1, "truncated": False, "transfer_bytes": 1,
            "headers": {"content-type": "text/html; charset=utf-8"},
            "body": f"<html><head>{head}</head><body><nav><a href='/'>Home</a></nav><main>{body}</main><footer><p>© {YEAR} Acme, all rights reserved here.</p></footer></body></html>".encode()}


class Dates(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(cd._date("2024-03-05T10:00:00+05:30").date().isoformat(), "2024-03-05")
        self.assertEqual(cd._date("March 5th, 2024").date().isoformat(), "2024-03-05")
        self.assertEqual(cd._date("5 Mar 2024").date().isoformat(), "2024-03-05")
        self.assertEqual(cd._date("Tue, 05 Mar 2024 10:00:00 GMT").date().isoformat(), "2024-03-05")
        self.assertIsNone(cd._date(f"{YEAR + 2}-01-01"))  # future dates are mistakes
        self.assertIsNone(cd._date("soon"))

    def test_modified_beats_published(self):
        html = '<script type="application/ld+json">{"datePublished":"2021-01-10","dateModified":"2023-06-01"}</script>'
        mod, pub = cd._dates(html, "")
        self.assertEqual((mod[0].date().isoformat(), mod[1]), ("2023-06-01", "structured data"))
        self.assertEqual(pub[0].date().isoformat(), "2021-01-10")
        mod, _ = cd._dates('<meta property="article:modified_time" content="2022-02-02T00:00:00Z">', "Last updated: 3 May 2024 by the team")
        self.assertEqual(mod[0].date().isoformat(), "2024-05-03")


class Check(unittest.TestCase):
    def setUp(self):
        self._fetch = cd.fetch

    def tearDown(self):
        cd.fetch = self._fetch

    def run_check(self, res, lastmod="", traffic=None):
        cd.fetch = lambda *a, **k: res
        return cd.check("https://acme.test/x", lastmod, traffic)

    def test_stale_dated_article_with_falling_traffic(self):
        words = " ".join(["word"] * 400)
        r = self.run_check(page(f"<h1>Best CRMs in {YEAR - 3}</h1><p>In {YEAR - 3} prices rose and in {YEAR - 4} they fell. {words}</p>",
                                f'<title>Best CRMs in {YEAR - 3} | Acme</title><meta property="article:modified_time" content="{OLD}">'),
                           traffic={"clicks": 40, "clicksBefore": 200, "impr": 5000, "imprBefore": 5200, "pos": 7.5, "posBefore": 3.1})
        self.assertEqual(r["verdict"], "refresh")
        self.assertEqual(r["titleYear"], YEAR - 3)
        self.assertEqual(r["oldYears"], [YEAR - 4, YEAR - 3])
        self.assertFalse(r["currentYear"])  # the footer's copyright year doesn't count
        self.assertEqual(r["traffic"]["lost"], 160)
        self.assertTrue(any("Clicks fell 80%" in x for x in r["reasons"]))
        self.assertTrue(any("position slipped" in x for x in r["reasons"]))

    def test_fresh_page(self):
        today = datetime.now(timezone.utc).date().isoformat()
        r = self.run_check(page(f"<h1>Pricing</h1><p>Our {YEAR} plans start low and include support for everyone.</p>",
                                f'<script type="application/ld+json">{{"dateModified":"{today}"}}</script>'))
        self.assertEqual(r["verdict"], "fresh")
        self.assertEqual(r["ageDays"], 0)

    def test_sitemap_lastmod_of_today_is_ignored(self):
        today = datetime.now(timezone.utc).date().isoformat()
        r = self.run_check(page("<h1>Guide</h1><p>Some text about a topic that matters a great deal.</p>"), lastmod=today)
        self.assertIsNone(r["updated"])
        r = self.run_check(page("<h1>Guide</h1><p>Some text about a topic that matters a great deal.</p>"), lastmod=OLD)
        self.assertEqual((r["updated"], r["updatedFrom"]), (OLD, "sitemap"))

    def test_missing_page_with_traffic(self):
        res = page("")
        res["status"] = 404
        r = self.run_check(res, traffic={"clicks": 0, "clicksBefore": 50, "impr": 0, "imprBefore": 900, "pos": None, "posBefore": 4})
        self.assertEqual((r["verdict"], r["traffic"]["lost"]), ("error", 50))


class Pick(unittest.TestCase):
    def setUp(self):
        self._sm = cd._sitemap_entries
        cd._sitemap_entries = lambda origin, limit: [("https://acme.test/contact/", ""), ("https://acme.test/blog/new/", f"{YEAR}-01-01"),
                                                     ("https://acme.test/blog/old/", "2019-01-01"), ("https://acme.test/services/", "2018-01-01")]

    def tearDown(self):
        cd._sitemap_entries = self._sm

    def test_losing_pages_first_then_old_content(self):
        tr = cd._clean_traffic([{"url": "https://acme.test/a/", "clicks": 10, "clicksBefore": 90},
                                {"url": "https://acme.test/b/", "clicks": 5, "clicksBefore": 200},
                                {"url": "https://other.test/c/", "clicks": 1, "clicksBefore": 999}])
        got = [u.split("acme.test")[1] for u, _ in cd.pick("https://acme.test", 10, tr)]
        self.assertEqual(got[:2], ["/b/", "/a/"])
        self.assertEqual(got[2:], ["/blog/old/", "/blog/new/", "/services/", "/contact/"])


if __name__ == "__main__":
    unittest.main()
