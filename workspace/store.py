"""The page store: each app page's saved data, kept under the same keys the pages used in
browser storage ("scoutly.campaigns", "scoutly.kwlists", …) and in the same shape.

The structured keys are stored in their own tables (campaigns, keyword lists, rank snapshots,
preferences), so the data is queryable and shows up in the admin and the REST endpoints. Any other
"scoutly.*" key is kept as-is in SavedState.
"""
import re
from datetime import date

from django.utils.dateparse import parse_date

from core.api import ApiError, json_size_ok
from core.db import atomic

from .models import Campaign, Keyword, KeywordList, Preferences, RankSnapshot, SavedState
from .services import site_for

KEY = re.compile(r"^scoutly\.[A-Za-z0-9][\w.\-]{0,55}$")
MAX_GENERIC = 512 * 1024
MAX_RANKS = 8 * 1024 * 1024
MAX_LISTS, MAX_LIST_ITEMS = 200, 5000
TRACKED = "scoutly.ranks.tracked"  # Position Tracking's starred rows, stored beside the snapshots


def valid_key(key):
    return bool(KEY.match(key or ""))


def _clip(v, n):
    return " ".join(str(v or "").split())[:n]


def _url(v):
    v = _clip(v, 2000)
    return v if not v or re.match(r"^https?://", v, re.I) else "https://" + v


# ------------------------------------------------------------------ generic

def _get_state(user, key):
    s = SavedState.objects.filter(user=user, key=key).first()
    return s.data if s else None


def _put_state(user, key, value):
    if value is None:
        SavedState.objects.filter(user=user, key=key).delete()
    else:
        SavedState.objects.update_or_create(user=user, key=key, defaults={"data": value})


# ------------------------------------------------------------------ scoutly.site / scoutly.prefs

def _put_site(user, value):
    _put_state(user, "scoutly.site", _clip(value, 253) or None)
    if value:
        site_for(user, _url(value))  # the current website is also one of the user's sites


def _get_prefs(user):
    p = Preferences.objects.filter(user=user).first()
    return p.data if p and p.data else None


def _put_prefs(user, value):
    if value is not None and not isinstance(value, dict):
        raise ApiError("Preferences should be an object.")
    Preferences.objects.update_or_create(user=user, defaults={"data": value or {}})


# ------------------------------------------------------------------ scoutly.campaigns

def _get_campaigns(user):
    return [{"id": c.client_id or str(c.pk), "name": c.name, "brand": c.brand, "loc": c.location, "url": c.url,
             "end": c.end_date.isoformat() if c.end_date else "", "status": c.status, "owner": user.name,
             "created": c.created.date().isoformat(), "backlinks": c.backlinks}
            for c in Campaign.objects.filter(user=user)]


def _put_campaigns(user, value):
    value = value or []
    if not isinstance(value, list) or len(value) > 1000:
        raise ApiError("Campaigns should be a list (up to 1,000).")
    have = {c.client_id or str(c.pk): c for c in Campaign.objects.filter(user=user)}
    keep = set()
    for item in value:
        if not isinstance(item, dict) or not item.get("id") or not _clip(item.get("name"), 200):
            continue
        cid = _clip(item["id"], 40)
        keep.add(cid)
        end = parse_date(str(item.get("end") or "")) if item.get("end") else None
        try:
            backlinks = max(0, int(item.get("backlinks") or 0))
        except (TypeError, ValueError):
            backlinks = 0
        fields = {"name": _clip(item.get("name"), 200), "brand": _clip(item.get("brand"), 150),
                  "location": _clip(item.get("loc"), 150), "url": _url(item.get("url")), "end_date": end,
                  "status": "Paused" if str(item.get("status")).lower() == "paused" else "Active", "backlinks": backlinks}
        c = have.get(cid)
        if c is None:
            Campaign.objects.create(user=user, client_id=cid, **fields)
        elif any(getattr(c, k) != v for k, v in fields.items()):
            for k, v in fields.items():
                setattr(c, k, v)
            c.save()
    for cid, c in have.items():
        if cid not in keep:
            c.delete()


# ------------------------------------------------------------------ scoutly.kwlists

def _get_lists(user):
    out = {}
    for kl in KeywordList.objects.filter(user=user).prefetch_related("keywords"):
        out[kl.name] = {"created": kl.created.isoformat(timespec="seconds"),
                        "items": [{"kw": k.kw, **k.data} for k in kl.keywords.all()]}
    return out or None


def _put_lists(user, value):
    value = value or {}
    if not isinstance(value, dict) or len(value) > MAX_LISTS:
        raise ApiError(f"Keyword lists should be an object of up to {MAX_LISTS} lists.")
    have = {kl.name: kl for kl in KeywordList.objects.filter(user=user)}
    names = set()
    for name, body in value.items():
        name = _clip(name, 120)
        items = body.get("items") if isinstance(body, dict) else None
        if not name or not isinstance(items, list):
            continue
        if len(items) > MAX_LIST_ITEMS:
            raise ApiError(f"A list holds up to {MAX_LIST_ITEMS:,} keywords.")
        names.add(name)
        kl = have.get(name) or KeywordList.objects.create(user=user, name=name)
        wanted = {}
        for it in items:
            if isinstance(it, dict) and _clip(it.get("kw"), 200):
                extra = {k: v for k, v in it.items() if k != "kw"}
                wanted.setdefault(_clip(it["kw"], 200), extra if json_size_ok(extra, 4096) else {})
        current = {k.kw: k for k in kl.keywords.all()}
        gone = [k.pk for kw, k in current.items() if kw not in wanted]
        if gone:
            Keyword.objects.filter(pk__in=gone).delete()
        Keyword.objects.bulk_create([Keyword(keyword_list=kl, kw=kw, data=d) for kw, d in wanted.items() if kw not in current])
        for kw, d in wanted.items():
            if kw in current and current[kw].data != d:
                current[kw].data = d
                current[kw].save(update_fields=["data"])
        kl.save(update_fields=["updated"])
    KeywordList.objects.filter(user=user).exclude(name__in=names).delete()


# ------------------------------------------------------------------ scoutly.ranks

def _get_ranks(user):
    snaps = [{"date": s.date.isoformat(), "queries": s.queries, "pages": s.pages} for s in RankSnapshot.objects.filter(user=user)]
    tracked = _get_state(user, TRACKED)
    if not snaps and tracked is None:
        return None
    return {"snaps": snaps, "tracked": tracked or {}}


def _put_ranks(user, value):
    value = value or {}
    snaps = value.get("snaps") if isinstance(value, dict) else None
    if not isinstance(snaps, list):
        raise ApiError("Rank data should look like {snaps: [...], tracked: {...}}.")
    have = {s.date: s for s in RankSnapshot.objects.filter(user=user)}
    dates = set()
    for s in snaps:
        if not isinstance(s, dict):
            continue
        try:
            d = date.fromisoformat(str(s.get("date")))
        except ValueError:
            continue
        dates.add(d)
        q, p = s.get("queries"), s.get("pages")
        cur = have.get(d)
        if cur is None:
            RankSnapshot.objects.create(user=user, date=d, queries=q, pages=p)
        elif cur.queries != q or cur.pages != p:
            cur.queries, cur.pages = q, p
            cur.save(update_fields=["queries", "pages"])
    RankSnapshot.objects.filter(user=user).exclude(date__in=dates).delete()
    tracked = value.get("tracked")
    _put_state(user, TRACKED, tracked if isinstance(tracked, dict) and tracked else None)


SPECIAL = {
    "scoutly.site": (lambda u: _get_state(u, "scoutly.site"), _put_site),
    "scoutly.prefs": (_get_prefs, _put_prefs),
    "scoutly.campaigns": (lambda u: _get_campaigns(u) or None, _put_campaigns),
    "scoutly.kwlists": (_get_lists, _put_lists),
    "scoutly.ranks": (_get_ranks, _put_ranks),
}


def get(user, key):
    if key in SPECIAL:
        return SPECIAL[key][0](user)
    return _get_state(user, key)


def put(user, key, value):
    if not valid_key(key) or key == TRACKED:
        raise ApiError("Unknown storage key.", status=404)
    if not json_size_ok(value, MAX_RANKS if key == "scoutly.ranks" else MAX_GENERIC):
        raise ApiError("That's too much to save at once.", status=413)
    with atomic():
        if key in SPECIAL:
            SPECIAL[key][1](user, value)
        else:
            _put_state(user, key, value)


def load(user, keys):
    return {k: get(user, k) for k in keys if valid_key(k)}


def all_keys(user):
    """Every key this user has data under, plus the structured ones."""
    keys = set(SavedState.objects.filter(user=user).exclude(key=TRACKED).values_list("key", flat=True))
    return sorted(keys | set(SPECIAL))


def clear(user):
    """Delete the user's saved work (sites, audits, lists, snapshots, campaigns, preferences, state)."""
    with atomic():
        for rel in ("sites", "audits", "keyword_lists", "rank_snapshots", "campaigns", "saved_states"):
            getattr(user, rel).all().delete()
        Preferences.objects.filter(user=user).delete()
