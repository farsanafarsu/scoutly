"""Blog Bank: AI-drafted SEO blogs, saved in the team database to copy into a website later.

Same switch as the rest of the team data: MONGODB_URI (SCOUTLY_DEV_MEMORY_DB=1 keeps them in memory for local
tries; the tests call use_memory()). The /api/blogs routes need the team key like the Action Center, and every
draft belongs to the team workspace and records who created and last changed it (X-Scoutly-User).

Generation runs in background threads, at most MAX_RUNNING at a time, so a batch of ten survives the browser
closing: each draft is saved as "queued" first, filled in when its call succeeds, or marked failed with the
reason so that one draft can be retried without touching the others. A server restart loses running jobs,
never saved drafts: anything left queued or running for STALE_MINUTES is marked failed on the next read.

Edits are saved with optimistic locking (version), and the previous content goes to blog_versions so an
earlier version can be restored. Article HTML is cleaned to an allow-list on every write.
"""
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from server import AuditError
import blogai
import linkstore

WS = "team"                  # one workspace per server: the team behind SCOUTLY_TEAM_KEY
MAX_BATCH = 10
MAX_RUNNING = 3              # AI calls at once, across all batches
MAX_PER_HOUR = 40            # blog generations per hour, a guard against runaway costs
MAX_SECTIONS_PER_HOUR = 60
STALE_MINUTES = 25
SNAPSHOT_EVERY = 600         # autosaves keep one version snapshot per 10 minutes; explicit saves always do
KEEP_VERSIONS = 25
STATUSES = {"ready", "in_progress", "used"}
TONES = {"Professional", "Friendly", "Conversational", "Authoritative", "Persuasive", "Educational", "Witty"}

_lock = threading.Lock()
_blogs = _versions = None
_slots = threading.Semaphore(MAX_RUNNING)
_recent, _recent_sections = [], []
_pages_cache = {}


# ------------------------------------------------------------------ storage

def use_memory():
    global _blogs, _versions
    import memcoll
    with _lock:
        _blogs, _versions = memcoll.MemoryCollection("blogs"), memcoll.MemoryCollection("blog_versions")
    return _blogs, _versions


def configured():
    return bool(linkstore.URI) or os.environ.get("SCOUTLY_DEV_MEMORY_DB") == "1"


def _collections():
    global _blogs, _versions
    if _blogs is not None:
        return _blogs, _versions
    if os.environ.get("SCOUTLY_DEV_MEMORY_DB") == "1":
        return use_memory()
    with _lock:
        if _blogs is None:
            try:
                db = linkstore._collection().database
            except linkstore.NotConfigured:
                raise linkstore.NotConfigured("The team database isn't set up on the server yet (MONGODB_URI). The Blog Bank keeps drafts only in the database.")
            b, v = db["blogs"], db["blog_versions"]
            try:
                b.create_index([("ws", 1), ("createdAt", -1)], name="ws_created")
                b.create_index([("ws", 1), ("batchId", 1)], name="ws_batch")
                v.create_index([("blogId", 1), ("v", -1)], name="blog_v")
            except Exception as e:
                raise linkstore._db_error(e)
            _blogs, _versions = b, v
    return _blogs, _versions


def _db(fn):
    try:
        return fn()
    except AuditError:
        raise
    except Exception as e:
        raise linkstore._db_error(e)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _actor(by):
    return " ".join(str(by or "").split())[:120] or "Someone"


# ------------------------------------------------------------------ cleaning

def _text(v, n):
    return " ".join(str(v if v is not None else "").split())[:n]


class _Clean(HTMLParser):
    """Keeps article markup (headings, paragraphs, lists, links, tables, emphasis); drops everything else."""
    KEEP = {"h2", "h3", "h4", "p", "ul", "ol", "li", "strong", "em", "a", "table", "thead", "tbody", "tr", "th", "td", "blockquote", "br"}
    RENAME = {"b": "strong", "i": "em", "h1": "h2", "h5": "h4", "h6": "h4"}
    DROP = {"script", "style", "template", "noscript", "iframe", "object", "svg", "math", "head", "title", "button", "select", "textarea"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.open, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.DROP:
            self.skip += 1
            return
        if self.skip:
            return
        tag = self.RENAME.get(tag, tag)
        if tag not in self.KEEP:
            return
        if tag == "br":
            self.out.append("<br>")
            return
        a = ""
        if tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            if re.match(r"^(https?://|mailto:|/|#)", href, re.I) and not re.match(r"^//", href):
                a = f' href="{escape(href, quote=True)}"'
        elif tag in ("td", "th"):
            for k, v in attrs:
                if k in ("colspan", "rowspan") and str(v or "").isdigit() and 0 < int(v) < 50:
                    a += f' {k}="{int(v)}"'
        self.out.append(f"<{tag}{a}>")
        self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self.skip:
            self.out.append("<br>")

    def handle_endtag(self, tag):
        if tag in self.DROP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        tag = self.RENAME.get(tag, tag)
        if tag in self.open:
            while self.open:
                t = self.open.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, d):
        if not self.skip:
            self.out.append(escape(d, quote=False))

    def result(self):
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        html = "".join(self.out)
        html = re.sub(r"<(p|h2|h3|h4|li|strong|em)>\s*</\1>", "", html)  # empty blocks left by the editor
        return html.strip()


def clean_html(html):
    p = _Clean()
    try:
        p.feed(str(html or "")[:400000])
        p.close()
    except Exception:
        pass
    return p.result()


def _plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def words(html):
    return len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", _plain(html).replace("&amp;", "&")))


def headings(html):
    return [{"level": int(m.group(1)), "text": _text(re.sub(r"<[^>]+>", "", m.group(2)), 200)}
            for m in re.finditer(r"<h([2-4])>(.*?)</h\1>", html or "", re.S)]


def slugify(s):
    s = re.sub(r"[^\w\s-]", "", str(s or "").lower(), flags=re.U)
    return re.sub(r"[\s_-]+", "-", s).strip("-")[:90]


def _url(v):
    v = str(v or "").strip()[:500]
    return v if re.match(r"^https?://[^\s/]+\.[^\s]+$", v, re.I) else ""


def _pairs(items, keys, limits, n):
    out = []
    for it in (items or [])[:n]:
        if isinstance(it, dict):
            row = {k: _text(it.get(k), lim) for k, lim in zip(keys, limits)}
            if any(row.values()):
                out.append(row)
    return out


def _search_key(d):
    return " ".join([d.get("title", ""), d.get("primaryKeyword", ""), " ".join(d.get("secondaryKeywords", [])), d.get("batch", "")]).lower()


def _finish(d):
    """Derived fields, recomputed on every write."""
    d["html"] = clean_html(d.get("html", ""))
    d["wordCount"] = words(d["html"])
    d["headings"] = headings(d["html"])
    d["search"] = _search_key(d)
    return d


def _out(d, full=True):
    d = dict(d)
    d["id"] = d.pop("_id")
    d.pop("search", None)
    d.pop("ws", None)
    if not full:
        d.pop("html", None)
        for k in ("faqs", "internalLinks", "imageAlts", "headings"):
            d[k + "Count"] = len(d.pop(k, []) or [])
    return d


def _get(bid):
    blogs, _ = _collections()
    d = _db(lambda: blogs.find_one({"_id": str(bid or "")[:40], "ws": WS}))
    if not d:
        raise AuditError("That blog was deleted. Reload the Blog Bank.")
    return _stale(d)


def _stale(d):
    """A draft left queued or running for too long lost its job (a server restart): let it be retried."""
    if d.get("gen") in ("queued", "running"):
        t = d.get("genAt") or d.get("updatedAt") or ""
        try:
            old = datetime.fromisoformat(t.replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(minutes=STALE_MINUTES)
        except ValueError:
            old = True
        if old:
            d.update(gen="failed", genError="The server restarted before this blog finished. Retry it.")
            blogs, _ = _collections()
            _db(lambda: blogs.replace_one({"_id": d["_id"], "version": d["version"]}, d))
    return d


# ------------------------------------------------------------------ reading

def list_blogs(q):
    blogs, _ = _collections()
    flt = {"ws": WS}
    if q.get("status") in STATUSES:
        flt["status"] = q["status"]
    if q.get("batch"):
        flt["batchId"] = str(q["batch"])[:40]
    dates = {}
    if re.match(r"^\d{4}-\d{2}-\d{2}$", q.get("from", "")):
        dates["$gte"] = q["from"]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", q.get("to", "")):
        dates["$lte"] = q["to"] + "T23:59:59Z"
    if dates:
        flt["createdAt"] = dates
    text = _text(q.get("q"), 100).lower()
    if text:
        flt["search"] = {"$regex": re.escape(text)}
    sort = {"oldest": ("createdAt", 1), "title": ("title", 1)}.get(q.get("sort"), ("createdAt", -1))
    docs = _db(lambda: [_stale(d) for d in blogs.find(flt).sort(*sort).limit(1000)])
    if sort[0] == "title":
        docs.sort(key=lambda d: (d.get("title") or d.get("brief", {}).get("topic", "")).lower())
    batches = {}
    for d in _db(lambda: list(blogs.find({"ws": WS}))):
        b = batches.setdefault(d.get("batchId", ""), {"id": d.get("batchId", ""), "label": d.get("batch", ""), "count": 0, "created": d.get("createdAt", "")})
        b["count"] += 1
        b["created"] = min(b["created"], d.get("createdAt", ""))
    return {"items": [_out(d, False) for d in docs], "batches": sorted(batches.values(), key=lambda b: b["created"], reverse=True),
            "ai": blogai.configured(), "model": blogai.MODEL}


def get_blog(bid):
    return {"item": _out(_get(bid))}


def versions(bid):
    _, vers = _collections()
    _get(bid)
    rows = _db(lambda: list(vers.find({"blogId": str(bid)}).sort("v", -1).limit(KEEP_VERSIONS)))
    return {"items": [{"v": r["v"], "savedAt": r["savedAt"], "savedBy": r.get("savedBy", ""), "title": r["doc"].get("title", ""),
                       "words": r["doc"].get("wordCount", 0), "why": r.get("why", "")} for r in rows]}


def get_version(bid, v):
    _, vers = _collections()
    _get(bid)
    r = _db(lambda: vers.find_one({"blogId": str(bid), "v": int(v)}))
    if not r:
        raise AuditError("That version isn't kept any more.")
    return {"item": {**r["doc"], "id": str(bid), "v": r["v"], "savedAt": r["savedAt"]}}


# ------------------------------------------------------------------ writing

def _snapshot(d, by, why):
    _, vers = _collections()
    last = _db(lambda: next(iter(vers.find({"blogId": d["_id"]}).sort("v", -1).limit(1)), None))
    keep = {k: d.get(k) for k in ("title", "html", "metaTitle", "metaDescription", "slug", "primaryKeyword", "secondaryKeywords",
                                   "faqs", "internalLinks", "imageAlts", "wordCount", "status", "publishedUrl")}
    v = (last["v"] if last else 0) + 1
    _db(lambda: vers.insert_one({"_id": f"{d['_id']}-{v}", "blogId": d["_id"], "v": v, "savedAt": now_iso(), "savedBy": _actor(by), "why": why, "doc": keep}))
    if v > KEEP_VERSIONS:
        _db(lambda: vers.delete_many({"blogId": d["_id"], "v": {"$lte": v - KEEP_VERSIONS}}))


def _save(old, new, by, why="", snapshot=False):
    """Write with optimistic locking; the old content becomes a version when it is about to change."""
    blogs, vers = _collections()
    if snapshot and old.get("gen") == "done" and old.get("html"):
        _snapshot(old, by, why)
    new = _finish({**new, "version": old["version"] + 1, "updatedAt": now_iso(), "updatedBy": _actor(by)})
    r = _db(lambda: blogs.replace_one({"_id": old["_id"], "version": old["version"]}, new))
    if not r.matched_count:
        e = AuditError("Someone else saved this blog a moment ago. Your text is kept in this browser; reload to see theirs, then paste your changes back.")
        e.extra = {"conflict": True}
        raise e
    return new


EDITABLE = {"title": 200, "metaTitle": 120, "metaDescription": 320, "slug": 90, "primaryKeyword": 120, "publishedUrl": 500, "batch": 80}


def update(bid, version, changes, by, checkpoint=False):
    d = _get(bid)
    if d.get("gen") in ("queued", "running"):
        raise AuditError("This blog is still being written. Wait until it's ready, then edit it.")
    if version is not None and int(version) != d["version"]:
        e = AuditError("This blog changed since you opened it (someone else saved it, or another tab). Your text is kept in this browser; reload to see the latest.")
        e.extra = {"conflict": True}
        raise e
    changes = changes or {}
    new = dict(d)
    for k, n in EDITABLE.items():
        if k in changes:
            new[k] = _text(changes[k], n)
    if "slug" in changes:
        new["slug"] = slugify(changes["slug"])
    if "publishedUrl" in changes:
        new["publishedUrl"] = _url(changes["publishedUrl"])
        if changes["publishedUrl"] and not new["publishedUrl"]:
            raise AuditError("The published URL should be a full address starting with https://.")
    if "status" in changes:
        if changes["status"] not in STATUSES:
            raise AuditError("Status must be Ready to Use, In Progress or Used.")
        new["status"] = changes["status"]
        if changes["status"] == "used" and not d.get("usedAt"):
            new["usedAt"] = now_iso()
    if "html" in changes:
        new["html"] = str(changes["html"] or "")
    if "secondaryKeywords" in changes:
        new["secondaryKeywords"] = [_text(k, 80) for k in (changes["secondaryKeywords"] or [])[:20] if _text(k, 80)]
    if "faqs" in changes:
        new["faqs"] = _pairs(changes["faqs"], ("q", "a"), (300, 2000), 20)
    if "internalLinks" in changes:
        new["internalLinks"] = [{**r, "url": _url(r["url"])} for r in _pairs(changes["internalLinks"], ("anchor", "url", "why"), (120, 500, 300), 20)]
    if "imageAlts" in changes:
        new["imageAlts"] = _pairs(changes["imageAlts"], ("where", "alt"), (120, 250), 20)
    content_changed = any(new.get(k) != d.get(k) for k in ("html", "title", "faqs", "metaTitle", "metaDescription"))
    if not any(new.get(k) != d.get(k) for k in new):
        return {"item": _out(d)}
    due = checkpoint or not d.get("snapAt") or time.time() - float(d.get("snapAt") or 0) > SNAPSHOT_EVERY
    if content_changed and due:
        new["snapAt"] = time.time()
    return {"item": _out(_save(d, new, by, "Before an edit", snapshot=content_changed and due))}


def duplicate(bid, by):
    blogs, _ = _collections()
    d = _get(bid)
    if d.get("gen") != "done":
        raise AuditError("Only finished blogs can be duplicated.")
    t = now_iso()
    copy = {**d, "_id": os.urandom(8).hex(), "title": (d.get("title") or "")[:190] + " (copy)", "status": "in_progress", "publishedUrl": "", "usedAt": "",
            "createdAt": t, "updatedAt": t, "createdBy": _actor(by), "updatedBy": _actor(by), "version": 1, "snapAt": 0,
            "slug": (d.get("slug") or "")[:84] + "-copy"}
    _db(lambda: blogs.insert_one(_finish(copy)))
    return {"item": _out(copy)}


def delete(bid):
    blogs, vers = _collections()
    _get(bid)
    _db(lambda: blogs.delete_many({"_id": str(bid), "ws": WS}))
    _db(lambda: vers.delete_many({"blogId": str(bid)}))
    return {"deleted": 1}


def restore(bid, v, version, by):
    _, vers = _collections()
    d = _get(bid)
    if version is not None and int(version) != d["version"]:
        e = AuditError("This blog changed since you opened it. Reload, then restore again.")
        e.extra = {"conflict": True}
        raise e
    r = _db(lambda: vers.find_one({"blogId": str(bid), "v": int(v)}))
    if not r:
        raise AuditError("That version isn't kept any more.")
    new = {**d, **{k: r["doc"].get(k, d.get(k)) for k in ("title", "html", "metaTitle", "metaDescription", "slug", "primaryKeyword",
                                                          "secondaryKeywords", "faqs", "internalLinks", "imageAlts")}, "snapAt": time.time()}
    return {"item": _out(_save(d, new, by, f"Before restoring version {v}", snapshot=True))}


# ------------------------------------------------------------------ generation

def _brief(settings, item):
    s, it = settings or {}, item or {}
    b = {"topic": _text(it.get("topic"), 200), "keyword": _text(it.get("keyword"), 120), "notes": _text(it.get("notes"), 500),
         "location": _text(s.get("location"), 120), "language": _text(s.get("language"), 60) or "English",
         "audience": _text(s.get("audience"), 200), "tone": _text(s.get("tone"), 40) or "Professional",
         "brand": _text(s.get("brand"), 1500), "website": ""}
    try:
        b["words"] = max(500, min(4000, int(s.get("words") or 1500)))
    except (TypeError, ValueError):
        b["words"] = 1500
    site = _text(s.get("website"), 300)
    if site:
        site = site if re.match(r"^https?://", site, re.I) else "https://" + site
        if not re.match(r"^https?://[^\s/]+\.[^\s/]+", site):
            raise AuditError("The website should look like yourwebsite.com.")
        b["website"] = site
    if not b["topic"]:
        raise AuditError("Each blog needs a topic.")
    if not b["keyword"]:
        raise AuditError(f"Add a primary keyword for “{b['topic']}”.")
    return b


def _hourly(bucket, n, limit, what):
    cut = time.time() - 3600
    with _lock:
        bucket[:] = [t for t in bucket if t > cut]
        if len(bucket) + n > limit:
            raise AuditError(f"That would go over the limit of {limit} {what} an hour (a guard against runaway AI costs). Try again later, or send fewer.")
        bucket.extend([time.time()] * n)


def generate(batch, settings, items, by):
    """Save every topic as a queued draft right away, then write them in the background."""
    blogs, _ = _collections()
    blogai._anthropic()  # fail now, not per draft, when AI isn't set up
    items = [i for i in (items or []) if isinstance(i, dict) and (_text(i.get("topic"), 10) or _text(i.get("keyword"), 10))]
    if not items:
        raise AuditError("Add at least one topic with a primary keyword.")
    if len(items) > MAX_BATCH:
        raise AuditError(f"Generate up to {MAX_BATCH} blogs at a time.")
    briefs = [_brief(settings, i) for i in items]
    _hourly(_recent, len(briefs), MAX_PER_HOUR, "blogs")
    batch = batch or {}
    bid_batch = _text(batch.get("id"), 40) or os.urandom(6).hex()
    label = _text(batch.get("label"), 80) or "Weekend batch · " + datetime.now().strftime("%a %d %b %Y")
    t = now_iso()
    docs = []
    for b in briefs:
        d = {"_id": os.urandom(8).hex(), "ws": WS, "batchId": bid_batch, "batch": label, "brief": b, "status": "ready",
             "gen": "queued", "genError": "", "genAt": t, "title": b["topic"], "html": "", "primaryKeyword": b["keyword"],
             "secondaryKeywords": [], "metaTitle": "", "metaDescription": "", "slug": slugify(b["keyword"]), "faqs": [],
             "internalLinks": [], "imageAlts": [], "publishedUrl": "", "usedAt": "", "createdAt": t, "updatedAt": t,
             "createdBy": _actor(by), "updatedBy": _actor(by), "version": 1, "snapAt": 0}
        _db(lambda: blogs.insert_one(_finish(d)))
        docs.append(d)
    for d in docs:
        _start(d["_id"], by)
    return {"batchId": bid_batch, "batch": label, "items": [_out(d, False) for d in docs]}


def retry(bid, by):
    d = _get(bid)
    if d.get("gen") != "failed":
        raise AuditError("Only blogs that failed to generate can be retried. To change a finished blog, edit it or regenerate a section.")
    _hourly(_recent, 1, MAX_PER_HOUR, "blogs")
    new = {**d, "gen": "queued", "genError": "", "genAt": now_iso()}
    new = _save(d, new, by)
    _start(new["_id"], by)
    return {"item": _out(new, False)}


def _start(bid, by):
    threading.Thread(target=_run, args=(bid, by), daemon=True, name=f"blog-{bid}").start()


def _pages(website, keyword):
    """Up to 60 of the site's own URLs, most relevant to the keyword first, for real internal link suggestions."""
    if not website:
        return []
    origin = "{0.scheme}://{0.netloc}".format(urlparse(website))
    hit = _pages_cache.get(origin)
    if not hit or time.time() - hit[0] > 3600:
        try:
            import indexability
            urls = [u["url"] for u in indexability.sitemaps(origin)["urls"]]
        except Exception:
            urls = []
        hit = (time.time(), urls)
        _pages_cache[origin] = hit
    words_ = set(re.findall(r"\w+", keyword.lower()))

    def label(u):
        path = urlparse(u).path.strip("/")
        return path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ") or "home page"
    ranked = sorted(hit[1], key=lambda u: -len(words_ & set(re.findall(r"\w+", u.lower()))))
    return [(u, label(u)) for u in ranked[:60]]


def _run(bid, by):
    blogs, _ = _collections()
    with _slots:
        try:
            d = _get(bid)
        except AuditError:
            return  # deleted while it waited
        if d.get("gen") != "queued":
            return
        d = _save(d, {**d, "gen": "running", "genAt": now_iso()}, by)
        try:
            b = d["brief"]
            out = blogai.write_blog(b, _pages(b.get("website"), b["keyword"]))
            fill = {"gen": "done", "genError": "", "title": _text(out.get("title"), 200) or b["topic"], "html": out.get("article_html", ""),
                    "metaTitle": _text(out.get("meta_title"), 120), "metaDescription": _text(out.get("meta_description"), 320),
                    "slug": slugify(out.get("slug")) or slugify(b["keyword"]),
                    "secondaryKeywords": [_text(k, 80) for k in out.get("secondary_keywords", [])[:12] if _text(k, 80)],
                    "faqs": _pairs([{"q": f.get("question"), "a": f.get("answer")} for f in out.get("faqs", []) if isinstance(f, dict)], ("q", "a"), (300, 2000), 12),
                    "internalLinks": [{"anchor": _text(r.get("anchor_text"), 120), "url": _url(r.get("url")), "why": _text(r.get("reason"), 300)}
                                      for r in out.get("internal_links", [])[:10] if isinstance(r, dict)],
                    "imageAlts": _pairs([{"where": r.get("placement"), "alt": r.get("alt_text")} for r in out.get("image_alts", []) if isinstance(r, dict)], ("where", "alt"), (120, 250), 8)}
        except AuditError as e:
            fill = {"gen": "failed", "genError": str(e)}
        except Exception as e:
            fill = {"gen": "failed", "genError": f"Generation failed unexpectedly ({e.__class__.__name__}). Retry it."}
        try:
            cur = _get(bid)
        except AuditError:
            return
        try:
            _save(cur, {**cur, **fill}, by)
        except AuditError:
            pass  # changed meanwhile (e.g. deleted and re-added); never overwrite someone's edit


def rewrite_section(bid, section_html, instruction, by):
    d = _get(bid)
    if d.get("gen") != "done":
        raise AuditError("This blog isn't finished yet.")
    section = clean_html(section_html)
    if len(_plain(section)) < 20:
        raise AuditError("Pick a section with some text in it.")
    _hourly(_recent_sections, 1, MAX_SECTIONS_PER_HOUR, "section rewrites")
    html = clean_html(blogai.rewrite_section(d, section, _text(instruction, 500)))
    if len(_plain(html)) < 20:
        raise AuditError("The AI returned an empty section. Try again.")
    return {"html": html}
