"""Action Center issue rules: turn real findings into task-shaped issues for taskstore.py.

Each function reads data Scoutly actually has (a Site Audit result, a PageSpeed Insights response, the team's
logged backlinks, an imported Search Console export) and returns issues:

    {source, type, scope, category, kind, title, description, why, fix, verify, priority, priorityReason,
     urls, urlCount, evidence, keywords, docs}

kind says how sure we are: "confirmed" (a crawler or API observed it), "inferred" (an opportunity read from
search performance numbers, worth a human look). Nothing here writes to a website or invents data: a check
that didn't run produces no issue.

The thresholds live in RULES and can be overridden with SCOUTLY_TASK_RULES (a JSON object), e.g.
SCOUTLY_TASK_RULES='{"many_urls": 20, "gsc_min_impressions": 1000}'.
"""
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

RULES = {
    "many_urls": 10,               # this many affected URLs raises a medium issue to high (and low to medium)
    "psi_poor": 50,                # Lighthouse performance score below this is poor (high priority)
    "psi_ok": 90,                  # below this, but not poor, needs improvement (medium)
    "psi_min_savings_ms": 300,     # a Lighthouse opportunity must save at least this much time to become a task
    "psi_min_savings_bytes": 100_000,
    "backlinks_many_lost": 5,      # lost backlinks for one page at or above this are high priority
    "backlinks_strong_da": 40,     # losing a link from a site with DA at or above this is high priority
    "backlinks_unchecked_days": 14,  # links never checked after this many days get a follow-up task
    "gsc_min_impressions": 500,    # pages need this many impressions before a CTR opportunity is raised
    "gsc_ctr_ratio": 0.5,          # CTR below this share of the typical CTR for its position is low
    "gsc_max_position": 20,
    "gsc_query_min_impressions": 200,
    "gsc_max_query_tasks": 15,     # at most this many query opportunities per import
    "gsc_drop_ratio": 0.4,         # clicks or impressions down by this share between two imports
    "gsc_drop_min_clicks": 50,     # ... from at least this many clicks before
}
try:
    RULES.update({k: v for k, v in json.loads(os.environ.get("SCOUTLY_TASK_RULES") or "{}").items() if k in RULES and isinstance(v, (int, float))})
except ValueError:
    pass

CATEGORIES = {
    "technical": "Technical SEO", "onpage": "On-Page SEO", "search": "Search Performance",
    "performance": "Performance & Core Web Vitals", "backlinks": "Backlink Monitoring",
    "ai": "AI Search Visibility", "other": "Other",
}
SOURCES = {
    "site-audit": "Site Audit", "pagespeed": "PageSpeed Insights", "backlink-manager": "Backlink Manager",
    "search-console": "Search Console import", "manual": "Manual",
}
KINDS = {"confirmed": "Confirmed by a scan", "inferred": "Opportunity from search data", "ai": "AI suggestion, needs review", "manual": "Added by a teammate"}

# Site Audit check categories → Action Center categories
AUDIT_CATS = {"crawl": "technical", "links": "technical", "schema": "technical", "perf": "performance",
              "onpage": "onpage", "content": "onpage", "internal": "onpage", "ai": "ai"}
# checks that describe the whole site rather than the audited page, so they dedupe per project
SITEWIDE = {"robots", "sitemap", "sitemap-robots", "http-redirect", "dup-titles", "dup-desc", "pages-no-title", "pages-no-desc",
            "orphans", "depth", "weak", "crawl-noindex", "crawl-blocked", "llms"}
# a failure here stops search engines reaching or indexing the start page
INDEXING_BLOCKERS = {"status", "noindex", "robots-block", "https"}
# informational rows that aren't problems to fix
SKIP = {"crawled", "unverified"}
DOCS = {
    "title": "https://developers.google.com/search/docs/appearance/title-link",
    "pages-no-title": "https://developers.google.com/search/docs/appearance/title-link",
    "dup-titles": "https://developers.google.com/search/docs/appearance/title-link",
    "desc": "https://developers.google.com/search/docs/appearance/snippet",
    "pages-no-desc": "https://developers.google.com/search/docs/appearance/snippet",
    "dup-desc": "https://developers.google.com/search/docs/appearance/snippet",
    "canonical": "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    "noindex": "https://developers.google.com/search/docs/crawling-indexing/block-indexing",
    "crawl-noindex": "https://developers.google.com/search/docs/crawling-indexing/block-indexing",
    "robots": "https://developers.google.com/search/docs/crawling-indexing/robots/intro",
    "robots-block": "https://developers.google.com/search/docs/crawling-indexing/robots/intro",
    "sitemap": "https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview",
    "sitemap-robots": "https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap",
    "in-sitemap": "https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview",
    "alt": "https://developers.google.com/search/docs/appearance/google-images",
    "redirect": "https://developers.google.com/search/docs/crawling-indexing/301-redirects",
    "int-redirects": "https://developers.google.com/search/docs/crawling-indexing/301-redirects",
    "hreflang": "https://developers.google.com/search/docs/specialty/international/localized-versions",
    "viewport": "https://developers.google.com/search/docs/crawling-indexing/mobile/mobile-sites-mobile-first-indexing",
    "https": "https://developers.google.com/search/docs/crawling-indexing/http-network-errors",
    "broken-int": "https://developers.google.com/search/docs/crawling-indexing/links-crawlable",
    "weak": "https://developers.google.com/search/docs/crawling-indexing/links-crawlable",
    "orphans": "https://developers.google.com/search/docs/crawling-indexing/links-crawlable",
    "anchors": "https://developers.google.com/search/docs/crawling-indexing/links-crawlable",
    "lcp": "https://web.dev/articles/lcp", "inp": "https://web.dev/articles/inp", "cls": "https://web.dev/articles/cls",
    "psi-score": "https://developers.google.com/speed/docs/insights/v5/about",
}
URL_RX = re.compile(r"https?://[^\s,)\"'<>]+")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def host_of(u):
    h = (urlparse(u if re.match(r"^https?://", u or "", re.I) else "https://" + (u or "")).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _urls(items, fallback=None):
    out = []
    for it in items or []:
        m = URL_RX.search(it if isinstance(it, str) else json.dumps(it))
        if m and m.group(0) not in out:
            out.append(m.group(0).rstrip(".;:"))
    if not out and fallback:
        out.append(fallback)
    return out


def _issue(**kw):
    base = {"kind": "confirmed", "urls": [], "evidence": {}, "keywords": [], "docs": "", "why": "", "fix": "", "verify": "", "description": ""}
    base.update(kw)
    base["urlCount"] = max(kw.get("urlCount") or 0, len(base["urls"]))
    return base


# ------------------------------------------------------------------ Site Audit

def priority_for_check(c):
    """(priority, reason) for a failed or warned Site Audit check."""
    sev, count, cid = c.get("sev") or "notice", int(c.get("count") or 0), c.get("id")
    if cid in INDEXING_BLOCKERS and c.get("status") == "fail":
        return "critical", "Search engines may not be able to reach or index the audited page, so everything else on it is at risk."
    base = {"critical": "high", "warning": "medium", "notice": "low"}.get(sev, "low")
    label = {"critical": "a serious", "warning": "a moderate", "notice": "a minor"}.get(sev, "a minor")
    reason = f"The Site Audit rates this {label} issue"
    many = RULES["many_urls"]
    if count >= many and base in ("medium", "low"):
        base = "high" if base == "medium" else "medium"
        reason += f", and it affects {count} URLs (the threshold for raising priority is {many})"
    elif count > 1:
        reason += f" on {count} URLs"
    return base, reason + "."


def from_audit(project, audit):
    """Issues from a /api/audit result: every check that failed or warned, with its own explanation."""
    final = audit.get("finalUrl") or audit.get("url") or ""
    kw = audit.get("keyword")
    out = []
    for c in audit.get("checks") or []:
        if c.get("status") not in ("fail", "warn") or c.get("id") in SKIP:
            continue
        cid = c["id"]
        prio, reason = priority_for_check(c)
        sitewide = cid in SITEWIDE
        urls = _urls(c.get("items"), None if sitewide else final)
        out.append(_issue(
            source="site-audit", type=cid, scope=project if sitewide else final,
            category=AUDIT_CATS.get(c.get("cat"), "other"), title=c.get("title") or cid,
            description=c.get("detected") or "", why=c.get("why") or "", fix=c.get("fix") or "",
            verify=c.get("verify") or "Run the Site Audit again and confirm this check passes.",
            priority=prio, priorityReason=reason, urls=urls, urlCount=max(int(c.get("count") or 0), len(urls)),
            evidence={"detected": c.get("detected") or "", "items": (c.get("items") or [])[:25], "count": int(c.get("count") or 0),
                      "severity": c.get("sev"), "check": cid, "auditedUrl": final, "fetchedAt": audit.get("fetchedAt")},
            keywords=[kw] if kw and cid.startswith("kw-") else [], docs=DOCS.get(cid, "")))
    return out


def audit_checked_types(audit):
    """Check ids the audit evaluated, so a task whose check now passes can be marked not detected."""
    return {c["id"] for c in audit.get("checks") or [] if c.get("id")}


# ------------------------------------------------------------------ PageSpeed Insights

PSI_OPPORTUNITIES = {
    "render-blocking-resources": ("Render-blocking resources", "CSS and JavaScript in the page head delay the first paint.",
                                  "Inline the CSS needed for the first screen, defer non-critical CSS, and load scripts with defer or async."),
    "uses-optimized-images": ("Images could be compressed", "Large image files slow down loading, especially on mobile data.",
                              "Compress the listed images (for example to WebP or AVIF at quality 75–85)."),
    "uses-responsive-images": ("Oversized images", "Images are served larger than they're displayed, so visitors download pixels they never see.",
                               "Serve images sized for their display slot with srcset and sizes."),
    "modern-image-formats": ("Images not in modern formats", "WebP and AVIF are usually much smaller than JPEG or PNG.",
                             "Convert the listed images to WebP or AVIF, keeping a fallback if you need one."),
    "offscreen-images": ("Off-screen images load too early", "Images below the fold compete with the content visitors see first.",
                         "Add loading=\"lazy\" to images that start off-screen."),
    "unused-javascript": ("Unused JavaScript", "The browser downloads and parses code this page never runs.",
                          "Remove unused libraries, split bundles per page, and load third-party scripts only where needed."),
    "unused-css-rules": ("Unused CSS", "Stylesheet rules that don't apply to the page still have to download.",
                         "Remove unused CSS or split stylesheets per template."),
    "unminified-javascript": ("JavaScript isn't minified", "Unminified scripts are larger than they need to be.", "Minify JavaScript in your build or CDN."),
    "total-byte-weight": ("Heavy page weight", "A large total download slows loading on slower connections.",
                          "Reduce image, font and script sizes; remove assets the page doesn't need."),
    "bootup-time": ("Excessive JavaScript execution", "Long script execution blocks the main thread and delays interaction.",
                    "Reduce or defer heavy scripts, and audit third-party tags."),
    "server-response-time": ("Slow server response", "A slow first byte delays everything that follows.",
                             "Add page caching or a CDN, and check slow database queries or plugins."),
}
FIELD = {"LARGEST_CONTENTFUL_PAINT_MS": ("lcp", "Largest Contentful Paint (LCP)", "ms"),
         "INTERACTION_TO_NEXT_PAINT": ("inp", "Interaction to Next Paint (INP)", "ms"),
         "CUMULATIVE_LAYOUT_SHIFT_SCORE": ("cls", "Cumulative Layout Shift (CLS)", "")}
LAB = {"largest-contentful-paint": "lcp", "cumulative-layout-shift": "cls"}


def from_pagespeed(project, url, psi, strategy):
    """Issues from a PageSpeed Insights v5 response (Lighthouse lab data plus Chrome field data when Google has it)."""
    lh = psi.get("lighthouseResult") or {}
    audits = lh.get("audits") or {}
    cat = ((lh.get("categories") or {}).get("performance") or {})
    out = []
    dev = "mobile" if strategy == "mobile" else "desktop"
    stamp = lh.get("fetchTime") or now_iso()
    score = cat.get("score")
    if isinstance(score, (int, float)):
        s = round(score * 100)
        if s < RULES["psi_ok"]:
            prio = "high" if s < RULES["psi_poor"] else "medium"
            out.append(_issue(
                source="pagespeed", type=f"psi-score-{dev}", scope=url, category="performance",
                title=f"{'Poor' if prio == 'high' else 'Slow'} {dev} performance", description=f"Lighthouse scored this page {s}/100 on {dev}.",
                why="Slow pages lose visitors and Core Web Vitals are part of Google's page experience signals.",
                fix="Work through the performance tasks for this page, starting with the largest savings.",
                verify="Run PageSpeed again for this URL and confirm the score is 90 or higher.",
                priority=prio, priorityReason=f"A score below {RULES['psi_poor']} is poor; below {RULES['psi_ok']} needs improvement (Lighthouse's own bands).",
                urls=[url], evidence={"score": s, "strategy": dev, "fetchedAt": stamp, "lighthouse": lh.get("lighthouseVersion")}, docs=DOCS["psi-score"]))
    # field data (real Chrome users) is the stronger signal; fall back to lab metrics
    field = ((psi.get("loadingExperience") or {}).get("metrics") or {})
    seen = set()
    for key, (mid, name, unit) in FIELD.items():
        m = field.get(key)
        if not m or m.get("category") not in ("SLOW", "AVERAGE"):
            continue
        seen.add(mid)
        val = m.get("percentile")
        shown = f"{val / 100:.2f}" if mid == "cls" and val is not None else f"{val} {unit}".strip()
        prio = "high" if m["category"] == "SLOW" else "medium"
        out.append(_issue(
            source="pagespeed", type=f"cwv-{mid}-{dev}", scope=url, category="performance",
            title=f"{name} {'is poor' if prio == 'high' else 'needs improvement'} on {dev}",
            description=f"Real Chrome users on {dev} see a 75th-percentile {name} of {shown} ({m['category'].lower().replace('average', 'needs improvement')}).",
            why="Core Web Vitals measure loading, responsiveness and visual stability for real visitors.",
            fix={"lcp": "Speed up the largest element: compress the hero image, preload it, and cut server response time.",
                 "inp": "Break up long JavaScript tasks and reduce work done in click and input handlers.",
                 "cls": "Reserve space for images, ads and embeds with width/height or aspect-ratio, and avoid inserting content above existing content."}[mid],
            verify="Field data updates over 28 days; re-check PageSpeed after the fix has been live for a few weeks.",
            priority=prio, priorityReason=f"Google rates this metric “{m['category'].lower()}” from real-user data.", kind="confirmed",
            urls=[url], evidence={"metric": name, "percentile": val, "category": m["category"], "strategy": dev, "source": "Chrome UX Report", "fetchedAt": stamp},
            docs=DOCS[mid]))
    for aid, mid in LAB.items():
        a = audits.get(aid) or {}
        if mid in seen or not isinstance(a.get("score"), (int, float)) or a["score"] >= 0.9:
            continue
        prio = "medium" if a["score"] < 0.5 else "low"
        out.append(_issue(
            source="pagespeed", type=f"lab-{mid}-{dev}", scope=url, category="performance",
            title=f"{a.get('title') or mid.upper()} is slow in lab tests ({dev})", description=f"Lighthouse measured {a.get('displayValue') or 'a poor value'} on {dev}.",
            why="Lab data shows how the page loads under test conditions; field data wasn't available for this URL.",
            fix="See the related performance tasks for this page for the specific resources to fix.",
            verify="Run PageSpeed again for this URL.", priority=prio,
            priorityReason="Lab measurement only (no real-user data for this URL), so it's ranked one step lower.",
            urls=[url], evidence={"metric": a.get("title"), "value": a.get("displayValue"), "score": a["score"], "strategy": dev, "fetchedAt": stamp}, docs=DOCS[mid]))
    for aid, (title, why, fix) in PSI_OPPORTUNITIES.items():
        a = audits.get(aid) or {}
        d = a.get("details") or {}
        ms, by = d.get("overallSavingsMs") or 0, d.get("overallSavingsBytes") or 0
        if not isinstance(a.get("score"), (int, float)) or a["score"] >= 0.9:
            continue
        if aid not in ("total-byte-weight", "bootup-time", "server-response-time") and ms < RULES["psi_min_savings_ms"] and by < RULES["psi_min_savings_bytes"]:
            continue
        items = [i.get("url") for i in d.get("items") or [] if isinstance(i, dict) and isinstance(i.get("url"), str) and i["url"].startswith("http")]
        prio = "medium" if ms >= 1000 or a["score"] < 0.5 else "low"
        saving = f"{ms:,.0f} ms" if ms else (f"{by / 1024:,.0f} KiB" if by else "")
        out.append(_issue(
            source="pagespeed", type=f"{aid}-{dev}", scope=url, category="performance", title=f"{title} ({dev})",
            description=(a.get("displayValue") or a.get("title") or title) + (f" · estimated saving {saving}" if saving else "") + ".",
            why=why, fix=fix, verify="Run PageSpeed again and confirm this opportunity is gone or much smaller.",
            priority=prio, priorityReason=("Lighthouse estimates a saving of a second or more." if ms >= 1000 else "Lighthouse scores this below 50." if a["score"] < 0.5
                                            else "A real but modest saving, according to Lighthouse."),
            urls=[url] + items[:200], urlCount=1 + len(items),
            evidence={"audit": aid, "savingsMs": ms, "savingsBytes": by, "display": a.get("displayValue"), "resources": items[:25], "strategy": dev, "fetchedAt": stamp}))
    return out


# ------------------------------------------------------------------ Backlink Manager

def _day(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def from_backlinks(project, links, target_status=None):
    """Issues from the team's logged backlinks whose target page is on this project.

    links: Backlink Manager documents. target_status: {target url: HTTP status} from checking each target page.
    Low-quality links become a manual-review task only; nothing is labelled toxic.
    """
    mine = [b for b in links if host_of(b.get("target") or "") == project]
    out = []
    by_target = {}
    for b in mine:
        by_target.setdefault(b.get("target") or "", []).append(b)
    many, strong = RULES["backlinks_many_lost"], RULES["backlinks_strong_da"]
    for target, bs in by_target.items():
        lost = [b for b in bs if b.get("status") == "missing"]
        if lost:
            top = max((b.get("da") or 0 for b in lost), default=0)
            prio = "high" if len(lost) >= many or top >= strong else "medium"
            out.append(_issue(
                source="backlink-manager", type="lost-backlinks", scope=target, category="backlinks",
                title=f"Lost backlinks to {target.replace('https://', '').replace('http://', '')}",
                description=f"{len(lost)} linking page{'s' if len(lost) != 1 else ''} no longer link to this page, according to the last Status checker run.",
                why="Lost links can reduce the authority and referral traffic this page earns.",
                fix="Contact each site to ask for the link back, or check whether the link moved to another of your URLs.",
                verify="Run the Backlink Manager's Status checker again and confirm the links are live.",
                priority=prio, priorityReason=(f"{len(lost)} links lost (threshold {many})" if len(lost) >= many else
                                                f"Includes a link from a site with DA {top:g} (threshold {strong})" if top >= strong else "A few links lost") + ".",
                urls=[b.get("link") for b in lost], evidence={"links": [{"link": b.get("link"), "da": b.get("da"), "checked": b.get("checked"), "campaign": b.get("campaign")} for b in lost[:50]]}))
        dead = [b for b in bs if b.get("status") == "broken"]
        if dead:
            out.append(_issue(
                source="backlink-manager", type="dead-linking-pages", scope=target, category="backlinks",
                title=f"Linking pages no longer load ({len(dead)})", description=f"{len(dead)} page{'s' if len(dead) != 1 else ''} that linked to {target} returned an error on the last check.",
                why="A link on a page that no longer loads passes no value.", fix="Check whether the page moved; ask the site owner to restore it, or replace the link elsewhere.",
                verify="Run the Status checker again.", priority="medium" if len(dead) >= many else "low",
                priorityReason=f"{len(dead)} dead linking page{'s' if len(dead) != 1 else ''}; pages may come back, so this needs a check rather than urgent action.",
                urls=[b.get("link") for b in dead], evidence={"links": [{"link": b.get("link"), "note": b.get("note"), "checked": b.get("checked")} for b in dead[:50]]}))
        poor = [b for b in bs if b.get("quality") == "poor"]
        if poor:
            out.append(_issue(
                source="backlink-manager", type="review-poor-links", scope=target, category="backlinks",
                title=f"Review {len(poor)} low-quality link{'s' if len(poor) != 1 else ''}", description="The Status checker graded these linking pages poor (for example noindex, nofollow, link farm or spam signals).",
                why="Patterns of low-quality links are worth a manual look. A low grade alone doesn't make a link harmful.",
                fix="Open each page and decide: keep it, ask for removal, or (only for clear manipulative patterns) add it to a disavow file.",
                verify="Record your decision in the task notes.", priority="low",
                priorityReason="Needs human judgement; Scoutly never marks links toxic automatically.",
                urls=[b.get("link") for b in poor], evidence={"links": [{"link": b.get("link"), "note": b.get("note")} for b in poor[:50]]}))
        cutoff = RULES["backlinks_unchecked_days"]
        stale = [b for b in bs if not b.get("checked") and _day(b.get("added")) and (datetime.now(timezone.utc) - _day(b.get("added"))).days >= cutoff]
        if stale:
            out.append(_issue(
                source="backlink-manager", type="unchecked-backlinks", scope=target, category="backlinks",
                title=f"Follow up on {len(stale)} unchecked backlink{'s' if len(stale) != 1 else ''}",
                description=f"These links were logged {cutoff}+ days ago and have never been checked.",
                why="Outreach links sometimes never go live or get removed quickly.", fix="Run the Status checker on them, and follow up with the site if a link isn't live.",
                verify="Run the Status checker.", priority="low", priorityReason="Housekeeping: confirms work already done.",
                urls=[b.get("link") for b in stale], evidence={"links": [{"link": b.get("link"), "added": b.get("added"), "by": b.get("by")} for b in stale[:50]]}))
        st = (target_status or {}).get(target)
        if st is not None and (st == 0 or st >= 400):
            out.append(_issue(
                source="backlink-manager", type="broken-backlinked-target", scope=target, category="backlinks",
                title="A page with backlinks is broken", description=f"{target} returned {st or 'no response'} and has {len(bs)} logged backlink{'s' if len(bs) != 1 else ''}.",
                why="Links to a broken page waste their value and send visitors to an error.",
                fix="Restore the page, or 301-redirect it to the closest working page.", verify="Open the URL and confirm it returns 200, then re-scan backlinks.",
                priority="high", priorityReason="Every backlink to this page is currently wasted.",
                urls=[target], evidence={"status": st, "backlinks": len(bs)}))
    return out


# ------------------------------------------------------------------ Search Console import

def expected_ctr(pos):
    """A rough typical organic CTR by position, used only as a comparison point (not a target)."""
    table = [(1, 28), (2, 15), (3, 11), (4, 8), (5, 7), (6, 5), (7, 4), (8, 3.5), (9, 3), (10, 2.5)]
    for p, c in table:
        if pos <= p + 0.5:
            return c
    return 1.5 if pos <= 15 else 1


def _rows(rows):
    out = []
    for r in (rows or [])[:5000]:
        if not isinstance(r, dict) or not isinstance(r.get("key"), str):
            continue
        try:
            out.append({"key": r["key"].strip()[:500], "clicks": float(r.get("clicks") or 0), "impr": float(r.get("impr") or 0),
                        "ctr": float(r.get("ctr") or 0), "pos": float(r.get("pos") or 0)})
        except (TypeError, ValueError):
            continue
    return out


def from_search_console(project, snapshots):
    """Opportunities from imported Search Console exports (Position Tracking's snapshots: {date, pages, queries}).

    These are inferred: a low CTR or a drop is a reason to look, not proof of a problem.
    """
    snaps = sorted([s for s in snapshots or [] if isinstance(s, dict) and re.match(r"^\d{4}-\d{2}-\d{2}$", str(s.get("date") or ""))], key=lambda s: s["date"])
    if not snaps:
        return []
    last = snaps[-1]
    prev = snaps[-2] if len(snaps) > 1 else None
    out = []
    pages = [r for r in _rows(last.get("pages")) if host_of(r["key"]) == project]
    for r in pages:
        if r["impr"] >= RULES["gsc_min_impressions"] and 0 < r["pos"] <= RULES["gsc_max_position"]:
            exp = expected_ctr(r["pos"])
            if r["ctr"] < exp * RULES["gsc_ctr_ratio"]:
                out.append(_issue(
                    source="search-console", type="low-ctr", scope=r["key"], category="search", kind="inferred",
                    title="High impressions, low click-through rate",
                    description=f"{r['impr']:,.0f} impressions at average position {r['pos']:.1f}, but a CTR of {r['ctr']:.1f}% (pages around this position often see about {exp:g}%).",
                    why="The page is being shown but rarely chosen; the title and description may not match what searchers want.",
                    fix="Rewrite the title and meta description to match the main queries for this page, and check whether rich results would help.",
                    verify="Import a new Search Console export in 4 weeks and compare the CTR.",
                    priority="medium", priorityReason=f"Based on your Search Console export from {last['date']}; CTR is under half the typical rate for this position.",
                    urls=[r["key"]], evidence={"export": last["date"], **r, "typicalCtr": exp}))
    # the queries with the most impressions first, so a big export gives a short, useful list
    queries = sorted((r for r in _rows(last.get("queries")) if r["impr"] >= RULES["gsc_query_min_impressions"] and 4 <= r["pos"] <= 15),
                     key=lambda r: -r["impr"])[:int(RULES["gsc_max_query_tasks"])]
    for r in queries:
        out.append(_issue(
            source="search-console", type="query-opportunity", scope=r["key"].lower(), category="search", kind="inferred",
            title=f"Ranking opportunity: “{r['key'][:80]}”",
            description=f"{r['impr']:,.0f} impressions at average position {r['pos']:.1f} ({r['clicks']:,.0f} clicks).",
            why="Queries just off the top results can gain the most clicks from a small improvement.",
            fix="Find the page that ranks for this query, make sure it answers it directly, and link to it from related pages.",
            verify="Compare its position in your next Search Console import.", priority="low",
            priorityReason="An opportunity, not a fault; ranked low until you decide it matters.",
            keywords=[r["key"]], evidence={"export": last["date"], **r}))
    if prev:
        before = {r["key"]: r for r in _rows(prev.get("pages")) if host_of(r["key"]) == project}
        for r in pages:
            b = before.get(r["key"])
            if not b or b["clicks"] < RULES["gsc_drop_min_clicks"]:
                continue
            drop = 1 - (r["clicks"] / b["clicks"]) if b["clicks"] else 0
            if drop >= RULES["gsc_drop_ratio"]:
                out.append(_issue(
                    source="search-console", type="clicks-drop", scope=r["key"], category="search", kind="inferred",
                    title="Clicks dropped for this page",
                    description=f"Clicks fell from {b['clicks']:,.0f} ({prev['date']}) to {r['clicks']:,.0f} ({last['date']}), down {drop * 100:.0f}%. Impressions went from {b['impr']:,.0f} to {r['impr']:,.0f}.",
                    why="A sharp drop can mean lost rankings, a technical problem or a seasonal change.",
                    fix="Check the page still loads and is indexable, compare its positions between the two exports, and look for seasonality before changing content.",
                    verify="Compare the next import.", priority="high" if drop >= 0.6 else "medium",
                    priorityReason=f"Down {drop * 100:.0f}% from at least {RULES['gsc_drop_min_clicks']} clicks between your two latest imports.",
                    urls=[r["key"]], evidence={"before": {**b, "export": prev["date"]}, "after": {**r, "export": last["date"]}}))
    return out
