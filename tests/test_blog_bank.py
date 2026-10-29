"""Blog Bank tests: run with  python -m unittest discover tests  (no MongoDB, no AI calls: memcoll and a fake client)."""
import json
import os
import sys
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seo"))
import engine  # noqa: E402

sys.modules["server"] = engine
import blogai  # noqa: E402
import blogbank  # noqa: E402
from server import AuditError  # noqa: E402

SETTINGS = {"location": "Kochi, India", "language": "English", "audience": "small business owners", "words": 1200, "tone": "Friendly", "brand": "Acme Digital"}
ARTICLE = ("<p>Intro about local seo for clinics in Kochi.</p><h2>Why local SEO matters</h2><p>Text <strong>bold</strong> and "
           "<a href=\"https://acme.in/services/\">our services</a>.</p><h3>Details</h3><ul><li>One</li><li>Two</li></ul>"
           "<table><tr><th>A</th><td colspan=\"2\">B</td></tr></table>")


def fake_blog(brief, pages):
    if "fail" in brief["topic"]:
        raise AuditError("The AI service is rate-limiting requests right now. Retry this one in a minute.")
    return {"title": brief["topic"].title(), "meta_title": "Meta " + brief["keyword"], "meta_description": "Desc", "slug": "Local SEO Guide!",
            "secondary_keywords": ["a", "b"], "article_html": ARTICLE + "<script>alert(1)</script>",
            "faqs": [{"question": "Q1?", "answer": "A1."}], "internal_links": [{"anchor_text": "services", "url": "https://acme.in/services/", "reason": "r"}],
            "image_alts": [{"placement": "top", "alt_text": "A clinic front desk"}]}


def wait(ids, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        docs = [blogbank._get(i) for i in ids]
        if all(d["gen"] in ("done", "failed") for d in docs):
            return docs
        time.sleep(0.05)
    raise AssertionError("generation didn't finish")


class BlogBank(unittest.TestCase):
    def setUp(self):
        self.blogs, self.vers = blogbank.use_memory()
        blogbank._recent.clear()
        blogbank._recent_sections.clear()
        self._orig = (blogai.write_blog, blogai._anthropic, blogai.rewrite_section)
        blogai.write_blog = fake_blog
        blogai._anthropic = lambda: object()
        blogai.rewrite_section = lambda blog, html, ins: "<h2>Better heading</h2><p>Rewritten <em>text</em>.</p><img src=x onerror=alert(1)>"

    def tearDown(self):
        blogai.write_blog, blogai._anthropic, blogai.rewrite_section = self._orig

    def gen(self, topics):
        r = blogbank.generate({"label": "Weekend batch · test"}, SETTINGS, [{"topic": t, "keyword": "local seo " + t} for t in topics], "Farsana")
        return r, wait([i["id"] for i in r["items"]])

    def test_batch_saves_every_draft_and_one_failure_does_not_affect_others(self):
        r, docs = self.gen(["clinics", "fail please", "dentists"])
        self.assertEqual([d["gen"] for d in docs], ["done", "failed", "done"])
        self.assertIn("rate-limiting", docs[1]["genError"])
        ok = docs[0]
        self.assertEqual(ok["status"], "ready")
        self.assertEqual(ok["slug"], "local-seo-guide")
        self.assertNotIn("<script", ok["html"])
        self.assertEqual(ok["faqs"], [{"q": "Q1?", "a": "A1."}])
        self.assertGreater(ok["wordCount"], 10)
        self.assertEqual([h["level"] for h in ok["headings"]], [2, 3])
        self.assertEqual(ok["createdBy"], "Farsana")
        # the failed one is retried on its own; the others keep their content and version
        before = blogbank._get(docs[2]["_id"])
        blogai.write_blog = lambda b, p: fake_blog({**b, "topic": "clinics"}, p)
        blogbank.retry(docs[1]["_id"], "Farsana")
        again = wait([docs[1]["_id"]])[0]
        self.assertEqual(again["gen"], "done")
        self.assertEqual(blogbank._get(docs[2]["_id"])["version"], before["version"])
        with self.assertRaises(AuditError):
            blogbank.retry(docs[0]["_id"], "Farsana")  # finished blogs aren't regenerated over edits

    def test_generate_validates_input(self):
        with self.assertRaises(AuditError):
            blogbank.generate({}, SETTINGS, [{"topic": "x", "keyword": ""}], "F")
        with self.assertRaises(AuditError):
            blogbank.generate({}, SETTINGS, [{"topic": f"t{i}", "keyword": "k"} for i in range(11)], "F")
        with self.assertRaises(AuditError):
            blogbank.generate({}, {**SETTINGS, "website": "not a site"}, [{"topic": "t", "keyword": "k"}], "F")
        self.assertEqual(self.blogs.count_documents({}), 0)

    def test_ai_not_configured_saves_nothing(self):
        blogai._anthropic = self._orig[1]
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with self.assertRaises(blogai.NotConfigured):
            blogbank.generate({}, SETTINGS, [{"topic": "t", "keyword": "k"}], "F")
        self.assertEqual(self.blogs.count_documents({}), 0)

    def test_hourly_limit(self):
        blogbank._recent.extend([time.time()] * (blogbank.MAX_PER_HOUR - 1))
        with self.assertRaises(AuditError):
            blogbank.generate({}, SETTINGS, [{"topic": "a", "keyword": "k"}, {"topic": "b", "keyword": "k"}], "F")

    def test_clean_html_allow_list(self):
        dirty = ('<h1>Big</h1><p style="color:red" onclick="x()">Hi <b>there</b> <i>you</i></p><a href="javascript:alert(1)">bad</a>'
                 '<a href="https://ok.com/x?a=1&b=2" class="c">ok</a><img src=x onerror=alert(1)><iframe>no</iframe><style>p{}</style>'
                 '<div><span>kept text</span></div><td rowspan="3" onmouseover="y">c</td><p></p>')
        out = blogbank.clean_html(dirty)
        for bad in ("onclick", "style", "javascript:", "<img", "iframe", "no<", "<div", "<span", "onmouseover", "<p></p>"):
            self.assertNotIn(bad, out)
        self.assertIn("<h2>Big</h2>", out)
        self.assertIn("<strong>there</strong>", out)
        self.assertIn("<em>you</em>", out)
        self.assertIn('<a href="https://ok.com/x?a=1&amp;b=2">ok</a>', out)
        self.assertIn("<a>bad</a>", out)
        self.assertIn('<td rowspan="3">c</td>', out)
        self.assertIn("kept text", out)
        self.assertEqual(blogbank.clean_html("<p>a < b & c</p>"), "<p>a &lt; b &amp; c</p>")

    def test_edit_versions_conflict_and_restore(self):
        _, docs = self.gen(["clinics"])
        d = docs[0]
        r = blogbank.update(d["_id"], d["version"], {"html": "<h2>New</h2><p>Edited body text here.</p>", "title": "New title"}, "Ann", checkpoint=True)["item"]
        self.assertEqual(r["title"], "New title")
        self.assertEqual(r["updatedBy"], "Ann")
        self.assertEqual(len(blogbank.versions(d["_id"])["items"]), 1)  # the generated text was kept
        with self.assertRaises(AuditError) as cm:
            blogbank.update(d["_id"], d["version"], {"title": "stale tab"}, "Bob")
        self.assertTrue(cm.exception.extra["conflict"])
        # autosaves within ten minutes don't add a version each time
        r2 = blogbank.update(d["_id"], r["version"], {"html": "<p>Autosave one.</p>"}, "Ann")["item"]
        r3 = blogbank.update(d["_id"], r2["version"], {"html": "<p>Autosave two.</p>"}, "Ann")["item"]
        self.assertEqual(len(blogbank.versions(d["_id"])["items"]), 1)
        v = blogbank.versions(d["_id"])["items"][0]["v"]
        self.assertIn("Why local SEO matters", blogbank.get_version(d["_id"], v)["item"]["html"])
        back = blogbank.restore(d["_id"], v, r3["version"], "Ann")["item"]
        self.assertIn("Why local SEO matters", back["html"])
        self.assertIn("Autosave two", blogbank.get_version(d["_id"], v + 1)["item"]["html"])  # restoring kept the edit too

    def test_status_used_published_url_and_validation(self):
        _, docs = self.gen(["clinics"])
        d = docs[0]
        r = blogbank.update(d["_id"], d["version"], {"status": "used", "publishedUrl": "https://acme.in/blog/x"}, "F")["item"]
        self.assertEqual((r["status"], r["publishedUrl"]), ("used", "https://acme.in/blog/x"))
        self.assertTrue(r["usedAt"])
        with self.assertRaises(AuditError):
            blogbank.update(d["_id"], r["version"], {"status": "published"}, "F")
        with self.assertRaises(AuditError):
            blogbank.update(d["_id"], r["version"], {"publishedUrl": "javascript:alert(1)"}, "F")
        r = blogbank.update(d["_id"], r["version"], {"slug": "My New Slug!!"}, "F")["item"]
        self.assertEqual(r["slug"], "my-new-slug")

    def test_cannot_edit_while_generating(self):
        blogai.write_blog = lambda b, p: time.sleep(0.5) or fake_blog(b, p)
        r = blogbank.generate({}, SETTINGS, [{"topic": "slow", "keyword": "k"}], "F")
        with self.assertRaises(AuditError):
            blogbank.update(r["items"][0]["id"], None, {"title": "x"}, "F")
        wait([r["items"][0]["id"]])

    def test_list_search_filter_sort_and_batches(self):
        _, a = self.gen(["clinics", "dentists"])
        r2 = blogbank.generate({"label": "Second weekend"}, SETTINGS, [{"topic": "bakeries", "keyword": "bakery marketing"}], "F")
        wait([i["id"] for i in r2["items"]])
        blogbank.update(a[0]["_id"], a[0]["version"], {"status": "used"}, "F")
        ls = blogbank.list_blogs({"q": "bakery"})
        self.assertEqual([i["title"] for i in ls["items"]], ["Bakeries"])
        self.assertNotIn("html", ls["items"][0])
        self.assertEqual(len(blogbank.list_blogs({"status": "used"})["items"]), 1)
        self.assertEqual(len(blogbank.list_blogs({"batch": r2["batchId"]})["items"]), 1)
        self.assertEqual([i["title"] for i in blogbank.list_blogs({"sort": "title"})["items"]], ["Bakeries", "Clinics", "Dentists"])
        self.assertEqual(sorted(b["count"] for b in ls["batches"]), [1, 2])
        self.assertEqual(len(blogbank.list_blogs({"from": "2000-01-01", "to": "2000-12-31"})["items"]), 0)

    def test_duplicate_and_delete(self):
        _, docs = self.gen(["clinics"])
        d = docs[0]
        blogbank.update(d["_id"], d["version"], {"html": "<p>v2 text</p>"}, "F", checkpoint=True)
        c = blogbank.duplicate(d["_id"], "F")["item"]
        self.assertTrue(c["title"].endswith("(copy)"))
        self.assertEqual((c["status"], c["version"]), ("in_progress", 1))
        blogbank.delete(d["_id"])
        self.assertEqual(self.vers.count_documents({"blogId": d["_id"]}), 0)
        with self.assertRaises(AuditError):
            blogbank.get_blog(d["_id"])
        self.assertTrue(blogbank.get_blog(c["id"]))

    def test_stale_jobs_become_retryable(self):
        d = {"_id": "s1", "ws": "team", "gen": "running", "genAt": "2020-01-01T00:00:00Z", "version": 2, "html": "", "title": "t",
             "brief": {}, "createdAt": "2020-01-01T00:00:00Z", "batchId": "b", "batch": "B"}
        self.blogs.insert_one(d)
        self.assertEqual(blogbank.get_blog("s1")["item"]["gen"], "failed")

    def test_rewrite_section_is_cleaned(self):
        _, docs = self.gen(["clinics"])
        out = blogbank.rewrite_section(docs[0]["_id"], "<h2>Why local SEO matters</h2><p>Some text of the section.</p>", "shorter", "F")["html"]
        self.assertTrue(out.startswith("<h2>Better heading</h2>"))
        self.assertNotIn("<img", out)


class FakeStream:
    def __init__(self, msg, seen, kw):
        self.msg, self.seen, self.kw = msg, seen, kw

    def __enter__(self):
        self.seen.update(self.kw)
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class BlogAI(unittest.TestCase):
    def call(self, msg):
        seen = {}
        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: FakeStream(msg, seen, kw))))
        orig = blogai._anthropic
        blogai._anthropic = lambda: client
        try:
            return blogai._call("sys", "prompt", blogai.BLOG_SCHEMA, 20000), seen
        finally:
            blogai._anthropic = orig

    def test_request_shape_and_parsing(self):
        out, seen = self.call(SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps({"a": 1}))]))
        self.assertEqual(out, {"a": 1})
        self.assertEqual(seen["model"], blogai.MODEL)
        self.assertEqual(seen["fallbacks"], "default")
        self.assertEqual(seen["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(seen["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("thinking", seen)
        self.assertNotIn("temperature", seen)

    def test_refusal_truncation_and_bad_json_are_errors(self):
        for stop, content in (("refusal", []), ("max_tokens", [SimpleNamespace(type="text", text="{")]), ("end_turn", [SimpleNamespace(type="text", text="not json")])):
            with self.assertRaises(AuditError):
                self.call(SimpleNamespace(stop_reason=stop, content=content))

    def test_schema_is_strict(self):
        def walk(s):
            if s.get("type") == "object":
                self.assertFalse(s["additionalProperties"])
                self.assertEqual(set(s["required"]), set(s["properties"]))
                for v in s["properties"].values():
                    walk(v)
            if s.get("type") == "array":
                walk(s["items"])
        walk(blogai.BLOG_SCHEMA)
        walk(blogai.SECTION_SCHEMA)


if __name__ == "__main__":
    unittest.main()
