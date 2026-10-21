from urllib.parse import urlparse

from . import scoring
from .models import Audit, Site


def host_of(url):
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def site_for(user, url):
    """The user's Site for this URL's host, created on first use."""
    site, _ = Site.objects.get_or_create(user=user, host=host_of(url), defaults={"url": url})
    return site


def save_audit(user, result):
    """Store an engine result for a logged-in user, with the summary the dashboard lists."""
    checks = result.get("checks") or []
    final = result.get("finalUrl") or result.get("url")
    site = site_for(user, final)
    site.save(update_fields=["updated"])  # most recently audited site first
    return Audit.objects.create(
        user=user, site=site, url=result.get("url") or final, final_url=final, keyword=(result.get("keyword") or "")[:200],
        score=scoring.overall(checks), pages=len(result.get("crawl") or []),
        broken_links=len((result.get("links") or {}).get("broken") or []), result=result, **scoring.counts(checks),
    )
