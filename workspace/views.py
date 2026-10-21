"""Workspace API: a logged-in user's sites, audits, keyword lists, rank snapshots, campaigns,
preferences and saved tool state. Every query is scoped to request.user."""
import re
from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone

from core.api import ApiError, api, body, int_param, json_size_ok, text
from core.db import atomic, is_id

from . import store
from .models import Audit, Campaign, Keyword, KeywordList, Preferences, RankSnapshot, SavedState, Site
from .services import host_of

MAX_KEYWORDS_PER_LIST = 5000
MAX_STATE_BYTES = 512 * 1024
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
STATE_KEY = re.compile(r"^[a-zA-Z0-9][\w.\-]{0,63}$")


def _own(model, request, **lookup):
    if "pk" in lookup and not is_id(lookup["pk"]):
        obj = None
    else:
        obj = model.objects.filter(user=request.user, **lookup).first()
    if obj is None:
        raise ApiError(f"That {model._meta.verbose_name} doesn't exist or isn't yours.", status=404)
    return obj


def _url(data, key="url", required=True):
    raw = text(data, key, required=required, max_len=2000, label="a web address")
    if not raw:
        return ""
    url = raw if re.match(r"^https?://", raw, re.I) else "https://" + raw
    try:
        URLValidator(schemes=["http", "https"])(url)
    except ValidationError:
        raise ApiError("That doesn't look like a web address.", field=key)
    return url


def _date(data, key, required=False):
    v = text(data, key, required=required, max_len=10, label="a date")
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ApiError("Use a date like 2026-10-22.", field=key)


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


# ------------------------------------------------------------------ serializers

def site_json(s, with_latest=False):
    out = {"id": str(s.pk), "host": s.host, "url": s.url, "name": s.name, "created": _iso(s.created), "updated": _iso(s.updated)}
    if with_latest:
        a = s.audits.defer("result").first()
        out["latestAudit"] = audit_json(a) if a else None
    return out


def audit_json(a, full=False):
    out = {"id": str(a.pk), "siteId": str(a.site_id) if a.site_id else None, "url": a.url, "finalUrl": a.final_url, "keyword": a.keyword, "score": a.score,
           "critical": a.critical, "warning": a.warning, "notice": a.notice, "pass": a.passed, "pages": a.pages,
           "broken": a.broken_links, "created": _iso(a.created)}
    if full:
        out["result"] = a.result
    return out


def list_json(l, keywords=False):
    out = {"id": str(l.pk), "name": l.name, "created": _iso(l.created), "updated": _iso(l.updated)}
    if keywords:
        out["items"] = [{"kw": k.kw, **k.data, "added": _iso(k.added)} for k in l.keywords.all()]
    else:
        out["count"] = l.n if hasattr(l, "n") else l.keywords.count()
    return out


def snapshot_json(s):
    return {"id": str(s.pk), "date": s.date.isoformat(), "queries": s.queries, "pages": s.pages, "created": _iso(s.created)}


def campaign_json(c):
    return {"id": str(c.pk), "name": c.name, "brand": c.brand, "loc": c.location, "url": c.url,
            "end": c.end_date.isoformat() if c.end_date else "", "status": c.status, "backlinks": c.backlinks,
            "created": c.created.date().isoformat(), "updated": _iso(c.updated)}


# ------------------------------------------------------------------ sites + audits

@api(["GET", "POST"], login=True)
def sites(request):
    if request.method == "POST":
        data = body(request)
        url = _url(data)
        site, created = Site.objects.get_or_create(user=request.user, host=host_of(url),
                                                   defaults={"url": url, "name": text(data, "name", max_len=150)})
        return {"site": site_json(site)}, 201 if created else 200
    return {"items": [site_json(s, with_latest=True) for s in Site.objects.filter(user=request.user)]}


@api(["GET", "PATCH", "DELETE"], login=True)
def site_detail(request, pk):
    site = _own(Site, request, pk=pk)
    if request.method == "DELETE":
        site.delete()
        return {"ok": True}
    if request.method == "PATCH":
        data = body(request)
        if "name" in data:
            site.name = text(data, "name", max_len=150)
        if "url" in data:
            url = _url(data)
            if host_of(url) != site.host:
                raise ApiError("Add a different website as a new site instead of changing its domain.", field="url")
            site.url = url
        site.save()
    return {"site": site_json(site, with_latest=True)}


@api(["GET"], login=True)
def audits(request):
    qs = Audit.objects.filter(user=request.user).defer("result")
    if request.GET.get("site"):
        qs = qs.filter(site_id=request.GET["site"]) if is_id(request.GET["site"]) else qs.none()
    limit = int_param(request, "limit", 50, 1, 200)
    return {"items": [audit_json(a) for a in qs[:limit]]}


@api(["GET", "DELETE"], login=True)
def audit_detail(request, pk):
    a = _own(Audit, request, pk=pk)
    if request.method == "DELETE":
        a.delete()
        return {"ok": True}
    return {"audit": audit_json(a, full=True)}


# ------------------------------------------------------------------ keyword lists

@api(["GET", "POST"], login=True)
def keyword_lists(request):
    if request.method == "POST":
        name = text(body(request), "name", required=True, max_len=120, label="a list name")
        try:
            with atomic():
                kl = KeywordList.objects.create(user=request.user, name=name)
        except IntegrityError:
            raise ApiError("You already have a list with that name.", status=409, field="name")
        return {"list": list_json(kl)}, 201
    qs =KeywordList.objects.filter(user=request.user).annotate(n=Count("keywords"))
    return {"items": [list_json(kl) for kl in qs]}


@api(["GET", "PATCH", "DELETE"], login=True)
def keyword_list_detail(request, pk):
    kl = _own(KeywordList, request, pk=pk)
    if request.method == "DELETE":
        kl.delete()
        return {"ok": True}
    if request.method == "PATCH":
        kl.name = text(body(request), "name", required=True, max_len=120, label="a list name")
        try:
            with atomic():
                kl.save()
        except IntegrityError:
            raise ApiError("You already have a list with that name.", status=409, field="name")
    return {"list": list_json(kl, keywords=True)}


@api(["POST", "DELETE"], login=True)
def keyword_list_items(request, pk):
    kl = _own(KeywordList, request, pk=pk)
    data = body(request)
    if request.method == "DELETE":
        kws = [str(k).strip().lower() for k in data.get("kws") or [] if str(k).strip()]
        removed, _ = kl.keywords.filter(kw__in=kws).delete()
        return {"removed": removed}
    items = data.get("items")
    if not isinstance(items, list):
        raise ApiError("Send items as a list like [{\"kw\": \"seo audit\", \"volume\": 880}].")
    have = set(kl.keywords.values_list("kw", flat=True))
    room = MAX_KEYWORDS_PER_LIST - len(have)
    new = []
    for it in items:
        if isinstance(it, str):
            it = {"kw": it}
        if not isinstance(it, dict):
            continue
        kw = " ".join(str(it.get("kw") or "").lower().split())[:200]
        if not kw or kw in have:
            continue
        extra = {k: v for k, v in it.items() if k != "kw"}
        if not json_size_ok(extra, 4096):
            extra = {}
        have.add(kw)
        new.append(Keyword(keyword_list=kl, kw=kw, data=extra))
    if len(new) > room:
        raise ApiError(f"A list holds up to {MAX_KEYWORDS_PER_LIST:,} keywords; this one has room for {max(room, 0):,} more.")
    Keyword.objects.bulk_create(new)  # already de-duplicated against the list above
    kl.save(update_fields=["updated"])
    return {"added": len(new), "list": list_json(kl)}


# ------------------------------------------------------------------ rank snapshots (Position Tracking)

def _rows(v, key):
    if v is None:
        return None
    if not isinstance(v, list):
        raise ApiError(f"{key} should be a list of rows.", field=key)
    return v


@api(["GET", "POST"], login=True)
def rank_snapshots(request):
    if request.method == "POST":
        data = body(request)
        day = _date(data, "date") or timezone.localdate()
        queries, pages = _rows(data.get("queries"), "queries"), _rows(data.get("pages"), "pages")
        if queries is None and pages is None:
            raise ApiError("Send queries or pages rows from a Search Console export.")
        if not json_size_ok([queries, pages], MAX_SNAPSHOT_BYTES):
            raise ApiError("That export is too large to store. Filter it down in Search Console first.", status=413)
        snap, created = RankSnapshot.objects.update_or_create(user=request.user, date=day,
                                                              defaults={"queries": queries, "pages": pages})
        return {"snapshot": snapshot_json(snap)}, 201 if created else 200
    return {"items": [snapshot_json(s) for s in RankSnapshot.objects.filter(user=request.user)]}


@api(["DELETE"], login=True)
def rank_snapshot_detail(request, day):
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise ApiError("Use a date like 2026-10-22.", status=404)
    _own(RankSnapshot, request, date=d).delete()
    return {"ok": True}


# ------------------------------------------------------------------ campaigns

def _campaign_fields(data, partial=False):
    f = {}
    if not partial or "name" in data:
        f["name"] = text(data, "name", required=True, max_len=200, label="a campaign name or target keyword")
    if not partial or "brand" in data:
        f["brand"] = text(data, "brand", required=True, max_len=150, label="the brand or client")
    if not partial or "url" in data:
        f["url"] = _url(data)
    if "loc" in data:
        f["location"] = text(data, "loc", max_len=150)
    if "end" in data:
        f["end_date"] = _date(data, "end")
    if "status" in data:
        st = text(data, "status", max_len=10).capitalize()
        if st not in Campaign.Status.values:
            raise ApiError("Status should be Active or Paused.", field="status")
        f["status"] = st
    if "backlinks" in data:
        try:
            f["backlinks"] = max(0, int(data["backlinks"]))
        except (TypeError, ValueError):
            raise ApiError("Backlinks should be a number.", field="backlinks")
    return f


@api(["GET", "POST"], login=True)
def campaigns(request):
    if request.method == "POST":
        c = Campaign.objects.create(user=request.user, **_campaign_fields(body(request)))
        return {"campaign": campaign_json(c)}, 201
    return {"items": [campaign_json(c) for c in Campaign.objects.filter(user=request.user)]}


@api(["GET", "PATCH", "DELETE"], login=True)
def campaign_detail(request, pk):
    c = _own(Campaign, request, pk=pk)
    if request.method == "DELETE":
        c.delete()
        return {"ok": True}
    if request.method == "PATCH":
        for k, v in _campaign_fields(body(request), partial=True).items():
            setattr(c, k, v)
        c.save()
    return {"campaign": campaign_json(c)}


# ------------------------------------------------------------------ preferences + saved tool state

@api(["GET", "PUT"], login=True)
def preferences(request):
    prefs, _ = Preferences.objects.get_or_create(user=request.user)
    if request.method == "PUT":
        data = body(request)
        if not json_size_ok(data, 16 * 1024):
            raise ApiError("Preferences are too large.", status=413)
        prefs.data = data
        prefs.save()
    return {"preferences": prefs.data}


@api(["GET"], login=True)
def states(request):
    return {"items": [{"key": s.key, "updated": _iso(s.updated)} for s in SavedState.objects.filter(user=request.user)]}


@api(["GET", "PUT", "DELETE"], login=True)
def state_detail(request, key):
    if not STATE_KEY.match(key):
        raise ApiError("Keys are up to 64 letters, digits, dots, dashes or underscores.", status=404)
    if request.method == "PUT":
        data = body(request)
        if "data" not in data:
            raise ApiError('Send {"data": ...}.')
        if not json_size_ok(data["data"], MAX_STATE_BYTES):
            raise ApiError("That's too much to save in one place (512 KB at most).", status=413)
        s, _ = SavedState.objects.update_or_create(user=request.user, key=key, defaults={"data": data["data"]})
        return {"key": s.key, "data": s.data, "updated": _iso(s.updated)}
    s = SavedState.objects.filter(user=request.user, key=key).first()
    if request.method == "DELETE":
        if s:
            s.delete()
        return {"ok": True}
    return {"key": key, "data": s.data if s else None, "updated": _iso(s.updated) if s else None}


# ------------------------------------------------------------------ page store (what assets/app.js reads and writes)

@api(["GET", "DELETE"], login=True)
def store_all(request):
    if request.method == "DELETE":
        store.clear(request.user)
        return {"ok": True}
    keys = [k for k in request.GET.get("keys", "").split(",") if k] or store.all_keys(request.user)
    return {"data": store.load(request.user, keys[:50])}


@api(["GET", "PUT"], login=True)
def store_key(request, key):
    if not store.valid_key(key):
        raise ApiError("Unknown storage key.", status=404)
    if request.method == "PUT":
        data = body(request)
        if "data" not in data:
            raise ApiError('Send {"data": ...}.')
        store.put(request.user, key, data["data"])
        return {"ok": True}
    return {"key": key, "data": store.get(request.user, key)}


# ------------------------------------------------------------------ export

@api(["GET"], login=True)
def export(request):
    """Everything stored for this account, as one JSON download (Settings → Your data)."""
    u = request.user
    lists = KeywordList.objects.filter(user=u).prefetch_related("keywords")
    prefs = Preferences.objects.filter(user=u).first()
    return {
        "app": "Scoutly", "exported": _iso(timezone.now()), "user": u.to_dict(),
        "sites": [site_json(s) for s in Site.objects.filter(user=u)],
        "audits": [audit_json(a, full=True) for a in Audit.objects.filter(user=u)],
        "keywordLists": [list_json(kl, keywords=True) for kl in lists],
        "rankSnapshots": [snapshot_json(s) for s in RankSnapshot.objects.filter(user=u)],
        "campaigns": [campaign_json(c) for c in Campaign.objects.filter(user=u)],
        "preferences": prefs.data if prefs else {},
        "state": {s.key: s.data for s in SavedState.objects.filter(user=u)},
    }
