from django.test import TestCase


class SiteServingTests(TestCase):
    def test_homepage_and_feature_pages(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<title>", b"".join(r.streaming_content))
        self.assertEqual(self.client.get("/website-seo-audit/").status_code, 200)
        self.assertEqual(self.client.get("/assets/site.css").status_code, 200)
        self.assertEqual(self.client.get("/robots.txt").status_code, 200)

    def test_folder_without_slash_redirects(self):
        r = self.client.get("/website-seo-audit")
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], "/website-seo-audit/")

    def test_code_config_and_data_are_never_served(self):
        for path in ["/manage.py", "/config/settings.py", "/seo/engine.py", "/.env", "/.env.example", "/.git/config",
                     "/db.sqlite3", "/requirements.txt", "/README.md", "/tools/build-pages.mjs", "/../manage.py",
                     "/accounts/migrations/0001_initial.py"]:
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_missing_page_uses_404_page(self):
        r = self.client.get("/no-such-page.html")
        self.assertEqual(r.status_code, 404)
        self.assertIn("text/html", r["Content-Type"])

    def test_unknown_api_endpoint_is_json(self):
        r = self.client.get("/api/no-such-thing")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.json())


class AppPageTests(TestCase):
    def setUp(self):
        from accounts.models import User
        self.user = User.objects.create_user("ana@example.com", "a-long-test-passphrase", name="Ana")

    def boot(self, path):
        import json
        import re
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")
        m = re.search(r'<script id="scoutly-boot" type="application/json">(.*?)</script>', r.content.decode())
        return json.loads(m.group(1))

    def test_app_pages_need_login(self):
        r = self.client.get("/dashboard.html")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/login.html?mode=login&next=%2Fdashboard.html")
        self.assertEqual(self.client.get("/").status_code, 200)  # marketing pages stay public
        self.assertEqual(self.client.get("/login.html").status_code, 200)

    def test_app_page_embeds_the_users_data(self):
        from workspace import store
        store.put(self.user, "scoutly.site", "example.com")
        store.put(self.user, "scoutly.ranks", {"snaps": [{"date": "2026-10-01", "queries": [], "pages": None}], "tracked": {}})
        self.client.force_login(self.user)
        b = self.boot("/dashboard.html")
        self.assertEqual(b["user"]["email"], "ana@example.com")
        self.assertEqual(b["data"]["scoutly.site"], "example.com")
        self.assertNotIn("scoutly.ranks", b["data"])
        self.assertEqual(b["withheld"], ["scoutly.ranks"])
        b = self.boot("/position-tracking.html")
        self.assertEqual(len(b["data"]["scoutly.ranks"]["snaps"]), 1)
        self.assertEqual(b["withheld"], [])

    def test_embedded_data_cannot_break_out_of_the_script_tag(self):
        from workspace import store
        store.put(self.user, "scoutly.draft", {"ti": "</script><script>alert(1)</script>"})
        self.client.force_login(self.user)
        html = self.client.get("/writing-assistant.html").content.decode()
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertEqual(self.boot("/writing-assistant.html")["data"]["scoutly.draft"]["ti"], "</script><script>alert(1)</script>")

    def test_login_page_sends_logged_in_users_on(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/login.html")["Location"], "/dashboard.html")
        self.assertEqual(self.client.get("/login.html?site=example.com")["Location"], "/audit.html?url=example.com")
        self.assertEqual(self.client.get("/login.html?next=/campaigns.html")["Location"], "/campaigns.html")
        self.assertEqual(self.client.get("/login.html?next=//evil.example")["Location"], "/dashboard.html")
