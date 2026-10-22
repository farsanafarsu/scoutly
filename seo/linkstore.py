"""The team's backlinks (Backlink Manager), stored in MongoDB.

Turned on by MONGODB_URI (e.g. a free MongoDB Atlas cluster: mongodb+srv://…); MONGODB_NAME picks the
database (default "scoutly"). Without it every call raises NotConfigured and the page keeps the links
in the browser instead.

One document per backlink, so teammates adding, editing and checking links at the same time never
overwrite each other's work. A unique index on (campaign, key) keeps a link from being logged twice in
the same campaign however its URL is typed.
"""
import os
import re
import threading
from datetime import datetime, timezone

from server import AuditError

URI = os.environ.get("MONGODB_URI", "").strip()
DB_NAME = os.environ.get("MONGODB_NAME", "").strip() or "scoutly"
STATUSES = {"", "live", "nofollow", "missing", "broken", "alive"}  # alive: page loads; no target page to look for
QUALITIES = {"", "good", "fair", "poor"}
MAX_BATCH = 2000

_lock = threading.Lock()
_coll = None


class NotConfigured(AuditError):
    """No database: the page falls back to keeping links in the browser."""
    extra = {"notConfigured": True}


def _collection():
    global _coll
    if not URI:
        raise NotConfigured("The team database isn't set up on the server yet, so backlinks are saved in this browser.")
    with _lock:
        if _coll is None:
            try:
                from pymongo import ASCENDING, DESCENDING, MongoClient
            except ImportError:
                raise NotConfigured("The server is missing pymongo (pip install pymongo), so backlinks are saved in this browser.")
            client = MongoClient(URI, serverSelectionTimeoutMS=10000, appname="scoutly")
            coll = client[DB_NAME]["backlinks"]
            coll.create_index([("campaign", ASCENDING), ("key", ASCENDING)], unique=True, name="campaign_link_unique")
            coll.create_index([("added", DESCENDING)], name="added")
            _coll = coll
    return _coll


def _db_error(e):
    return AuditError(f"The team database didn't answer ({e.__class__.__name__}). Try again in a moment.")


# ------------------------------------------------------------------ cleaning (mirrors backlinks.html)

def _clip(v, n):
    return " ".join(str(v if v is not None else "").split())[:n]


def _url(u):
    u = _clip(u, 2000)
    return u if not u or re.match(r"^https?://", u, re.I) else "https://" + u


def link_key(u):
    """One key per link however it's typed: no scheme, no www., no trailing slash, lower-case host."""
    u = re.sub(r"^https?://(www\.)?", "", _url(u), flags=re.I)
    u = re.sub(r"[/#?]+$", "", u)
    i = u.find("/")
    return u.lower() if i < 0 else u[:i].lower() + u[i:]


def _da(v):
    try:
        n = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, round(n, 1)))


def _iso(v):
    v = _clip(v, 40)
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except ValueError:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _doc(item):
    link = _url(item.get("link"))
    camp = _clip(item.get("campaign"), 200)
    if not link or not camp or not re.match(r"^https?://[\w-]+(\.[\w-]+)+", link, re.I):
        return None
    return {
        "_id": _clip(item.get("id"), 40) or os.urandom(8).hex(),
        "campaign": camp, "link": link, "key": link_key(link), "target": _url(item.get("target")),
        "type": _clip(item.get("type"), 40), "da": _da(item.get("da")) if item.get("da") not in (None, "") else None,
        "by": _clip(item.get("by"), 120), "added": _iso(item.get("added")),
        "status": item.get("status") if item.get("status") in STATUSES else "", "note": _clip(item.get("note"), 300),
        "checked": _clip(item.get("checked"), 40),
        "quality": item.get("quality") if item.get("quality") in QUALITIES else "",
    }


def _out(d):
    d = dict(d)
    d["id"] = d.pop("_id")
    d.pop("key", None)
    return d


# ------------------------------------------------------------------ operations

def list_links():
    coll = _collection()
    try:
        return {"items": [_out(d) for d in coll.find().sort("added", -1)]}
    except Exception as e:
        raise _db_error(e)


def add(items):
    """Insert new links; ones already in the same campaign are counted as duplicates, not errors."""
    coll = _collection()
    if not isinstance(items, list) or len(items) > MAX_BATCH:
        raise AuditError(f"Send items as a list of up to {MAX_BATCH} backlinks.")
    docs, seen = [], set()
    for it in items:
        d = _doc(it) if isinstance(it, dict) else None
        if d and (d["campaign"], d["key"]) not in seen:
            seen.add((d["campaign"], d["key"]))
            docs.append(d)
    if not docs:
        return {"added": 0, "duplicates": len(items), "items": []}
    from pymongo.errors import BulkWriteError
    try:
        coll.insert_many(docs, ordered=False)
        failed = set()
    except BulkWriteError as e:
        if any(err.get("code") != 11000 for err in e.details.get("writeErrors", [])):
            raise _db_error(e)
        failed = {err["index"] for err in e.details.get("writeErrors", [])}
    except Exception as e:
        raise _db_error(e)
    saved = [_out(d) for i, d in enumerate(docs) if i not in failed]
    return {"added": len(saved), "duplicates": len(items) - len(saved), "items": saved}


def update(item):
    """Edit one link (campaign, link, target, type, DA, added by). Moving it clears its old status."""
    coll = _collection()
    lid = _clip((item or {}).get("id"), 40)
    try:
        cur = coll.find_one({"_id": lid})
    except Exception as e:
        raise _db_error(e)
    if not cur:
        raise AuditError("That backlink was deleted by someone else. Refresh the page.")
    d = _doc({**_out(cur), **{k: v for k, v in item.items() if k in ("campaign", "link", "target", "type", "da", "by")}})
    if not d:
        raise AuditError("Add a campaign and a valid backlink URL.")
    if d["key"] != cur["key"] or d["target"] != cur.get("target"):
        d.update(status="", note="", checked="", quality="")
    from pymongo.errors import DuplicateKeyError
    try:
        coll.replace_one({"_id": lid}, d)
    except DuplicateKeyError:
        raise AuditError("That link is already in this campaign.")
    except Exception as e:
        raise _db_error(e)
    return {"item": _out(d)}


def delete(ids):
    coll = _collection()
    ids = [_clip(i, 40) for i in (ids or []) if i][:MAX_BATCH]
    try:
        return {"deleted": coll.delete_many({"_id": {"$in": ids}}).deleted_count}
    except Exception as e:
        raise _db_error(e)


def set_status(updates):
    """Save Check status results: [{id, status, note, checked, quality}]."""
    coll = _collection()
    from pymongo import UpdateOne
    ops = [UpdateOne({"_id": _clip(u.get("id"), 40)}, {"$set": {"status": u.get("status"), "note": _clip(u.get("note"), 300), "checked": _clip(u.get("checked"), 40),
                                                          "quality": u.get("quality") if u.get("quality") in QUALITIES else ""}})
           for u in (updates or [])[:MAX_BATCH] if isinstance(u, dict) and u.get("status") in STATUSES]
    if not ops:
        return {"updated": 0}
    try:
        return {"updated": coll.bulk_write(ops, ordered=False).modified_count}
    except Exception as e:
        raise _db_error(e)
