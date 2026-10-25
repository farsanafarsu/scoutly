"""Content Decay Monitor: pages that are losing traffic or going stale, and what to refresh first.

  * pick(origin, limit, traffic, previous): the pages to check. With Search Console rows, the pages losing the most
    clicks come first, then the pages the last run checked (so runs can be compared); the rest is filled from the sitemap, oldest lastmod first, content pages before
    utility pages (contact, privacy, tag and category archives)
  * check(url): when the page was last updated (dateModified / article:modified_time / a visible
    "Updated" line, falling back to the sitemap's lastmod and the publish date), past years in the title,
    H1 and text ("Best CRMs in 2023"), and how much main text it has
  * score: traffic change (clicks, impressions, position) weighs most, then age and outdated years;
    each page gets a verdict (refresh / review / fresh) with the reasons and what to do

No paid data: traffic comes from the user's own Search Console export (sent by the page as rows).
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

from server import SKIP_EXT, AuditError, _try, decode, fetch, same_site

MAX_PAGES = 100
BUDGET = 50               # seconds for starting new page fetches, inside the hosting proxy's ~100 s limit
WORKERS = 4
MAX_TRAFFIC = 5000        # Search Console rows accepted per request
THIN = 300                # words of main text below which an article looks thin

UTILITY = re.compile(r"/(contact|about|privacy|terms|legal|cookie|login|log-in|signin|sign-in|register|signup|sign-up|account|cart|checkout|"
                     r"basket|search|tag|tags|category|categories|author|authors|feed|wp-json|cdn-cgi|thank-you|thanks)(/|$)|/page/\d+/?$", re.I)
CONTENT = re.compile(r"/(blog|news|article|articles|post|posts|guide|guides|learn|resources|insights|stories|tutorials?|how-to|knowledge|kb|help)/", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _now():
    return datetime.now(timezone.utc)


def _date(s):
    """A date from ISO 8601, an HTTP date or plain text like "March 3, 2024" / "3 Mar 2024"; None if there isn't one."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", s) or re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", s)
        if m:
            a, b, y = m.groups()
            mon, d = (a, b) if not a.isdigit() else (b, a)
            mo, d, y = MONTHS.get(mon[:3].lower()), int(d), int(y)
            if not mo:
                return None
        else:
            try:
                dt = parsedate_to_datetime(s)
                return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, IndexError):
                return None
    try:
        dt = datetime(y, mo, d, tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt if 1995 <= y and dt <= _now() else None


# ------------------------------------------------------------------ which pages

def _sitemap_entries(origin, limit):
    """(url, lastmod) from the site's sitemaps, read quickly: a handful of files, a few seconds."""
    import indexability
    rb = indexability.robots(origin)
    queue = [urljoin(origin + "/", s) for s in rb["sitemaps"]] or [origin + "/sitemap.xml", origin + "/sitemap_index.xml", origin + "/wp-sitemap.xml"]
    out, seen, files, started = [], set(), 0, time.time()
    while queue and files < 10 and len(out) < limit and time.time() - started < 15:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        r, err = indexability._read_sitemap(u)
        if err or not r or r["status"] != 200:
            continue
        files += 1
        xml = decode(r)
        if "<sitemapindex" in xml[:5000].lower():
            # post sitemaps before page, category and tag sitemaps: that's where decaying content lives
            kids = [indexability.LOC.search(e).group(1).strip() for e in indexability.ENTRY["sitemap"].findall(xml) if indexability.LOC.search(e)]
            queue += sorted(kids, key=lambda k: 0 if re.search(r"post|blog|article|news", k, re.I) else 2 if re.search(r"tag|categor|author|archive", k, re.I) else 1)
            continue
        for e in indexability.ENTRY["url"].findall(xml):
            m = indexability.LOC.search(e)
            if m:
                lm = indexability.LASTMOD.search(e)
                out.append((m.group(1).strip().replace("&amp;", "&"), lm.group(1).strip() if lm else ""))
    return out


def _key(u):
    p = urlparse(u)
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host + (p.path.rstrip("/") or "/") + ("?" + p.query if p.query else "")


def _clean_traffic(rows):
    out = {}
    for r in (rows or [])[:MAX_TRAFFIC]:
        if not isinstance(r, dict) or not str(r.get("url") or "").startswith(("http://", "https://")):
            continue

        def num(k):
            try:
                v = float(r.get(k))
                return v if v == v else None
            except (TypeError, ValueError):
                return None
        out[_key(r["url"])] = {"url": str(r["url"])[:2000], "clicks": num("clicks") or 0, "clicksBefore": num("clicksBefore"),
                               "impr": num("impr") or 0, "imprBefore": num("imprBefore"), "pos": num("pos"), "posBefore": num("posBefore")}
    return out


def pick(origin, limit, traffic, previous=()):
    """The pages to check, best candidates for decay first."""
    chosen, seen = [], set()

    def add(u, lastmod=""):
        k = _key(u)
        if k in seen or len(chosen) >= limit:
            return
        seen.add(k)
        chosen.append((u, lastmod))

    if traffic:
        rows = [t for t in traffic.values() if same_site(t["url"], origin)]
        if not rows:
            raise AuditError("None of the Search Console pages are on this website. Check you exported the right property.")

        def loss(t):
            return (t["clicksBefore"] or 0) - t["clicks"]
        # losing pages first, then the pages that bring the most clicks (the most to lose), leaving room for sitemap pages
        for t in sorted(rows, key=loss, reverse=True)[:int(limit * 0.6)]:
            if loss(t) > 0:
                add(t["url"])
        for t in sorted(rows, key=lambda t: -(t["clicks"] + (t["clicksBefore"] or 0)))[:limit]:
            if len(chosen) >= int(limit * 0.8):
                break
            add(t["url"])
    for u in previous or ():
        if isinstance(u, str) and u.startswith(("http://", "https://")) and same_site(u, origin):
            add(u)
    entries = [(u, lm) for u, lm in _sitemap_entries(origin, 5000)
               if same_site(u, origin) and not SKIP_EXT.search(urlparse(u).path) and urlparse(u).path not in ("", "/")]
    if not entries and not chosen:
        r = _try(fetch, origin + "/")
        if r and r["status"] == 200:  # no sitemap: the pages the home page links to
            for h in re.findall(r"<a\s[^>]*href=[\"']([^\"'#]+)", decode(r), re.I):
                u = urljoin(origin + "/", h)
                if same_site(u, origin) and urlparse(u).path not in ("", "/") and not SKIP_EXT.search(urlparse(u).path):
                    entries.append((u.split("#")[0], ""))
    entries.sort(key=lambda e: (bool(UTILITY.search(urlparse(e[0]).path)), not CONTENT.search(urlparse(e[0]).path + "/"), _date(e[1]) or _now()))
    for u, lm in entries:
        add(u, lm)
    lastmods = {_key(u): lm for u, lm in entries}
    return [(u, lm or lastmods.get(_key(u), "")) for u, lm in chosen]


# ------------------------------------------------------------------ one page

LD_DATE = re.compile(r"\"(dateModified|datePublished|uploadDate)\"\s*:\s*\"([^\"]{8,40})\"")
META = re.compile(r"<meta\s[^>]*>", re.I)
ATTR = re.compile(r"([\w:-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
UPDATED_TEXT = re.compile(r"(?:last\s+)?(?:updated|modified|reviewed|refreshed)(?:\s+on)?\s*:?\s*([A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|\d{4}-\d{2}-\d{2})", re.I)
YEAR = re.compile(r"(?<![\d/.-])(20[0-4]\d)(?![\d/.-])")


def _dates(html, text):
    """Modified and published dates found in the page, each with where it came from."""
    mod, pub = [], []
    for kind, val in LD_DATE.findall(html):
        d = _date(val)
        if d:
            (mod if kind == "dateModified" else pub).append((d, "structured data"))
    for tag in META.findall(html[:200000]):
        a = {k.lower(): v.strip("\"'") for k, v in ATTR.findall(tag)}
        name = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
        d = _date(a.get("content") or a.get("datetime"))
        if not d:
            continue
        if name in ("article:modified_time", "og:updated_time", "last-modified", "datemodified", "dc.date.modified", "modified"):
            mod.append((d, "meta tag"))
        elif name in ("article:published_time", "datepublished", "dc.date", "dc.date.issued", "date", "pubdate", "publish-date"):
            pub.append((d, "meta tag"))
    for m in re.finditer(r"itemprop=[\"'](dateModified|datePublished)[\"'][^>]*(?:content|datetime)=[\"']([^\"']+)", html[:300000], re.I):
        d = _date(m.group(2))
        if d:
            (mod if m.group(1).lower() == "datemodified" else pub).append((d, "page markup"))
    m = UPDATED_TEXT.search(text[:20000])
    if m and _date(m.group(1)):
        mod.append((_date(m.group(1)), "page text"))
    if not pub:
        m = re.search(r"<time[^>]+datetime=[\"']([^\"']+)", html[:300000], re.I)
        if m and _date(m.group(1)):
            pub.append((_date(m.group(1)), "page markup"))
    return (max(mod) if mod else None), (min(pub) if pub else None)


def check(url, lastmod="", traffic=None):
    import internallinks
    now = _now()
    out = {"url": url, "final": url, "status": 0, "title": "", "h1": "", "words": 0, "updated": None, "updatedFrom": "", "published": None,
           "ageDays": None, "titleYear": None, "oldYears": [], "currentYear": False, "traffic": traffic, "score": 0, "verdict": "fresh",
           "reasons": [], "actions": []}
    r = _try(fetch, url)
    if not r or r["status"] in (429, 503):  # no answer or rate limited: wait a moment and ask once more
        time.sleep(2)
        r = _try(fetch, url) or r
    if not r:
        out.update(verdict="unchecked", reasons=["The page didn't answer in time, so it couldn't be checked. Try again later."])
        return out
    out.update(status=r["status"], final=r["url"])
    if r["status"] >= 400:
        out.update(verdict="error", score=100 if traffic else 0)
        if traffic and traffic.get("clicksBefore") is not None:
            traffic["lost"] = round(traffic["clicksBefore"] - traffic["clicks"])
            traffic["pct"] = round(traffic["lost"] / traffic["clicksBefore"] * 100) if traffic["clicksBefore"] else 0
        out["reasons"].append(f"Returns HTTP {r['status']}" + (": the page is gone." if r["status"] in (404, 410) else "."))
        out["actions"].append("Restore the page, or 301-redirect it to the closest live page so its links and rankings carry over.")
        return out
    if "html" not in r["headers"].get("content-type", "html"):
        out.update(verdict="skip", reasons=["Not an HTML page."])
        return out
    html = decode(r)
    P = internallinks._Page(r["url"])
    try:
        P.feed(html)
        P.close()
    except Exception:
        pass
    main = [b for b in P.blocks if not b["boiler"]]
    text = " ".join("".join(t for t, _ in b["runs"]) for b in main)
    out.update(title=P.title[:300], h1=P.h1[:300], words=len(re.findall(r"[^\W_]+", text)))
    mod, pub = _dates(html, text)
    sm = _date(lastmod)
    hdr = _date(r["headers"].get("last-modified", ""))
    if mod:
        out["updated"], out["updatedFrom"] = mod[0], mod[1]
    elif sm and (not pub or sm >= pub[0]) and (now - sm).days > 1:  # sitemaps rebuilt on every request say "today" for every page
        out["updated"], out["updatedFrom"] = sm, "sitemap"
    elif pub:
        out["updated"], out["updatedFrom"] = pub[0], "publish date"
    elif hdr and (now - hdr).days > 1:
        out["updated"], out["updatedFrom"] = hdr, "server header"
    if pub:
        out["published"] = pub[0].date().isoformat()
    if out["updated"]:
        out["ageDays"] = (now - out["updated"]).days
        out["updated"] = out["updated"].date().isoformat()

    # past years where a reader would notice: the title and H1 ("… in 2023"), and the main text
    year = now.year
    head_years = [int(y) for y in YEAR.findall(P.title + " " + P.h1)]
    if head_years and max(head_years) < year and max(head_years) >= year - 6:
        out["titleYear"] = max(head_years)
    body_years = [int(y) for y in YEAR.findall(text)]
    out["currentYear"] = any(y >= year for y in body_years + head_years)
    old = sorted({y for y in body_years if year - 6 <= y <= year - 2})
    out["oldYears"] = old if len([y for y in body_years if y in old]) >= 2 else []

    _score(out, now)
    return out


def _score(p, now):
    s, why, todo = 0, p["reasons"], p["actions"]
    t = p["traffic"]
    if t and t.get("clicksBefore") is not None:
        before, nowc = t["clicksBefore"], t["clicks"]
        lost = before - nowc
        pct = lost / before * 100 if before else 0
        t["lost"], t["pct"] = round(lost), round(pct)
        if lost >= 5 and pct >= 15:
            s += min(55, 15 + pct * 0.4 + min(15, lost / 20))
            why.append(f"Clicks fell {round(pct)}% ({round(before):,} → {round(nowc):,}).")
        elif before >= 10 and nowc == 0:
            s += 50
            why.append(f"Clicks fell from {round(before):,} to none.")
        if t.get("posBefore") and t.get("pos") and t["pos"] - t["posBefore"] >= 2:
            s += min(20, (t["pos"] - t["posBefore"]) * 3)
            why.append(f"Average position slipped from {t['posBefore']:.1f} to {t['pos']:.1f}.")
            todo.append("Search the page's main query and compare it with the pages now ranking above it: what do they cover that this page doesn't?")
        elif t.get("imprBefore") and t["imprBefore"] >= 50 and (t["imprBefore"] - t["impr"]) / t["imprBefore"] >= 0.3 and lost >= 5:
            why.append(f"Impressions fell {round((t['imprBefore'] - t['impr']) / t['imprBefore'] * 100)}%: it shows up in search less often, not just gets fewer clicks.")
            s += 8
        elif lost >= 5 and pct >= 15 and t.get("imprBefore") and t["impr"] >= t["imprBefore"] * 0.9:
            why.append("Impressions held up, so it still ranks but fewer people click: the title and description may look dated next to newer results.")
            todo.append("Rewrite the title and meta description to make the result more compelling (and current).")
    age = p["ageDays"]
    if age is not None:
        if age >= 730:
            s += 25
            why.append(f"Last updated {age // 365} years ago ({p['updated']}, from the {p['updatedFrom']}).")
        elif age >= 365:
            s += 15
            why.append(f"Last updated over a year ago ({p['updated']}, from the {p['updatedFrom']}).")
        elif age >= 180:
            s += 5
    if p["titleYear"]:
        s += 25
        why.append(f"The title or H1 mentions {p['titleYear']}, which tells searchers it's out of date.")
        todo.append(f"Update the content for {now.year}, then change the year in the title and H1 (and the URL only if it doesn't contain the year; otherwise leave the URL alone).")
    if p["oldYears"] and not p["currentYear"]:
        s += 10
        why.append("The text refers to " + ", ".join(map(str, p["oldYears"])) + f" but never to {now.year - 1} or {now.year}.")
        todo.append("Refresh statistics, prices, screenshots and examples that point to past years.")
    if p["words"] and p["words"] < THIN and CONTENT.search(urlparse(p["final"]).path + "/"):
        s += 5
        why.append(f"Only about {p['words']} words of main text.")
        todo.append("Expand it: answer the questions people search for around this topic, with examples.")
    if age is not None and age >= 365:
        todo.append("Review every section for accuracy, add what's changed since it was written, and fix broken or outdated links.")
        todo.append("Update the dateModified in the structured data (and a visible “Updated” date) once the content has really changed.")
    if age is None and not t:
        why.append("No updated or published date found on the page.")
        todo.append("Show a visible “Updated” date and add datePublished / dateModified to the Article structured data.")
    if s and t and t.get("lost", 0) > 0:
        todo.append("Add internal links to it from newer, related pages (Internal Link Opportunities can find them).")
    p["score"] = int(min(100, round(s)))
    p["verdict"] = "refresh" if p["score"] >= 45 else "review" if p["score"] >= 20 else "fresh"
    seen = set()
    p["actions"] = [a for a in todo if not (a in seen or seen.add(a))]


def run(site, limit=50, traffic=None, previous=None):
    site = (site or "").strip()
    if not site:
        raise AuditError("Enter your website.")
    if not re.match(r"^https?://", site, re.I):
        site = "https://" + site
    p = urlparse(site)
    if not p.hostname:
        raise AuditError("That doesn't look like a web address.")
    limit = max(10, min(MAX_PAGES, int(limit) if str(limit).isdigit() else 50))
    try:
        home = fetch(f"{p.scheme}://{p.netloc}/", want_body=False)
    except AuditError as e:
        raise AuditError(f"Couldn't reach {p.hostname}. {e}")
    f = urlparse(home["url"])
    origin = f"{f.scheme}://{f.netloc}"
    tr = _clean_traffic(traffic)
    targets = pick(origin, limit, tr, (previous or [])[:MAX_PAGES])
    if not targets:
        raise AuditError("Couldn't find any pages to check: there's no sitemap and the home page has no links. Add a Search Console export to check the pages it lists.")
    started, pages = time.time(), []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = []
        for u, lm in targets:
            futs.append(pool.submit(lambda u=u, lm=lm: check(u, lm, tr.get(_key(u))) if time.time() - started < BUDGET else None))
        for fu in futs:
            r = fu.result()
            if r:
                pages.append(r)
    pages = [x for x in pages if x["verdict"] != "skip"]
    order = {"error": 0, "refresh": 1, "review": 2, "fresh": 3, "unchecked": 4}
    pages.sort(key=lambda x: (order[x["verdict"]], -x["score"], -((x["traffic"] or {}).get("lost") or 0)))
    ages = sorted(x["ageDays"] for x in pages if x["ageDays"] is not None)
    return {"origin": origin, "checked": len(pages), "planned": len(targets), "withTraffic": bool(tr), "year": _now().year,
            "medianAge": ages[len(ages) // 2] if ages else None, "dated": len(ages), "seconds": round(time.time() - started), "pages": pages}
