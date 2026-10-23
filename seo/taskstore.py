"""SEO Action Center tasks, stored in MongoDB next to the backlinks and campaigns (linkstore.py).

Same switch as the rest of the team data: MONGODB_URI turns it on (SCOUTLY_DEV_MEMORY_DB=1 keeps tasks in
memory instead, for trying it locally; they're gone when the server stops). There is no browser fallback:
the database is the only copy of the tasks.

Collections
  tasks       one document per issue or manual task. Notes and the activity history live inside it, so a
              task and its history always change together in one atomic write. A unique index on
              (project, fp) means repeated scans update the same task instead of creating duplicates.
  task_scans  one document per scan or import (when, what, by whom, what it found), for "last scan".

Indexes are created on first use (MongoDB needs no migrations): see _collections().

Every write checks the task's version, so two teammates editing the same task can't silently overwrite each
other: the second save is refused and asks for a reload.
"""
import hashlib
import os
import re
import threading
from datetime import datetime, timedelta, timezone

from server import AuditError
import actions
import linkstore

PRIORITIES = ["critical", "high", "medium", "low"]
RANK = {p: i for i, p in enumerate(PRIORITIES)}
STATUSES = {"open": "Open", "in_progress": "In Progress", "blocked": "Blocked", "resolved": "Resolved", "dismissed": "Dismissed"}
ACTIVE = ["open", "in_progress", "blocked"]
TRANSITIONS = {
    "open": {"in_progress", "blocked", "resolved", "dismissed"},
    "in_progress": {"open", "blocked", "resolved", "dismissed"},
    "blocked": {"open", "in_progress", "resolved", "dismissed"},
    "resolved": {"open"},
    "dismissed": {"open"},
}
VIEWS = {"all", "mine", "critical", "high", "overdue", "recent", "in_progress", "resolved"}
SORTS = {
    "priority": [("priorityRank", 1), ("lastDetected", -1)],
    "detected": [("lastDetected", -1)],
    "updated": [("updated", -1)],
    "due": [("dueSort", 1), ("priorityRank", 1)],
}
MAX_URLS = 500
MAX_NOTES = 200
MAX_HISTORY = 300
MAX_PAGE = 100
MAX_EXPORT = 5000
LIST_FIELDS = ["project", "title", "category", "source", "kind", "priority", "priorityRank", "status", "urls", "urlCount",
               "due", "assignee", "lastDetected", "firstDetected", "updated", "created", "lastScan", "version", "type"]

_lock = threading.Lock()
_tasks = None
_scans = None


def use_memory():
    """Keep everything in memory (tests, or SCOUTLY_DEV_MEMORY_DB=1). Returns the two collections."""
    global _tasks, _scans
    import memcoll
    with _lock:
        _tasks, _scans = memcoll.MemoryCollection("tasks"), memcoll.MemoryCollection("task_scans")
        _index(_tasks, _scans)
    return _tasks, _scans


def _index(tasks, scans):
    tasks.create_index([("project", 1), ("fp", 1)], unique=True, name="project_fingerprint_unique")
    tasks.create_index([("project", 1), ("status", 1), ("priorityRank", 1)], name="project_status_priority")
    tasks.create_index([("project", 1), ("lastDetected", -1)], name="project_detected")
    tasks.create_index([("project", 1), ("updated", -1)], name="project_updated")
    tasks.create_index([("project", 1), ("dueSort", 1)], name="project_due")
    tasks.create_index([("project", 1), ("assignee", 1)], name="project_assignee")
    scans.create_index([("project", 1), ("at", -1)], name="project_at")


def configured():
    return bool(linkstore.URI) or os.environ.get("SCOUTLY_DEV_MEMORY_DB") == "1"


def _collections():
    global _tasks, _scans
    if _tasks is not None:
        return _tasks, _scans
    if os.environ.get("SCOUTLY_DEV_MEMORY_DB") == "1":
        return use_memory()
    with _lock:
        if _tasks is None:
            try:
                db = linkstore._collection().database
            except linkstore.NotConfigured:
                raise linkstore.NotConfigured("The team database isn't set up on the server yet. The Action Center keeps tasks only in the database, so it needs MONGODB_URI.")
            t, s = db["tasks"], db["task_scans"]
            try:
                _index(t, s)
            except Exception as e:
                raise linkstore._db_error(e)
            _tasks, _scans = t, s
    return _tasks, _scans


def _db(fn):
    """Run a database call, turning driver errors into a readable message."""
    try:
        return fn()
    except AuditError:
        raise
    except Exception as e:
        if e.__class__.__name__ == "DuplicateKeyError":
            raise
        raise linkstore._db_error(e)


# ------------------------------------------------------------------ cleaning

_clip = linkstore._clip
now_iso = actions.now_iso
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def project_id(raw):
    """A project is a website, identified by its host name without www."""
    p = actions.host_of(_clip(raw, 300))
    if not HOST.match(p):
        raise AuditError("Pick a website (for example example.com) for the Action Center.")
    return p


def fingerprint(project, source, issue_type, scope):
    """Stable id for one issue: same project, source, issue type and affected URL/scope → same task."""
    scope = re.sub(r"[/#?]+$", "", re.sub(r"^https?://(www\.)?", "", str(scope or "").strip(), flags=re.I)).lower()
    return hashlib.sha1("|".join([project, source, issue_type, scope]).encode()).hexdigest()[:24]


def _date(v):
    v = _clip(v, 10)
    if not v:
        return ""
    if not DATE.match(v):
        raise AuditError("Use a date like 2026-12-31.")
    return v


def _today(v):
    v = _clip(v, 10)
    return v if DATE.match(v) else datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _urls(v):
    out = []
    for u in (v or [])[:MAX_URLS] if isinstance(v, list) else []:
        u = _clip(u, 2000)
        if re.match(r"^https?://[\w-]+(\.[\w-]+)+", u, re.I) and u not in out:
            out.append(u)
    return out


def _actor(v):
    return _clip(v, 120) or "Someone"


def _hist(doc, by, action, text=""):
    doc.setdefault("history", []).append({"at": now_iso(), "by": by, "action": action, "text": _clip(text, 500)})
    doc["history"] = doc["history"][-MAX_HISTORY:]


def _out(d, full=True):
    d = dict(d)
    d["id"] = d.pop("_id")
    d.pop("fp", None)
    d.pop("dueSort", None)
    if not full:
        d["urls"] = (d.get("urls") or [])[:3]
    return d


def _new_id():
    return os.urandom(8).hex()


# ------------------------------------------------------------------ reading

def _filter(project, p):
    f = {"project": project}
    today = _today(p.get("today"))
    view = p.get("view") or "all"
    if view not in VIEWS:
        raise AuditError("Unknown view.")
    if view == "mine":
        f["assignee"] = _clip(p.get("me"), 120) or "\0"
    elif view in ("critical", "high"):
        f.update(priority=view, status={"$in": ACTIVE})
    elif view == "overdue":
        f.update(status={"$in": ACTIVE}, due={"$lt": today, "$gt": ""})
    elif view == "recent":
        f["firstDetected"] = {"$gte": (datetime.fromisoformat(today) - timedelta(days=7)).strftime("%Y-%m-%d")}
    elif view in ("in_progress", "resolved"):
        f["status"] = view
    st = p.get("status") or ""  # an explicit status filter overrides the view's
    if st == "active":
        f["status"] = {"$in": ACTIVE}
    elif st in STATUSES:
        f["status"] = st
    for key, allowed in (("priority", PRIORITIES), ("category", actions.CATEGORIES), ("source", actions.SOURCES)):
        v = p.get(key) or ""
        if v and v in allowed:
            f[key] = v
    if "assignee" in p and p.get("assignee") is not None and view != "mine":
        a = _clip(p.get("assignee"), 120)
        if a == "-":
            f["assignee"] = ""
        elif a:
            f["assignee"] = a
    due = p.get("due") or ""
    if due == "overdue":
        f["due"] = {"$lt": today, "$gt": ""}
        f["status"] = {"$in": ACTIVE}
    elif due == "week":
        f["due"] = {"$gte": today, "$lte": (datetime.fromisoformat(today) + timedelta(days=7)).strftime("%Y-%m-%d")}
    elif due == "none":
        f["due"] = ""
    q = _clip(p.get("q"), 200)
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        f["$or"] = [{"title": rx}, {"urls": rx}, {"description": rx}]
    return f


def list_tasks(p):
    tasks, _ = _collections()
    project = project_id(p.get("project"))
    f = _filter(project, p)
    sort = SORTS.get(p.get("sort") or "priority", SORTS["priority"])
    try:
        size = max(1, min(MAX_PAGE, int(p.get("size") or 25)))
        page = max(1, int(p.get("page") or 1))
    except ValueError:
        raise AuditError("Page and size must be numbers.")

    def go():
        total = tasks.count_documents(f)
        docs = tasks.find(f, {k: 1 for k in LIST_FIELDS}).sort(sort).skip((page - 1) * size).limit(size)
        return {"items": [_out(d, full=False) for d in docs], "total": total, "page": page, "size": size,
                "pages": max(1, -(-total // size))}
    return _db(go)


def summary(p):
    tasks, scans = _collections()
    project = project_id(p.get("project"))
    today = _today(p.get("today"))
    month = today[:8] + "01"
    week = (datetime.fromisoformat(today) - timedelta(days=7)).strftime("%Y-%m-%d")

    def n(extra):
        return tasks.count_documents({"project": project, **extra})

    def go():
        total = n({"status": {"$ne": "dismissed"}})
        resolved = n({"status": "resolved"})
        last = next(iter(scans.find({"project": project}).sort("at", -1).limit(1)), None)
        return {
            "project": project,
            "open": n({"status": {"$in": ACTIVE}}),
            "critical": n({"status": {"$in": ACTIVE}, "priority": "critical"}),
            "high": n({"status": {"$in": ACTIVE}, "priority": "high"}),
            "completed": resolved,
            "overdue": n({"status": {"$in": ACTIVE}, "due": {"$lt": today, "$gt": ""}}),
            "fixedThisMonth": n({"status": "resolved", "resolvedAt": {"$gte": month}}),
            "newThisWeek": n({"firstDetected": {"$gte": week}}),
            "resolvedThisWeek": n({"status": "resolved", "resolvedAt": {"$gte": week}}),
            "dismissed": n({"status": "dismissed"}),
            "total": total,
            "progress": round(resolved / total * 100) if total else 0,
            "assignees": sorted(a for a in tasks.distinct("assignee", {"project": project}) if a),
            "lastScan": {k: last.get(k) for k in ("at", "source", "url", "by", "counts", "ok", "error")} if last else None,
        }
    return _db(go)


def get_task(tid):
    tasks, _ = _collections()
    d = _db(lambda: tasks.find_one({"_id": _clip(tid, 40)}))
    if not d:
        raise AuditError("That task no longer exists. It may have been removed.")
    return {"item": _out(d)}


def projects():
    tasks, _ = _collections()
    ps = set(_db(lambda: tasks.distinct("project")))
    try:
        import campstore
        ps |= {actions.host_of(c.get("url") or "") for c in campstore.list_campaigns()["items"]}
    except Exception:
        pass  # campaigns are optional here
    return {"items": sorted(p for p in ps if p and HOST.match(p))}


def export(p):
    tasks, _ = _collections()
    project = project_id(p.get("project"))
    f = _filter(project, p)
    docs = _db(lambda: list(tasks.find(f).sort(SORTS.get(p.get("sort") or "priority", SORTS["priority"])).limit(MAX_EXPORT)))
    items = []
    for d in docs:
        d = _out(d)
        d.pop("history", None)
        d["notes"] = len(d.get("notes") or [])
        d.pop("evidence", None)
        items.append(d)
    return {"items": items, "truncated": len(docs) >= MAX_EXPORT}


# ------------------------------------------------------------------ writing

def _save(old, new):
    """Replace a task only if nobody changed it since it was read."""
    tasks, _ = _collections()
    new["version"] = int(old.get("version") or 0) + 1
    new["updated"] = now_iso()
    new["dueSort"] = new.get("due") or "9999-12-31"
    r = _db(lambda: tasks.replace_one({"_id": old["_id"], "version": old.get("version") or 0}, new))
    if not r.matched_count:
        e = AuditError("Someone else changed this task a moment ago. Reload it to see their changes, then try again.")
        e.extra = {"conflict": True}
        raise e
    return new


def _set_status(d, status, by, reason=""):
    cur = d.get("status")
    if status == cur:
        return
    if status not in STATUSES:
        raise AuditError("Unknown status.")
    if status not in TRANSITIONS.get(cur, set()):
        raise AuditError(f"A {STATUSES[cur].lower()} task can't move straight to {STATUSES[status].lower()}." +
                         (" Reopen it first." if cur in ("resolved", "dismissed") else ""))
    if status == "dismissed":
        reason = _clip(reason, 500)
        if not reason:
            raise AuditError("Say why you're dismissing this task, for example “false positive: the page is meant to be noindex”.")
        d["dismissReason"] = reason
    if status == "resolved":
        d["resolvedAt"] = now_iso()
        d["verifiedAt"] = ""
    if status == "open" and cur in ("resolved", "dismissed"):
        d["resolvedAt"] = ""
        d["dismissReason"] = ""
    d["status"] = status
    _hist(d, by, "status", f"{STATUSES[cur]} → {STATUSES[status]}" + (f": {reason}" if status == "dismissed" else ""))


def create(item, by):
    """A task a teammate adds by hand."""
    tasks, _ = _collections()
    item = item if isinstance(item, dict) else {}
    project = project_id(item.get("project"))
    title = _clip(item.get("title"), 200)
    if not title:
        raise AuditError("Give the task a title.")
    cat = item.get("category") if item.get("category") in actions.CATEGORIES else "other"
    prio = item.get("priority") if item.get("priority") in RANK else "medium"
    by = _actor(by)
    now = now_iso()
    tid = _new_id()
    urls = _urls(item.get("urls"))
    d = {
        "_id": tid, "project": project, "fp": fingerprint(project, "manual", tid, ""), "source": "manual", "type": "manual", "scope": "",
        "category": cat, "kind": "manual", "title": title, "description": str(item.get("description") or "")[:5000].strip(),
        "why": "", "fix": str(item.get("fix") or "")[:3000].strip(), "verify": "", "docs": "",
        "priority": prio, "priorityRank": RANK[prio], "priorityReason": f"Set by {by} when the task was created.", "priorityManual": True,
        "status": "open", "urls": urls, "urlCount": len(urls), "evidence": {}, "keywords": [_clip(k, 120) for k in (item.get("keywords") or [])[:20] if _clip(k, 120)],
        "scanRef": "", "created": now, "updated": now, "firstDetected": now[:10], "lastDetected": now, "detectCount": 0,
        "due": _date(item.get("due")), "assignee": _clip(item.get("assignee"), 120), "createdBy": by,
        "resolvedAt": "", "verifiedAt": "", "lastScan": None, "dismissReason": "", "notes": [], "history": [], "version": 1,
    }
    d["dueSort"] = d["due"] or "9999-12-31"
    _hist(d, by, "created", "Added by hand")
    _db(lambda: tasks.insert_one(d))
    return {"item": _out(d)}


EDITABLE = ("title", "description", "fix", "priority", "status", "due", "assignee", "keywords", "urls", "category")


def _apply(d, changes, by):
    """Apply edits to a task document in place, recording each one in its history."""
    if "title" in changes:
        t = _clip(changes["title"], 200)
        if not t:
            raise AuditError("A task needs a title.")
        if t != d["title"]:
            _hist(d, by, "edited", "Title changed")
            d["title"] = t
    for key, n in (("description", 5000), ("fix", 3000)):
        if key in changes:
            v = str(changes[key] or "")[:n].strip()
            if v != d.get(key):
                d[key] = v
                _hist(d, by, "edited", ("Description" if key == "description" else "Recommended fix") + " changed")
    if "category" in changes and changes["category"] in actions.CATEGORIES and changes["category"] != d["category"]:
        d["category"] = changes["category"]
        _hist(d, by, "edited", f"Category set to {actions.CATEGORIES[d['category']]}")
    if "priority" in changes:
        p = changes["priority"]
        if p not in RANK:
            raise AuditError("Priority must be critical, high, medium or low.")
        if p != d["priority"]:
            _hist(d, by, "priority", f"{d['priority'].title()} → {p.title()}")
            d.update(priority=p, priorityRank=RANK[p], priorityManual=True,
                     priorityReason=f"Set to {p} by {by}. " + ("" if d["source"] == "manual" else "Scans won't change it again."))
    if "due" in changes:
        due = _date(changes["due"])
        if due != d.get("due", ""):
            d["due"] = due
            _hist(d, by, "due", f"Due {due}" if due else "Due date removed")
    if "assignee" in changes:
        a = _clip(changes["assignee"], 120)
        if a != d.get("assignee", ""):
            _hist(d, by, "assigned", f"Assigned to {a}" if a else "Unassigned")
            d["assignee"] = a
    if "keywords" in changes and isinstance(changes["keywords"], list):
        d["keywords"] = [_clip(k, 120) for k in changes["keywords"][:20] if _clip(k, 120)]
    if "urls" in changes and d["source"] == "manual":
        d["urls"] = _urls(changes["urls"])
        d["urlCount"] = len(d["urls"])
    if "status" in changes:
        _set_status(d, changes["status"], by, changes.get("dismissReason") or "")


def update(tid, changes, version, by):
    tasks, _ = _collections()
    if not isinstance(changes, dict) or not changes:
        raise AuditError("Nothing to change.")
    old = _db(lambda: tasks.find_one({"_id": _clip(tid, 40)}))
    if not old:
        raise AuditError("That task no longer exists. It may have been removed.")
    if version is not None and int(version) != int(old.get("version") or 0):
        e = AuditError("Someone else changed this task a moment ago. Reload it to see their changes, then try again.")
        e.extra = {"conflict": True}
        raise e
    d = dict(old)
    d["history"] = list(old.get("history") or [])
    _apply(d, {k: v for k, v in changes.items() if k in EDITABLE or k == "dismissReason"}, _actor(by))
    return {"item": _out(_save(old, d))}


def bulk(ids, changes, by):
    """Change status, priority, assignee or due date on many tasks; tasks that can't take the change are skipped."""
    tasks, _ = _collections()
    ids = [_clip(i, 40) for i in (ids or []) if i][:500] if isinstance(ids, list) else []
    changes = {k: v for k, v in (changes or {}).items() if k in ("status", "priority", "assignee", "due", "dismissReason")} if isinstance(changes, dict) else {}
    if not ids or not changes:
        raise AuditError("Select tasks and choose what to change.")
    by = _actor(by)
    done, skipped = 0, []
    for tid in ids:
        for attempt in range(2):  # one retry if a teammate saved the same task at that moment
            old = _db(lambda: tasks.find_one({"_id": tid}))
            if not old:
                skipped.append({"id": tid, "reason": "no longer exists"})
                break
            d = dict(old)
            d["history"] = list(old.get("history") or [])
            try:
                _apply(d, changes, by)
                _save(old, d)
                done += 1
                break
            except AuditError as e:
                if getattr(e, "extra", {}).get("conflict") and attempt == 0:
                    continue
                skipped.append({"id": tid, "title": old.get("title"), "reason": str(e)})
                break
    return {"updated": done, "skipped": skipped}


def add_note(tid, text, by):
    tasks, _ = _collections()
    text = str(text or "").strip()[:3000]
    if not text:
        raise AuditError("Write a note first.")
    for attempt in range(2):
        old = _db(lambda: tasks.find_one({"_id": _clip(tid, 40)}))
        if not old:
            raise AuditError("That task no longer exists. It may have been removed.")
        d = dict(old)
        d["history"] = list(old.get("history") or [])
        d["notes"] = (list(old.get("notes") or []) + [{"id": _new_id(), "at": now_iso(), "by": _actor(by), "text": text}])[-MAX_NOTES:]
        _hist(d, _actor(by), "note", text[:120])
        try:
            return {"item": _out(_save(old, d))}
        except AuditError as e:
            if not getattr(e, "extra", {}).get("conflict") or attempt:
                raise


# ------------------------------------------------------------------ scans

def ingest(project, issues, source, by, scan_url="", evaluated=None, scopes=None, error=""):
    """Record one scan's issues.

    New issues become open tasks. A known issue updates its evidence and last-detected time; if it had been
    resolved it's reopened, if it was dismissed it stays dismissed (it was judged a false positive).
    Active tasks from the same source and scope that this scan looked for but didn't find are marked
    "not detected"; resolved ones are marked verified. evaluated: issue types the scan checked (None = all).
    """
    tasks, scans = _collections()
    project = project_id(project)
    by = _actor(by)
    now = now_iso()
    scan_id = _new_id()
    counts = {"found": 0, "new": 0, "updated": 0, "reopened": 0, "notDetected": 0, "verified": 0}
    seen = set()
    for it in issues:
        fp = fingerprint(project, it["source"], it["type"], it["scope"])
        if fp in seen:
            continue
        seen.add(fp)
        counts["found"] += 1
        for attempt in range(3):
            old = _db(lambda: tasks.find_one({"project": project, "fp": fp}))
            if old is None:
                d = {
                    "_id": _new_id(), "project": project, "fp": fp, "source": it["source"], "type": it["type"], "scope": it["scope"],
                    "category": it["category"], "kind": it.get("kind") or "confirmed", "title": it["title"][:200], "description": it["description"],
                    "why": it["why"], "fix": it["fix"], "verify": it["verify"], "docs": it.get("docs") or "",
                    "priority": it["priority"], "priorityRank": RANK[it["priority"]], "priorityReason": it["priorityReason"], "priorityManual": False,
                    "status": "open", "urls": it["urls"][:MAX_URLS], "urlCount": it["urlCount"], "evidence": it["evidence"], "keywords": it.get("keywords") or [],
                    "scanRef": scan_id, "created": now, "updated": now, "firstDetected": now[:10], "lastDetected": now, "detectCount": 1,
                    "due": "", "dueSort": "9999-12-31", "assignee": "", "createdBy": f"{actions.SOURCES[it['source']]} scan",
                    "resolvedAt": "", "verifiedAt": "", "lastScan": {"at": now, "result": "detected", "scan": scan_id}, "dismissReason": "",
                    "notes": [], "history": [], "version": 1,
                }
                _hist(d, d["createdBy"], "detected", it["description"][:300])
                try:
                    _db(lambda: tasks.insert_one(d))
                    counts["new"] += 1
                    break
                except Exception as e:
                    if e.__class__.__name__ == "DuplicateKeyError" and attempt < 2:
                        continue  # another scan created it a moment ago: update that one instead
                    raise
            d = dict(old)
            d["history"] = list(old.get("history") or [])
            d.update(description=it["description"], why=it["why"] or old.get("why", ""), fix=old.get("fix") if old.get("source") == "manual" else it["fix"],
                     verify=it["verify"], urls=it["urls"][:MAX_URLS], urlCount=it["urlCount"], evidence=it["evidence"], scanRef=scan_id, lastDetected=now,
                     detectCount=int(old.get("detectCount") or 0) + 1, lastScan={"at": now, "result": "detected", "scan": scan_id}, docs=it.get("docs") or old.get("docs", ""))
            if it.get("keywords"):
                d["keywords"] = it["keywords"]
            if not old.get("priorityManual") and old.get("priority") != it["priority"]:
                _hist(d, by, "priority", f"{old['priority'].title()} → {it['priority'].title()} (scan)")
                d.update(priority=it["priority"], priorityRank=RANK[it["priority"]], priorityReason=it["priorityReason"])
            if old["status"] == "resolved":
                d.update(status="open", resolvedAt="", verifiedAt="")
                _hist(d, f"{actions.SOURCES[it['source']]} scan", "reopened", "Detected again by a new scan")
                counts["reopened"] += 1
            else:
                counts["updated"] += 1
            try:
                _save(old, d)
                break
            except AuditError as e:
                if not getattr(e, "extra", {}).get("conflict") or attempt == 2:
                    raise
    # what this scan looked for but no longer sees
    f = {"project": project, "source": source, "status": {"$in": ACTIVE + ["resolved"]}}
    if scopes is not None:
        f["scope"] = {"$in": list(scopes)}
    for old in _db(lambda: list(tasks.find(f))):
        if old["fp"] in seen or (evaluated is not None and old.get("type") not in evaluated):
            continue
        d = dict(old)
        d["history"] = list(old.get("history") or [])
        if old["status"] == "resolved":
            if old.get("verifiedAt"):
                continue
            d.update(verifiedAt=now, lastScan={"at": now, "result": "verified", "scan": scan_id})
            _hist(d, f"{actions.SOURCES[source]} scan", "verified", "A new scan no longer finds this issue")
            counts["verified"] += 1
        else:
            already = (old.get("lastScan") or {}).get("result") == "not-detected"
            d["lastScan"] = {"at": now, "result": "not-detected", "scan": scan_id}
            if not already:
                _hist(d, f"{actions.SOURCES[source]} scan", "not-detected", "The latest scan didn't find this issue. Check it and mark it resolved.")
            counts["notDetected"] += 1
        try:
            _save(old, d)
        except AuditError:
            pass  # a teammate is editing it; the next scan will catch up
    _db(lambda: scans.insert_one({"_id": scan_id, "project": project, "source": source, "at": now, "by": by, "url": _clip(scan_url, 2000),
                                  "counts": counts, "ok": not error, "error": _clip(error, 300)}))
    return {"scan": scan_id, "counts": counts}


def record_failed_scan(project, source, by, url, error):
    _, scans = _collections()
    _db(lambda: scans.insert_one({"_id": _new_id(), "project": project_id(project), "source": source, "at": now_iso(), "by": _actor(by),
                                  "url": _clip(url, 2000), "counts": {}, "ok": False, "error": _clip(error, 300)}))
