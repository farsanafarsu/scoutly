#!/usr/bin/env python3
"""Scoutly local server.

Serves the static site and runs real SEO audits at /api/audit?url=...&kw=...

    python server.py            # http://localhost:8000
    python server.py 9000       # another port

Standard library only. The audit fetches the page you give it, reads robots.txt and
the XML sitemap, crawls a limited number of internal pages (respecting robots.txt),
checks links, and returns every finding as JSON for audit.html to render.
Core Web Vitals come from the PageSpeed Insights API, which audit.html calls directly.
"""
import gzip
import html
import ipaddress
import json
import os
import re
import socket
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib import robotparser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = os.path.dirname(os.path.abspath(__file__))
# Many sites (Amazon, Cloudflare-protected shops) refuse or stall unknown bots, so pages are
# requested like a normal Chrome browser. robots.txt rules are still read for ScoutlyBot and "*".
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
BOT = "ScoutlyBot"
TIMEOUT = 12
PAGE_TIMEOUT = 25   # the start page gets longer: slow sites are exactly the ones worth auditing
MAX_BYTES = 4 * 1024 * 1024
MAX_PAGES = 25          # pages crawled per audit, including the start page
MAX_DEPTH = 3
MAX_LINK_CHECKS = 120   # unique links whose status is checked
ALLOW_PRIVATE = os.environ.get("SCOUTLY_ALLOW_PRIVATE") == "1"  # lets you audit localhost while testing
# Google's keyless PageSpeed quota is shared and often used up. A free key from
# https://developers.google.com/speed/docs/insights/v5/get-started goes in this env var.
PSI_KEY = os.environ.get("PAGESPEED_API_KEY", "").strip()
AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "PerplexityBot", "Google-Extended", "CCBot"]
AI_SEARCH_BOTS = {"OAI-SearchBot", "PerplexityBot", "Claude-SearchBot"}
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|pdf|zip|rar|gz|mp4|webm|mp3|wav|css|js|json|xml|txt|woff2?|ttf|eot|docx?|xlsx?|pptx?)$", re.I)
GENERIC_ANCHORS = {"click here", "here", "read more", "more", "learn more", "link", "this", "go", "continue", "details"}


# ---------------------------------------------------------------- fetching

class AuditError(Exception):
    pass


def safe_host(host):
    """Refuse hosts that resolve to private, loopback or reserved addresses (SSRF guard)."""
    if ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise AuditError(f"Couldn't find the website “{host}”. Check the address for typos.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise AuditError("That address points to a private or local network, which the audit doesn't scan.")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = build_opener(_NoRedirect)


def to_ascii_url(url):
    """Percent-encode non-ASCII paths and IDNA-encode hosts (urllib can't send raw Unicode URLs)."""
    p = urlparse(url)
    host = p.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    netloc = host + (f":{p.port}" if p.port else "")
    if p.username:
        netloc = p.username + "@" + netloc
    return urlunparse((p.scheme, netloc, quote(p.path, safe="/%:@!$&'()*+,;=-._~"),
                       p.params, quote(p.query, safe="=&%+/:,;@!$'()*-._~?"), ""))


BLOCK_MARKERS = re.compile(r"captcha|cf-chl|challenge-platform|just a moment\.\.\.|attention required|access denied|robot check|"
                           r"automated access|are you a robot|verify you are human|unusual traffic|request unsuccessful|incapsula|perimeterx|px-captcha", re.I)


def fetch(url, method="GET", max_hops=6, want_body=True, timeout=TIMEOUT):
    """Fetch a URL following redirects by hand, so every hop is checked and recorded."""
    chain = []
    started = time.perf_counter()
    for _ in range(max_hops):
        url = to_ascii_url(url)
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise AuditError("Only http and https web addresses can be audited.")
        safe_host(p.hostname)
        req = Request(url, method=method, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
        })
        t0 = time.perf_counter()
        try:
            resp = _opener.open(req, timeout=timeout)
            status = resp.status
        except HTTPError as e:
            resp = e
            status = e.code
        except (URLError, socket.timeout, ConnectionError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise AuditError(f"Couldn't connect to {p.hostname}: {reason}")
        ttfb = round((time.perf_counter() - t0) * 1000)
        headers = {k.lower(): v for k, v in resp.headers.items()}
        if status in (301, 302, 303, 307, 308) and headers.get("location"):
            chain.append({"url": url, "status": status})
            try:
                resp.close()
            except Exception:
                pass
            url = urljoin(url, headers["location"])
            continue
        body = b""
        if want_body and method != "HEAD":
            try:
                body = resp.read(MAX_BYTES + 1)
            except Exception:
                body = b""
        try:
            resp.close()
        except Exception:
            pass
        raw_len = len(body)
        enc = headers.get("content-encoding", "").lower()
        try:
            if enc == "gzip":
                body = gzip.decompress(body)
            elif enc == "deflate":
                body = zlib.decompress(body)
        except Exception:
            pass
        return {
            "url": url, "status": status, "headers": headers, "body": body, "chain": chain,
            "ttfb": ttfb, "ms": round((time.perf_counter() - started) * 1000),
            "transfer_bytes": raw_len, "truncated": raw_len > MAX_BYTES,
        }
    raise AuditError("Too many redirects (possible redirect loop).")


def decode(res):
    ctype = res["headers"].get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    charset = m.group(1) if m else None
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', res["body"][:4096], re.I)
        charset = m.group(1).decode() if m else "utf-8"
    try:
        return res["body"].decode(charset, errors="replace")
    except LookupError:
        return res["body"].decode("utf-8", errors="replace")


def link_status(url):
    """Status of a link: HEAD first, GET when servers refuse HEAD."""
    try:
        r = fetch(url, method="HEAD", want_body=False)
        if r["status"] in (403, 405, 501, 400):
            r = fetch(url, method="GET", want_body=False)
        return {"status": r["status"], "final": r["url"], "hops": len(r["chain"])}
    except AuditError as e:
        return {"status": 0, "final": url, "hops": 0, "error": str(e)}


# ---------------------------------------------------------------- parsing

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.titles = 0
        self.metas = []
        self.links = []          # <link>
        self.anchors = []        # <a>
        self.images = []
        self.scripts = []
        self.jsonld = []
        self.headings = []       # (level, text)
        self.lang = None
        self.has_charset = False
        self.text = []
        self.iframes = 0
        self.lists = 0
        self.tables = 0
        self.microdata = 0
        self.rdfa = 0
        self.stylesheets_head = 0
        self._stack = []
        self._in_head = False
        self._skip = 0
        self._cur_a = None
        self._cur_h = None
        self._cur_title = None
        self._cur_jsonld = None
        self._svg = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if "itemscope" in a:
            self.microdata += 1
        if "typeof" in a:
            self.rdfa += 1
        if tag == "html":
            self.lang = a.get("lang") or None
        elif tag == "head":
            self._in_head = True
        elif tag == "body":
            self._in_head = False
        elif tag == "title" and self._cur_title is None and not self._svg:
            self._cur_title = []
            self.titles += 1
        elif tag == "meta":
            if "charset" in a or a.get("http-equiv", "").lower() == "content-type":
                self.has_charset = True
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
            if "stylesheet" in a.get("rel", "").lower() and self._in_head:
                self.stylesheets_head += 1
        elif tag == "a":
            self._cur_a = {"href": a.get("href", ""), "rel": a.get("rel", "").lower(), "text": [], "title": a.get("title", ""), "img_alt": ""}
        elif tag == "img":
            self.images.append(a)
            if self._cur_a is not None and a.get("alt"):
                self._cur_a["img_alt"] += a.get("alt")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._cur_h = [int(tag[1]), []]
        elif tag == "script":
            self.scripts.append({**a, "in_head": self._in_head})
            if a.get("type", "").lower() == "application/ld+json":
                self._cur_jsonld = []
            self._skip += 1
        elif tag in ("style", "noscript", "template", "svg"):
            self._skip += 1
            if tag == "svg":
                self._svg += 1
        elif tag == "iframe":
            self.iframes += 1
        elif tag in ("ul", "ol"):
            self.lists += 1
        elif tag == "table":
            self.tables += 1

    def handle_endtag(self, tag):
        if tag == "title" and self._cur_title is not None:
            if self.title is None:
                self.title = " ".join("".join(self._cur_title).split())
            self._cur_title = None
        elif tag == "head":
            self._in_head = False
        elif tag == "a" and self._cur_a is not None:
            self._cur_a["text"] = " ".join("".join(self._cur_a["text"]).split())
            self.anchors.append(self._cur_a)
            self._cur_a = None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._cur_h:
            self.headings.append((self._cur_h[0], " ".join("".join(self._cur_h[1]).split())))
            self._cur_h = None
        elif tag == "script":
            if self._cur_jsonld is not None:
                self.jsonld.append("".join(self._cur_jsonld))
                self._cur_jsonld = None
            self._skip = max(0, self._skip - 1)
        elif tag in ("style", "noscript", "template", "svg"):
            self._skip = max(0, self._skip - 1)
            if tag == "svg":
                self._svg = max(0, self._svg - 1)

    def handle_data(self, data):
        if self._cur_jsonld is not None:
            self._cur_jsonld.append(data)
            return
        if self._cur_title is not None:
            self._cur_title.append(data)
            return
        if self._skip:
            return
        if self._cur_a is not None:
            self._cur_a["text"].append(data)
        if self._cur_h is not None:
            self._cur_h[1].append(data)
        if not self._in_head:
            self.text.append(data)

    def meta(self, key, attr="name"):
        for m in self.metas:
            if m.get(attr, "").lower() == key:
                return m.get("content", "").strip()
        return None

    def rel(self, value):
        return [l for l in self.links if value in l.get("rel", "").lower().split()]


def parse(text):
    p = PageParser()
    try:
        p.feed(text)
        p.close()
    except Exception:
        pass
    p.body_text = " ".join(" ".join(p.text).split())
    p.words = re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", p.body_text)
    return p


def norm_url(u):
    p = urlparse(u)
    path = p.path or "/"
    return urlunparse((p.scheme.lower(), (p.hostname or "").lower() + (f":{p.port}" if p.port else ""), path, "", p.query, ""))


def same_site(a, b):
    ha, hb = urlparse(a).hostname or "", urlparse(b).hostname or ""
    strip = lambda h: h[4:] if h.startswith("www.") else h
    return strip(ha) == strip(hb)


# ---------------------------------------------------------------- audit

class Report:
    def __init__(self):
        self.checks = []

    def add(self, cat, cid, status, title, detected, sev="warning", why="", verify="", fix="", items=None, weight=None):
        """status: pass | fail | warn | info.  sev: critical | warning | notice (used when not passing)."""
        if weight is None:
            weight = {"critical": 3, "warning": 2, "notice": 1}[sev]
        self.checks.append({
            "cat": cat, "id": cid, "status": status, "sev": sev if status in ("fail", "warn") else None,
            "title": title, "detected": detected, "why": why, "verify": verify, "fix": fix,
            "items": (items or [])[:25], "count": len(items or []), "weight": weight,
        })


def run_audit(raw_url, keyword=""):
    raw_url = raw_url.strip()
    if not re.match(r"^https?://", raw_url, re.I):
        raw_url = "https://" + raw_url
    keyword = (keyword or "").strip()
    started = time.perf_counter()
    R = Report()

    # 1. the page itself
    try:
        res = fetch(raw_url, timeout=PAGE_TIMEOUT)
    except AuditError as e:
        if raw_url.startswith("https://") and "timed out" not in str(e):
            try:
                res = fetch("http://" + raw_url[8:], timeout=PAGE_TIMEOUT)
            except AuditError:
                raise e
        else:
            raise
    final = res["url"]
    fp = urlparse(final)
    origin = f"{fp.scheme}://{fp.netloc}"
    ctype = res["headers"].get("content-type", "")
    is_html = "html" in ctype or res["body"][:500].lstrip().lower().startswith((b"<!doctype html", b"<html"))
    text = decode(res) if is_html else ""
    P = parse(text)
    hdr = res["headers"]
    # a CAPTCHA or bot-protection page isn't the site: stop rather than score the block page.
    # Visible text only for 200 responses, because normal pages often load reCAPTCHA scripts.
    visible = (P.title or "") + " " + P.body_text[:3000]
    blocked_status = res["status"] in (202, 403, 429, 503)
    if (len(P.words) < 150 and BLOCK_MARKERS.search(visible)) \
            or (blocked_status and BLOCK_MARKERS.search(visible + " " + text[:20000])) \
            or (blocked_status and len(P.words) < 30):  # e.g. Amazon's empty 202 JavaScript challenge
        raise AuditError(f"{fp.hostname} answered with a bot check (CAPTCHA or “access denied” page) instead of the real page, "
                         "so an audit would only describe that block page. If this is your site, allow the audit in your firewall or bot "
                         "protection (Cloudflare, Akamai, etc.) and try again.")

    with ThreadPoolExecutor(max_workers=8) as pool:
        robots_f = pool.submit(lambda: _try(fetch, origin + "/robots.txt"))
        llms_f = pool.submit(lambda: _try(fetch, origin + "/llms.txt", "GET", 4, False))
        http_f = pool.submit(lambda: _try(fetch, "http://" + fp.netloc + (fp.path or "/"), "HEAD", 6, False)) if fp.scheme == "https" else None
        robots = robots_f.result()
        llms = llms_f.result()
        http_ver = http_f.result() if http_f else None

    robots_txt = decode(robots) if robots and robots["status"] == 200 else ""
    rp = robotparser.RobotFileParser()
    rp.parse(robots_txt.splitlines())

    # sitemap discovery
    sm_urls = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_txt)
    sitemap, sitemap_url, sitemap_locs = None, None, []
    for cand in sm_urls + [origin + "/sitemap.xml", origin + "/sitemap_index.xml"]:
        r = _try(fetch, cand)
        if r and r["status"] == 200 and b"<" in r["body"][:200]:
            sitemap, sitemap_url = r, r["url"]
            xml = decode(r)
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
            if "<sitemapindex" in xml and locs:
                child = _try(fetch, html.unescape(locs[0]))
                sitemap_locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", decode(child)) if child and child["status"] == 200 else []
                sitemap["children"] = len(locs)
            else:
                sitemap_locs = locs
            sitemap_locs = [html.unescape(x) for x in sitemap_locs]
            break

    # 2. crawl internal pages (breadth first, respecting robots.txt)
    pages = {norm_url(final): {"url": final, "status": res["status"], "depth": 0, "title": P.title, "desc": P.meta("description"),
                              "h1": [h for l, h in P.headings if l == 1], "noindex": _noindex(P, hdr), "ms": res["ttfb"],
                              "canonical": _first_href(P.rel("canonical"), final), "words": len(P.words), "inlinks": 0, "redirected": bool(res["chain"])}}
    parsed = {norm_url(final): P}
    link_graph = {}  # target -> set(sources)
    frontier = [(final, P)]
    blocked = []
    depth = 0
    while frontier and len(pages) < MAX_PAGES and depth < MAX_DEPTH:
        depth += 1
        nxt = []
        for src, sp in frontier:
            for a in sp.anchors:
                href = _abs(a["href"], src)
                if not href or not same_site(href, final) or SKIP_EXT.search(urlparse(href).path):
                    continue
                key = norm_url(href)
                link_graph.setdefault(key, set()).add(norm_url(src))
                if key in pages or key in [norm_url(u) for u in nxt]:
                    continue
                if not (rp.can_fetch(BOT, href) and rp.can_fetch("*", href)):
                    if href not in blocked:
                        blocked.append(href)
                    continue
                nxt.append(href)
        nxt = nxt[: MAX_PAGES - len(pages)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda u: (u, _try(fetch, u)), nxt))
        frontier = []
        for u, r in results:
            if not r:
                pages[norm_url(u)] = {"url": u, "status": 0, "depth": depth, "title": None, "desc": None, "h1": [], "noindex": False, "ms": None, "canonical": None, "words": 0, "inlinks": 0, "redirected": False}
                continue
            key = norm_url(r["url"])
            if r["chain"] and key in pages:  # e.g. /news redirecting to /news/: same page, not a duplicate
                link_graph.setdefault(key, set()).update(link_graph.get(norm_url(u), set()))
                continue
            sp = parse(decode(r)) if "html" in r["headers"].get("content-type", "") else parse("")
            pages[norm_url(u)] = {"url": u, "final": r["url"], "status": r["status"], "depth": depth, "title": sp.title, "desc": sp.meta("description"),
                                  "h1": [h for l, h in sp.headings if l == 1], "noindex": _noindex(sp, r["headers"]), "ms": r["ttfb"],
                                  "canonical": _first_href(sp.rel("canonical"), r["url"]), "words": len(sp.words), "inlinks": 0, "redirected": bool(r["chain"])}
            if r["status"] == 200 and key not in parsed:
                parsed[key] = sp
                frontier.append((r["url"], sp))
    for k, pg in pages.items():
        pg["inlinks"] = len(link_graph.get(k, set()) - {k})

    # 3. link checks: every link on the start page, plus internal links found while crawling
    start_links = []
    for a in P.anchors:
        href = _abs(a["href"], final)
        if href:
            start_links.append((href, a))
    to_check = {}
    for href, a in start_links:
        to_check.setdefault(norm_url(href), {"url": href, "from": final, "anchor": a["text"] or a["img_alt"]})
    for k, srcs in link_graph.items():
        if k not in to_check:
            to_check[k] = {"url": pages[k]["url"] if k in pages else k, "from": next(iter(srcs)), "anchor": ""}
    status_of = {}
    for k, pg in pages.items():
        if pg["status"]:
            status_of[k] = {"status": pg["status"], "hops": 1 if pg.get("redirected") else 0}
    pending = [v for k, v in to_check.items() if k not in status_of][:MAX_LINK_CHECKS]
    with ThreadPoolExecutor(max_workers=10) as pool:
        for v, st in zip(pending, pool.map(lambda v: link_status(v["url"]), pending)):
            status_of[norm_url(v["url"])] = st
    broken, unverified, redirecting = [], [], []
    for k, v in to_check.items():
        st = status_of.get(k)
        if not st:
            continue
        s = st["status"]
        internal = same_site(v["url"], final)
        row = {"url": v["url"], "status": s, "from": v["from"], "anchor": v["anchor"], "internal": internal}
        if s in (401, 403, 429, 999) or (s == 0 and not internal):
            unverified.append(row)
        elif s == 0 or s >= 400:
            broken.append(row)
        elif st.get("hops") and internal:
            redirecting.append(row)

    # ------------------------------------------------------------ checks
    C = "crawl"
    s = res["status"]
    if s == 200:
        R.add(C, "status", "pass", "Page returns 200 OK", f"{final} answered with HTTP {s}.")
    else:
        R.add(C, "status", "fail", f"Page returns HTTP {s}", f"{final} answered with HTTP {s} instead of 200.", "critical",
              "Search engines only index pages that load successfully. Error responses are dropped from the index over time.",
              "Open the URL in a private window, or check it with the URL Inspection tool in Google Search Console.",
              "Fix the server error or restore the page. If it moved, redirect it with a single 301 to the new URL.")
    if res["chain"]:
        hops = " → ".join(f"{h['url']} ({h['status']})" for h in res["chain"]) + f" → {final}"
        bad = len(res["chain"]) > 1 or any(h["status"] in (302, 307) for h in res["chain"])
        R.add(C, "redirect", "warn" if bad else "info", "Redirect chain" if len(res["chain"]) > 1 else "URL redirects", hops,
              "warning" if len(res["chain"]) > 1 else "notice",
              "Each hop slows the visit and can dilute signals. Temporary (302/307) redirects tell search engines the move isn't final.",
              "Request the original URL with a redirect checker or `curl -I` and follow the Location headers.",
              "Link straight to the final URL and use one permanent 301 redirect from the old address.")
    if fp.scheme == "https":
        R.add(C, "https", "pass", "Served over HTTPS", "The page loads securely over HTTPS.")
        if http_ver is not None:
            if http_ver and http_ver["url"].startswith("https://"):
                R.add(C, "http-redirect", "pass", "HTTP redirects to HTTPS", f"http://{fp.netloc} forwards to the secure version.")
            else:
                R.add(C, "http-redirect", "warn", "HTTP version doesn't redirect to HTTPS", f"http://{fp.netloc}{fp.path} {'did not respond' if not http_ver else 'stays on HTTP'}.", "warning",
                      "Two versions of the same page split signals and can show visitors an insecure page.",
                      f"Open http://{fp.netloc} and check whether the address bar switches to https.",
                      "Add a site-wide 301 redirect from http:// to https://.")
    else:
        R.add(C, "https", "fail", "Not served over HTTPS", f"The page loads over plain HTTP: {final}", "critical",
              "Browsers mark HTTP pages as “Not secure”, and HTTPS is a confirmed Google ranking signal.",
              "Look for the padlock in the browser address bar.",
              "Install a TLS certificate (many hosts offer free Let's Encrypt certificates) and redirect all HTTP traffic to HTTPS.")

    if robots and robots["status"] == 200:
        R.add(C, "robots", "pass", "robots.txt found", f"{origin}/robots.txt is reachable ({len(robots_txt.splitlines())} lines).")
    elif robots and robots["status"] >= 500:
        R.add(C, "robots", "fail", "robots.txt returns a server error", f"{origin}/robots.txt answered with HTTP {robots['status']}.", "critical",
              "When robots.txt fails with a 5xx error, Google may stop crawling the whole site until it recovers.",
              f"Open {origin}/robots.txt in a browser.", "Make robots.txt return 200 (or 404 if you don't need one).")
    else:
        R.add(C, "robots", "warn", "No robots.txt file", f"{origin}/robots.txt returned {robots['status'] if robots else 'no response'}.", "notice",
              "Without robots.txt crawlers assume everything is allowed, which is fine, but you lose a place to point them to your sitemap.",
              f"Open {origin}/robots.txt.", "Add a robots.txt with your rules and a `Sitemap:` line pointing to your XML sitemap.")
    allowed = rp.can_fetch("Googlebot", final) and rp.can_fetch("*", final)
    if robots_txt and not allowed:
        R.add(C, "robots-block", "fail", "Page is blocked by robots.txt", f"robots.txt disallows {fp.path or '/'} for search engine crawlers.", "critical",
              "Crawlers can't fetch blocked pages, so their content can't be read. The URL may still appear in results without a description.",
              f"Open {origin}/robots.txt and look for a matching Disallow rule, or use Search Console's robots.txt report.",
              "Remove or narrow the Disallow rule if this page should be found, then request a recrawl.")
    elif robots_txt:
        R.add(C, "robots-block", "pass", "Page is allowed by robots.txt", "No Disallow rule blocks this URL for search engines.")

    if sitemap:
        extra = f" ({sitemap.get('children')} child sitemaps)" if sitemap.get("children") else ""
        R.add(C, "sitemap", "pass", "XML sitemap found", f"{sitemap_url}{extra} lists {len(sitemap_locs)} URL{'s' if len(sitemap_locs) != 1 else ''}" + (" in its first file." if extra else "."))
        if not sm_urls:
            R.add(C, "sitemap-robots", "warn", "Sitemap isn't referenced in robots.txt", "robots.txt has no `Sitemap:` line.", "notice",
                  "Listing the sitemap in robots.txt helps every crawler find it, not just the ones you submit it to.",
                  f"Open {origin}/robots.txt.", f"Add the line `Sitemap: {sitemap_url}` to robots.txt.")
        if sitemap_locs and len(sitemap_locs) < 50000:
            in_sm = any(norm_url(x) == norm_url(final) for x in sitemap_locs)
            if not in_sm and not sitemap.get("children"):
                R.add(C, "in-sitemap", "warn", "Page isn't in the sitemap", f"{final} isn't listed in {sitemap_url}.", "notice",
                      "Sitemaps tell search engines which URLs you consider important.", "Search the sitemap file for the URL.",
                      "Add the page to your XML sitemap if it should be indexed.")
    else:
        R.add(C, "sitemap", "warn", "No XML sitemap found", "No sitemap at /sitemap.xml, /sitemap_index.xml or in robots.txt.", "warning",
              "A sitemap helps search engines discover pages, especially new ones or pages with few internal links.",
              f"Open {origin}/sitemap.xml.", "Generate an XML sitemap (most CMSs and SEO plugins do this) and reference it in robots.txt.")

    robots_meta = ",".join(filter(None, [P.meta("robots"), P.meta("googlebot")])).lower()
    xrt = hdr.get("x-robots-tag", "").lower()
    if "noindex" in robots_meta or "noindex" in xrt:
        where = "meta robots tag" if "noindex" in robots_meta else "X-Robots-Tag HTTP header"
        R.add(C, "noindex", "fail", "Page is set to noindex", f"A {where} tells search engines not to index this page.", "critical",
              "A noindex directive removes the page from search results entirely.",
              "View the page source and search for “noindex”, and check response headers in your browser's network tab.",
              "Remove the noindex directive if the page should appear in search, then request indexing in Search Console.")
    else:
        R.add(C, "noindex", "pass", "Page can be indexed", "No noindex directive in the meta robots tag or X-Robots-Tag header.")
    if "nofollow" in robots_meta or "nofollow" in xrt:
        R.add(C, "nofollow", "warn", "Page-level nofollow", "Robots directives tell crawlers not to follow any links on this page.", "warning",
              "Crawlers won't use this page's links to discover or value other pages.", "Search the source for “nofollow”.",
              "Remove the page-wide nofollow unless you have a specific reason for it.")

    canon = P.rel("canonical")
    if not canon:
        R.add(C, "canonical", "warn", "No canonical tag", "The page has no <link rel=\"canonical\">.", "warning",
              "Without a canonical, search engines guess which version to index when the same content is reachable at several URLs (tracking parameters, www vs non-www).",
              "View source and search for rel=\"canonical\".", f"Add <link rel=\"canonical\" href=\"{final}\"> to the <head>.")
    else:
        href = canon[0].get("href", "")
        absu = urljoin(final, href)
        if len(canon) > 1:
            R.add(C, "canonical", "warn", "Multiple canonical tags", f"{len(canon)} canonical tags were found; search engines may ignore them all.", "warning",
                  "Conflicting canonicals are ignored, so search engines fall back to guessing.", "Search the source for rel=\"canonical\".",
                  "Keep exactly one canonical tag per page.")
        elif norm_url(absu) != norm_url(final):
            R.add(C, "canonical", "warn", "Canonical points to another URL", f"Canonical is {absu}, not this page.", "warning",
                  "This asks search engines to index the other URL instead of this one. That's right for duplicates but wrong for unique pages.",
                  "View source and compare the canonical href with the page address.",
                  "If this page should rank on its own, make the canonical point to itself.")
        else:
            R.add(C, "canonical", "pass", "Self-referencing canonical", f"Canonical points to this page{' (relative URL)' if not href.startswith('http') else ''}.")

    hreflangs = [l for l in P.rel("alternate") if l.get("hreflang")]
    if hreflangs:
        codes = [l["hreflang"].lower() for l in hreflangs]
        self_ref = any(norm_url(urljoin(final, l.get("href", ""))) == norm_url(final) for l in hreflangs)
        if not self_ref or "x-default" not in codes:
            miss = [x for x, ok in (("a self-reference", self_ref), ("an x-default", "x-default" in codes)) if not ok]
            R.add(C, "hreflang", "warn", "Incomplete hreflang set", f"{len(hreflangs)} hreflang tags ({', '.join(codes[:8])}) but missing {' and '.join(miss)}.", "notice",
                  "Each language version should list itself and an x-default fallback for hreflang to work reliably.",
                  "Search the source for hreflang.", "Add the missing hreflang entries on every language version.")
        else:
            R.add(C, "hreflang", "pass", "hreflang tags look complete", f"{len(hreflangs)} language versions declared, including x-default.")

    crawled_ok = [p for p in pages.values() if p["status"] == 200]
    noindexed = [p["url"] for p in crawled_ok if p["noindex"]]
    if len(pages) > 1:
        if noindexed:
            R.add(C, "crawl-noindex", "warn", "Crawled pages set to noindex", f"{len(noindexed)} of {len(crawled_ok)} crawled pages carry noindex.", "warning",
                  "Linked pages with noindex waste crawl effort and may be hidden by mistake.", "Open each URL and search the source for noindex.",
                  "Remove noindex from pages that should rank; stop linking to pages that shouldn't.", noindexed)
        if blocked:
            R.add(C, "crawl-blocked", "info", "Linked pages blocked by robots.txt", f"{len(blocked)} internal links point to URLs disallowed in robots.txt.", "notice",
                  "Fine for admin or cart pages; a problem if any of these should rank.", "Compare the list with your robots.txt rules.",
                  "Unblock any page that should be found in search.", blocked)

    # on-page
    C = "onpage"
    t = P.title or ""
    if not t:
        R.add(C, "title", "fail", "Missing page title", "The page has no <title> tag (or it's empty).", "critical",
              "The title is the main headline in search results and a strong relevance signal.", "View source and search for <title>.",
              "Write a unique, descriptive title of about 50–60 characters with the main topic near the start.")
    elif len(t) < 30 or len(t) > 60:
        R.add(C, "title", "warn", "Title too short" if len(t) < 30 else "Title may be cut off", f"“{t}” is {len(t)} characters.", "notice" if len(t) < 30 else "warning",
              "Short titles waste space to describe the page; titles over ~60 characters are often truncated in results.",
              "Count the characters, or preview the snippet with a SERP preview tool.", "Aim for roughly 50–60 characters.")
    else:
        R.add(C, "title", "pass", "Title length is good", f"“{t}” ({len(t)} characters).")
    if P.titles > 1:
        R.add(C, "title-multi", "warn", "Multiple title tags", f"{P.titles} <title> tags found.", "warning",
              "Search engines use one; the rest add confusion.", "Search the source for <title>.", "Keep a single <title> in the <head>.")

    d = P.meta("description") or ""
    if not d:
        R.add(C, "desc", "warn", "Missing meta description", "No <meta name=\"description\"> found.", "warning",
              "Search engines often use it as the snippet under your title. Without one they pick text from the page, which may not sell the click.",
              "View source and search for name=\"description\".", "Write a 120–160 character summary that answers why someone should click.")
    elif len(d) < 70 or len(d) > 160:
        R.add(C, "desc", "warn", "Meta description too short" if len(d) < 70 else "Meta description may be cut off", f"{len(d)} characters: “{d[:180]}”", "notice",
              "Very short descriptions under-use the snippet; long ones are truncated.", "Count the characters.", "Aim for about 120–160 characters.")
    else:
        R.add(C, "desc", "pass", "Meta description length is good", f"{len(d)} characters.")

    h1s = [h for l, h in P.headings if l == 1]
    if not h1s:
        R.add(C, "h1", "warn", "Missing H1 heading", "The page has no <h1>.", "warning",
              "The H1 tells visitors and search engines what the page is about.", "Search the source for <h1.", "Add one H1 that states the page's main topic.")
    elif not any(h1s):
        R.add(C, "h1", "warn", "Empty H1 heading", "The H1 has no text (it may contain only an image without alt text).", "warning",
              "An empty heading carries no meaning for search engines or screen readers.", "Inspect the H1 element.", "Put the page's main topic in the H1 as text.")
    elif len(h1s) > 1:
        R.add(C, "h1", "warn", "Multiple H1 headings", f"{len(h1s)} H1s: " + " | ".join(h[:60] for h in h1s[:5]), "notice",
              "Several H1s blur the page's main topic. It's allowed, but one clear H1 is easier to understand.", "Search the source for <h1.",
              "Keep one H1 and turn the others into H2s.")
    else:
        R.add(C, "h1", "pass", "One H1 heading", f"“{h1s[0][:120]}”")
    levels = [l for l, _ in P.headings]
    skips = []
    for a_, b_ in zip(levels, levels[1:]):
        if b_ > a_ + 1:
            skips.append(f"H{a_} → H{b_}")
    if skips:
        R.add(C, "headings", "warn", "Heading levels are skipped", f"{len(skips)} jump(s), e.g. " + ", ".join(sorted(set(skips))[:4]) + ".", "notice",
              "A logical outline (H1 → H2 → H3) helps search engines and screen readers follow the structure.", "Use a headings outline browser extension.",
              "Don't skip levels; style headings with CSS instead of picking a level for its size.")
    elif len(P.headings) > 1:
        R.add(C, "headings", "pass", "Heading structure is logical", f"{len(P.headings)} headings, no skipped levels.")

    vp = P.meta("viewport")
    if not vp:
        R.add(C, "viewport", "fail", "No mobile viewport tag", "No <meta name=\"viewport\"> found.", "critical",
              "Google indexes the mobile version of pages. Without a viewport tag phones render the desktop layout, zoomed out.",
              "Open the page on a phone, or run Lighthouse in Chrome DevTools.", "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">.")
    else:
        R.add(C, "viewport", "pass", "Mobile viewport is set", vp)
    if not P.lang:
        R.add(C, "lang", "warn", "No language declared", "The <html> tag has no lang attribute.", "notice",
              "lang helps search engines serve the right audience and screen readers pick the right voice.", "Check the opening <html> tag.",
              "Add lang, for example <html lang=\"en\">.")
    else:
        R.add(C, "lang", "pass", "Language declared", f"lang=\"{P.lang}\"")
    if not P.has_charset:
        R.add(C, "charset", "warn", "No character encoding declared", "No <meta charset> in the page.", "notice",
              "Without it some browsers can show garbled characters.", "Search the source for charset.", "Add <meta charset=\"utf-8\"> as the first tag in <head>.")
    icons = [l for l in P.links if "icon" in l.get("rel", "").lower()]
    if not icons:
        R.add(C, "favicon", "warn", "No favicon declared", "No <link rel=\"icon\"> found.", "notice",
              "Google shows the favicon next to your result on mobile; a blank icon looks less trustworthy.", "Search the source for rel=\"icon\".",
              "Add a square favicon (at least 48×48 px) and reference it with <link rel=\"icon\">.")
    else:
        R.add(C, "favicon", "pass", "Favicon declared", icons[0].get("href", ""))
    path = fp.path or "/"
    url_issues = []
    if len(final) > 115:
        url_issues.append(f"long ({len(final)} characters)")
    if "_" in path:
        url_issues.append("uses underscores")
    if re.search(r"[A-Z]", path):
        url_issues.append("has uppercase letters")
    if fp.query.count("&") >= 2:
        url_issues.append("has several parameters")
    if url_issues:
        R.add(C, "url", "warn", "URL could be cleaner", f"{path} " + ", ".join(url_issues) + ".", "notice",
              "Short, lowercase, hyphenated URLs are easier to read, share and understand.", "Look at the address bar.",
              "Use short lowercase words separated by hyphens. Redirect old URLs if you change them.")
    else:
        R.add(C, "url", "pass", "URL is clean", path)

    if len(crawled_ok) > 1:
        def dupes(field):
            seen = {}
            for p in crawled_ok:
                v = (p.get(field) or "").strip().lower()
                if v:
                    seen.setdefault(v, []).append(p["url"])
            return [(v, us) for v, us in seen.items() if len(us) > 1]
        dt = dupes("title")
        if dt:
            items = [f"“{v[:70]}” on {len(us)} pages: " + ", ".join(us[:4]) for v, us in dt]
            R.add(C, "dup-titles", "warn", "Duplicate titles across pages", f"{sum(len(us) for _, us in dt)} crawled pages share {len(dt)} title(s).", "warning",
                  "Identical titles make it hard for search engines to tell pages apart and pick the right one.", "Compare the titles in the crawled pages table below.",
                  "Give every page its own title that names what's unique about it.", items)
        else:
            R.add(C, "dup-titles", "pass", "No duplicate titles", f"All {len(crawled_ok)} crawled pages have distinct titles.")
        dd = dupes("desc")
        if dd:
            items = [f"“{v[:70]}” on {len(us)} pages: " + ", ".join(us[:4]) for v, us in dd]
            R.add(C, "dup-desc", "warn", "Duplicate meta descriptions", f"{sum(len(us) for _, us in dd)} crawled pages share {len(dd)} description(s).", "notice",
                  "Repeated descriptions make results look the same and are often replaced by Google.", "Compare descriptions across pages.",
                  "Write a specific description for each important page.", items)
        no_t = [p["url"] for p in crawled_ok if not p["title"]]
        no_d = [p["url"] for p in crawled_ok if not p["desc"]]
        if no_t:
            R.add(C, "pages-no-title", "fail", "Crawled pages without a title", f"{len(no_t)} of {len(crawled_ok)} crawled pages have no title.", "critical",
                  "Pages without titles rarely rank and look broken in results.", "Open each URL and check the browser tab.", "Add a unique title to each page.", no_t)
        if no_d:
            R.add(C, "pages-no-desc", "warn", "Crawled pages without a meta description", f"{len(no_d)} of {len(crawled_ok)} crawled pages have none.", "notice",
                  "Search engines will pick their own snippet for these pages.", "View source on each URL.", "Add descriptions, starting with your most important pages.", no_d)

    # content & keywords
    C = "content"
    wc = len(P.words)
    if wc < 300:
        R.add(C, "words", "warn", "Thin content", f"About {wc} words of visible text.", "warning" if wc < 100 else "notice",
              "Pages with little text give search engines and AI answers little to understand or quote. (Short is fine for some page types, like contact pages.)",
              "Select all text on the page and paste it into a word counter.", "Expand the page with genuinely useful detail that answers your visitors' questions.")
    else:
        R.add(C, "words", "pass", "Enough text content", f"About {wc} words of visible text.")
    imgs = [i for i in P.images if i.get("src") or i.get("data-src") or i.get("srcset")]
    no_alt = [urljoin(final, i.get("src") or i.get("data-src") or "") for i in imgs if "alt" not in i]
    if no_alt:
        R.add(C, "alt", "warn", "Images missing alt text", f"{len(no_alt)} of {len(imgs)} images have no alt attribute.", "warning",
              "Alt text describes images to search engines and screen-reader users, and helps images rank in image search.",
              "Inspect the images, or run an accessibility checker.", "Add a short alt describing each meaningful image; use alt=\"\" for decorative ones.", no_alt)
    elif imgs:
        R.add(C, "alt", "pass", "All images have alt attributes", f"{len(imgs)} images checked.")
    html_len = len(text) or 1
    ratio = len(P.body_text) / html_len * 100
    if text and ratio < 10:
        R.add(C, "ratio", "warn", "Low text-to-code ratio", f"Visible text is {ratio:.0f}% of the HTML.", "notice",
              "Heavy markup with little text can slow pages and dilute content signals.", "Compare page text with View Source size.",
              "Trim unused markup and inline code, and make sure the main content is in the HTML.")
    if keyword:
        kw = keyword.lower()
        toks = [w for w in re.findall(r"\w+", kw) if len(w) > 2] or [kw]
        def has(s):
            s = (s or "").lower()
            return kw in s or all(t_ in s for t_ in toks)
        first100 = " ".join(P.words[:100])
        slug = (fp.path or "").lower().replace("-", " ").replace("_", " ")
        body_l = " " + " ".join(P.words).lower() + " "
        occ = body_l.count(" " + kw + " ") if " " in kw else sum(1 for w in P.words if w.lower() == kw)
        density = occ * len(kw.split()) / max(wc, 1) * 100
        kc = [
            ("kw-title", "title", has(P.title), "warning", "The title is the strongest on-page relevance signal."),
            ("kw-desc", "meta description", has(d), "notice", "Matching words are often bolded in the snippet, which draws clicks."),
            ("kw-h1", "H1", any(has(h) for h in h1s), "warning", "The H1 confirms the page's topic to visitors and search engines."),
            ("kw-intro", "first 100 words", has(first100), "notice", "Stating the topic early shows the page answers the search."),
            ("kw-sub", "subheadings (H2/H3)", any(has(h) for l, h in P.headings if l in (2, 3)), "notice", "Subheadings that use the topic help structure and scanning."),
            ("kw-url", "URL", has(slug), "notice", "A descriptive URL reinforces relevance and reads well in results."),
            ("kw-alt", "image alt text", any(has(i.get("alt")) for i in imgs), "notice", "Relevant alt text can help the page appear in image results."),
        ]
        for cid, where, ok, sev, why in kc:
            if ok:
                R.add(C, cid, "pass", f"Keyword in {where}", f"“{keyword}” appears in the {where}.")
            else:
                R.add(C, cid, "warn", f"Keyword missing from {where}", f"“{keyword}” wasn't found in the {where}.", sev, why,
                      f"Check the {where} on the page.", f"Use “{keyword}” (or a close variant) naturally in the {where}.")
        if occ and density > 3:
            R.add(C, "kw-density", "warn", "Keyword may be overused", f"“{keyword}” appears {occ} times (~{density:.1f}% of words).", "notice",
                  "Repeating a phrase unnaturally reads poorly and can look like keyword stuffing.", "Search the page text for the phrase.",
                  "Use synonyms and related terms instead of repeating the exact phrase.")
        elif occ:
            R.add(C, "kw-density", "pass", "Keyword usage looks natural", f"“{keyword}” appears {occ} time{'s' if occ != 1 else ''} (~{density:.1f}% of words).")

    # links & redirects
    C = "links"
    if broken:
        bi = [b for b in broken if b["internal"]]
        be = [b for b in broken if not b["internal"]]
        if bi:
            R.add(C, "broken-int", "fail", "Broken internal links", f"{len(bi)} internal link{'s' if len(bi) != 1 else ''} lead to errors.", "warning",
                  "Broken links send visitors and crawlers to dead ends and waste the value those links could pass.",
                  "Click the links listed, or check them with the broken links table below.", "Update each link to a working URL, or restore/redirect the missing page.",
                  [f"{b['url']} ({b['status'] or 'no response'}) linked from {b['from']}" for b in bi])
        if be:
            R.add(C, "broken-ext", "fail", "Broken external links", f"{len(be)} outbound link{'s' if len(be) != 1 else ''} lead to errors.", "warning",
                  "Links to dead pages hurt user trust and the page's perceived quality.", "Open the listed links.",
                  "Replace them with working sources or remove them.",
                  [f"{b['url']} ({b['status'] or 'no response'})" for b in be])
    checked = len([k for k in to_check if k in status_of])
    if not broken:
        R.add(C, "broken", "pass", "No broken links found", f"{checked} unique links checked.")
    if unverified:
        R.add(C, "unverified", "info", "Links that couldn't be verified", f"{len(unverified)} sites refused or rate-limited automated checks (401/403/429).", "notice",
              "Many social and large sites block bots, so these may work fine in a browser.", "Open them in your browser.", "No action needed unless they fail for real visitors.",
              [f"{u['url']} ({u['status'] or 'no response'})" for u in unverified])
    if redirecting:
        R.add(C, "int-redirects", "warn", "Internal links point to redirects", f"{len(redirecting)} internal links go through a redirect.", "notice",
              "Every redirect adds a round trip and slows crawling.", "Follow the listed links and watch the address change.",
              "Update the links to point straight at the final URLs.", [r["url"] for r in redirecting])
    internal_links = [h for h, a in start_links if same_site(h, final)]
    external_links = [h for h, a in start_links if not same_site(h, final)]
    if not internal_links:
        R.add(C, "int-count", "warn", "No internal links on the page", "This page doesn't link to any other page on the site.", "warning",
              "Internal links help crawlers discover pages and spread authority through the site.", "Look for links in the page body and navigation.",
              "Link to related pages with descriptive anchor text.")
    else:
        R.add(C, "int-count", "pass", "Page links to other pages", f"{len(set(map(norm_url, internal_links)))} unique internal and {len(set(map(norm_url, external_links)))} external links.")
    generic = [f"“{a['text']}” → {h}" for h, a in start_links if a["text"].lower().strip(" .…»›→") in GENERIC_ANCHORS]
    empty = [h for h, a in start_links if not a["text"] and not a["img_alt"] and not a["title"]]
    if generic or empty:
        R.add(C, "anchors", "warn", "Unclear link text", f"{len(generic)} generic and {len(empty)} empty link text{'s' if len(generic) + len(empty) != 1 else ''}.", "notice",
              "Anchor text tells search engines and screen-reader users what the linked page is about. “Click here” and icon-only links say nothing.",
              "Read the listed links out of context.", "Use descriptive words for links, and add aria-label or alt text to icon links.", generic + empty)
    nf_int = [h for h, a in start_links if same_site(h, final) and "nofollow" in a["rel"]]
    if nf_int:
        R.add(C, "nofollow-int", "warn", "Nofollow on internal links", f"{len(nf_int)} internal links use rel=\"nofollow\".", "notice",
              "Nofollow on your own links stops crawlers from following them and wastes internal link value.", "Search the source for nofollow.",
              "Remove nofollow from internal links.", nf_int)

    # internal linking (site structure)
    C = "internal"
    R.add(C, "crawled", "info", "Pages crawled", f"{len(pages)} page{'s' if len(pages) != 1 else ''} crawled from the start page (limit {MAX_PAGES}, up to {MAX_DEPTH} clicks deep).", "notice")
    if len(crawled_ok) > 1:
        deep = [p["url"] for p in crawled_ok if p["depth"] >= 3]
        if deep:
            R.add(C, "depth", "warn", "Pages buried deep in the site", f"{len(deep)} pages are 3+ clicks from the start page.", "notice",
                  "Pages far from the homepage are crawled less often and look less important.", "Count the clicks needed to reach them.",
                  "Link important pages from the homepage, main navigation or hub pages.", deep)
        else:
            R.add(C, "depth", "pass", "Pages are close to the start page", "Every crawled page is within 2 clicks.")
        # only meaningful when the crawl covered the site; otherwise unvisited pages would hide inlinks
        weak = [p["url"] for p in crawled_ok if p["inlinks"] <= 1 and p["depth"] > 0] if len(pages) < MAX_PAGES else []
        if weak:
            R.add(C, "weak", "warn", "Pages with few internal links", f"{len(weak)} crawled pages are linked from only one crawled page.", "notice",
                  "Pages with few internal links pointing to them get less crawl attention and authority.", "Use the inlinks column in the crawled pages table.",
                  "Add links to these pages from related content.", weak)
        if sitemap_locs:
            found = {norm_url(p["url"]) for p in pages.values()} | set(link_graph.keys())
            orphans = [u for u in sitemap_locs if same_site(u, final) and norm_url(u) not in found]
            if orphans and len(pages) < MAX_PAGES:
                R.add(C, "orphans", "warn", "Possible orphan pages", f"{len(orphans)} sitemap URLs weren't linked from any crawled page.", "warning",
                      "Pages that nothing links to are hard for crawlers and visitors to find.", "Search your site for links to these URLs.",
                      "Link to them from relevant pages, or remove them from the sitemap if they're retired.", orphans)
            elif orphans:
                R.add(C, "orphans", "info", "Sitemap pages outside this crawl", f"{len(orphans)} sitemap URLs weren't reached within the {MAX_PAGES}-page limit.", "notice",
                      "They may be linked from pages this crawl didn't reach.", "Run an audit on a subfolder to check those sections.", "", orphans)

    # performance & security
    C = "perf"
    ttfb = res["ttfb"]
    if ttfb > 1800:
        R.add(C, "ttfb", "fail", "Slow server response", f"The server took {ttfb} ms to respond.", "warning",
              "A slow first response delays everything else and hurts Largest Contentful Paint.", "Measure with PageSpeed Insights or your browser's network tab.",
              "Add page caching or a CDN, upgrade hosting, and cut slow database queries.")
    elif ttfb > 800:
        R.add(C, "ttfb", "warn", "Server response could be faster", f"The server took {ttfb} ms to respond (good is under 800 ms).", "notice",
              "Faster responses help pages load and get crawled quicker.", "Measure with PageSpeed Insights.", "Use caching or a CDN.")
    else:
        R.add(C, "ttfb", "pass", "Fast server response", f"{ttfb} ms to first byte from the audit server.")
    size = len(res["body"])
    if size > 1_500_000:
        R.add(C, "size", "warn", "Very large HTML", f"The HTML is {size/1024:.0f} KB.", "warning", "Large documents take longer to download and parse, especially on phones.",
              "Check the document size in the network tab.", "Remove inline data, unused markup and large inline scripts or styles.")
    elif size > 500_000:
        R.add(C, "size", "warn", "Large HTML", f"The HTML is {size/1024:.0f} KB.", "notice", "Large documents load slower on mobile.", "Check the network tab.", "Trim unnecessary markup.")
    else:
        R.add(C, "size", "pass", "HTML size is reasonable", f"{size/1024:.0f} KB.")
    if hdr.get("content-encoding", "").lower() in ("gzip", "br", "deflate", "zstd"):
        R.add(C, "compress", "pass", "Text compression enabled", f"Served with {hdr['content-encoding']}.")
    elif size > 10_000:
        R.add(C, "compress", "warn", "No text compression", "The HTML isn't compressed with gzip or Brotli.", "warning",
              "Compression typically cuts text transfer size by 60–80%.", "Check the Content-Encoding response header.", "Enable gzip or Brotli on the server or CDN.")
    blocking = [s_.get("src") for s_ in P.scripts if s_["in_head"] and s_.get("src") and "async" not in s_ and "defer" not in s_ and s_.get("type", "").lower() not in ("module", "application/ld+json")]
    if blocking:
        R.add(C, "blocking", "warn", "Render-blocking scripts", f"{len(blocking)} script{'s' if len(blocking) != 1 else ''} in <head> load{'s' if len(blocking) == 1 else ''} without async or defer.", "notice",
              "The browser stops drawing the page until these download and run.", "Run Lighthouse and see “Eliminate render-blocking resources”.",
              "Add defer (or async) to scripts that don't need to run before the page shows.", [urljoin(final, b) for b in blocking])
    else:
        R.add(C, "blocking", "pass", "No render-blocking scripts in <head>", "Head scripts use async/defer or there are none.")
    nreq = len([s_ for s_ in P.scripts if s_.get("src")]) + len([l for l in P.links if "stylesheet" in l.get("rel", "").lower()])
    if nreq > 30:
        R.add(C, "requests", "warn", "Many script and style files", f"{nreq} external scripts and stylesheets.", "notice",
              "Each file is another request; many small files slow loading.", "Count them in the network tab.", "Bundle files and remove unused libraries.")
    no_dim = [urljoin(final, i.get("src", "")) for i in imgs if not (i.get("width") and i.get("height"))]
    if no_dim:
        R.add(C, "img-dims", "warn", "Images without width and height", f"{len(no_dim)} of {len(imgs)} images don't declare their size.", "notice",
              "Without dimensions the layout jumps as images load, which hurts Cumulative Layout Shift.", "Inspect the <img> tags.",
              "Set width and height attributes (or CSS aspect-ratio) on images.", no_dim)
    if len(imgs) > 5 and not any(i.get("loading", "").lower() == "lazy" for i in imgs):
        R.add(C, "lazy", "warn", "No lazy-loaded images", f"{len(imgs)} images, none with loading=\"lazy\".", "notice",
              "Loading every image up front competes with the content people see first.", "Inspect the <img> tags.",
              "Add loading=\"lazy\" to images below the fold (not to the main hero image).")
    old = [i.get("src", "") for i in imgs if re.search(r"\.(jpe?g|png|gif)(\?|$)", i.get("src", ""), re.I)]
    if imgs and len(old) / len(imgs) > 0.5 and len(old) >= 3:
        R.add(C, "formats", "warn", "Images could use modern formats", f"{len(old)} of {len(imgs)} images are JPEG/PNG/GIF.", "notice",
              "WebP and AVIF are usually 25–50% smaller at the same quality.", "Check image URLs or the network tab.",
              "Serve WebP or AVIF versions, for example with <picture> or your CDN's image optimization.", [urljoin(final, o) for o in old])
    if fp.scheme == "https":
        res_urls = [i.get("src", "") for i in P.images] + [s_.get("src", "") for s_ in P.scripts] +                    [l.get("href", "") for l in P.links if "stylesheet" in l.get("rel", "").lower()]
        mixed = sorted({u for u in res_urls if u.lower().startswith("http://")})
        if mixed:
            R.add(C, "mixed", "warn", "Mixed content", f"{len(mixed)} resources load over insecure HTTP on an HTTPS page.", "warning",
                  "Browsers block or warn about insecure resources, which can break the page and its padlock.", "Check the browser console for mixed-content warnings.",
                  "Change these URLs to https://.", mixed)
        if hdr.get("strict-transport-security"):
            R.add(C, "hsts", "pass", "HSTS enabled", "Strict-Transport-Security header is set.")
        else:
            R.add(C, "hsts", "warn", "No HSTS header", "Strict-Transport-Security isn't set.", "notice",
                  "HSTS tells browsers to always use HTTPS, preventing downgrade attacks.", "Check response headers in the network tab.",
                  "Add a Strict-Transport-Security header once HTTPS works everywhere on the site.")

    # structured data & social
    C = "schema"
    types, invalid = [], 0
    objs = []
    for block in P.jsonld:
        try:
            data = json.loads(block.strip())
        except Exception:
            invalid += 1
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "@graph" in o and isinstance(o["@graph"], list):
                    stack.extend(o["@graph"])
                if o.get("@type"):
                    tt = o["@type"] if isinstance(o["@type"], list) else [o["@type"]]
                    types.extend(str(x) for x in tt)
                    objs.append(o)
    if invalid:
        R.add(C, "jsonld-invalid", "fail", "Invalid JSON-LD", f"{invalid} JSON-LD block{'s' if invalid != 1 else ''} can't be parsed.", "warning",
              "Search engines ignore structured data with syntax errors.", "Paste the page into Google's Rich Results Test or the Schema Markup Validator.",
              "Fix the JSON syntax (often a trailing comma or unescaped quote).")
    if types:
        R.add(C, "jsonld", "pass", "Structured data found", "Types: " + ", ".join(sorted(set(types))))
        REQ = {"Article": ["headline", "author", "datePublished"], "BlogPosting": ["headline", "author", "datePublished"], "NewsArticle": ["headline", "author", "datePublished"],
               "Product": ["name"], "LocalBusiness": ["name", "address"], "Organization": ["name", "url"], "FAQPage": ["mainEntity"],
               "BreadcrumbList": ["itemListElement"], "Event": ["name", "startDate", "location"], "Recipe": ["name", "image"], "WebSite": ["name", "url"]}
        miss = []
        for o in objs:
            tt = o["@type"] if isinstance(o["@type"], list) else [o["@type"]]
            for t_ in tt:
                need = REQ.get(t_, [])
                if t_.endswith("Business") or t_ in ("Dentist", "MedicalClinic", "Restaurant", "Store"):
                    need = REQ["LocalBusiness"]
                gone = [n for n in need if not o.get(n)]
                if t_ == "Product" and not any(o.get(k) for k in ("offers", "review", "aggregateRating")):
                    gone.append("offers, review or aggregateRating")
                if gone:
                    miss.append(f"{t_}: missing {', '.join(gone)}")
        if miss:
            R.add(C, "jsonld-props", "warn", "Structured data is missing properties", f"{len(miss)} item{'s lack' if len(miss) != 1 else ' lacks'} key properties.", "warning",
                  "Rich results need certain properties; without them the markup may not qualify.", "Test the page in Google's Rich Results Test.",
                  "Add the listed properties.", miss)
    elif P.microdata or P.rdfa:
        R.add(C, "jsonld", "pass", "Structured data found", f"{P.microdata} Microdata and {P.rdfa} RDFa items (no JSON-LD).")
    else:
        R.add(C, "jsonld", "warn", "No structured data", "No JSON-LD, Microdata or RDFa found.", "notice",
              "Structured data helps search engines understand the page and can make it eligible for rich results.", "Run the Schema Markup Validator.",
              "Add JSON-LD for what the page is: Organization and WebSite on the homepage; Article, Product, LocalBusiness, FAQPage or BreadcrumbList elsewhere.")
    if path in ("", "/") and not any(t_ in ("Organization", "LocalBusiness", "WebSite") or t_.endswith("Business") for t_ in types):
        R.add(C, "org", "warn", "Homepage lacks Organization markup", "No Organization, LocalBusiness or WebSite schema on the homepage.", "notice",
              "This markup helps search engines connect your name, logo and profiles (knowledge panel, sitelinks search).", "Run the Rich Results Test on the homepage.",
              "Add Organization (or LocalBusiness) and WebSite JSON-LD to the homepage.")
    og = {k: P.meta("og:" + k, "property") or P.meta("og:" + k) for k in ("title", "description", "image", "url", "type")}
    og_miss = [k for k in ("title", "description", "image") if not og[k]]
    if og_miss:
        R.add(C, "og", "warn", "Incomplete Open Graph tags", "Missing og:" + ", og:".join(og_miss) + ".", "notice" if len(og_miss) < 3 else "warning",
              "Open Graph controls how the page looks when shared on Facebook, LinkedIn, WhatsApp and Slack.", "Paste the URL into a social share preview tool.",
              "Add og:title, og:description and og:image (1200×630 px works well).")
    else:
        R.add(C, "og", "pass", "Open Graph tags present", "og:title, og:description and og:image are set.")
    if not (P.meta("twitter:card") or P.meta("twitter:card", "property")):
        R.add(C, "twitter", "warn", "No Twitter/X card tag", "No twitter:card meta tag.", "notice",
              "X falls back to Open Graph, but twitter:card controls the large-image layout.", "Search the source for twitter:card.",
              "Add <meta name=\"twitter:card\" content=\"summary_large_image\">.")

    # AI search readiness (AEO / GEO)
    C = "ai"
    if robots_txt:
        blocked_ai = [b for b in AI_BOTS if not rp.can_fetch(b, final)]
        bs = [b for b in blocked_ai if b in AI_SEARCH_BOTS]
        if bs:
            R.add(C, "ai-bots", "warn", "AI search crawlers are blocked", "robots.txt blocks " + ", ".join(bs) + ".", "warning",
                  "These crawlers fetch pages for AI search answers (ChatGPT search, Perplexity, Claude). Blocking them can keep you out of those answers.",
                  f"Read {origin}/robots.txt for these user agents.", "Allow AI search crawlers if you want to be cited in AI answers. Training crawlers can stay blocked separately.")
        elif blocked_ai:
            R.add(C, "ai-bots", "info", "Some AI training crawlers are blocked", "Blocked: " + ", ".join(blocked_ai) + ". AI search crawlers are allowed.", "notice",
                  "Blocking training crawlers (like GPTBot or Google-Extended) is a policy choice and doesn't stop AI search citations.", "Read robots.txt.", "")
        else:
            R.add(C, "ai-bots", "pass", "AI crawlers can read the page", "No robots.txt rule blocks the major AI crawlers.")
    if llms and llms["status"] == 200:
        R.add(C, "llms", "pass", "llms.txt found", f"{origin}/llms.txt exists.")
    else:
        R.add(C, "llms", "warn", "No llms.txt", f"{origin}/llms.txt wasn't found.", "notice",
              "llms.txt is an emerging, optional convention that gives AI tools a clean summary of your site. It's not a ranking factor.",
              f"Open {origin}/llms.txt.", "Optionally add an llms.txt with a short site summary and links to key pages.", weight=0)
    js_heavy = len([s_ for s_ in P.scripts if s_.get("src")]) >= 5
    if wc < 80 and js_heavy:
        R.add(C, "render", "fail", "Content may need JavaScript to appear", f"Only {wc} words are in the HTML, with many scripts.", "critical",
              "Many AI crawlers don't run JavaScript, and search engines render it later. Content that only appears after scripts run may be missed.",
              "Disable JavaScript in your browser and reload the page.", "Server-render or pre-render the main content so it's in the HTML.")
    else:
        R.add(C, "render", "pass", "Main content is in the HTML", f"{wc} words readable without running JavaScript.")
    q_heads = [h for l, h in P.headings if l in (2, 3, 4) and re.match(r"^(how|what|why|when|where|which|who|can|does|do|is|are|should|will)\b|\?$", h.strip(), re.I)]
    has_faq = any(t_ in ("FAQPage", "QAPage", "HowTo") for t_ in types)
    if q_heads or has_faq:
        R.add(C, "answers", "pass", "Content is structured as answers", f"{len(q_heads)} question-style headings" + (", plus FAQ/HowTo markup." if has_faq else "."))
    else:
        R.add(C, "answers", "warn", "No question-and-answer structure", "No question-style headings or FAQ markup found.", "notice",
              "AI answers and featured snippets favour content that states a question and answers it directly.", "Scan the subheadings.",
              "Add headings phrased as the questions your customers ask, each followed by a direct 1–3 sentence answer.")
    has_author = bool(P.meta("author") or P.meta("article:published_time", "property") or any(o.get("author") or o.get("datePublished") for o in objs))
    if has_author:
        R.add(C, "eeat", "pass", "Author or date signals present", "Author and/or publish date are declared.")
    else:
        R.add(C, "eeat", "warn", "No author or date signals", "No author or publish date in meta tags or structured data.", "notice",
              "Clear authorship and freshness help search engines and AI systems judge trust, especially for advice content.", "Search the source for author and datePublished.",
              "Show the author and last-updated date on articles and add them to the page's Article markup.")
    if P.lists or P.tables:
        R.add(C, "scannable", "pass", "Uses lists or tables", f"{P.lists} lists and {P.tables} tables help answers be extracted.")

    page = {
        "title": P.title, "description": d or None, "h1": h1s[:3], "lang": P.lang, "canonical": _first_href(canon, final),
        "words": wc, "images": len(imgs), "internalLinks": len(set(map(norm_url, internal_links))), "externalLinks": len(set(map(norm_url, external_links))),
        "ogImage": og["image"] and urljoin(final, og["image"]), "favicon": urljoin(final, icons[0].get("href", "/favicon.ico")) if icons else urljoin(final, "/favicon.ico"),
        "server": hdr.get("server"), "contentType": ctype,
    }
    crawl = sorted(({k: v for k, v in p.items() if k in ("url", "status", "depth", "title", "inlinks", "ms", "noindex", "words")} for p in pages.values()),
                   key=lambda p: (p["depth"], p["url"]))
    return {
        "url": raw_url, "finalUrl": final, "status": res["status"], "ttfb": res["ttfb"], "bytes": len(res["body"]),
        "redirects": res["chain"], "keyword": keyword or None, "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "durationMs": round((time.perf_counter() - started) * 1000), "page": page, "crawl": crawl,
        "links": {"checked": checked, "broken": broken, "unverified": unverified},
        "sitemap": {"url": sitemap_url, "urls": len(sitemap_locs)} if sitemap else None,
        "limits": {"pages": MAX_PAGES, "depth": MAX_DEPTH, "links": MAX_LINK_CHECKS},
        "checks": R.checks,
    }


def _try(fn, *a):
    try:
        return fn(*a)
    except AuditError:
        return None
    except Exception:
        return None


def _abs(href, base):
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "sms:", "whatsapp:")):
        return None
    u = urljoin(base, href)
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return None
    return urlunparse((p.scheme, p.netloc, p.path or "/", "", p.query, ""))


def _noindex(p, headers):
    m = ",".join(filter(None, [p.meta("robots"), p.meta("googlebot")])).lower()
    return "noindex" in m or "noindex" in headers.get("x-robots-tag", "").lower()


def _first_href(links, base):
    return urljoin(base, links[0].get("href", "")) if links else None


# ---------------------------------------------------------------- server

def _tools():
    import tools  # imported lazily: tools.py builds on the fetch/parse helpers above
    return tools


TOOL_ROUTES = {
    "/api/keywords": lambda q: _tools().keyword_ideas(q.get("q", ""), q.get("hl", "en"), q.get("gl", "us"), q.get("deep", "1") != "0"),
    "/api/topics": lambda q: _tools().topics(q.get("q", ""), q.get("hl", "en"), q.get("gl", "us")),
    "/api/page": lambda q: _tools().page(q.get("url", ""), q.get("kw", "")),
    "/api/domain": lambda q: _tools().domain(q.get("d", "")),
    "/api/intent": lambda q: {"items": [{"kw": k, "intent": _tools().intent(k)} for k in
                                        [" ".join(x.lower().split()) for x in q.get("q", "").split("\n")][:500] if k]},
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _tool(self, fn):
        """Run a research tool; expected failures go back as {error} with a 200, like /api/audit."""
        try:
            return self._json(200, fn())
        except AuditError as e:
            return self._json(200, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"The tool failed unexpectedly: {e.__class__.__name__}"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/backlinks":
            self.send_error(404)
            return
        length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "Send JSON."})
        import tools
        return self._tool(lambda: tools.backlink_audit(str(body.get("target") or ""), [str(s) for s in body.get("sources") or []]))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/audit":
            q = parse_qs(u.query)
            target = (q.get("url") or [""])[0]
            if not target.strip():
                return self._json(400, {"error": "Add a website address to audit."})
            try:
                return self._json(200, run_audit(target, (q.get("kw") or [""])[0]))
            except AuditError as e:  # an expected outcome (site blocked, unknown domain), so not an HTTP error
                return self._json(200, {"error": str(e)})
            except Exception as e:  # keep the server alive and tell the page what happened
                return self._json(500, {"error": f"The audit failed unexpectedly: {e.__class__.__name__}"})
        if u.path == "/api/pagespeed":
            return self._pagespeed((parse_qs(u.query).get("url") or [""])[0])
        if u.path in TOOL_ROUTES:
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            return self._tool(lambda: TOOL_ROUTES[u.path](q))
        if u.path == "/api/health":
            return self._json(200, {"ok": True})
        # never serve source control, the server itself or dotfiles
        parts = [p for p in u.path.split("/") if p]
        if any(p.startswith(".") for p in parts) or u.path.endswith(".py"):
            self.send_error(404)
            return
        return super().do_GET()

    def _pagespeed(self, target):
        """Proxy PageSpeed Insights so an API key stays on the server."""
        if not re.match(r"^https?://", target or "", re.I):
            return self._json(400, {"error": {"message": "Missing url"}})
        q = "strategy=mobile&category=performance&url=" + quote(target, safe="")
        if PSI_KEY:
            q += "&key=" + quote(PSI_KEY, safe="")
        try:
            with build_opener().open(Request("https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + q, headers={"User-Agent": UA}), timeout=100) as r:
                body = json.loads(r.read())
            body["scoutlyHasKey"] = bool(PSI_KEY)
            return self._json(200, body)
        except HTTPError as e:
            try:
                body = json.loads(e.read())
            except Exception:
                body = {"error": {"message": f"HTTP {e.code}"}}
            # answer 200 and put Google's status in the body: a quota limit is an expected outcome,
            # not a broken request, so the browser shouldn't log it as a failed resource
            body["scoutlyHasKey"] = bool(PSI_KEY)
            body["scoutlyStatus"] = e.code
            return self._json(200, body)
        except Exception as e:
            return self._json(200, {"error": {"message": f"PageSpeed Insights didn't respond ({e.__class__.__name__})"}, "scoutlyStatus": 504, "scoutlyHasKey": bool(PSI_KEY)})

    def log_message(self, fmt, *args):
        msg = fmt % args
        if "/api/" in msg:  # keep the console quiet for static files
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), msg))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Scoutly running at http://localhost:{port}  (audit API: /api/audit?url=example.com)")
    print("PageSpeed Insights: " + ("using your API key" if PSI_KEY else "no PAGESPEED_API_KEY set, using Google's shared quota"))
    ThreadingHTTPServer(("", port), Handler).serve_forever()
