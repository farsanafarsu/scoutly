"""Optional paid data for Scoutly from DataForSEO (https://dataforseo.com).

Turned on by two environment variables, read only here on the server:

    DATAFORSEO_LOGIN=you@example.com
    DATAFORSEO_PASSWORD=your-api-password     (the API password from the DataForSEO dashboard)

Without them (or when DataForSEO rejects them) every function raises NotConnected and the pages
keep showing their free data plus "Needs a data provider".

Costs are kept down three ways:
  * every response is cached on disk for 24 hours (.cache/dataforseo/), keyword metrics per keyword
  * keyword volume and difficulty go out in batches of up to 1,000 keywords per call
  * each lookup first answers with what's cached and how many calls a fresh run would make
    (run=False); the page shows that and only calls DataForSEO when the user confirms (run=True)
"""
import base64
import hashlib
import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from server import AuditError

LOGIN = os.environ.get("DATAFORSEO_LOGIN", "").strip()
PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
BASE = os.environ.get("DATAFORSEO_BASE", "https://api.dataforseo.com").rstrip("/")  # override only for testing
CACHE_DIR = os.environ.get("DATAFORSEO_CACHE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "dataforseo")
TTL = 24 * 3600
BATCH = 1000  # keywords per Keywords Data / bulk difficulty call

# Country codes used by the keyword pages → DataForSEO location codes. English results everywhere.
LOCATIONS = {"us": 2840, "in": 2356, "gb": 2826, "ae": 2784, "au": 2036, "ca": 2124, "sg": 2702}

_lock = threading.Lock()
_status = {"at": 0, "value": None}


class NotConnected(AuditError):
    """No usable DataForSEO credentials; pages fall back to their free data."""
    extra = {"notConnected": True}


class ProviderError(AuditError):
    pass


# ------------------------------------------------------------------ http + cache

def _auth():
    return "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()


def _call(path, payload=None, timeout=60):
    """One request to DataForSEO. Returns the first task's `result` list, raises ProviderError otherwise."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(BASE + path, data=data, method="POST" if data else "GET",
                  headers={"Authorization": _auth(), "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
    except HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {"status_code": e.code * 100, "status_message": f"HTTP {e.code}"}
    except (URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"DataForSEO didn't respond ({e.__class__.__name__}). Try again in a minute.")
    except ValueError:
        raise ProviderError("DataForSEO sent a response Scoutly couldn't read.")
    if "user_data" not in path:  # the free status check isn't counted
        _ledger(path, body.get("cost") or 0)
    code = body.get("status_code")
    if code != 20000:
        raise ProviderError(_message(code, body.get("status_message")))
    task = (body.get("tasks") or [{}])[0]
    if task.get("status_code") != 20000:
        raise ProviderError(_message(task.get("status_code"), task.get("status_message")))
    return task.get("result") or []


def _message(code, msg):
    if code == 40100:
        return "DataForSEO rejected the login or API password. Check DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD."
    if code in (40200, 40210):
        return "Your DataForSEO balance is too low for this request. Add funds in the DataForSEO dashboard."
    if code == 40202:
        return "DataForSEO's rate limit was hit. Wait a minute and try again."
    return f"DataForSEO: {msg or 'request failed'} ({code})"


def _path(key):
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


def _get_cached(key):
    try:
        with open(_path(key), encoding="utf-8") as f:
            hit = json.load(f)
        if time.time() - hit["at"] < TTL:
            return hit
    except (OSError, ValueError, KeyError):
        pass
    return None


def _put_cached(key, value):
    os.makedirs(CACHE_DIR, exist_ok=True)
    hit = {"at": time.time(), "value": value}
    tmp = _path(key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hit, f)
    os.replace(tmp, _path(key))
    return hit


def _ledger(path, cost):
    """Running total of calls and money DataForSEO reports, shown on the Settings page."""
    with _lock:
        os.makedirs(CACHE_DIR, exist_ok=True)
        p = os.path.join(CACHE_DIR, "ledger.json")
        try:
            with open(p, encoding="utf-8") as f:
                led = json.load(f)
        except (OSError, ValueError):
            led = {"since": time.strftime("%Y-%m-%d"), "calls": 0, "cost": 0.0}
        led["calls"] += 1
        led["cost"] = round(led["cost"] + float(cost), 6)
        led["last"] = {"path": path, "cost": cost, "at": time.strftime("%Y-%m-%d %H:%M")}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(led, f)


def _lookup(name, params, calls, build, run):
    """Cached multi-call lookup. Without run, answers from the cache or says how many calls it would take."""
    _require()
    key = name + ":" + json.dumps(params, sort_keys=True)
    hit = _get_cached(key)
    if hit:
        return {"cached": True, "fetchedAt": int(hit["at"]), "calls": 0, **hit["value"]}
    if not run:
        return {"cached": False, "needsRun": True, "calls": calls}
    hit = _put_cached(key, build())
    return {"cached": False, "fetchedAt": int(hit["at"]), "calls": calls, **hit["value"]}


# ------------------------------------------------------------------ status

def status(refresh=False):
    """Are credentials set, and does DataForSEO accept them? Checked with the free user_data call."""
    if not (LOGIN and PASSWORD):
        return {"provider": "DataForSEO", "configured": False, "ok": False, "connected": False,
                "message": "Not connected. Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD, then restart python server.py."}
    with _lock:
        fresh = _status["value"] and time.time() - _status["at"] < (600 if _status["value"]["ok"] else 60)
        if fresh and not refresh:
            return _status["value"]
    try:
        res = _call("/v3/appendix/user_data", timeout=20)
        money = (res[0] if res else {}).get("money") or {}
        val = {"provider": "DataForSEO", "configured": True, "ok": True, "connected": True, "login": LOGIN,
               "balance": money.get("balance"), "message": "Connected"}
    except ProviderError as e:
        val = {"provider": "DataForSEO", "configured": True, "ok": False, "connected": False, "login": LOGIN, "message": str(e)}
    with _lock:
        _status.update(at=time.time(), value=val)
    return val


def usage():
    led = {"calls": 0, "cost": 0.0, "since": None}
    try:
        with open(os.path.join(CACHE_DIR, "ledger.json"), encoding="utf-8") as f:
            led.update(json.load(f))
    except (OSError, ValueError):
        pass
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json") and f != "ledger.json"] if os.path.isdir(CACHE_DIR) else []
    return {**led, "cachedResponses": len(files), "cacheHours": TTL // 3600}


def clear_cache():
    n = 0
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".json") and f != "ledger.json":
                os.remove(os.path.join(CACHE_DIR, f))
                n += 1
    return {"removed": n}


def _require():
    s = status()
    if not s["ok"]:
        raise NotConnected(s["message"])


def _loc(gl):
    return {"location_code": LOCATIONS.get((gl or "us").lower(), 2840), "language_code": "en"}


def _domain(d):
    d = re.sub(r"^[a-z]+://", "", (d or "").strip().lower()).split("/")[0].split("?")[0]
    d = re.sub(r"^www\.", "", d).strip(".")
    if not re.match(r"^[a-z0-9.-]+\.[a-z0-9-]{2,}$", d):
        raise ProviderError("Enter a domain like example.com.")
    return d


# ------------------------------------------------------------------ keywords

# Google Ads rejects keywords with symbols; these are sent, anything else is skipped.
OK_KW = re.compile(r"^[\w\s'&.\-+]+$")


def _clean_kws(keywords):
    out = []
    for k in keywords:
        k = " ".join(str(k).lower().split())
        if k and len(k) <= 80 and len(k.split()) <= 10 and OK_KW.match(k) and k not in out:
            out.append(k)
    return out[:5000]


def keyword_metrics(keywords, gl="us", run=False):
    """Search volume, CPC, competition (Keywords Data) and difficulty (Labs) per keyword, cached per keyword."""
    _require()
    kws = _clean_kws(keywords)
    loc = _loc(gl)
    tag = f"kw:{loc['location_code']}:"
    items, missing = {}, []
    for k in kws:
        hit = _get_cached(tag + k)
        if hit:
            items[k] = hit["value"]
        else:
            missing.append(k)
    batches = [missing[i:i + BATCH] for i in range(0, len(missing), BATCH)]
    calls = 2 * len(batches)  # one volume call + one difficulty call per batch
    out = {"items": items, "cached": len(items), "missing": len(missing), "calls": calls,
           "skipped": len(set(" ".join(str(k).lower().split()) for k in keywords if str(k).strip())) - len(kws)}
    if not run or not missing:
        return out
    for batch in batches:
        vol = _call("/v3/keywords_data/google_ads/search_volume/live", [{"keywords": batch, **loc}])
        kd_res = _call("/v3/dataforseo_labs/google/bulk_keyword_difficulty/live", [{"keywords": batch, **loc}])
        kd = {i.get("keyword"): i.get("keyword_difficulty") for i in ((kd_res[0] if kd_res else {}).get("items") or [])}
        got = {}
        for r in vol:
            got[(r.get("keyword") or "").lower()] = {
                "vol": r.get("search_volume"), "cpc": r.get("cpc"), "comp": r.get("competition"),
                "trend": [m.get("search_volume") for m in reversed(r.get("monthly_searches") or [])][-12:]}
        for k in batch:
            v = got.get(k, {"vol": None, "cpc": None, "comp": None, "trend": []})
            v["kd"] = kd.get(k)
            items[k] = _put_cached(tag + k, v)["value"]
    out.update(items=items, missing=0, fetched=len(missing))
    return out


def serp(q, gl="us", run=False):
    """Google organic top 10 for one keyword (SERP API, 1 call)."""
    q = " ".join((q or "").lower().split())
    if not q:
        raise ProviderError("Enter a keyword.")

    def build():
        res = _call("/v3/serp/google/organic/live/regular", [{"keyword": q, "depth": 10, **_loc(gl)}])
        r = res[0] if res else {}
        organic = [i for i in r.get("items") or [] if i.get("type") == "organic"][:10]
        return {"total": r.get("se_results_count"),
                "features": sorted({i.get("type") for i in r.get("items") or [] if i.get("type") != "organic"}),
                "items": [{"pos": i.get("rank_group"), "domain": i.get("domain"), "title": i.get("title"),
                           "url": i.get("url"), "desc": i.get("description")} for i in organic]}
    return _lookup("serp", {"q": q, "gl": gl}, 1, build, run)


# ------------------------------------------------------------------ domains (DataForSEO Labs)

def _organic_metrics(m):
    m = m or {}
    return {"keywords": m.get("count"), "traffic": m.get("etv"), "value": m.get("estimated_paid_traffic_cost"),
            "pos": {"1": m.get("pos_1"), "2-3": m.get("pos_2_3"), "4-10": m.get("pos_4_10"), "11-20": m.get("pos_11_20"),
                    "21-50": sum(m.get(k) or 0 for k in ("pos_21_30", "pos_31_40", "pos_41_50")),
                    "51-100": sum(m.get(k) or 0 for k in ("pos_51_60", "pos_61_70", "pos_71_80", "pos_81_90", "pos_91_100"))},
            "new": m.get("is_new"), "up": m.get("is_up"), "down": m.get("is_down"), "lost": m.get("is_lost")}


def organic(d, gl="us", run=False):
    """Organic Research: top 100 ranking keywords, overview metrics and competitors (2 calls)."""
    d = _domain(d)

    def build():
        loc = _loc(gl)
        rk = _call("/v3/dataforseo_labs/google/ranked_keywords/live",
                   [{"target": d, "limit": 100, "order_by": ["ranked_serp_element.serp_item.etv,desc"], **loc}])
        rk = rk[0] if rk else {}
        comp = _call("/v3/dataforseo_labs/google/competitors_domain/live",
                     [{"target": d, "limit": 20, "exclude_top_domains": True, **loc}])
        comp = comp[0] if comp else {}
        pages = {}
        kws = []
        for it in rk.get("items") or []:
            kd = it.get("keyword_data") or {}
            ki = kd.get("keyword_info") or {}
            si = (it.get("ranked_serp_element") or {}).get("serp_item") or {}
            ch = si.get("rank_changes") or {}
            kws.append({"kw": kd.get("keyword"), "pos": si.get("rank_group"), "prev": ch.get("previous_rank_absolute"),
                        "isNew": ch.get("is_new"), "vol": ki.get("search_volume"), "cpc": ki.get("cpc"),
                        "kd": (kd.get("keyword_properties") or {}).get("keyword_difficulty"),
                        "intent": ((kd.get("search_intent_info") or {}).get("main_intent") or "")[:1].upper() or None,
                        "traffic": si.get("etv"), "url": si.get("url"), "type": si.get("type")})
            u = si.get("url")
            if u:
                p = pages.setdefault(u, {"url": u, "traffic": 0, "keywords": 0})
                p["traffic"] += si.get("etv") or 0
                p["keywords"] += 1
        return {"domain": d, "total": rk.get("total_count"),
                "metrics": _organic_metrics((rk.get("metrics") or {}).get("organic")),
                "keywords": kws,
                "pages": sorted(pages.values(), key=lambda p: -p["traffic"])[:20],
                "competitors": [{"domain": c.get("domain"), "common": c.get("intersections"), "avgPos": c.get("avg_position"),
                                 "keywords": ((c.get("full_domain_metrics") or {}).get("organic") or {}).get("count"),
                                 "traffic": ((c.get("full_domain_metrics") or {}).get("organic") or {}).get("etv")}
                                for c in comp.get("items") or [] if c.get("domain") != d]}
    return _lookup("organic", {"d": d, "gl": gl}, 2, build, run)


def traffic(d, gl="us", run=False):
    """Traffic Analytics: estimated Google search traffic now, its trend and top pages (3 calls)."""
    d = _domain(d)

    def build():
        loc = _loc(gl)
        ov = _call("/v3/dataforseo_labs/google/domain_rank_overview/live", [{"target": d, **loc}])
        ov = ((ov[0] if ov else {}).get("items") or [{}])[0].get("metrics") or {}
        hist = _call("/v3/dataforseo_labs/google/historical_rank_overview/live",
                     [{"target": d, "date_from": time.strftime("%Y-%m-01", time.gmtime(time.time() - 400 * 86400)), **loc}])
        hist = (hist[0] if hist else {}).get("items") or []
        rp = _call("/v3/dataforseo_labs/google/relevant_pages/live",
                   [{"target": d, "limit": 20, "order_by": ["metrics.organic.etv,desc"], **loc}])
        rp = (rp[0] if rp else {}).get("items") or []
        paid = ov.get("paid") or {}
        return {"domain": d, "organic": _organic_metrics(ov.get("organic")),
                "paid": {"keywords": paid.get("count"), "traffic": paid.get("etv"), "value": paid.get("estimated_paid_traffic_cost")},
                "trend": sorted([{"month": f"{h.get('year')}-{int(h.get('month') or 0):02d}",
                                  "organic": ((h.get("metrics") or {}).get("organic") or {}).get("etv"),
                                  "paid": ((h.get("metrics") or {}).get("paid") or {}).get("etv")} for h in hist],
                                key=lambda x: x["month"]),
                "pages": [{"url": p.get("page_address"), "traffic": ((p.get("metrics") or {}).get("organic") or {}).get("etv"),
                           "keywords": ((p.get("metrics") or {}).get("organic") or {}).get("count")} for p in rp]}
    return _lookup("traffic", {"d": d, "gl": gl}, 3, build, run)


# ------------------------------------------------------------------ backlinks

def backlinks(d, run=False):
    """Backlink Analytics: summary, top referring domains and anchors (3 calls)."""
    d = _domain(d)

    def build():
        common = {"target": d, "rank_scale": "one_hundred", "internal_list_limit": 10}
        s = _call("/v3/backlinks/summary/live", [common])
        s = s[0] if s else {}
        rd = _call("/v3/backlinks/referring_domains/live", [{**common, "limit": 100, "order_by": ["rank,desc"]}])
        an = _call("/v3/backlinks/anchors/live", [{**common, "limit": 100, "order_by": ["backlinks,desc"]}])
        attrs = s.get("referring_links_attributes") or {}
        return {"domain": d,
                "summary": {"rank": s.get("rank"), "backlinks": s.get("backlinks"), "domains": s.get("referring_domains"),
                            "domainsNofollow": s.get("referring_domains_nofollow"), "mainDomains": s.get("referring_main_domains"),
                            "ips": s.get("referring_ips"), "broken": s.get("broken_backlinks"), "spam": s.get("backlinks_spam_score"),
                            "nofollow": attrs.get("nofollow"), "types": s.get("referring_links_types") or {},
                            "tlds": s.get("referring_links_tld") or {}, "firstSeen": s.get("first_seen")},
                "domains": [{"domain": i.get("domain"), "rank": i.get("rank"), "backlinks": i.get("backlinks"),
                             "spam": i.get("backlinks_spam_score"), "firstSeen": (i.get("first_seen") or "")[:10],
                             "nofollow": i.get("referring_pages_nofollow")}
                            for i in (rd[0] if rd else {}).get("items") or []],
                "anchors": [{"anchor": i.get("anchor"), "backlinks": i.get("backlinks"), "domains": i.get("referring_domains"),
                             "firstSeen": (i.get("first_seen") or "")[:10]}
                            for i in (an[0] if an else {}).get("items") or []]}
    return _lookup("backlinks", {"d": d}, 3, build, run)


def backlink_gap(you, competitors, mode="partial", run=False):
    """Backlink Gap: domains linking to competitors but not to you (1 call, domain_intersection)."""
    you = _domain(you)
    comps = []
    for c in competitors:
        c = _domain(c)
        if c != you and c not in comps:
            comps.append(c)
    if not comps:
        raise ProviderError("Add at least one competitor domain.")
    comps = comps[:4]
    mode = "all" if mode == "all" else "partial"

    def build():
        res = _call("/v3/backlinks/domain_intersection/live", [{
            "targets": {str(i + 1): c for i, c in enumerate(comps)}, "exclude_targets": [you],
            "intersection_mode": mode, "limit": 200, "rank_scale": "one_hundred",
            "order_by": ["1.rank,desc"]}])
        r = res[0] if res else {}
        rows = []
        for it in r.get("items") or []:
            inter = it.get("domain_intersection") or {}
            per = {comps[int(k) - 1]: {"backlinks": v.get("backlinks"), "rank": v.get("rank")}
                   for k, v in inter.items() if v and k.isdigit() and int(k) <= len(comps)}
            first = next((v for v in inter.values() if v), {})
            rows.append({"domain": first.get("target"), "rank": max((p["rank"] or 0) for p in per.values()) if per else None,
                         "spam": first.get("backlinks_spam_score"), "links": per, "count": len(per)})
        rows.sort(key=lambda x: (-x["count"], -(x["rank"] or 0)))
        return {"you": you, "competitors": comps, "mode": mode, "total": r.get("total_count"), "items": rows}
    return _lookup("gap", {"you": you, "c": comps, "mode": mode}, 1, build, run)
