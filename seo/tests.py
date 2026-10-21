import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import User
from workspace.models import Audit

from .engine import AuditError

RESULT = {
    "url": "example.com", "finalUrl": "https://www.example.com/", "keyword": None, "crawl": [{}, {}],
    "links": {"checked": 3, "broken": [{"url": "https://www.example.com/x"}], "unverified": []},
    "checks": [
        {"cat": "crawl", "status": "fail", "sev": "critical", "weight": 3, "count": 1},
        {"cat": "onpage", "status": "warn", "sev": "warning", "weight": 2, "count": 4},
        {"cat": "onpage", "status": "pass", "sev": None, "weight": 2, "count": 0},
        {"cat": "ai", "status": "info", "sev": None, "weight": 1, "count": 0},
    ],
}


class AuditApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_requires_url(self):
        self.assertEqual(self.client.get("/api/audit").status_code, 400)

    @mock.patch("seo.views.engine.run_audit", return_value=dict(RESULT))
    def test_anonymous_audit_is_not_saved(self, run):
        r = self.client.get("/api/audit", {"url": "example.com", "kw": "seo"})
        self.assertEqual(r.status_code, 200)
        run.assert_called_once_with("example.com", "seo")
        self.assertNotIn("auditId", r.json())
        self.assertFalse(Audit.objects.exists())

    @mock.patch("seo.views.engine.run_audit", return_value=dict(RESULT))
    def test_logged_in_audit_is_saved_with_summary(self, run):
        user = User.objects.create_user("ana@example.com", "a-long-test-passphrase", name="Ana")
        self.client.force_login(user)
        data = self.client.get("/api/audit", {"url": "example.com"}).json()
        a = Audit.objects.get(pk=data["auditId"])
        self.assertEqual((a.critical, a.warning, a.passed, a.pages, a.broken_links), (1, 1, 1, 2, 1))
        self.assertEqual(a.site.host, "example.com")
        # crawl 55, onpage 100-22*1.3=71 → (25*55+20*71)/45 = 62.1, minus 10 for the critical issue
        self.assertEqual(a.score, 52)

    @mock.patch("seo.views.engine.run_audit", side_effect=AuditError("Couldn't find the website"))
    def test_expected_failure_is_200_with_error(self, run):
        r = self.client.get("/api/audit", {"url": "nope.invalid"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["error"], "Couldn't find the website")

    @mock.patch("seo.views.engine.run_audit", side_effect=RuntimeError("boom"))
    def test_unexpected_failure_is_500(self, run):
        r = self.client.get("/api/audit", {"url": "example.com"})
        self.assertEqual(r.status_code, 500)
        self.assertIn("error", r.json())

    @override_settings(SCOUTLY_RATE_LIMITS={"audit": (2, 2), "tool": (300, 300), "signup": (10, 10)})
    @mock.patch("seo.views.engine.run_audit", return_value=dict(RESULT))
    def test_rate_limited(self, run):
        for _ in range(2):
            self.assertEqual(self.client.get("/api/audit", {"url": "example.com"}).status_code, 200)
        self.assertEqual(self.client.get("/api/audit", {"url": "example.com"}).status_code, 429)


class ToolApiTests(TestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("seo.views.tools.keyword_ideas", return_value={"items": [{"kw": "seo audit"}]})
    def test_keywords(self, ideas):
        r = self.client.get("/api/keywords", {"q": "seo", "gl": "in", "deep": "0"})
        self.assertEqual(r.json(), {"items": [{"kw": "seo audit"}]})
        ideas.assert_called_once_with("seo", "en", "in", False)

    def test_intent_runs_locally(self):
        items = self.client.get("/api/intent", {"q": "how to fix 404\nbuy running shoes"}).json()["items"]
        self.assertEqual([i["kw"] for i in items], ["how to fix 404", "buy running shoes"])

    @mock.patch("seo.views.tools.backlink_audit", return_value={"items": []})
    def test_post_tool_needs_no_csrf_token(self, audit):
        from django.test import Client
        r = Client(enforce_csrf_checks=True).post("/api/backlinks", data=json.dumps({"target": "a.com", "sources": ["b.com"]}),
                                                  content_type="application/json")
        self.assertEqual(r.status_code, 200)
        audit.assert_called_once_with("a.com", ["b.com"])

    def test_wrong_method(self):
        self.assertEqual(self.client.post("/api/keywords").status_code, 405)

    @override_settings(SCOUTLY_PAID_REQUIRES_LOGIN=True)
    @mock.patch("seo.views.provider.serp", return_value={"items": []})
    def test_paid_lookups_need_login(self, serp):
        r = self.client.get("/api/provider/serp", {"q": "seo", "run": "1"})
        self.assertEqual(r.status_code, 401)
        serp.assert_not_called()
        self.client.get("/api/provider/serp", {"q": "seo"})  # cache-only answer is fine
        serp.assert_called_once_with("seo", "us", False)

    @override_settings(DEBUG=False)
    def test_clear_cache_needs_staff_in_production(self):
        self.assertEqual(self.client.post("/api/provider/clear-cache").status_code, 403)

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").json(), {"ok": True, "database": True})
