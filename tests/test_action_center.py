"""Action Center tests: run with  python -m unittest discover tests  (no MongoDB needed; uses memcoll)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seo"))
import engine  # noqa: E402

sys.modules["server"] = engine
import actions  # noqa: E402
import taskstore  # noqa: E402
from server import AuditError  # noqa: E402

AUDIT = {"finalUrl": "https://example.com/", "fetchedAt": "2026-10-24T00:00:00+00:00", "checks": [
    {"cat": "onpage", "id": "title", "status": "fail", "sev": "critical", "title": "Missing page title", "detected": "No title.", "items": [], "count": 0},
    {"cat": "onpage", "id": "desc", "status": "warn", "sev": "warning", "title": "Missing meta description", "detected": "None.", "items": [], "count": 0},
    {"cat": "crawl", "id": "noindex", "status": "fail", "sev": "critical", "title": "Page is set to noindex", "detected": "meta robots", "items": [], "count": 0},
    {"cat": "onpage", "id": "h1", "status": "pass", "sev": None, "title": "One H1", "detected": "", "items": [], "count": 0},
]}


def scan(audit=AUDIT):
    return taskstore.ingest("example.com", actions.from_audit("example.com", audit), "site-audit", "Test", audit["finalUrl"],
                            evaluated=actions.audit_checked_types(audit), scopes={audit["finalUrl"], "example.com"})["counts"]


class ActionCenter(unittest.TestCase):
    def setUp(self):
        self.tasks, _ = taskstore.use_memory()

    def test_scan_creates_tasks_with_sensible_priorities(self):
        self.assertEqual(scan()["new"], 3)
        by = {d["type"]: d for d in self.tasks.find({})}
        self.assertEqual(by["noindex"]["priority"], "critical")
        self.assertEqual(by["desc"]["priority"], "medium")  # a missing description is never critical
        self.assertTrue(by["title"]["priorityReason"])

    def test_repeated_scans_do_not_duplicate(self):
        scan()
        c = scan()
        self.assertEqual((c["new"], c["updated"]), (0, 3))
        self.assertEqual(self.tasks.count_documents({}), 3)

    def test_fixed_issue_is_not_detected_then_reopened(self):
        scan()
        t = self.tasks.find_one({"type": "title"})
        taskstore.update(t["_id"], {"status": "resolved"}, t["version"], "Test")
        fixed = {**AUDIT, "checks": [c for c in AUDIT["checks"] if c["id"] != "title"] + [{"cat": "onpage", "id": "title", "status": "pass"}]}
        self.assertEqual(scan(fixed)["verified"], 1)
        self.assertTrue(self.tasks.find_one({"type": "title"})["verifiedAt"])
        self.assertEqual(scan()["reopened"], 1)
        self.assertEqual(self.tasks.find_one({"type": "title"})["status"], "open")

    def test_dismissed_issue_stays_dismissed(self):
        scan()
        t = self.tasks.find_one({"type": "desc"})
        with self.assertRaises(AuditError):  # needs a reason
            taskstore.update(t["_id"], {"status": "dismissed"}, t["version"], "Test")
        taskstore.update(t["_id"], {"status": "dismissed", "dismissReason": "false positive"}, t["version"], "Test")
        scan()
        self.assertEqual(self.tasks.find_one({"type": "desc"})["status"], "dismissed")

    def test_status_transitions(self):
        t = taskstore.create({"project": "example.com", "title": "Fix footer links"}, "Test")["item"]
        r = taskstore.update(t["id"], {"status": "resolved"}, t["version"], "Test")["item"]
        with self.assertRaises(AuditError):  # resolved must be reopened first
            taskstore.update(t["id"], {"status": "in_progress"}, r["version"], "Test")
        r = taskstore.update(t["id"], {"status": "open"}, r["version"], "Test")["item"]
        self.assertEqual([h["action"] for h in r["history"]], ["created", "status", "status"])

    def test_stale_version_is_refused(self):
        t = taskstore.create({"project": "example.com", "title": "A"}, "Test")["item"]
        taskstore.update(t["id"], {"title": "B"}, t["version"], "One")
        with self.assertRaises(AuditError):
            taskstore.update(t["id"], {"title": "C"}, t["version"], "Two")

    def test_create_validates(self):
        with self.assertRaises(AuditError):
            taskstore.create({"project": "example.com", "title": ""}, "Test")
        with self.assertRaises(AuditError):
            taskstore.create({"project": "not a site", "title": "x"}, "Test")

    def test_list_filters_summary_and_bulk(self):
        scan()
        self.assertEqual(taskstore.list_tasks({"project": "example.com", "view": "critical"})["total"], 1)
        self.assertEqual(taskstore.list_tasks({"project": "example.com", "q": "description"})["total"], 1)
        self.assertEqual(taskstore.list_tasks({"project": "other.com"})["total"], 0)  # projects don't leak
        ids = [d["_id"] for d in self.tasks.find({})]
        self.assertEqual(taskstore.bulk(ids, {"assignee": "Sam"}, "Test")["updated"], 3)
        s = taskstore.summary({"project": "example.com"})
        self.assertEqual((s["open"], s["critical"], s["assignees"]), (3, 1, ["Sam"]))

    def test_server_refuses_tasks_without_team_key(self):
        handler = engine.Handler.__new__(engine.Handler)
        sent = {}
        handler._json = lambda code, obj: sent.update(code=code, obj=obj)
        handler.headers = {}
        old = engine.TEAM_KEY, os.environ.get("SCOUTLY_DEV_MEMORY_DB")
        engine.TEAM_KEY, os.environ["SCOUTLY_DEV_MEMORY_DB"] = "secret", "1"
        try:
            self.assertTrue(handler._refused("/api/tasks"))
            self.assertEqual(sent["code"], 401)
        finally:
            engine.TEAM_KEY = old[0]
            if old[1] is None:
                os.environ.pop("SCOUTLY_DEV_MEMORY_DB")


if __name__ == "__main__":
    unittest.main()
