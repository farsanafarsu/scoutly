"""The team's SEO Campaigns, stored in MongoDB next to the backlinks (linkstore.py).

Same switch as the backlinks: MONGODB_URI turns it on, and without it every call raises NotConfigured and
the page keeps the campaigns in the browser instead. One document per campaign, so teammates creating and
editing campaigns at the same time never overwrite each other's work.
"""
import os
import re
import threading

from server import AuditError
import linkstore

STATUSES = {"Active", "Paused"}
MAX_BATCH = 500

_lock = threading.Lock()
_coll = None


def _collection():
    global _coll
    with _lock:
        if _coll is None:
            try:
                db = linkstore._collection().database
            except linkstore.NotConfigured as e:
                raise linkstore.NotConfigured(str(e).replace("backlinks are", "campaigns are"))
            coll = db["campaigns"]
            coll.create_index([("created", -1)], name="created")
            _coll = coll
    return _coll


_clip = linkstore._clip
_url = linkstore._url


def _doc(item):
    name, brand, url = _clip(item.get("name"), 200), _clip(item.get("brand"), 120), _url(item.get("url"))
    if not name or not brand or not re.match(r"^https?://[\w-]+(\.[\w-]+)+", url, re.I):
        return None
    created = _clip(item.get("created"), 10)
    end = _clip(item.get("end"), 10)
    return {
        "_id": _clip(item.get("id"), 40) or os.urandom(8).hex(),
        "name": name, "brand": brand, "loc": _clip(item.get("loc"), 120), "url": url,
        "end": end if re.match(r"^\d{4}-\d{2}-\d{2}$", end) else "",
        "status": item.get("status") if item.get("status") in STATUSES else "Active",
        "owner": _clip(item.get("owner"), 120),
        "created": created if re.match(r"^\d{4}-\d{2}-\d{2}$", created) else "",
    }


def _out(d):
    d = dict(d)
    d["id"] = d.pop("_id")
    return d


# ------------------------------------------------------------------ operations

def list_campaigns():
    coll = _collection()
    try:
        return {"items": [_out(d) for d in coll.find().sort([("created", -1), ("_id", -1)])]}
    except Exception as e:
        raise linkstore._db_error(e)


def save(items):
    """Create or replace campaigns by id (one from the dialog, or a browser's campaigns moving in)."""
    coll = _collection()
    if not isinstance(items, list) or len(items) > MAX_BATCH:
        raise AuditError(f"Send items as a list of up to {MAX_BATCH} campaigns.")
    docs = [d for d in (_doc(it) if isinstance(it, dict) else None for it in items) if d]
    if not docs:
        raise AuditError("Add a campaign name, a brand and a valid landing page URL.")
    from pymongo import ReplaceOne
    try:
        coll.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs], ordered=False)
    except Exception as e:
        raise linkstore._db_error(e)
    return {"items": [_out(d) for d in docs]}


def delete(ids):
    coll = _collection()
    ids = [_clip(i, 40) for i in (ids or []) if i][:MAX_BATCH]
    try:
        return {"deleted": coll.delete_many({"_id": {"$in": ids}}).deleted_count}
    except Exception as e:
        raise linkstore._db_error(e)
