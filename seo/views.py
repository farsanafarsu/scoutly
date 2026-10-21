"""The SEO API the pages call: the audit, PageSpeed, the free research tools and DataForSEO data.

Request and response shapes match the old server.py exactly, so the pages work unchanged.
Expected failures (site unreachable, provider not connected) answer 200 with {"error": ...};
only unexpected ones are 500.
"""
import json
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener

from django.conf import settings
from django.db import connection

from core import ratelimit
from core.api import ApiError, api, body

from . import engine, provider, tools
from .engine import PSI_KEY, UA, AuditError


def _run(fn):
    """Call a tool; AuditError is an expected outcome, so it goes back as 200 + {error}."""
    try:
        return fn()
    except AuditError as e:
        return {"error": str(e), **getattr(e, "extra", {})}


def _paid(request, q):
    """run=1 spends DataForSEO credit: outside DEBUG only logged-in users may ask for it."""
    run = q.get("run") in ("1", True, "true")
    if run and settings.SCOUTLY_PAID_REQUIRES_LOGIN and not request.user.is_authenticated:
        raise ApiError("Log in to run paid DataForSEO lookups.", status=401, auth=True)
    return run


# ------------------------------------------------------------------ audit

@api(["GET"])
def audit(request):
    target = request.GET.get("url", "")
    if not target.strip():
        raise ApiError("Add a website address to audit.")
    ratelimit.limit(request, "audit")
    try:
        result = engine.run_audit(target, request.GET.get("kw", ""))
    except AuditError as e:
        return {"error": str(e)}
    if request.user.is_authenticated:
        from workspace.services import save_audit  # the workspace app stores a logged-in user's audits
        result["auditId"] = str(save_audit(request.user, result).pk)
    return result


@api(["GET"])
def pagespeed(request):
    """Proxy PageSpeed Insights so an API key stays on the server."""
    target = request.GET.get("url", "")
    if not re.match(r"^https?://", target, re.I):
        return {"error": {"message": "Missing url"}}, 400
    ratelimit.limit(request, "tool")
    q = "strategy=mobile&category=performance&url=" + quote(target, safe="")
    if PSI_KEY:
        q += "&key=" + quote(PSI_KEY, safe="")
    try:
        req = Request("https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + q, headers={"User-Agent": UA})
        with build_opener().open(req, timeout=100) as r:
            data = json.loads(r.read())
    except HTTPError as e:
        try:
            data = json.loads(e.read())
        except Exception:
            data = {"error": {"message": f"HTTP {e.code}"}}
        # a quota limit is an expected outcome, not a broken request: Google's status goes in the body
        data["scoutlyStatus"] = e.code
    except Exception as e:
        data = {"error": {"message": f"PageSpeed Insights didn't respond ({e.__class__.__name__})"}, "scoutlyStatus": 504}
    data["scoutlyHasKey"] = bool(PSI_KEY)
    return data


@api(["GET"])
def health(request):
    try:
        connection.ensure_connection()
        db = True
    except Exception:
        db = False
    return {"ok": db, "database": db}


# ------------------------------------------------------------------ free research tools (GET)

def _intent(q):
    kws = [" ".join(x.lower().split()) for x in q.get("q", "").split("\n")][:500]
    return {"items": [{"kw": k, "intent": tools.intent(k)} for k in kws if k]}


TOOLS = {
    "keywords": lambda q: tools.keyword_ideas(q.get("q", ""), q.get("hl", "en"), q.get("gl", "us"), q.get("deep", "1") != "0"),
    "topics": lambda q: tools.topics(q.get("q", ""), q.get("hl", "en"), q.get("gl", "us")),
    "page": lambda q: tools.page(q.get("url", ""), q.get("kw", "")),
    "domain": lambda q: tools.domain(q.get("d", "")),
    "intent": _intent,
    "ai-ready": lambda q: tools.ai_ready(q.get("url", "")),
    "aeo": lambda q: tools.aeo(q.get("url", ""), q.get("q", "")),
}


def tool(name):
    fn = TOOLS[name]

    @api(["GET"])
    def view(request):
        ratelimit.limit(request, "tool")
        return _run(lambda: fn(request.GET.dict()))

    view.__name__ = f"tool_{name.replace('-', '_')}"
    return view


@api(["POST"], csrf=False)
def backlinks(request):
    ratelimit.limit(request, "tool")
    b = body(request)
    return _run(lambda: tools.backlink_audit(str(b.get("target") or ""), [str(s) for s in b.get("sources") or []]))


# ------------------------------------------------------------------ DataForSEO (optional, paid)
# Without run=1 only the 24-hour cache answers, together with what a fresh lookup would cost.

PROVIDER = {
    "serp": lambda q, run: provider.serp(q.get("q", ""), q.get("gl", "us"), run),
    "organic": lambda q, run: provider.organic(q.get("d", ""), q.get("gl", "us"), run),
    "traffic": lambda q, run: provider.traffic(q.get("d", ""), q.get("gl", "us"), run),
    "backlinks": lambda q, run: provider.backlinks(q.get("d", ""), run),
    "gap": lambda q, run: provider.backlink_gap(q.get("you", ""), [c for c in q.get("c", "").split(",") if c.strip()],
                                                q.get("mode", "partial"), run),
}


def provider_lookup(name):
    fn = PROVIDER[name]

    @api(["GET"])
    def view(request):
        q = request.GET.dict()
        run = _paid(request, q)
        return _run(lambda: fn(q, run))

    view.__name__ = f"provider_{name}"
    return view


@api(["GET"])
def provider_status(request):
    return _run(lambda: provider.status(request.GET.get("refresh") == "1"))


@api(["GET"])
def provider_usage(request):
    return _run(provider.usage)


@api(["POST"], csrf=False)
def provider_keywords(request):
    b = body(request)
    run = _paid(request, {"run": bool(b.get("run"))})
    return _run(lambda: provider.keyword_metrics([str(k) for k in b.get("keywords") or []][:5000], str(b.get("gl") or "us"), run))


@api(["POST"], csrf=False)
def provider_clear_cache(request):
    if not settings.DEBUG and not request.user.is_staff:
        raise ApiError("Only an admin can clear the data cache.", status=403)
    return _run(provider.clear_cache)
