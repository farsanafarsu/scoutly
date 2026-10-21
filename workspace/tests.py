import json

from django.test import TestCase

from accounts.models import User

from .models import Audit, Site


class WorkspaceTests(TestCase):
    def setUp(self):
        self.ana = User.objects.create_user("ana@example.com", "a-long-test-passphrase", name="Ana")
        self.ben = User.objects.create_user("ben@example.com", "a-long-test-passphrase", name="Ben")
        self.client.force_login(self.ana)

    def send(self, method, path, data=None):
        return getattr(self.client, method)(path, data=json.dumps(data or {}), content_type="application/json")

    def test_login_required(self):
        self.client.logout()
        for path in ["/api/sites", "/api/audits", "/api/keyword-lists", "/api/rank-snapshots", "/api/campaigns",
                     "/api/preferences", "/api/state", "/api/export"]:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 401, path)
            self.assertTrue(r.json()["auth"])

    def test_sites(self):
        r = self.send("post", "/api/sites", {"url": "www.Example.com/shop"})
        self.assertEqual(r.status_code, 201)
        site = r.json()["site"]
        self.assertEqual((site["host"], site["url"]), ("example.com", "https://www.Example.com/shop"))
        self.assertEqual(self.send("post", "/api/sites", {"url": "https://example.com"}).status_code, 200)  # same host
        self.assertEqual(self.send("post", "/api/sites", {"url": "not a url"}).status_code, 400)
        self.assertEqual(self.send("patch", f"/api/sites/{site['id']}", {"name": "Shop"}).json()["site"]["name"], "Shop")
        self.assertEqual(self.send("patch", f"/api/sites/{site['id']}", {"url": "other.com"}).status_code, 400)
        self.assertEqual(len(self.client.get("/api/sites").json()["items"]), 1)
        self.assertEqual(self.send("delete", f"/api/sites/{site['id']}").status_code, 200)

    def test_users_cannot_see_each_others_data(self):
        site = Site.objects.create(user=self.ben, host="ben.com", url="https://ben.com")
        audit = Audit.objects.create(user=self.ben, site=site, url="https://ben.com", final_url="https://ben.com", result={})
        self.assertEqual(self.client.get(f"/api/sites/{site.pk}").status_code, 404)
        self.assertEqual(self.client.get("/api/sites/123").status_code, 404)  # not an ObjectId
        self.assertEqual(self.client.get(f"/api/audits/{audit.pk}").status_code, 404)
        self.assertEqual(self.send("delete", f"/api/audits/{audit.pk}").status_code, 404)
        self.assertEqual(self.client.get("/api/audits").json()["items"], [])
        self.assertEqual(self.client.get("/api/audits", {"site": site.pk}).json()["items"], [])
        self.assertTrue(Audit.objects.filter(pk=audit.pk).exists())

    def test_audits_list_and_detail(self):
        site = Site.objects.create(user=self.ana, host="ana.com", url="https://ana.com")
        a = Audit.objects.create(user=self.ana, site=site, url="ana.com", final_url="https://ana.com/", score=88,
                                 result={"checks": []})
        listed = self.client.get("/api/audits").json()["items"][0]
        self.assertEqual((listed["id"], listed["score"]), (str(a.pk), 88))
        self.assertNotIn("result", listed)
        self.assertEqual(self.client.get(f"/api/audits/{a.pk}").json()["audit"]["result"], {"checks": []})
        self.assertEqual(self.client.get("/api/sites").json()["items"][0]["latestAudit"]["id"], str(a.pk))

    def test_keyword_lists(self):
        kl = self.send("post", "/api/keyword-lists", {"name": "Blog ideas"}).json()["list"]
        self.assertEqual(self.send("post", "/api/keyword-lists", {"name": "Blog ideas"}).status_code, 409)
        path = f"/api/keyword-lists/{kl['id']}/keywords"
        r = self.send("post", path, {"items": [{"kw": "SEO  Audit", "volume": 880}, {"kw": "seo audit"}, "broken links", 5]})
        self.assertEqual(r.json()["added"], 2)
        self.assertEqual(self.send("post", path, {"items": ["broken links"]}).json()["added"], 0)
        items = self.client.get(f"/api/keyword-lists/{kl['id']}").json()["list"]["items"]
        self.assertEqual([(i["kw"], i.get("volume")) for i in items], [("seo audit", 880), ("broken links", None)])
        self.assertEqual(self.send("delete", path, {"kws": ["Broken Links"]}).json()["removed"], 1)
        self.assertEqual(self.client.get("/api/keyword-lists").json()["items"][0]["count"], 1)

    def test_rank_snapshots_upsert_by_date(self):
        rows = [{"key": "seo audit", "pos": 4.2, "clicks": 10}]
        self.assertEqual(self.send("post", "/api/rank-snapshots", {"date": "2026-10-01", "queries": rows}).status_code, 201)
        self.assertEqual(self.send("post", "/api/rank-snapshots", {"date": "2026-10-01", "pages": rows}).status_code, 200)
        snaps = self.client.get("/api/rank-snapshots").json()["items"]
        self.assertEqual(len(snaps), 1)
        self.assertEqual((snaps[0]["queries"], snaps[0]["pages"]), (None, rows))
        self.assertEqual(self.send("post", "/api/rank-snapshots", {"date": "2026-10-02"}).status_code, 400)
        self.assertEqual(self.send("delete", "/api/rank-snapshots/2026-10-01").status_code, 200)
        self.assertEqual(self.send("delete", "/api/rank-snapshots/2026-10-01").status_code, 404)

    def test_campaigns(self):
        bad = self.send("post", "/api/campaigns", {"name": "web design london"})
        self.assertEqual((bad.status_code, bad.json()["field"]), (400, "brand"))
        c = self.send("post", "/api/campaigns", {"name": "web design london", "brand": "Acme", "loc": "London",
                                                 "url": "acme.com/web-design", "end": "2026-12-31", "status": "paused"}).json()["campaign"]
        self.assertEqual((c["status"], c["url"], c["end"]), ("Paused", "https://acme.com/web-design", "2026-12-31"))
        c = self.send("patch", f"/api/campaigns/{c['id']}", {"status": "Active", "backlinks": 12}).json()["campaign"]
        self.assertEqual((c["status"], c["backlinks"]), ("Active", 12))
        self.assertEqual(self.send("patch", f"/api/campaigns/{c['id']}", {"status": "Done"}).status_code, 400)

    def test_preferences_and_state(self):
        self.assertEqual(self.client.get("/api/preferences").json()["preferences"], {})
        self.assertEqual(self.send("put", "/api/preferences", {"gl": "in"}).json()["preferences"], {"gl": "in"})
        self.assertIsNone(self.client.get("/api/state/scoutly.draft").json()["data"])
        self.send("put", "/api/state/scoutly.draft", {"data": {"ti": "Title", "md": "Meta"}})
        self.assertEqual(self.client.get("/api/state/scoutly.draft").json()["data"]["ti"], "Title")
        self.assertEqual(self.client.get("/api/state").json()["items"][0]["key"], "scoutly.draft")
        self.assertEqual(self.client.get("/api/state/bad key!").status_code, 404)
        self.assertEqual(self.send("put", "/api/state/big", {"data": "x" * 600_000}).status_code, 413)

    def test_export(self):
        self.send("post", "/api/sites", {"url": "ana.com"})
        self.send("put", "/api/state/scoutly.local", {"data": {"name": "Ana's"}})
        data = self.client.get("/api/export").json()
        self.assertEqual(data["user"]["email"], "ana@example.com")
        self.assertEqual(data["sites"][0]["host"], "ana.com")
        self.assertEqual(data["state"], {"scoutly.local": {"name": "Ana's"}})


class PageStoreTests(TestCase):
    """The page store keeps each page's data in its own shape, backed by the real tables."""

    def setUp(self):
        self.ana = User.objects.create_user("ana@example.com", "a-long-test-passphrase", name="Ana")
        self.client.force_login(self.ana)

    def put(self, key, data):
        return self.client.put(f"/api/store/{key}", data=json.dumps({"data": data}), content_type="application/json")

    def get(self, key):
        return self.client.get(f"/api/store/{key}").json()["data"]

    def test_campaigns_keep_the_pages_ids(self):
        from .models import Campaign
        c = {"id": "lx1", "name": "web design london", "brand": "Acme", "loc": "London", "url": "acme.com", "end": "", "status": "Active", "backlinks": 0}
        self.assertEqual(self.put("scoutly.campaigns", [c]).status_code, 200)
        self.put("scoutly.campaigns", [dict(c, status="Paused"), dict(c, id="lx2", name="seo agency")])
        self.assertEqual(Campaign.objects.count(), 2)  # the first one was updated, not duplicated
        got = self.get("scoutly.campaigns")
        self.assertEqual({(x["id"], x["status"]) for x in got}, {("lx1", "Paused"), ("lx2", "Active")})
        self.assertEqual(got[0]["owner"], "Ana")
        self.put("scoutly.campaigns", [dict(c, id="lx2", name="seo agency")])
        self.assertEqual(list(Campaign.objects.values_list("client_id", flat=True)), ["lx2"])

    def test_keyword_lists_sync(self):
        from .models import Keyword, KeywordList
        self.put("scoutly.kwlists", {"Blog": {"created": "x", "items": [{"kw": "seo audit", "intent": "I"}, {"kw": "404 errors"}]},
                                     "Shop": {"items": [{"kw": "buy shoes"}]}})
        self.put("scoutly.kwlists", {"Blog": {"items": [{"kw": "seo audit", "intent": "C"}, {"kw": "redirects"}]}})
        self.assertEqual(list(KeywordList.objects.values_list("name", flat=True)), ["Blog"])
        self.assertEqual(list(Keyword.objects.values_list("kw", flat=True)), ["seo audit", "redirects"])
        self.assertEqual(self.get("scoutly.kwlists")["Blog"]["items"][0], {"kw": "seo audit", "intent": "C"})

    def test_ranks_sync(self):
        rows = [{"key": "seo", "pos": 3}]
        self.put("scoutly.ranks", {"snaps": [{"date": "2026-09-01", "queries": rows, "pages": None},
                                             {"date": "2026-10-01", "queries": rows, "pages": None}], "tracked": {"queries": {"seo": 1}}})
        self.put("scoutly.ranks", {"snaps": [{"date": "2026-10-01", "queries": rows, "pages": rows}], "tracked": {"queries": {"seo": 1}}})
        got = self.get("scoutly.ranks")
        self.assertEqual([s["date"] for s in got["snaps"]], ["2026-10-01"])
        self.assertEqual(got["snaps"][0]["pages"], rows)
        self.assertEqual(got["tracked"], {"queries": {"seo": 1}})
        self.assertEqual(self.put("scoutly.ranks", {"oops": 1}).status_code, 400)

    def test_site_and_prefs_and_generic_state(self):
        self.put("scoutly.site", "example.com")
        self.assertEqual(self.get("scoutly.site"), "example.com")
        self.assertTrue(Site.objects.filter(user=self.ana, host="example.com").exists())
        self.put("scoutly.prefs", {"gl": "in"})
        self.assertEqual(self.client.get("/api/preferences").json()["preferences"], {"gl": "in"})
        self.put("scoutly.draft", {"ti": "Title"})
        self.assertEqual(self.get("scoutly.draft"), {"ti": "Title"})
        self.put("scoutly.draft", None)
        self.assertIsNone(self.get("scoutly.draft"))
        data = self.client.get("/api/store", {"keys": "scoutly.site,scoutly.prefs"}).json()["data"]
        self.assertEqual(data, {"scoutly.site": "example.com", "scoutly.prefs": {"gl": "in"}})

    def test_bad_keys_and_sizes(self):
        self.assertEqual(self.put("notscoutly", 1).status_code, 404)
        self.assertEqual(self.put("scoutly.ranks.tracked", {}).status_code, 404)
        self.assertEqual(self.put("scoutly.big", "x" * 600_000).status_code, 413)

    def test_clear_keeps_the_account(self):
        self.put("scoutly.site", "example.com")
        self.put("scoutly.kwlists", {"Blog": {"items": [{"kw": "seo"}]}})
        self.assertEqual(self.client.delete("/api/store").status_code, 200)
        self.assertEqual(self.client.get("/api/store").json()["data"]["scoutly.kwlists"], None)
        self.assertFalse(Site.objects.exists())
        self.assertTrue(User.objects.filter(pk=self.ana.pk).exists())

    def test_needs_login(self):
        self.client.logout()
        self.assertEqual(self.put("scoutly.site", "example.com").status_code, 401)
