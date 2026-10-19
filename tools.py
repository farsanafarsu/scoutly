"""Scoutly research tools: keyword ideas, page analysis, domain facts and backlink checks.

Everything here uses free, public sources:
  * keyword ideas come from Google Autocomplete (the suggestions under the search box)
  * page and domain facts come from fetching the site itself, RDAP (domain registration),
    Google Public DNS over HTTPS and the site's TLS certificate
Search volume, keyword difficulty, rankings, traffic and backlink indexes are NOT available
for free; the pages that need them say so instead of showing made-up numbers.
"""
import json
import re
import socket
import ssl
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlparse

from server import UA, AuditError, _abs, _try, decode, fetch, norm_url, parse, safe_host, same_site

STOP = set("""a about above after again against all am an and any are aren't as at be because been before being below between both but by
can can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from further had hadn't has hasn't have haven't
having he he'd he'll he's her here here's hers herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself let's
me more most mustn't my myself no nor not of off on once only or other ought our ours ourselves out over own same shan't she she'd she'll she's
should shouldn't so some such than that that's the their theirs them themselves then there there's these they they'd they'll they're they've
this those through to too under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's where where's which while
who who's whom why why's will with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves also just get got like one
two us may new use using used via per etc com www http https amp nbsp s t re ve ll d m""".split())

META_REFRESH = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\']?\s*\d+\s*;\s*url=([^"\'>\s]+)', re.I)


def fetch_page(url, timeout=25):
    """fetch() plus one hop of <meta http-equiv="refresh"> redirects (e.g. ruby-lang.org → /en/)."""
    res = fetch(url, timeout=timeout)
    if "html" in res["headers"].get("content-type", "") and len(res["body"]) < 20000:
        m = META_REFRESH.search(res["body"][:20000].decode("utf-8", errors="replace"))
        if m:
            nxt = urljoin(res["url"], m.group(1).strip())
            if nxt != res["url"]:
                try:
                    r2 = fetch(nxt, timeout=timeout)
                    r2["chain"] = res["chain"] + [{"url": res["url"], "status": "meta refresh"}] + r2["chain"]
                    return r2
                except AuditError:
                    pass
    return res


# ------------------------------------------------------------------ keyword ideas

_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    if len(_cache) > 5000:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:1000]:
            _cache.pop(k, None)
    return val


def suggest(q, hl="en", gl="us"):
    """Google Autocomplete suggestions for one query (up to ~10)."""
    def go():
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&hl={quote(hl)}&gl={quote(gl)}&q={quote(q)}"
        r = fetch(url)
        if r["status"] != 200:
            return []
        try:
            data = json.loads(r["body"].decode("utf-8", errors="replace"))
            return [s.strip() for s in data[1] if isinstance(s, str) and s.strip()]
        except Exception:
            return []
    return _cached(("s", q.lower(), hl, gl), 6 * 3600, go)


QUESTION_WORDS = ["how", "what", "why", "when", "where", "who", "which", "can", "does", "is", "are", "should", "will"]
PREPOSITIONS = ["for", "with", "without", "near", "to", "in", "vs", "like", "and", "or", "best", "cheap", "free"]

QUESTION_RX = re.compile(r"^(how|what|why|when|where|who|which)\b|^(can|does|do|is|are|should|will)\s+(?!a\b|an\b|the\b|one\b)\w|\?$")
INTENT_RULES = [
    ("N", r"\b(login|log in|sign in|signin|sign up|official site|official website|customer care|customer service|phone number|contact number)\b|\.(com|in|org|net|io)\b"),
    ("I", r"^(how|what|why|when|where|who|which)\b|\b(guide|tutorial|tips|ideas|examples?|meaning|definition|learn|course|benefits|"
          r"side effects|steps|checklist|history|facts|explained|process|recovery|before and after|aftercare|types of)\b"),
    ("T", r"\b(buy|price|prices|pricing|cost|costs|cheap|cheapest|deal|deals|discount|coupon|promo|order|hire|book|booking|for sale|shop|purchase|"
          r"subscription|subscribe|download|free|trial|quote|rent|rental|near me|delivery|online|appointment)\b"),
    ("C", r"\b(best|top|review|reviews|vs|versus|compare|comparison|alternative|alternatives|affordable|rated|ranking|brands?|recommended|worth it|"
          r"tool|tools|software|app|apps|checker|platform|services?|agency|agencies|company|companies|clinic|clinics|providers?|doctor|specialist)\b"),
]


def intent(kw):
    """Search intent from the wording: I informational, N navigational, C commercial, T transactional.
    The first matching rule wins, so a question about prices ("how much does x cost") stays informational."""
    k = kw.lower()
    for code, rx in INTENT_RULES:
        if re.search(rx, k):
            return code
    return "I" if len(k.split()) >= 4 else "C"


def keyword_ideas(seed, hl="en", gl="us", deep=True):
    seed = " ".join(seed.lower().split())[:80]
    if not seed:
        raise AuditError("Enter a keyword.")
    jobs = [("seed", seed), ("seed", seed + " ")]
    if deep:
        jobs += [("a–z", f"{seed} {c}") for c in "abcdefghijklmnopqrstuvwxyz0123456789"[:30]]
        jobs += [("questions", f"{w} {seed}") for w in QUESTION_WORDS]
        jobs += [("prepositions", f"{seed} {w}") for w in PREPOSITIONS]
        jobs += [("prefix", f"{w} {seed}") for w in ("best", "top", "cheap", "free", "online")]
    found = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for (src, q), res in zip(jobs, pool.map(lambda j: _try(suggest, j[1], hl, gl) or [], jobs)):
            for i, s in enumerate(res):
                k = " ".join(s.lower().split())
                if k not in found:
                    found[k] = {"src": src, "rank": i}
    seed_toks = set(re.findall(r"\w+", seed))
    rows = []
    for k, meta in found.items():
        toks = re.findall(r"\w+", k)
        q = bool(QUESTION_RX.search(k))
        rows.append({"kw": k, "intent": intent(k), "words": len(toks), "question": q,
                     "local": bool(re.search(r"\bnear me\b|\bnear\b|\bin [a-z]+$", k)), "src": meta["src"],
                     "rank": meta["rank"], "hasSeed": seed_toks.issubset(set(toks))})
    rows.sort(key=lambda r: (not r["hasSeed"], r["rank"], r["words"], r["kw"]))
    groups = Counter(t for r in rows for t in set(re.findall(r"[a-z0-9]+", r["kw"])) if t not in seed_toks and t not in STOP and len(t) > 1)
    return {"seed": seed, "hl": hl, "gl": gl, "count": len(rows), "keywords": rows,
            "groups": [{"term": t, "count": c} for t, c in groups.most_common(40) if c >= 2]}


def topics(seed, hl="en", gl="us"):
    """Topic Research: subtopics built from autocomplete, each with the questions people ask."""
    data = keyword_ideas(seed, hl, gl)
    seed_toks = set(re.findall(r"\w+", data["seed"]))
    cards = {}
    for r in data["keywords"]:
        extra = [t for t in re.findall(r"[a-z0-9]+", r["kw"]) if t not in seed_toks and t not in STOP and len(t) > 2]
        if not extra:
            continue
        key = extra[0]
        c = cards.setdefault(key, {"topic": key, "keywords": [], "questions": []})
        (c["questions"] if r["question"] else c["keywords"]).append(r["kw"])
    out = sorted(cards.values(), key=lambda c: -(len(c["keywords"]) + 2 * len(c["questions"])))
    qs = [r["kw"] for r in data["keywords"] if r["question"]]
    return {"seed": data["seed"], "cards": [c for c in out if len(c["keywords"]) + len(c["questions"]) >= 2][:24],
            "questions": qs[:60], "count": data["count"]}


# ------------------------------------------------------------------ page analysis

def syllables(word):
    w = word.lower()
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def readability(text):
    sentences = max(1, len(re.findall(r"[.!?]+(\s|$)", text)))
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return None
    syl = sum(syllables(w) for w in words)
    return round(206.835 - 1.015 * (len(words) / sentences) - 84.6 * (syl / len(words)), 1)


def terms(text, top=150):
    toks = [t.strip("-'’") for t in re.findall(r"[a-z0-9][a-z0-9'’-]*", text.lower())]
    toks = [t for t in toks if t]
    c = Counter()
    for n in (1, 2, 3):
        for i in range(len(toks) - n + 1):
            g = toks[i:i + n]
            if g[0] in STOP or g[-1] in STOP or any(len(x) < 2 or re.fullmatch(r"[\d'’-]+", x) for x in g):
                continue
            c[" ".join(g)] += 1
    out = []
    for g, n in c.most_common(top * 3):
        if n < 2 and len(out) > 40:
            continue
        out.append({"term": g, "count": n})
        if len(out) >= top:
            break
    return out


def page(url, keyword=""):
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.strip()
    res = fetch_page(url, timeout=25)
    text = decode(res) if "html" in res["headers"].get("content-type", "") else ""
    P = parse(text)
    body = P.body_text
    h = [{"level": l, "text": t} for l, t in P.headings]
    imgs = [i for i in P.images if i.get("src") or i.get("data-src")]
    schema, local = [], []
    for block in P.jsonld:
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if isinstance(o.get("@graph"), list):
                    stack.extend(o["@graph"])
                t = o.get("@type")
                for tt in (t if isinstance(t, list) else [t] if t else []):
                    schema.append(str(tt))
                    if str(tt).endswith(("Business", "Organization")) or str(tt) in ("Dentist", "MedicalClinic", "Physician", "Restaurant", "Store", "Hotel", "LegalService", "AutoRepair", "HealthAndBeautyBusiness", "BeautySalon"):
                        addr = o.get("address")
                        if isinstance(addr, dict):
                            addr = ", ".join(str(addr.get(k)) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry") if addr.get(k))
                        local.append({"type": str(tt), "name": o.get("name"), "telephone": o.get("telephone"), "address": addr,
                                      "geo": bool(o.get("geo")), "hours": bool(o.get("openingHours") or o.get("openingHoursSpecification")),
                                      "rating": bool(o.get("aggregateRating")), "url": o.get("url"), "sameAs": o.get("sameAs") if isinstance(o.get("sameAs"), list) else []})
    links = [(_abs(a["href"], res["url"]), a) for a in P.anchors]
    final = res["url"]
    weighted = " ".join([(P.title or "")] * 3 + [t for l, t in P.headings if l == 1] * 2 + [t for l, t in P.headings if l in (2, 3)] + [body])
    return {
        "url": url, "finalUrl": final, "status": res["status"], "ms": res["ttfb"],
        "title": P.title, "description": P.meta("description"), "lang": P.lang,
        "h1": [t for l, t in P.headings if l == 1], "headings": h[:80], "words": len(P.words),
        "readability": readability(body), "text": body[:60000],  # long enough to reach footers (addresses, phone numbers)
        "terms": terms(weighted), "schema": sorted(set(schema)), "local": local,
        "images": len(imgs), "imagesNoAlt": len([i for i in imgs if "alt" not in i]),
        "internalLinks": len({norm_url(u) for u, a in links if u and same_site(u, final)}),
        "externalLinks": len({norm_url(u) for u, a in links if u and not same_site(u, final)}),
        "tel": sorted({a["href"][4:].strip() for a in P.anchors if a["href"].lower().startswith("tel:")})[:10],
        "maps": any(re.search(r"google\.[a-z.]+/maps|maps\.google|goo\.gl/maps|maps\.app\.goo\.gl", a["href"] or "") for a in P.anchors)
                or bool(re.search(r"<iframe[^>]+(google\.[a-z.]+/maps|maps\.google)", text, re.I)),
        "canonical": urljoin(final, P.rel("canonical")[0].get("href", "")) if P.rel("canonical") else None,
        "viewport": bool(P.meta("viewport")), "noindex": "noindex" in (P.meta("robots") or "").lower(),
        "ogImage": bool(P.meta("og:image", "property") or P.meta("og:image")),
    }


# ------------------------------------------------------------------ domain facts

TECH = [
    ("WordPress", r"wp-content|wp-includes|<meta[^>]+generator[^>]+wordpress"), ("WooCommerce", r"woocommerce"),
    ("Shopify", r"cdn\.shopify\.com|shopify\.theme|myshopify"), ("Wix", r"static\.wixstatic\.com|wix\.com"),
    ("Squarespace", r"squarespace\.com|static1\.squarespace"), ("Webflow", r"webflow\.(com|io)|data-wf-page"),
    ("Drupal", r"drupal|sites/default/files"), ("Joomla", r"/media/jui/|joomla"), ("Ghost", r"ghost\.(io|org)|content=\"ghost"),
    ("HubSpot", r"js\.hs-scripts\.com|hubspot"), ("Next.js", r"__NEXT_DATA__|/_next/static"), ("Nuxt", r"__NUXT__|/_nuxt/"),
    ("Gatsby", r"___gatsby"), ("React", r"react(-dom)?(\.production)?(\.min)?\.js|data-reactroot"), ("Vue.js", r"vue(\.runtime)?(\.min)?\.js|data-v-[0-9a-f]{6}"),
    ("Angular", r"ng-version=|angular(\.min)?\.js"), ("Svelte", r"svelte"), ("jQuery", r"jquery(\.min)?\.js|jquery-[0-9.]+"),
    ("Bootstrap", r"bootstrap(\.min)?\.(css|js)"), ("Tailwind CSS", r"tailwind"), ("Elementor", r"elementor"),
    ("Yoast SEO", r"yoast"), ("Rank Math", r"rank-math|rankmath"), ("Google Analytics", r"google-analytics\.com|gtag\(|googletagmanager\.com/gtag|['\"]G-[A-Z0-9]{6,}"),
    ("Google Tag Manager", r"googletagmanager\.com/gtm|GTM-[A-Z0-9]{4,}"), ("Meta Pixel", r"connect\.facebook\.net|fbq\("),
    ("Hotjar", r"hotjar"), ("Microsoft Clarity", r"clarity\.ms"), ("Google Ads", r"googleadservices|AW-[0-9]{6,}"),
    ("Intercom", r"intercom"), ("Zendesk", r"zdassets|zendesk"), ("Tawk.to", r"tawk\.to"), ("Crisp", r"crisp\.chat"),
    ("Stripe", r"js\.stripe\.com"), ("Razorpay", r"razorpay"), ("PayPal", r"paypal\.com/sdk"), ("reCAPTCHA", r"recaptcha"),
    ("Cloudflare", r"cdnjs\.cloudflare|cloudflareinsights"), ("Font Awesome", r"font-?awesome"), ("Google Fonts", r"fonts\.googleapis\.com"),
]
SOCIAL = [("Facebook", r"facebook\.com/(?!sharer|share|dialog|plugins|tr\b)[\w.\-]+"), ("Instagram", r"instagram\.com/[\w.\-]+"),
          ("X / Twitter", r"(?:twitter|x)\.com/(?!share|intent|home)[\w]+"), ("LinkedIn", r"linkedin\.com/(?:company|in|school)/[\w.\-%]+"),
          ("YouTube", r"youtube\.com/(?:@|c/|channel/|user/)[\w.\-]+"), ("TikTok", r"tiktok\.com/@[\w.\-]+"), ("Pinterest", r"pinterest\.[a-z.]+/[\w.\-]+"),
          ("GitHub", r"github\.com/[\w.\-]+"), ("WhatsApp", r"wa\.me/\d+|api\.whatsapp\.com")]


def _doh(name, rtype):
    r = _try(fetch, f"https://dns.google/resolve?name={quote(name)}&type={rtype}")
    if not r or r["status"] != 200:
        return []
    try:
        return [a.get("data", "").rstrip(".") for a in json.loads(r["body"]).get("Answer", []) if a.get("type") in ({"A": 1, "AAAA": 28, "MX": 15, "NS": 2, "TXT": 16}[rtype],)]
    except Exception:
        return []


def _rdap(domain):
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        d = ".".join(parts[i:])
        r = _try(fetch, f"https://rdap.org/domain/{d}")
        if r and r["status"] == 200:
            try:
                j = json.loads(r["body"])
            except Exception:
                continue
            ev = {e.get("eventAction"): e.get("eventDate") for e in j.get("events", [])}
            registrar = None
            for e in j.get("entities", []):
                if "registrar" in e.get("roles", []):
                    v = e.get("vcardArray", [None, []])[1]
                    registrar = next((x[3] for x in v if x[0] == "fn"), None) or e.get("handle")
            return {"domain": d, "created": ev.get("registration"), "expires": ev.get("expiration"), "updated": ev.get("last changed"), "registrar": registrar}
    return None


def _cert(host):
    try:
        safe_host(host)
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as s, ctx.wrap_socket(s, server_hostname=host) as ss:
            c = ss.getpeercert()
        issuer = dict(x[0] for x in c.get("issuer", ()))
        exp = datetime.strptime(c["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return {"valid": True, "issuer": issuer.get("organizationName") or issuer.get("commonName"), "expires": exp.isoformat(),
                "daysLeft": (exp - datetime.now(timezone.utc)).days, "names": [v for k, v in c.get("subjectAltName", ())][:8]}
    except ssl.SSLCertVerificationError as e:
        return {"valid": False, "error": str(e.verify_message or e)}
    except Exception as e:
        return {"valid": None, "error": e.__class__.__name__}


def domain(d):
    d = d.strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].split("?")[0]
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", d):
        raise AuditError("Enter a domain like example.com.")
    host = d
    try:
        res = fetch_page("https://" + host, timeout=25)
    except AuditError:
        res = fetch_page("http://" + host, timeout=25)
    final = res["url"]
    fh = urlparse(final).hostname
    text = decode(res) if "html" in res["headers"].get("content-type", "") else ""
    P = parse(text)
    blob = text[:400000] + " " + " ".join(f"{k}: {v}" for k, v in res["headers"].items())
    tech = [n for n, rx in TECH if re.search(rx, blob, re.I)]
    hdr = res["headers"]
    server = hdr.get("server")
    if "cf-ray" in hdr:
        tech.append("Cloudflare CDN")
    if hdr.get("x-vercel-id"):
        tech.append("Vercel")
    if "netlify" in (server or "").lower():
        tech.append("Netlify")
    socials = {}
    for a in P.anchors:
        for name, rx in SOCIAL:
            m = re.search(rx, a["href"] or "", re.I)
            if m and name not in socials:
                socials[name] = a["href"]
    origin = f"{urlparse(final).scheme}://{urlparse(final).netloc}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        f_rdap = pool.submit(_rdap, re.sub(r"^www\.", "", host))
        f_cert = pool.submit(_cert, fh)
        f_a = pool.submit(_doh, fh, "A")
        f_mx = pool.submit(_doh, re.sub(r"^www\.", "", host), "MX")
        f_ns = pool.submit(_doh, re.sub(r"^www\.", "", host), "NS")
        f_txt = pool.submit(_doh, re.sub(r"^www\.", "", host), "TXT")
        f_robots = pool.submit(_try, fetch, origin + "/robots.txt")
        f_sm = pool.submit(_try, fetch, origin + "/sitemap.xml")
        f_llms = pool.submit(_try, fetch, origin + "/llms.txt", "GET", 4, False)
        rd, cert, a, mx, ns, txt, robots, sm, llms = (f.result() for f in (f_rdap, f_cert, f_a, f_mx, f_ns, f_txt, f_robots, f_sm, f_llms))
    robots_txt = decode(robots) if robots and robots["status"] == 200 else ""
    sm_count = None
    sm_url = None
    cands = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_txt)
    for cand in cands[:1]:
        r = _try(fetch, cand)
        if r and r["status"] == 200:
            sm = r
    if sm and sm["status"] == 200 and b"<" in sm["body"][:300]:
        xml = decode(sm)
        sm_url = sm["url"]
        locs = re.findall(r"<loc>", xml)
        sm_count = {"urls": len(locs), "index": "<sitemapindex" in xml}
    age = None
    if rd and rd.get("created"):
        try:
            age = round((datetime.now(timezone.utc) - datetime.fromisoformat(rd["created"].replace("Z", "+00:00"))).days / 365.25, 1)
        except Exception:
            pass
    mail = None
    mxs = " ".join(mx).lower()
    for name, rx in (("Google Workspace", "google|aspmx"), ("Microsoft 365", "outlook|protection.outlook"), ("Zoho Mail", "zoho"), ("Proton Mail", "protonmail"), ("Cloudflare Email", "cloudflare")):
        if re.search(rx, mxs):
            mail = name
            break
    spf = any(t.strip('"').startswith("v=spf1") for t in txt)
    return {
        "domain": host, "finalUrl": final, "status": res["status"], "ms": res["ttfb"], "https": final.startswith("https://"),
        "title": P.title, "description": P.meta("description"), "lang": P.lang, "words": len(P.words),
        "tech": sorted(set(tech)), "server": server, "socials": socials,
        "ssl": cert, "registration": rd, "ageYears": age,
        "dns": {"a": a[:6], "mx": mx[:6], "ns": ns[:6], "mail": mail, "spf": spf},
        "robots": bool(robots_txt), "sitemap": sm_count, "sitemapUrl": sm_url, "llms": bool(llms and llms["status"] == 200),
        "internalLinks": len({norm_url(_abs(x["href"], final)) for x in P.anchors if _abs(x["href"], final) and same_site(_abs(x["href"], final), final)}),
        "schema": sorted({str(m.group(1)) for m in re.finditer(r'"@type"\s*:\s*"([A-Za-z]+)"', " ".join(P.jsonld))}),
    }


# ------------------------------------------------------------------ backlink audit

SPAM_TLDS = {"xyz", "top", "click", "loan", "work", "gq", "ml", "cf", "tk", "ga", "buzz", "rest", "icu", "cyou", "monster", "win", "bid", "party", "review", "date", "stream", "download", "racing", "accountant", "cricket", "faith", "science"}
SPAM_WORDS = re.compile(r"casino|poker|porn|xxx|viagra|cialis|escort|loan|payday|bet(ting)?\b|crypto-?signal|seo-?link|backlink|link-?farm|free-?links|directory", re.I)


def check_backlink(src, target):
    """Fetch a linking page and report whether (and how) it links to the target site."""
    row = {"source": src, "status": 0, "found": False, "rel": [], "anchor": "", "noindex": False, "outbound": 0, "title": None, "flags": [], "risk": 0}
    try:
        r = fetch_page(src, timeout=15)
    except AuditError as e:
        row["error"] = str(e)
        row["flags"].append("Unreachable")
        return row
    row["status"] = r["status"]
    row["final"] = r["url"]
    if r["status"] >= 400:
        row["flags"].append(f"Page returns {r['status']}")
        return row
    P = parse(decode(r)) if "html" in r["headers"].get("content-type", "") else parse("")
    row["title"] = P.title
    row["noindex"] = "noindex" in (P.meta("robots") or "").lower() or "noindex" in r["headers"].get("x-robots-tag", "").lower()
    th = (urlparse(target if "//" in target else "https://" + target).hostname or target).lower()
    th = th[4:] if th.startswith("www.") else th
    outs = set()
    for a in P.anchors:
        u = _abs(a["href"], r["url"])
        if not u:
            continue
        h = (urlparse(u).hostname or "").lower()
        if not same_site(u, r["url"]):
            outs.add(h)
        if h == th or h.endswith("." + th):
            if not row["found"]:
                row.update(found=True, href=u, anchor=a["text"] or a["img_alt"] or "", rel=a["rel"].split())
    row["outbound"] = len(outs)
    sh = (urlparse(r["url"]).hostname or "").lower()
    if not row["found"]:
        row["flags"].append("Link not found")
    if row["noindex"]:
        row["flags"].append("Page is noindex")
    if row["outbound"] > 100:
        row["flags"].append(f"{row['outbound']} outbound domains")
    if sh.rsplit(".", 1)[-1] in SPAM_TLDS:
        row["flags"].append("Spam-prone TLD")
    if SPAM_WORDS.search(sh) or SPAM_WORDS.search(P.title or ""):
        row["flags"].append("Spammy topic words")
    if row["found"] and re.search(r"^(https?://|www\.)", row["anchor"] or "") is None and SPAM_WORDS.search(row["anchor"] or ""):
        row["flags"].append("Spammy anchor text")
    risk = 0
    for f in row["flags"]:
        risk += 40 if f in ("Spam-prone TLD", "Spammy topic words", "Spammy anchor text") else 25 if "outbound" in f else 15 if f == "Page is noindex" else 0
    row["risk"] = min(100, risk)
    return row


def backlink_audit(target, sources):
    sources = [s.strip() for s in sources if s.strip()][:300]
    sources = [s if re.match(r"^https?://", s, re.I) else "https://" + s for s in sources]
    if not target:
        raise AuditError("Enter your website (the site the links should point to).")
    if not sources:
        raise AuditError("Add at least one linking page URL.")
    with ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(lambda s: check_backlink(s, target), sources))
    return {"target": target, "count": len(rows), "rows": rows}


# ------------------------------------------------------------------ AI search readiness (AEO / GEO)

from html.parser import HTMLParser
from urllib import robotparser
from urllib.request import Request, build_opener

# who each AI crawler works for, and whether blocking it hides you from live AI answers ("search") or only from training
AI_CRAWLERS = [
    ("OAI-SearchBot", "OpenAI", "ChatGPT search results", "search"),
    ("ChatGPT-User", "OpenAI", "ChatGPT opening a page a user asked about", "search"),
    ("GPTBot", "OpenAI", "Training future OpenAI models", "training"),
    ("Claude-SearchBot", "Anthropic", "Claude search results", "search"),
    ("Claude-User", "Anthropic", "Claude opening a page a user asked about", "search"),
    ("ClaudeBot", "Anthropic", "Training future Claude models", "training"),
    ("PerplexityBot", "Perplexity", "Perplexity answers and search index", "search"),
    ("Perplexity-User", "Perplexity", "Perplexity opening a page a user asked about", "search"),
    ("Googlebot", "Google", "Google Search, AI Overviews and AI Mode", "search"),
    ("Google-Extended", "Google", "Using your content in Gemini and model training", "training"),
    ("Bingbot", "Microsoft", "Bing and Copilot (ChatGPT search also leans on Bing)", "search"),
    ("Applebot-Extended", "Apple", "Apple Intelligence training", "training"),
    ("Meta-ExternalAgent", "Meta", "Meta AI training", "training"),
    ("CCBot", "Common Crawl", "Open web dataset many AI models train on", "training"),
    ("Bytespider", "ByteDance", "ByteDance / Doubao AI training", "training"),
    ("Amazonbot", "Amazon", "Alexa and Amazon AI answers", "search"),
]
# real user-agent strings, used to see whether a firewall (e.g. Cloudflare "block AI bots") turns the crawler away
LIVE_UA = {
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
}


def _ua_status(url, ua):
    """HTTP status a crawler with this user agent gets (first hop only, body not read)."""
    p = urlparse(url)
    safe_host(p.hostname)
    try:
        r = build_opener().open(Request(url, method="GET", headers={"User-Agent": ua, "Accept": "text/html"}), timeout=12)
        code = r.status
        r.close()
        return code
    except Exception as e:  # HTTPError carries a code; anything else means the request was refused or timed out
        return getattr(e, "code", 0)


def ai_ready(target):
    if not target.strip():
        raise AuditError("Enter your website.")
    url = target.strip() if re.match(r"^https?://", target.strip(), re.I) else "https://" + target.strip()
    pg = page(url)
    final = pg["finalUrl"]
    fp = urlparse(final)
    origin = f"{fp.scheme}://{fp.netloc}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        f_robots = pool.submit(_try, fetch, origin + "/robots.txt")
        f_llms = pool.submit(_try, fetch, origin + "/llms.txt")
        f_full = pool.submit(_try, fetch, origin + "/llms-full.txt", "GET", 4, False)
        f_live = {name: pool.submit(_ua_status, final, ua) for name, ua in LIVE_UA.items()}
        robots, llms, full = f_robots.result(), f_llms.result(), f_full.result()
        live = {k: f.result() for k, f in f_live.items()}
    rtxt = decode(robots) if robots and robots["status"] == 200 else ""
    rp = robotparser.RobotFileParser()
    rp.parse(rtxt.splitlines())
    crawlers = []
    for name, owner, purpose, kind in AI_CRAWLERS:
        allowed = rp.can_fetch(name, final) if rtxt else True
        explicit = bool(re.search(r"(?im)^\s*user-agent:\s*" + re.escape(name) + r"\s*$", rtxt))
        crawlers.append({"bot": name, "owner": owner, "purpose": purpose, "kind": kind, "allowed": allowed,
                         "explicit": explicit, "live": live.get(name)})
    llms_txt = decode(llms) if llms and llms["status"] == 200 and b"<html" not in llms["body"][:500].lower() else ""
    title = re.search(r"(?m)^#\s+(.+)$", llms_txt) if llms_txt else None
    return {
        "url": final, "robots": bool(rtxt), "crawlers": crawlers, "liveBrowser": pg["status"],
        "llms": {"exists": bool(llms_txt), "title": title.group(1) if title else None,
                 "links": len(re.findall(r"\]\(https?://", llms_txt)), "bytes": len(llms_txt), "full": bool(full and full["status"] == 200)},
        "page": {"title": pg["title"], "description": pg["description"], "words": pg["words"], "schema": pg["schema"],
                 "noindex": pg["noindex"], "readability": pg["readability"], "lang": pg["lang"]},
    }


class _Blocks(HTMLParser):
    """The page as an ordered list of headings, paragraphs and list items (for answer extraction)."""
    SKIP = ("script", "style", "noscript", "svg", "nav", "footer", "header", "form")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.cur, self.skip, self.tables, self.lists, self.links, self.times = [], None, 0, 0, 0, [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "p", "li", "dt", "dd", "summary") and not self.skip:
            self.cur = [tag, []]
        elif tag == "table":
            self.tables += 1
        elif tag in ("ul", "ol"):
            self.lists += 1
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif self.cur and tag == self.cur[0]:
            t = " ".join("".join(self.cur[1]).split())
            if t:
                self.blocks.append((tag, t))
            self.cur = None

    def handle_data(self, d):
        if self.cur is not None and not self.skip:
            self.cur[1].append(d)


QUESTION_HEAD = re.compile(r"\?$|^(how|what|why|when|where|who|which|can|does|do|is|are|should|will|could)\b", re.I)


def aeo(url, question=""):
    if not url.strip():
        raise AuditError("Enter a page URL.")
    if not re.match(r"^https?://", url.strip(), re.I):
        url = "https://" + url.strip()
    res = fetch_page(url, timeout=25)
    html = decode(res) if "html" in res["headers"].get("content-type", "") else ""
    b = _Blocks()
    try:
        b.feed(html)
    except Exception:
        pass
    pg = page(url)  # shared signals: schema, words, readability, title
    pairs = []
    for i, (tag, text) in enumerate(b.blocks):
        if tag in ("h2", "h3", "h4", "summary", "dt") and QUESTION_HEAD.search(text) and len(text) < 160:
            ans = []
            for tag2, t2 in b.blocks[i + 1:]:
                if tag2 in ("h1", "h2", "h3", "h4", "summary", "dt"):
                    break
                ans.append(t2)
                if len(" ".join(ans).split()) > 120:
                    break
            if ans:
                pairs.append({"q": text, "a": " ".join(ans)[:900], "firstWords": len(ans[0].split()), "first": ans[0][:400]})
    paras = [t for tg, t in b.blocks if tg == "p"]
    intro = " ".join(paras[:2])
    final = res["url"]
    outbound = set()
    for h in b.links:
        u = _abs(h, final)
        if u and not same_site(u, final):
            outbound.add(urlparse(u).hostname)
    mod = re.search(r'"dateModified"\s*:\s*"([^"]+)"', html) or re.search(r'article:modified_time"\s+content="([^"]+)"', html)
    pub = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html) or re.search(r'article:published_time"\s+content="([^"]+)"', html)
    author = re.search(r'"author"\s*:\s*(?:\[\s*)?\{[^}]*?"name"\s*:\s*"([^"]+)"', html) or re.search(r'<meta[^>]+name="author"[^>]+content="([^"]+)"', html)
    stats = len(re.findall(r"\b\d+(?:[.,]\d+)?\s?(?:%|percent|per cent|million|billion|crore|lakh)", pg["text"], re.I))
    q_terms = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in STOP and len(w) > 2]
    heads = [t for tg, t in b.blocks if tg in ("h1", "h2", "h3")]
    return {
        "url": final, "status": res["status"], "title": pg["title"], "words": pg["words"], "readability": pg["readability"],
        "schema": pg["schema"], "pairs": pairs[:30], "questionHeadings": len(pairs), "lists": b.lists, "tables": b.tables,
        "intro": intro[:600], "introWords": len(intro.split()), "outboundDomains": sorted(x for x in outbound if x)[:40], "stats": stats,
        "modified": mod.group(1) if mod else None, "published": pub.group(1) if pub else None,
        "author": author.group(1) if author else None, "question": question.strip() or None,
        "qInHeading": bool(q_terms) and any(all(w in h.lower() for w in q_terms) for h in heads),
        "qInIntro": bool(q_terms) and all(w in intro.lower() for w in q_terms),
        "paragraphs": len(paras), "longParagraphs": len([p for p in paras if len(p.split()) > 120]),
    }
