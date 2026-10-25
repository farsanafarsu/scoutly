"""Internal Link Opportunities: pages on a site that mention another page's topic without linking to it.

  * crawl(site): from the home page, follows internal links breadth-first (the way a crawler finds pages),
    then fills any room left with sitemap URLs nothing linked to; per page it keeps the title, H1, the
    body text split into blocks (paragraphs, list items, cells), and its links, each marked as in-content
    or template (nav, header, footer, aside, and any link that repeats on most pages)
  * analyze(pages): per page, in-content links in and out and click depth; then, for each page worth
    linking to, the phrases that describe it (its title, H1, URL slug and their distinctive 2-4 word
    parts) are searched for in other pages' unlinked text, giving a source page, anchor text and the
    sentence to put the link in

Only HTML pages that return 200 and are canonical to themselves are linked to or from.
"""
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

from server import SKIP_EXT, AuditError, _try, decode, fetch, norm_url, same_site

MAX_PAGES = 150           # hard cap per scan
BUDGET = 55               # seconds for starting new page fetches, so a scan stays inside the hosting proxy's ~100 s limit
WORKERS = 4               # gentle: many sites rate-limit bursts of requests
MAX_OPPS = 400            # opportunities sent back to the page
PER_PAIR_TARGET = 8       # suggestions kept per target page, best first
TEMPLATE_SHARE = 0.5      # a link or text block on at least this share of pages is part of the template

BLOCK_TAGS = {"h1", "p", "li", "td", "th", "dd", "dt", "blockquote", "figcaption", "h2", "h3", "h4", "h5", "h6", "div", "section", "article", "summary"}
BOILER_TAGS = {"nav", "header", "footer", "aside", "form"}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "select", "button", "iframe"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
STOP = set("""a an and are as at be but by for from has have how i if in into is it its of on or our so than that the their them then there
these this to too was we what when where which who why will with you your vs via can do does not no yes all any more most new best top guide
page home about contact us blog services service products product shop news post posts article articles category tag tags archive""".split())


class _Page(HTMLParser):
    """Title, H1, canonical, robots, links (with in-content flag) and text blocks made of linked / unlinked runs."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.title, self.h1, self.canonical, self.robots = "", "", None, ""
        self.links, self.blocks = [], []
        self._t = self._h = None
        self._skip = self._boiler = 0
        self._stack = []
        self._a = None
        self._runs = []

    def _flush(self):
        text = "".join(t for t, _ in self._runs)
        if len(text.split()) >= 4:
            runs, last = [], None
            for t, href in self._runs:
                if runs and href == last:
                    runs[-1][0] += t
                else:
                    runs.append([t, href])
                last = href
            self.blocks.append({"runs": [(re.sub(r"\s+", " ", t), h) for t, h in runs], "boiler": self._boiler > 0})
        self._runs = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title" and self._t is None and not self.title:
            self._t = []
        elif tag == "meta" and a.get("name", "").lower() in ("robots", "googlebot"):
            self.robots += "," + a.get("content", "").lower()
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and self.canonical is None:
            self.canonical = urljoin(self.base, a.get("href", ""))
        elif tag == "h1" and not self.h1 and self._h is None:
            self._h = []
        elif tag == "br":
            self._runs.append((" ", self._a["href"] if self._a else None))
        if tag in VOID:
            return
        role = a.get("role", "").lower()
        boiler = tag in BOILER_TAGS or role in ("navigation", "banner", "contentinfo", "complementary", "menu", "menubar")
        if tag in BLOCK_TAGS:
            self._flush()
        self._stack.append((tag, boiler))
        if boiler:
            self._boiler += 1
        if tag == "a":
            href = a.get("href", "")
            self._a = {"href": href, "rel": a.get("rel", "").lower(), "text": [], "boiler": self._boiler > 0}

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title" and self._t is not None:
            self.title = " ".join("".join(self._t).split())
            self._t = None
        elif tag == "h1" and self._h is not None:
            self.h1 = " ".join("".join(self._h).split())
            self._h = None
        if tag in VOID or not any(t == tag for t, _ in self._stack):
            return
        while self._stack:  # close anything left open inside it, the way browsers do
            t, boiler = self._stack.pop()
            if boiler:
                self._boiler -= 1
            if t == "a" and self._a is not None:
                self._a["text"] = " ".join("".join(self._a["text"]).split())
                self.links.append(self._a)
                self._a = None
            if t in BLOCK_TAGS:
                self._flush()
            if t == tag:
                break

    def handle_data(self, data):
        if self._t is not None:
            self._t.append(data)
            return
        if self._skip:
            return
        if self._h is not None:
            self._h.append(data)
        if self._a is not None:
            self._a["text"].append(data)
        self._runs.append((data, self._a["href"] if self._a else None))

    def close(self):
        super().close()
        self._flush()


def _clean(u):
    """Comparable form of a URL: no fragment, no trailing slash differences, no common tracking parameters."""
    p = urlparse(u)
    q = "&".join(x for x in p.query.split("&") if x and not re.match(r"(utm_\w+|gclid|fbclid|ref|replytocom)=", x, re.I))
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunparse(("", host, path, "", q, ""))[2:]


def _link(href, base):
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "sms:", "whatsapp:")):
        return None
    u = urljoin(base, href)
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return None
    return urlunparse((p.scheme, p.netloc, p.path or "/", "", p.query, ""))


def _read(url):
    """One page: fetched and parsed, or the reason it can't be used."""
    r = _try(fetch, url)
    if r and r["status"] in (429, 503):  # rate limited: wait a moment and ask once more
        time.sleep(2)
        r = _try(fetch, url) or r
    if not r:
        return {"url": url, "status": 0, "error": "No answer"}
    out = {"url": url, "final": r["url"], "status": r["status"]}
    ctype = r["headers"].get("content-type", "")
    if r["status"] != 200:
        return {**out, "error": f"HTTP {r['status']}"}
    if r["chain"]:
        out["redirected"] = True
    if "html" not in ctype and b"<html" not in r["body"][:2000].lower():
        return {**out, "error": "Not an HTML page"}
    P = _Page(r["url"])
    try:
        P.feed(decode(r))
        P.close()
    except Exception:
        pass
    noindex = "noindex" in P.robots or "noindex" in r["headers"].get("x-robots-tag", "").lower()
    canon_other = bool(P.canonical) and _clean(P.canonical) != _clean(r["url"])
    links = []
    for a in P.links:
        u = _link(a["href"], r["url"])
        if u:
            links.append({"url": u, "text": a["text"], "boiler": a["boiler"], "nofollow": "nofollow" in a["rel"]})
    return {**out, "title": P.title[:300], "h1": P.h1[:300], "links": links, "blocks": P.blocks,
            "noindex": noindex, "canonical": P.canonical if canon_other else None}


def _sitemap_urls(origin, limit):
    """Page URLs from the site's sitemaps (a quick read: a handful of files, a few seconds)."""
    import indexability
    rb = indexability.robots(origin)
    queue = [urljoin(origin + "/", s) for s in rb["sitemaps"]] or [origin + "/sitemap.xml", origin + "/sitemap_index.xml", origin + "/wp-sitemap.xml"]
    out, seen, files, started = [], set(), 0, time.time()
    while queue and files < 8 and len(out) < limit and time.time() - started < 12:
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
            queue += [indexability.LOC.search(e).group(1).strip() for e in indexability.ENTRY["sitemap"].findall(xml) if indexability.LOC.search(e)]
            continue
        for e in indexability.ENTRY["url"].findall(xml):
            m = indexability.LOC.search(e)
            if m:
                out.append(m.group(1).strip().replace("&amp;", "&"))
    return out


def crawl(site, limit=60):
    site = (site or "").strip()
    if not site:
        raise AuditError("Enter your website.")
    if not re.match(r"^https?://", site, re.I):
        site = "https://" + site
    p = urlparse(site)
    if not p.hostname:
        raise AuditError("That doesn't look like a web address.")
    limit = max(10, min(MAX_PAGES, int(limit) if str(limit).isdigit() else 60))
    try:
        home = fetch(f"{p.scheme}://{p.netloc}/")
    except AuditError as e:
        raise AuditError(f"Couldn't reach {p.hostname}. {e}")
    f = urlparse(home["url"])
    origin = f"{f.scheme}://{f.netloc}"
    started = time.time()
    pages, seen, depth = {}, set(), {}
    start = [origin + "/"]
    if p.path not in ("", "/"):
        start.append(norm_url(site))  # the page that was entered, if it isn't the home page
    queue = []
    for u in start:
        k = _clean(u)
        if k not in seen:
            seen.add(k)
            depth[k] = 0
            queue.append(u)
    sm_added = False
    while time.time() - started < BUDGET and len(pages) < limit:
        if not queue:
            if sm_added:
                break
            sm_added = True  # links found everything they can; sitemap URLs nothing linked to are orphan candidates
            for u in _sitemap_urls(origin, limit * 4):
                k = _clean(u)
                if same_site(u, origin) and not SKIP_EXT.search(urlparse(u).path) and k not in seen:
                    seen.add(k)
                    queue.append(u)
            if not queue:
                break
            continue
        batch, queue = queue[:min(WORKERS * 2, limit - len(pages))], queue[min(WORKERS * 2, limit - len(pages)):]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            got = list(pool.map(_read, batch))
        for pg in got:
            k = _clean(pg["url"])
            pg["key"] = k
            pg["depth"] = depth.get(k)
            pg["fromSitemap"] = k not in depth
            pages[k] = pg
            if pg.get("final") and _clean(pg["final"]) != k:
                fk = _clean(pg["final"])
                if fk in seen and fk != k:
                    pg["error"] = pg.get("error") or "Redirects to a page already scanned"
                seen.add(fk)
            for l in pg.get("links", []):
                if not same_site(l["url"], origin) or SKIP_EXT.search(urlparse(l["url"]).path):
                    continue
                lk = _clean(l["url"])
                if lk not in seen:
                    seen.add(lk)
                    if pg["depth"] is not None:
                        depth[lk] = pg["depth"] + 1
                    if not sm_added:
                        queue.append(l["url"])
                    else:
                        queue.insert(0, l["url"])
    return origin, list(pages.values()), not queue and len(pages) < limit, round(time.time() - started)


# ------------------------------------------------------------------ analysis

WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.U)


def _words(s):
    return WORD.findall((s or "").lower())


def _segments(title):
    return [s.strip() for s in re.split(r"\s+[|\-–—:·•»]\s+|\s*\|\s*|\s+::\s+", title or "") if s.strip()]


def _phrases(pg, common_segments):
    """Ways another page might mention this page, best first: (phrase words, source of the phrase, weight)."""
    out = []
    segs = [s for s in _segments(pg["title"]) if s.lower() not in common_segments]
    seen = set()

    def add(words, src, w):
        words = tuple(words)
        while words and words[0] in STOP:
            words = words[1:]
        while words and words[-1] in STOP:
            words = words[:-1]
        if not words or words in seen or all(x in STOP or x.isdigit() for x in words):
            return
        if len(words) == 1 and (w < 1 or len(words[0]) < 5):
            return  # one word alone is too vague, unless it's the whole title or H1
        seen.add(words)
        out.append((words, src, w))

    full = [(_words(pg["h1"]), "H1"), (_words(max(segs, key=len) if segs else ""), "title")]
    slug = urlparse(pg["final"] or pg["url"]).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.\w{2,5}$", "", slug)
    if "-" in slug or "_" in slug:
        full.append((_words(re.sub(r"[-_]+", " ", slug)), "URL"))
    for words, src in full:
        if 1 <= len(words) <= 8:
            add(words, src, 1.0)
    for words, src in full:  # shorter pieces: 4, 3 and 2 word runs of the same phrases
        for n in (4, 3, 2):
            for i in range(0, max(0, len(words) - n + 1)):
                add(words[i:i + n], src, 0.35 + 0.15 * n)
    return out


def _rx(words):
    """A phrase as whole words, any spacing or hyphens between them (text is matched lower-cased)."""
    return r"(?<![\w])" + r"[\s\-–]+".join(re.escape(w) for w in words) + r"(?![\w])"


def analyze(origin, pages, complete, focus="", keywords=""):
    ok = [p for p in pages if not p.get("error") and not p.get("noindex") and not p.get("canonical")]
    by = {p["key"]: p for p in ok}
    alias = {}
    for p in pages:
        if p.get("final"):
            alias[_clean(p["final"])] = p["key"]
    n = max(1, len(ok))

    # template links and text: whatever repeats across most pages (menus, footers, sidebars, cookie notes)
    link_freq, block_freq, seg_freq = Counter(), Counter(), Counter()
    for p in ok:
        link_freq.update({(_clean(l["url"]), l["text"].lower()) for l in p["links"]})
        block_freq.update({"".join(t for t, _ in b["runs"]).lower() for b in p["blocks"]})
        seg_freq.update({s.lower() for s in _segments(p["title"])})
    tmpl = TEMPLATE_SHARE * n if n >= 4 else n + 1
    common_segments = {s for s, c in seg_freq.items() if c >= max(3, 0.3 * n)}

    def target_of(url):
        k = _clean(url)
        return alias.get(k, k)

    inbound, inbound_all, outbound = {k: set() for k in by}, {k: set() for k in by}, {k: set() for k in by}
    links_to = {k: set() for k in by}
    for p in ok:
        for l in p["links"]:
            t = target_of(l["url"])
            if t not in by or t == p["key"]:
                continue
            links_to[p["key"]].add(t)
            inbound_all[t].add(p["key"])
            if not l["boiler"] and link_freq[(_clean(l["url"]), l["text"].lower())] < tmpl:
                inbound[t].add(p["key"])
                outbound[p["key"]].add(t)

    home = _clean(origin + "/")
    focus_k = target_of(focus) if focus else ""
    if focus and focus_k not in by:
        fp = _read(focus if re.match(r"^https?://", focus, re.I) else "https://" + focus)
        if fp.get("error"):
            raise AuditError(f"Couldn't read the page to link to ({fp['error']}).")
        fp["key"], fp["depth"], fp["fromSitemap"] = _clean(fp["url"]), None, False
        by[fp["key"]] = fp
        inbound[fp["key"]], inbound_all[fp["key"]], outbound[fp["key"]], links_to[fp["key"]] = set(), set(), set(), set()
        focus_k = fp["key"]
        pages = pages + [fp]

    # phrases per target; a phrase that describes several pages, or turns up on most pages, says nothing about any one of them
    def content(b):
        return not b["boiler"] and block_freq["".join(x for x, _ in b["runs"]).lower()] < tmpl

    text_of = {k: " ".join("".join(t for t, _ in b["runs"]) for b in p["blocks"] if content(b)).lower() for k, p in by.items()}
    phrases = {}
    owners = Counter()
    for k, p in by.items():
        if k == home and k != focus_k:
            continue
        ph = _phrases(p, common_segments)
        if k == focus_k:
            for kw in re.split(r"[,\n]+", keywords or ""):
                w = _words(kw)
                if w:
                    ph.insert(0, (tuple(w), "keyword", 1.2))
        phrases[k] = ph
        owners.update({w for w, _, _ in ph})
    common_text = Counter()
    rx = {w: re.compile(_rx(w)) for w in owners}
    for words, r in rx.items():
        common_text[words] = sum(1 for t in text_of.values() if r.search(t))

    opps = []
    targets = [focus_k] if focus_k else list(phrases)
    for t in targets:
        tp = by[t]
        cand = [(w, src, wt) for w, src, wt in phrases.get(t, [])
                if (owners[w] <= 2 or src == "keyword") and (common_text[w] <= max(4, 0.5 * n) or src == "keyword")]
        if not cand:
            continue
        any_rx = re.compile("|".join(_rx(w) for w, _, _ in cand))  # quick test on the whole page before going block by block
        found = []
        for s, sp in by.items():
            if s == t or t in outbound[s] or s not in outbound or not any_rx.search(text_of[s]):
                continue
            best = None
            for b in sp["blocks"]:
                if not content(b):
                    continue
                for ri, (run, href) in enumerate(b["runs"]):
                    if href is not None:
                        continue  # already a link (to this page or another): don't nest links
                    low = run.lower()
                    for words, src, wt in cand:
                        if best and best["w"] >= wt * len(words):
                            continue
                        m = (rx.get(words) or re.compile(_rx(words))).search(low)
                        hit = m and (m.start(), m.end())
                        if hit:
                            before = "".join(x for x, _ in b["runs"][:ri]) + run[:hit[0]]
                            after = run[hit[1]:] + "".join(x for x, _ in b["runs"][ri + 1:])
                            best = {"w": wt * len(words), "anchor": run[hit[0]:hit[1]], "src": src, "full": wt >= 1.0,
                                    "before": ("…" if len(before) > 90 else "") + before[-90:].lstrip(),
                                    "after": after[:90].rstrip() + ("…" if len(after) > 90 else "")}
            if best:
                found.append((s, best))
        weak = len(inbound[t])
        for s, best in found:
            sp = by[s]
            # stronger: the whole title / H1 matched, the target has few links in, the source is close to the home page and well linked
            score = best["w"] * 10 + max(0, 6 - weak) * 4 + (6 - min(5, sp.get("depth") if sp.get("depth") is not None else 5)) * 2 + min(10, len(inbound[s]))
            if s in links_to and t in links_to[s]:
                score -= 8  # it links already, but only from the menu or footer
            opps.append({"source": sp["final"] or sp["url"], "sourceTitle": sp["title"] or sp["h1"], "target": tp["final"] or tp["url"],
                         "targetTitle": tp["title"] or tp["h1"], "anchor": best["anchor"], "before": best["before"], "after": best["after"],
                         "matched": best["src"], "fullMatch": best["full"], "templateLinkOnly": t in links_to[s],
                         "score": round(score), "targetInbound": weak})
    opps.sort(key=lambda o: -o["score"])
    per, kept = Counter(), []
    for o in opps:
        if focus_k or per[o["target"]] < PER_PAIR_TARGET:
            per[o["target"]] += 1
            kept.append(o)
    if kept:
        hi = kept[0]["score"]
        for o in kept:
            o["strength"] = "strong" if o["fullMatch"] and o["score"] >= 0.6 * hi else "good" if o["fullMatch"] or o["score"] >= 0.45 * hi else "possible"

    rows = []
    for p in pages:
        k = p["key"]
        good = k in by
        rows.append({"url": p.get("final") or p["url"], "title": p.get("title") or p.get("h1") or "", "status": p.get("status", 0),
                     "depth": p.get("depth"), "fromSitemap": p.get("fromSitemap", False),
                     "inbound": len(inbound.get(k, ())), "inboundAll": len(inbound_all.get(k, ())), "outbound": len(outbound.get(k, ())),
                     "opportunities": per.get(p.get("final") or p["url"], 0) if good else 0,
                     "skipped": None if good else (p.get("error") or ("noindex" if p.get("noindex") else "Canonical points to another URL")),
                     "orphan": good and k != home and not inbound_all.get(k)})
    return {"origin": origin, "complete": complete, "scanned": len(pages), "usable": len(ok), "focus": by[focus_k]["final"] or by[focus_k]["url"] if focus_k else "",
            "opportunities": kept[:MAX_OPPS], "total": len(kept), "pages": rows}


def run(site, limit=60, focus="", keywords=""):
    origin, pages, complete, secs = crawl(site, limit)
    if not any(not p.get("error") for p in pages):
        raise AuditError("Couldn't read any pages on that site. It may block automated visits, or build its pages with JavaScript.")
    out = analyze(origin, pages, complete, (focus or "").strip(), keywords)
    out["seconds"] = secs
    return out
