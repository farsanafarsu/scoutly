"""Indexability & Sitemap Checker: can Google crawl and index these URLs, and is the sitemap healthy?

  * sitemaps(site): robots.txt (rules for Googlebot, Sitemap: lines) and every XML sitemap it can find,
    following sitemap indexes, with the URLs they list and the problems in each file
  * check(urls): per URL, the HTTP status and redirects, whether robots.txt blocks Googlebot, meta robots /
    X-Robots-Tag directives and the canonical, rolled up into an indexable / not indexable verdict

robots.txt is matched the way Google documents it (most specific user-agent group, longest matching rule,
Allow wins ties, * and $ wildcards), which Python's urllib.robotparser doesn't do.
"""
import gzip
import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from server import BLOCK_MARKERS, AuditError, _try, decode, fetch, norm_url, parse

MAX_FILES = 60            # sitemap files read per site (indexes included)
MAX_LIST = 5000           # URLs sent back to the page (all of them are counted)
MAX_COUNT = 500000        # stop counting after this many
MAX_CHECK = 20            # URLs per check() call; the page sends batches
SM_LIMIT_URLS = 50000     # sitemaps.org limits per file
SM_LIMIT_BYTES = 50 * 1024 * 1024
SM_TIMEOUT = 30           # big sitemaps are slow to generate
BUDGET = 40               # seconds for starting new sitemap reads, so a scan stays inside the hosting proxy's ~100 s limit

_robots_cache, _lock = {}, threading.Lock()


# ------------------------------------------------------------------ robots.txt (Google's rules)

def parse_robots(txt):
    """Groups of (user agents, [(allow, pattern)]) plus Sitemap: lines."""
    groups, sitemaps, cur, in_agents = [], [], None, False
    for raw in txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not in_agents:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
            cur["agents"].append(val.lower())
            in_agents = True
        elif key in ("allow", "disallow") and cur is not None:
            cur["rules"].append((key == "allow", val))
            in_agents = False
        elif key == "sitemap":
            sitemaps.append(val)
        else:
            in_agents = False
    return groups, sitemaps


def rules_for(groups, agent="googlebot"):
    """Rules of the most specific matching group (all groups naming it are merged), else the * group."""
    names = {a for g in groups for a in g["agents"]}
    best = max((a for a in names if a != "*" and agent.startswith(a)), key=len, default=None)
    pick = best or ("*" if "*" in names else None)
    if not pick:
        return None, []
    return pick, [r for g in groups if pick in g["agents"] for r in g["rules"]]


def _pattern(p):
    rx = re.escape(p).replace(r"\*", ".*")
    return re.compile(rx[:-2] + "$" if rx.endswith(r"\$") else rx)


def allowed(rules, url):
    """(allowed, matching rule) for a URL's path and query; the longest pattern wins, Allow wins ties."""
    u = urlparse(url)
    path = (u.path or "/") + ("?" + u.query if u.query else "")
    best = None
    for allow, pat in rules:
        if not pat:
            continue  # an empty Disallow allows everything
        if _pattern(pat).match(path):
            if best is None or len(pat) > len(best[1]) or (len(pat) == len(best[1]) and allow and not best[0]):
                best = (allow, pat)
    if best is None:
        return True, None
    return best[0], ("Allow: " if best[0] else "Disallow: ") + best[1]


def robots(origin):
    """robots.txt for an origin, cached for ten minutes."""
    with _lock:
        hit = _robots_cache.get(origin)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
    r = _try(fetch, origin + "/robots.txt")
    status = r["status"] if r else 0
    txt = decode(r) if r and status == 200 and b"<html" not in r["body"][:500].lower() else ""
    groups, sitemaps = parse_robots(txt)
    agent, rules = rules_for(groups)
    out = {"url": origin + "/robots.txt", "status": status, "exists": bool(txt), "text": txt[:20000], "sitemaps": sitemaps,
           "agent": agent, "rules": rules, "groups": len(groups),
           # Google: 4xx means no restrictions; 5xx or no answer means it stops crawling the site for now
           "blocksAll": status >= 500 or status == 0}
    with _lock:
        _robots_cache[origin] = (time.time(), out)
    return out


# ------------------------------------------------------------------ sitemaps

LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S | re.I)
LASTMOD = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.S | re.I)
ENTRY = {"url": re.compile(r"<url[\s>].*?</url>", re.S | re.I), "sitemap": re.compile(r"<sitemap[\s>].*?</sitemap>", re.S | re.I)}
W3C_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2}))?)?)?$")


def _origin(site):
    site = (site or "").strip()
    if not site:
        raise AuditError("Enter a website or page address.")
    if not re.match(r"^https?://", site, re.I):
        site = "https://" + site
    p = urlparse(site)
    if not p.hostname:
        raise AuditError("That doesn't look like a web address.")
    # follow redirects on the home page so http:// or a bare domain lands on the real origin
    home = _try(fetch, f"{p.scheme}://{p.netloc}/", "GET", 6, False)
    if home:
        f = urlparse(home["url"])
        return f"{f.scheme}://{f.netloc}", site
    raise AuditError(f"Couldn't reach {p.hostname}. Check the address, or try again if the site is slow.")


def _read_sitemap(url):
    r = _try(fetch, url, "GET", 6, True, SM_TIMEOUT)
    if r and r["status"] in (429, 503):  # rate limited: wait a moment and ask once more
        time.sleep(2)
        r = _try(fetch, url, "GET", 6, True, SM_TIMEOUT) or r
    if not r:
        return None, f"Couldn't download it (no answer within {SM_TIMEOUT} seconds)."
    body = r["body"]
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:
            return r, "It's a broken .gz file."
        r = {**r, "body": body}
    return r, None


def sitemaps(site):
    origin, entered = _origin(site)
    host = urlparse(origin).hostname
    rb = robots(origin)
    listed = [urljoin(origin + "/", s) for s in rb["sitemaps"]]
    queue = [(u, "robots.txt", 0) for u in dict.fromkeys(listed)]
    if not queue:
        queue = [(origin + p, "guessed", 0) for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap.xml.gz")]
    files, seen, urls, counted, issues = [], set(), {}, 0, []
    dup = cross = badmod = 0
    guessed_hit, started = False, time.time()
    while queue and len(files) < MAX_FILES and time.time() - started < BUDGET:
        batch, queue = queue[:4], queue[4:]
        batch = [b for b in batch if norm_url(b[0]) not in seen]
        for b in batch:
            seen.add(norm_url(b[0]))
        with ThreadPoolExecutor(max_workers=4) as pool:
            got = list(pool.map(lambda b: _read_sitemap(b[0]), batch))
        for (url, via, depth), (r, err) in zip(batch, got):
            if via == "guessed" and (not r or r["status"] != 200):
                continue  # a missing guess isn't a problem; only report sitemaps that exist or are listed
            f = {"url": url, "via": via, "status": r["status"] if r else 0, "kind": "", "urls": 0, "children": 0, "lastmod": "", "problems": []}
            files.append(f)
            if err or not r:
                f["problems"].append(err or "Couldn't download it.")
                continue
            if r["url"] != url:
                f["problems"].append(f"Redirects to {r['url']}. List the final address in robots.txt.")
            if r["status"] != 200:
                f["problems"].append(f"Returns HTTP {r['status']}, so search engines can't read it.")
                continue
            xml = decode(r)
            head = xml[:1000].lower()
            if "<urlset" not in xml[:5000].lower() and "<sitemapindex" not in xml[:5000].lower():
                f["problems"].append("Isn't an XML sitemap" + (" (it returns an HTML page)." if "<html" in head else "."))
                continue
            if via == "guessed":
                guessed_hit = True
            if r.get("truncated"):
                f["problems"].append("Is bigger than 4 MB, so Scoutly only read the start of it; counts for this file are partial.")
            is_index = "<sitemapindex" in xml[:5000].lower()
            f["kind"] = "index" if is_index else "urlset"
            entries = ENTRY["sitemap" if is_index else "url"].findall(xml)
            mods = []
            for e in entries:
                m = LOC.search(e)
                if not m:
                    continue
                loc = html.unescape(m.group(1).strip())
                lm = LASTMOD.search(e)
                lm = lm.group(1).strip() if lm else ""
                if lm:
                    if W3C_DATE.match(lm):
                        mods.append(lm)
                    else:
                        badmod += 1
                if is_index:
                    f["children"] += 1
                    if depth < 3:
                        queue.append((urljoin(url, loc), "index", depth + 1))
                    continue
                f["urls"] += 1
                if (urlparse(loc).hostname or "").lower() != host.lower():
                    cross += 1
                    continue
                key = norm_url(loc)
                if key in urls:
                    dup += 1
                    continue
                counted += 1
                if len(urls) < MAX_LIST:
                    urls[key] = {"url": loc, "lastmod": lm[:10] if lm else ""}
                if counted >= MAX_COUNT:
                    break
            f["lastmod"] = max(mods)[:10] if mods else ""
            if not is_index and f["urls"] > SM_LIMIT_URLS:
                f["problems"].append(f"Lists {f['urls']:,} URLs; the limit is 50,000 per file. Split it and use a sitemap index.")
            if not entries:
                f["problems"].append("Is empty: it lists no URLs.")
            if not is_index and entries and not mods:
                f["problems"].append("Has no <lastmod> dates, so search engines can't tell which pages changed.")
    if queue:
        issues.append({"sev": "notice", "text": f"Only the first {len(files)} sitemap files were read" +
                       (" (the rest took too long)" if len(files) < MAX_FILES else "") + f"; {len(queue)} more weren't. URL counts cover the files read."})
    found = [f for f in files if f["kind"]]
    if not found:
        issues.append({"sev": "warning", "text": "No XML sitemap found" + (" at the addresses listed in robots.txt." if listed else
                       " in robots.txt, at /sitemap.xml, /sitemap_index.xml or /wp-sitemap.xml.") +
                       " A sitemap helps search engines find new pages and pages with few links."})
    elif guessed_hit and not listed:
        issues.append({"sev": "notice", "text": f"robots.txt doesn't list the sitemap. Add “Sitemap: {found[0]['url']}” so every crawler finds it."})
    if any(f["problems"] for f in files):
        issues.append({"sev": "warning", "text": f"{sum(1 for f in files if f['problems'])} sitemap file{'s have' if sum(1 for f in files if f['problems']) != 1 else ' has'} problems (see the Sitemaps table)."})
    if cross:
        issues.append({"sev": "warning", "text": f"{cross:,} sitemap URL{'s are' if cross != 1 else ' is'} on another host (www vs non-www, or another domain). Search engines ignore URLs that don't match the sitemap's host."})
    if dup:
        issues.append({"sev": "notice", "text": f"{dup:,} URL{'s are' if dup != 1 else ' is'} listed more than once."})
    if badmod:
        issues.append({"sev": "notice", "text": f"{badmod:,} <lastmod> date{'s aren’t' if badmod != 1 else ' isn’t'} in W3C format (e.g. 2026-10-25), so they may be ignored."})
    if rb["blocksAll"]:
        issues.append({"sev": "critical", "text": f"robots.txt {'returns HTTP ' + str(rb['status']) if rb['status'] else 'didn’t answer'}. When robots.txt fails like this, Google stops crawling the whole site until it works again."})
    elif not rb["exists"]:
        issues.append({"sev": "notice", "text": "No robots.txt file. Crawlers treat everything as allowed, which is fine, but it's the standard place to list your sitemap."})
    elif any(not a and p in ("/", "/*") for a, p in rb["rules"]) and not any(a and p in ("/", "/*") for a, p in rb["rules"]):
        issues.append({"sev": "critical", "text": "robots.txt blocks Googlebot from the whole site (Disallow: /)."})
    return {"origin": origin, "entered": entered, "robots": rb, "files": files, "total": counted, "urls": list(urls.values()),
            "truncated": counted > len(urls), "issues": issues}


# ------------------------------------------------------------------ one URL

def _check(url):
    out = {"url": url, "status": 0, "final": url, "chain": [], "robots": None, "rule": None, "meta": "", "header": "",
           "canonical": None, "canonState": "missing", "title": "", "ms": 0, "type": "", "verdict": "indexable", "reasons": []}

    def no(t):
        out["verdict"] = "blocked"
        out["reasons"].append(t)

    def warn(t):
        if out["verdict"] == "indexable":
            out["verdict"] = "warning"
        out["reasons"].append(t)

    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        no("Not a valid http(s) address.")
        return out
    rb = robots(f"{p.scheme}://{p.netloc}")
    ok, rule = (False, "robots.txt unavailable") if rb["blocksAll"] else allowed(rb["rules"], url)
    out["robots"], out["rule"] = ok, rule
    try:
        r = fetch(url)
        if r["status"] == 429:  # rate limited: wait a moment and ask once more
            time.sleep(3)
            r = fetch(url)
    except AuditError as e:
        no(str(e))
        return out
    if r["status"] == 429:
        out.update(status=429, final=r["url"], ms=r["ttfb"], verdict="unchecked")
        out["reasons"].append("The site rate-limited Scoutly (HTTP 429), so this URL couldn't be checked. That's about Scoutly's requests, not Google; try again in a minute or check fewer URLs.")
        return out
    out.update(status=r["status"], final=r["url"], chain=[{"url": c["url"], "status": c["status"]} for c in r["chain"]], ms=r["ttfb"])
    hdr = r["headers"]
    out["type"] = hdr.get("content-type", "").split(";")[0].strip()
    out["header"] = hdr.get("x-robots-tag", "")
    if not ok:
        no("robots.txt stops Google from crawling it" + (f" ({rule})." if rule and rule != "robots.txt unavailable" else "."))
    if r["chain"]:
        no(f"Redirects ({r['chain'][0]['status']}) to {r['url']}. Google indexes the final URL instead; link and list that one.")
    if r["status"] >= 400:
        no(f"Returns HTTP {r['status']}" + (": the page is missing." if r["status"] in (404, 410) else ", so it can't be indexed."))
    elif r["status"] != 200 and not r["chain"]:
        warn(f"Returns HTTP {r['status']} instead of 200.")
    is_html = "html" in out["type"] or (not out["type"] and b"<html" in r["body"][:2000].lower())
    P = None
    if is_html and r["body"]:
        text = decode(r)
        P = parse(text)
        out["title"] = (P.title or "")[:200]
        out["meta"] = ", ".join(filter(None, [P.meta("robots"), P.meta("googlebot")]))
        if r["status"] in (202, 403, 429, 503) and (len(P.words) < 30 or BLOCK_MARKERS.search((P.title or "") + " " + text[:20000])):
            warn("Answered Scoutly with a bot check, so the page itself couldn't be read. Google may see it differently; check it in Search Console.")
    elif r["status"] == 200 and not is_html:
        warn(f"Not an HTML page ({out['type'] or 'unknown type'}). Google can index some files (like PDFs), but they can't carry meta robots or canonical tags.")
    directives = (out["meta"] + "," + out["header"]).lower()
    if "noindex" in directives or re.search(r"(^|[,\s])none([,\s]|$)", directives):
        no("Has a noindex directive in the " + ("meta robots tag." if re.search(r"noindex|none", out["meta"].lower()) else "X-Robots-Tag header."))
        if not ok:
            out["reasons"].append("Because robots.txt blocks it, Google can't see that noindex. Unblock it so the noindex can take effect.")
    if "nofollow" in directives:
        warn("Has nofollow: Google won't follow the links on this page.")
    if P is not None:
        canon = P.rel("canonical")
        if not canon and hdr.get("link") and 'rel="canonical"' in hdr.get("link", "").lower():
            m = re.search(r"<([^>]+)>\s*;\s*rel=\"?canonical", hdr["link"], re.I)
            canon = [{"href": m.group(1)}] if m else []
        if len(canon) > 1 and len({norm_url(urljoin(r["url"], c.get("href", ""))) for c in canon}) > 1:
            out["canonState"] = "multiple"
            out["canonical"] = urljoin(r["url"], canon[0].get("href", ""))
            warn(f"Has {len(canon)} different canonical tags, so Google may ignore them all.")
        elif canon:
            out["canonical"] = urljoin(r["url"], canon[0].get("href", ""))
            if norm_url(out["canonical"]) == norm_url(r["url"]):
                out["canonState"] = "self"
            else:
                out["canonState"] = "other"
                warn(f"Its canonical points to {out['canonical']}, so Google will most likely index that URL instead.")
        elif r["status"] == 200:
            out["reasons"].append("Has no canonical tag. Usually fine, but a self-referencing canonical stops duplicates (tracking parameters, www vs non-www) from competing.")
    return out


def check(urls):
    urls = [str(u).strip() for u in (urls or []) if str(u).strip()]
    if not urls:
        raise AuditError("Add at least one URL to check.")
    urls = [u if re.match(r"^https?://", u, re.I) else "https://" + u for u in urls[:MAX_CHECK]]
    with ThreadPoolExecutor(max_workers=4) as pool:  # gentle: many sites rate-limit bursts of requests
        return {"items": list(pool.map(_check, urls))}
