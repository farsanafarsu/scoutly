"""Schema Markup Checker: the structured data on a page, checked against Google's rich result rules.

  * extract(html): JSON-LD blocks (with parse errors), microdata items and an RDFa count; @graph is
    flattened and {"@id": …} references are resolved, so an Article's author can live elsewhere on the page
  * check(item): per type, the properties Google requires (missing = not eligible for that rich result)
    and recommends, plus value formats (ISO 8601 dates, absolute URLs, numeric prices and ratings)

The rules follow Google Search Central's structured data docs for the types small sites use most; types
with no rich result (WebPage, Person, ImageObject…) are listed as detected, without errors.
"""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from server import AuditError, decode, fetch

MAX_CODE = 900_000           # the server reads POST bodies up to 1 MB

# ------------------------------------------------------------------ extraction


class _Micro(HTMLParser):
    """Microdata items: itemscope / itemtype / itemprop, nested items as values."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.items, self.stack, self.rdfa, self.jsonld, self._ld = base, [], [], 0, [], None

    def _val(self, tag, a):
        for attr in ("content",) + (("href",) if tag in ("a", "link", "area") else ()) + (("src",) if tag in ("img", "audio", "video", "source", "iframe", "embed") else ()) + (("datetime",) if tag == "time" else ()) + (("value",) if tag == "data" else ()):
            if attr in a:
                v = a[attr] or ""
                return urljoin(self.base, v) if attr in ("href", "src") else v
        return None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if "typeof" in a or "vocab" in a:
            self.rdfa += 1
        if tag == "script" and a.get("type", "").lower().strip() == "application/ld+json":
            self._ld = []
        props = a.get("itemprop", "").split()
        if "itemscope" in a:
            t = [x.rsplit("/", 1)[-1] for x in a.get("itemtype", "").split()]
            item = {"@type": t[0] if len(t) == 1 else (t or "Thing"), "_src": "microdata"}
            if a.get("itemid"):
                item["@id"] = a["itemid"]
            parent = next((f for f in reversed(self.stack) if f["item"] is not None), None)
            if props and parent:
                for p in props:
                    parent["item"].setdefault(p, []).append(item)
            elif not props or not parent:
                self.items.append(item)
            self.stack.append({"tag": tag, "item": item, "prop": None})
        elif props:
            v = self._val(tag, a)
            frame = {"tag": tag, "item": None, "prop": (props, [] if v is None else None)}
            owner = next((f for f in reversed(self.stack) if f["item"] is not None), None)
            if owner and v is not None:
                for p in props:
                    owner["item"].setdefault(p, []).append(v)
            frame["owner"] = owner
            self.stack.append(frame)
        else:
            self.stack.append({"tag": tag, "item": None, "prop": None})
        if tag in self.VOID:
            self._close(tag)

    def _close(self, tag):
        while self.stack:
            f = self.stack.pop()
            if f["prop"] and f["prop"][1] is not None and f.get("owner"):
                text = " ".join("".join(f["prop"][1]).split())
                for p in f["prop"][0]:
                    f["owner"]["item"].setdefault(p, []).append(text)
            if f["tag"] == tag:
                break

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld))
            self._ld = None
        if any(f["tag"] == tag for f in self.stack):
            self._close(tag)

    def handle_data(self, d):
        if self._ld is not None:
            self._ld.append(d)
            return
        for f in self.stack:
            if f["prop"] and f["prop"][1] is not None:
                f["prop"][1].append(d)


def _flat(v):
    """Microdata values come as lists; single values become plain values."""
    if isinstance(v, list):
        v = [_flat(x) for x in v]
        return v[0] if len(v) == 1 else v
    if isinstance(v, dict):
        return {k: (_flat(x) if not k.startswith("@") and k != "_src" else x) for k, x in v.items()}
    return v


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def extract(text, base):
    m = _Micro(base)
    try:
        m.feed(text)
        m.close()
    except Exception:
        pass
    blocks, nodes = [], []
    for i, raw in enumerate(m.jsonld):
        src = raw.strip()
        if src.startswith("<!--"):
            src = src[4:]
        if src.endswith("-->"):
            src = src[:-3]
        b = {"n": i + 1, "code": raw.strip()[:20000], "error": None, "types": []}
        try:
            data = json.loads(src)
        except ValueError as e:
            pos = getattr(e, "pos", 0) or 0
            b["error"] = f"{getattr(e, 'msg', str(e))} (line {getattr(e, 'lineno', _line_of(src, pos))}, column {getattr(e, 'colno', 0)})"
            blocks.append(b)
            continue
        top = data if isinstance(data, list) else [data]
        for d in top:
            if not isinstance(d, dict):
                continue
            ctx = d.get("@context")
            graph = d.get("@graph") if isinstance(d.get("@graph"), list) else None
            for n in (graph or [d]):
                if isinstance(n, dict):
                    n = dict(n)
                    n["_src"], n["_block"], n["_ctx"] = "json-ld", i + 1, ctx if ctx is not None else n.get("@context")
                    nodes.append(n)
                    b["types"].append(_tname(n))
        blocks.append(b)
    micro = [_flat(x) for x in m.items]
    # resolve {"@id": "..."} references to nodes defined elsewhere on the page
    ids = {}
    for n in nodes + micro:
        if isinstance(n, dict) and n.get("@id") and len(n) > 2:
            ids.setdefault(n["@id"], n)

    def resolve(v, depth=0):
        if depth > 6:
            return v
        if isinstance(v, list):
            return [resolve(x, depth + 1) for x in v]
        if isinstance(v, dict):
            if set(v) <= {"@id"} and v.get("@id") in ids:
                return resolve(ids[v["@id"]], depth + 1)
            return {k: (resolve(x, depth + 1) if not k.startswith("_") else x) for k, x in v.items()}
        return v
    items = [resolve(n) for n in nodes] + micro
    return {"blocks": blocks, "items": items, "rdfa": m.rdfa}


# ------------------------------------------------------------------ value checks

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
DURATION = re.compile(r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?$")


def _tname(n):
    t = n.get("@type") if isinstance(n, dict) else None
    if isinstance(t, list):
        t = next((x for x in t if x), "Thing")
    return str(t or "Thing").rsplit("/", 1)[-1].replace("schema:", "")


def _types(n):
    t = n.get("@type") if isinstance(n, dict) else None
    return [str(x).rsplit("/", 1)[-1].replace("schema:", "") for x in (t if isinstance(t, list) else [t]) if x]


def _get(obj, path):
    """Values at a dotted path, following lists."""
    cur = [obj]
    for part in path.split("."):
        nxt = []
        for c in cur:
            if isinstance(c, list):
                c_list = c
            else:
                c_list = [c]
            for x in c_list:
                if isinstance(x, dict) and part in x:
                    v = x[part]
                    nxt.extend(v if isinstance(v, list) else [v])
        cur = nxt
    return [v for v in cur if v not in (None, "", [], {})]


def _text(v):
    if isinstance(v, dict):
        return str(v.get("name") or v.get("@id") or v.get("url") or "")
    return str(v)


def _is_url(v):
    if isinstance(v, dict):
        v = v.get("url") or v.get("contentUrl") or v.get("@id")
    return isinstance(v, str) and bool(re.match(r"^https?://[^\s/]+\.[^\s]+", v.strip()))


def _num(v):
    try:
        float(str(v).replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", str(v)) else str(v))
        return True
    except ValueError:
        return False


LOCAL = {"LocalBusiness", "Restaurant", "Store", "Dentist", "MedicalClinic", "Physician", "Hotel", "LegalService", "Attorney", "AutoRepair",
         "BeautySalon", "HairSalon", "DaySpa", "HealthAndBeautyBusiness", "RealEstateAgent", "ProfessionalService", "HomeAndConstructionBusiness",
         "Plumber", "Electrician", "HVACBusiness", "RoofingContractor", "GeneralContractor", "Locksmith", "MovingCompany", "AccountingService",
         "FinancialService", "InsuranceAgency", "TravelAgency", "EducationalOrganization", "School", "Preschool", "ChildCare", "GymOrHealthClub",
         "ExerciseGym", "SportsActivityLocation", "FoodEstablishment", "Bakery", "BarOrPub", "CafeOrCoffeeShop", "FastFoodRestaurant",
         "ClothingStore", "ElectronicsStore", "FurnitureStore", "GroceryStore", "HardwareStore", "JewelryStore", "MobilePhoneStore",
         "PetStore", "ShoeStore", "SportingGoodsStore", "AutoDealer", "VeterinaryCare", "Optician", "Pharmacy", "MedicalBusiness",
         "LodgingBusiness", "BedAndBreakfast", "Motel", "Resort", "EntertainmentBusiness", "NightClub", "AnimalShelter", "Library",
         "EmploymentAgency", "RecyclingCenter", "SelfStorage", "TouristInformationCenter", "Dentistry", "MedicalOrganization"}
ARTICLE = {"Article", "NewsArticle", "BlogPosting", "TechArticle", "Report", "ScholarlyArticle", "LiveBlogPosting"}
ORG = {"Organization", "Corporation", "NGO", "OnlineStore", "OnlineBusiness", "NewsMediaOrganization", "SportsOrganization", "GovernmentOrganization"}


class _C:
    def __init__(self):
        self.errors, self.warnings, self.ok = [], [], []

    def need(self, item, path, what, recommended=False, fix=""):
        has = bool(_get(item, path))
        if has:
            self.ok.append(f"{what} ({path})")
        else:
            (self.warnings if recommended else self.errors).append(
                f"{'Add' if recommended else 'Missing required'} “{path}”: {what}." + (f" {fix}" if fix else ""))
        return has

    def any_of(self, item, paths, what, recommended=False):
        if any(_get(item, p) for p in paths):
            self.ok.append(what)
            return True
        (self.warnings if recommended else self.errors).append(f"{'Add' if recommended else 'Missing required'}: {what} (one of {', '.join('“' + p + '”' for p in paths)}).")
        return False

    def dates(self, item, paths):
        for p in paths:
            for v in _get(item, p):
                if isinstance(v, str) and not ISO_DATE.match(v.strip()):
                    self.warnings.append(f"“{p}” is “{v[:40]}”; use ISO 8601, like 2026-10-25 or 2026-10-25T09:00:00+05:30.")

    def urls(self, item, paths):
        for p in paths:
            for v in _get(item, p):
                if (isinstance(v, str) or isinstance(v, dict)) and not _is_url(v):
                    shown = v if isinstance(v, str) else (v.get("url") or v.get("@id") or "an object without a url")
                    self.warnings.append(f"“{p}” should be a full URL starting with https://, not “{str(shown)[:60]}”.")

    def durations(self, item, paths):
        for p in paths:
            for v in _get(item, p):
                if isinstance(v, str) and not DURATION.match(v.strip()):
                    self.warnings.append(f"“{p}” is “{v[:30]}”; use an ISO 8601 duration, like PT30M or PT1H15M.")


def _offers(c, item, merchant=False):
    offers = _get(item, "offers")
    for o in offers:
        if not isinstance(o, dict):
            continue
        if "AggregateOffer" in _types(o):
            c.need(o, "lowPrice", "lowest price in the AggregateOffer")
            c.need(o, "priceCurrency", "currency code like USD or INR")
            continue
        price = _get(o, "price") or _get(o, "priceSpecification.price")
        if not price:
            c.errors.append("Missing required “offers.price”: the price (use 0 for free).")
        elif not all(_num(p) for p in price):
            c.errors.append(f"“offers.price” is “{_text(price[0])[:20]}”; it must be a plain number like 1299.00, without a currency symbol.")
        cur = _get(o, "priceCurrency") or _get(o, "priceSpecification.priceCurrency")
        if not cur:
            (c.errors if merchant else c.warnings).append("Add “offers.priceCurrency”: a 3-letter currency code like USD, GBP or INR.")
        elif not all(re.fullmatch(r"[A-Z]{3}", str(x)) for x in cur):
            c.errors.append(f"“offers.priceCurrency” is “{_text(cur[0])}”; use a 3-letter ISO 4217 code like USD or INR.")
        if not _get(o, "availability"):
            c.warnings.append("Add “offers.availability”, like https://schema.org/InStock.")
        c.dates(o, ["priceValidUntil"])


def _rating(c, item):
    for r in _get(item, "aggregateRating"):
        if isinstance(r, dict):
            if not _get(r, "ratingValue"):
                c.errors.append("Missing required “aggregateRating.ratingValue”.")
            elif not all(_num(v) for v in _get(r, "ratingValue")):
                c.errors.append("“aggregateRating.ratingValue” must be a number, like 4.6.")
            if not (_get(r, "ratingCount") or _get(r, "reviewCount")):
                c.errors.append("“aggregateRating” needs “ratingCount” or “reviewCount”.")
    for r in _get(item, "review"):
        if isinstance(r, dict):
            if not _get(r, "author"):
                c.errors.append("Each “review” needs an “author” (a Person or Organization with a name).")
            elif any(isinstance(a, str) for a in _get(r, "author")):
                c.warnings.append("“review.author” is plain text; Google prefers a Person or Organization object with a “name”.")
            if not _get(r, "reviewRating.ratingValue"):
                c.warnings.append("Add “review.reviewRating.ratingValue” so the review can show stars.")


def check(item):
    """(rich result name or None, checks) for one top-level item."""
    ts = set(_types(item))
    c = _C()
    name = None
    if item.get("_src") == "json-ld":
        ctx = item.get("_ctx")
        ctxs = json.dumps(ctx) if ctx is not None else ""
        if not ctx:
            c.errors.append("No “@context”. Add \"@context\": \"https://schema.org\" or search engines can't read this block.")
        elif "schema.org" not in ctxs:
            c.errors.append(f"“@context” is {ctxs[:60]}; it should be \"https://schema.org\".")
    if not ts:
        c.errors.append("No “@type”, so search engines don't know what this describes.")
    if ts & ARTICLE:
        name = "Article"
        c.need(item, "headline", "the article title", True)
        for h in _get(item, "headline"):
            if isinstance(h, str) and len(h) > 110:
                c.warnings.append(f"“headline” is {len(h)} characters; Google may cut titles over 110.")
        c.need(item, "image", "an image of at least 1200 px wide", True)
        c.need(item, "datePublished", "when it was first published", True)
        c.need(item, "dateModified", "when it was last changed", True)
        if c.need(item, "author", "who wrote it", True):
            if any(isinstance(a, str) for a in _get(item, "author")):
                c.warnings.append("“author” is plain text; use a Person (or Organization) with “name” and “url”.")
            elif not _get(item, "author.name"):
                c.warnings.append("“author” has no “name”.")
        c.dates(item, ["datePublished", "dateModified"])
        c.urls(item, ["image", "author.url"])
    if "Product" in ts or "ProductGroup" in ts:
        name = "Product snippet"
        c.need(item, "name", "the product name")
        c.any_of(item, ["offers", "review", "aggregateRating"], "an offer, a review or an aggregate rating")
        c.need(item, "image", "a product photo", True)
        c.need(item, "description", "a product description", True)
        c.any_of(item, ["sku", "gtin", "gtin13", "gtin12", "gtin8", "gtin14", "mpn"], "a product identifier (sku, gtin or mpn)", True)
        c.need(item, "brand.name", "the brand", True)
        if _get(item, "offers"):
            name = "Product snippet / merchant listing"
        _offers(c, item, merchant=bool(_get(item, "offers")))
        _rating(c, item)
        c.urls(item, ["image"])
    if ts & LOCAL:
        name = "Local business"
        c.need(item, "name", "the business name")
        if c.need(item, "address", "the business address (a PostalAddress)"):
            if any(isinstance(a, str) for a in _get(item, "address")):
                c.warnings.append("“address” is plain text; use a PostalAddress with streetAddress, addressLocality, postalCode and addressCountry.")
            else:
                for p, w in (("streetAddress", "street"), ("addressLocality", "city"), ("postalCode", "postcode"), ("addressCountry", "country code, like IN or GB")):
                    c.need(item, "address." + p, w, True)
        c.need(item, "telephone", "a phone number, ideally with the country code (+91…)", True)
        c.need(item, "url", "the business website", True)
        c.need(item, "geo.latitude", "map coordinates (geo.latitude and geo.longitude)", True)
        c.any_of(item, ["openingHoursSpecification", "openingHours"], "opening hours", True)
        c.need(item, "image", "a photo of the business", True)
        c.need(item, "priceRange", "a price range like ££ or ₹500–2000", True)
        _rating(c, item)
        c.urls(item, ["url", "image"])
    elif ts & ORG:
        name = "Organization (logo and knowledge panel)"
        c.need(item, "name", "the organization name", True)
        c.need(item, "url", "the official website", True)
        c.need(item, "logo", "a logo image of at least 112 × 112 px", True)
        c.need(item, "sameAs", "links to official profiles (LinkedIn, Facebook, Wikipedia…)", True)
        c.any_of(item, ["contactPoint", "telephone", "email"], "a way to contact you", True)
        c.urls(item, ["url", "logo", "sameAs"])
    if "BreadcrumbList" in ts:
        name = "Breadcrumb"
        els = [e for e in _get(item, "itemListElement") if isinstance(e, dict)]
        if not els:
            c.errors.append("Missing required “itemListElement”: the list of breadcrumb steps.")
        else:
            if len(els) < 2:
                c.warnings.append("Google shows breadcrumbs with at least two steps; this has one.")
            for i, e in enumerate(els):
                if not _get(e, "position"):
                    c.errors.append(f"Breadcrumb step {i + 1} has no “position”.")
                if not (_get(e, "name") or _get(e, "item.name")):
                    c.errors.append(f"Breadcrumb step {i + 1} has no “name”.")
                if i < len(els) - 1 and not _get(e, "item"):
                    c.errors.append(f"Breadcrumb step {i + 1} has no “item” URL (only the last step may leave it out).")
            c.urls({"x": [e.get("item") for e in els if isinstance(e.get("item"), (str, dict))]}, ["x"])
            if not c.errors:
                c.ok.append(f"{len(els)} breadcrumb steps")
    if "FAQPage" in ts:
        name = "FAQ"
        qs = [q for q in _get(item, "mainEntity") if isinstance(q, dict)]
        if not qs:
            c.errors.append("Missing required “mainEntity”: the list of Questions.")
        for i, q in enumerate(qs):
            if not _get(q, "name"):
                c.errors.append(f"Question {i + 1} has no “name” (the question text).")
            if not _get(q, "acceptedAnswer.text"):
                c.errors.append(f"Question {i + 1} has no “acceptedAnswer.text”.")
        if qs and not c.errors:
            c.ok.append(f"{len(qs)} questions with answers")
        c.warnings.append("Since August 2023 Google shows FAQ rich results only for well-known government and health sites. The markup still helps AI search and other engines understand the page.")
    if "QAPage" in ts:
        name = "Q&A"
        c.need(item, "mainEntity.name", "the question")
        c.any_of(item, ["mainEntity.acceptedAnswer", "mainEntity.suggestedAnswer"], "at least one answer")
    if "HowTo" in ts:
        name = name or "How-to (retired)"
        c.warnings.append("Google stopped showing How-to rich results in September 2023. The markup is harmless but won't change how the page looks in search.")
    if "Recipe" in ts:
        name = "Recipe"
        c.need(item, "name", "the dish name")
        c.need(item, "image", "a photo of the dish")
        for p, w in (("author", "who created it"), ("datePublished", "publish date"), ("description", "a short description"), ("recipeIngredient", "the ingredients"),
                     ("recipeInstructions", "the steps"), ("totalTime", "total time, like PT45M"), ("recipeYield", "servings"), ("nutrition.calories", "calories"),
                     ("recipeCategory", "category, like Dessert"), ("recipeCuisine", "cuisine"), ("aggregateRating", "ratings"), ("keywords", "keywords")):
            c.need(item, p, w, True)
        c.durations(item, ["totalTime", "prepTime", "cookTime"])
        c.dates(item, ["datePublished"])
        _rating(c, item)
    if ts & {"Event", "MusicEvent", "BusinessEvent", "EducationEvent", "SportsEvent", "Festival", "TheaterEvent", "ExhibitionEvent", "SocialEvent", "ComedyEvent", "DanceEvent", "FoodEvent", "LiteraryEvent", "ScreeningEvent", "SaleEvent", "ChildrensEvent", "CourseInstance"}:
        name = "Event"
        c.need(item, "name", "the event name")
        c.need(item, "startDate", "when it starts")
        if c.need(item, "location", "where it happens (a Place with an address, or a VirtualLocation with a url)"):
            for loc in _get(item, "location"):
                if isinstance(loc, dict) and "VirtualLocation" not in _types(loc) and not _get(loc, "address"):
                    c.errors.append("“location” needs an “address” (or use a VirtualLocation with a url for online events).")
        for p, w in (("endDate", "when it ends"), ("eventStatus", "status, like https://schema.org/EventScheduled"), ("eventAttendanceMode", "online, offline or mixed"),
                     ("image", "an image"), ("description", "a description"), ("offers", "tickets"), ("organizer", "the organizer"), ("performer", "the performer")):
            c.need(item, p, w, True)
        c.dates(item, ["startDate", "endDate"])
        if _get(item, "offers"):
            _offers(c, item)
    if "JobPosting" in ts:
        name = "Job posting"
        for p, w in (("title", "the job title"), ("description", "the full job description (HTML allowed)"), ("datePosted", "when it was posted"), ("hiringOrganization.name", "the employer")):
            c.need(item, p, w)
        remote = any("TELECOMMUTE" in str(v).upper() for v in _get(item, "jobLocationType"))
        if remote:
            c.need(item, "applicantLocationRequirements", "where remote applicants may live")
        else:
            c.need(item, "jobLocation.address", "where the job is (or set jobLocationType to TELECOMMUTE for remote work)")
        for p, w in (("validThrough", "when the posting expires"), ("employmentType", "FULL_TIME, PART_TIME, CONTRACTOR…"), ("baseSalary", "the salary"), ("identifier", "your job ID")):
            c.need(item, p, w, True)
        c.dates(item, ["datePosted", "validThrough"])
    if "VideoObject" in ts:
        name = name or "Video"
        c.need(item, "name", "the video title")
        c.need(item, "thumbnailUrl", "a thumbnail image")
        c.need(item, "uploadDate", "when it was published")
        c.need(item, "description", "a description", True)
        c.any_of(item, ["contentUrl", "embedUrl"], "the video file or player URL", True)
        c.need(item, "duration", "length, like PT2M30S", True)
        c.dates(item, ["uploadDate"])
        c.durations(item, ["duration"])
        c.urls(item, ["thumbnailUrl", "contentUrl", "embedUrl"])
    if "SoftwareApplication" in ts or "MobileApplication" in ts or "WebApplication" in ts:
        name = "Software app"
        c.need(item, "name", "the app name")
        c.need(item, "offers.price", "the price (0 if free)")
        c.any_of(item, ["aggregateRating", "review"], "a rating or review")
        c.need(item, "applicationCategory", "category, like BusinessApplication", True)
        c.need(item, "operatingSystem", "supported systems", True)
        _rating(c, item)
    if "Course" in ts:
        name = "Course"
        c.need(item, "name", "the course title")
        c.need(item, "description", "a description")
        c.need(item, "provider.name", "who offers it", True)
        c.need(item, "offers", "price details", True)
        c.need(item, "hasCourseInstance", "when and how it's taught", True)
    if "Review" in ts and not name:
        name = "Review snippet"
        c.need(item, "itemReviewed", "what is being reviewed")
        c.need(item, "reviewRating.ratingValue", "the rating")
        c.need(item, "author", "who wrote the review")
    if "WebSite" in ts:
        name = name or "Site name"
        c.need(item, "name", "your site's name, as Google should show it", True)
        c.need(item, "url", "the home page URL", True)
        if _get(item, "potentialAction"):
            c.warnings.append("The sitelinks search box (potentialAction SearchAction) was retired by Google in November 2024; you can remove it.")
    return name, c


def _summary(item, depth=0):
    """A readable copy of an item for the page: no internal keys, long strings trimmed, depth-limited."""
    if depth > 5:
        return "…"
    if isinstance(item, dict):
        return {k: _summary(v, depth + 1) for k, v in item.items() if not k.startswith("_") and k != "@context"}
    if isinstance(item, list):
        return [_summary(v, depth + 1) for v in item[:25]] + (["… " + str(len(item) - 25) + " more"] if len(item) > 25 else [])
    if isinstance(item, str) and len(item) > 300:
        return item[:300] + "…"
    return item


def analyze(url="", code=""):
    if code:
        if len(code) > MAX_CODE:
            raise AuditError("That code is too long. Paste one page's HTML or the JSON-LD blocks.")
        text, final, status = code, url or "https://example.com/", None
        if code.lstrip().startswith(("{", "[")):
            text = f'<script type="application/ld+json">{code}</script>'
    else:
        url = (url or "").strip()
        if not url:
            raise AuditError("Enter the page you want to test.")
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        res = fetch(url, timeout=25)
        if "html" not in res["headers"].get("content-type", "") and b"<html" not in res["body"][:2000].lower():
            raise AuditError(f"{res['url']} isn't an HTML page ({res['headers'].get('content-type', 'unknown type')}).")
        text, final, status = decode(res), res["url"], res["status"]
    ex = extract(text, final)
    items = []
    for it in ex["items"]:
        rich, c = check(it)
        items.append({"type": " + ".join(_types(it)) or "Thing", "source": it.get("_src"), "block": it.get("_block"), "rich": rich,
                      "eligible": bool(rich) and not c.errors, "errors": c.errors, "warnings": c.warnings, "ok": c.ok, "data": _summary(it)})
    order = lambda x: (0 if x["rich"] else 1, -len(x["errors"]), -len(x["warnings"]))
    items.sort(key=order)
    js = len(re.findall(r"<script[^>]+src=[^>]+>", text, re.I))
    return {"url": final, "status": status, "items": items, "blocks": ex["blocks"], "rdfa": ex["rdfa"],
            "jsHeavy": not items and js > 15 and len(re.sub(r"<[^>]+>", " ", text).split()) < 200,
            "host": urlparse(final).hostname or ""}
