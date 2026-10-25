"""Internal Link Opportunities tests: run with  python -m unittest discover tests  (no network; pages are parsed from strings)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seo"))
import engine  # noqa: E402

sys.modules["server"] = engine
import internallinks as il  # noqa: E402

NAV = '<nav><a href="/">Home</a><a href="/services/">Services</a><a href="/blog/">Blog</a></nav>'
FOOT = '<footer><p>© Acme Plumbing, all rights reserved in every city we serve.</p></footer>'


def page(path, title, h1, body, depth=1):
    url = "https://acme.test" + path
    P = il._Page(url)
    P.feed(f"<html><head><title>{title} | Acme Plumbing</title></head><body>{NAV}<main><h1>{h1}</h1>{body}</main>{FOOT}</body></html>")
    P.close()
    links = [{"url": il._link(a["href"], url), "text": a["text"], "boiler": a["boiler"], "nofollow": False} for a in P.links]
    return {"url": url, "final": url, "status": 200, "key": il._clean(url), "depth": depth, "fromSitemap": False, "title": P.title,
            "h1": P.h1, "links": links, "blocks": P.blocks, "noindex": False, "canonical": None}


SITE = [
    page("/", "Plumbers in Leeds", "Your local plumbers", '<p>We fix leaks, boilers and drains across Leeds every day of the week.</p>'
         '<p>Read about <a href="/services/boiler-repair/">boiler repair</a> or our guide to unblocking drains.</p>', 0),
    page("/services/", "Services", "Our services", '<p>Boiler repair, drain unblocking and emergency call-outs for homes and offices.</p>'),
    page("/services/boiler-repair/", "Boiler Repair", "Boiler repair in Leeds", '<p>Gas Safe engineers repair all major boiler brands quickly.</p>', 2),
    page("/services/drain-unblocking/", "Drain Unblocking", "Drain unblocking", '<p>We clear blocked sinks, toilets and outside drains the same day.</p>', 2),
    page("/blog/", "Blog", "Plumbing blog", '<p>Tips from our engineers on keeping your heating and pipes in good shape.</p>'),
    page("/blog/frozen-pipes/", "How to Thaw Frozen Pipes", "How to thaw frozen pipes",
         '<p>When a pipe freezes, pressure can build up behind the ice and cause a burst, so call for drain unblocking or emergency help.</p>'
         '<p>If the heating stops too, you may need boiler repair before the house warms up again.</p>', 2),
    page("/blog/winter-checklist/", "Winter Plumbing Checklist", "Winter plumbing checklist",
         '<p>Before the cold arrives, learn how to thaw frozen pipes safely and book a boiler service.</p>', 2),
]


class InternalLinks(unittest.TestCase):
    def setUp(self):
        self.r = il.analyze("https://acme.test", SITE, True)
        self.pairs = {(o["source"].split("acme.test")[1], o["target"].split("acme.test")[1]): o for o in self.r["opportunities"]}

    def test_parser_keeps_spacing_between_linked_and_plain_text(self):
        text = " ".join("".join(t for t, _ in b["runs"]) for b in SITE[0]["blocks"])
        self.assertIn("Read about boiler repair or our guide", text)

    def test_unlinked_mentions_become_suggestions(self):
        o = self.pairs[("/blog/frozen-pipes/", "/services/boiler-repair/")]
        self.assertEqual(o["anchor"].lower(), "boiler repair")
        self.assertIn("you may need", o["before"])
        self.assertIn(("/blog/frozen-pipes/", "/services/drain-unblocking/"), self.pairs)
        self.assertIn(("/blog/winter-checklist/", "/blog/frozen-pipes/"), self.pairs)

    def test_existing_links_and_nav_text_are_not_suggested(self):
        self.assertNotIn(("/", "/services/boiler-repair/"), self.pairs)  # already links in the content
        self.assertFalse(any(o["anchor"].lower() in ("home", "blog", "services") for o in self.r["opportunities"]))
        self.assertFalse(any(t == "/" for _, t in self.pairs))  # everyone links home already

    def test_page_stats(self):
        rows = {r["url"].split("acme.test")[1]: r for r in self.r["pages"]}
        self.assertEqual(rows["/services/boiler-repair/"]["inbound"], 1)
        self.assertTrue(rows["/blog/winter-checklist/"]["orphan"])
        self.assertFalse(rows["/services/"]["orphan"])  # linked from the menu

    def test_focus_page_with_keyword(self):
        r = il.analyze("https://acme.test", SITE, True, "https://acme.test/services/drain-unblocking/", "blocked sinks, emergency call-outs")
        self.assertTrue(r["opportunities"])
        self.assertTrue(all(o["target"].endswith("/drain-unblocking/") for o in r["opportunities"]))
        self.assertIn("emergency call-outs", [o["anchor"].lower() for o in r["opportunities"]])


if __name__ == "__main__":
    unittest.main()
