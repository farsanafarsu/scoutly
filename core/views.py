"""Serves the static site (the HTML pages and assets in the repository root) and JSON errors."""
import posixpath
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import redirect
from django.utils._os import safe_join
from django.utils.html import json_script
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.static import serve

from .api import respond

# Only these file types are ever served, so source code, the database and config files never are.
SERVABLE = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg", ".ico",
            ".xml", ".woff", ".woff2", ".pdf", ".webmanifest"}
SERVABLE_FILES = {"robots.txt", "llms.txt"}
# Root-level pages that load the app script are the logged-in workspace.
APP_PAGE = b'src="assets/app.js"'
# Folders that hold code rather than pages.
PRIVATE_DIRS = {"config", "core", "accounts", "seo", "workspace", "tools", "staticfiles", "node_modules", "venv", ".venv"}


def _servable(rel):
    parts = rel.split("/")
    if any(p.startswith(".") for p in parts) or parts[0] in PRIVATE_DIRS:
        return False
    name = parts[-1]
    return name in SERVABLE_FILES or Path(name).suffix.lower() in SERVABLE


def _not_found(request):
    if request.path.startswith("/api/"):
        return respond({"error": "No such API endpoint."}, 404)
    page = Path(settings.SITE_ROOT) / "404.html"
    if page.is_file():
        return FileResponse(open(page, "rb"), content_type="text/html; charset=utf-8", status=404)
    return respond({"error": "Not found."}, 404)


def site(request, path):
    if request.method not in ("GET", "HEAD") or request.path.startswith("/api/"):
        return _not_found(request)
    rel = posixpath.normpath(path).lstrip("/") if path else ""
    if rel in ("", "."):
        rel = "index.html"
    try:
        full = Path(safe_join(settings.SITE_ROOT, rel))
    except Exception:  # SuspiciousFileOperation: the path tried to leave the site root
        return _not_found(request)
    if full.is_dir():
        if not request.path.endswith("/"):
            return HttpResponsePermanentRedirect(request.path + "/")
        rel = posixpath.join(rel, "index.html")
        full = full / "index.html"
    if not full.is_file() or not _servable(rel):
        return _not_found(request)
    if rel.endswith(".html") and "/" not in rel:
        if rel == "login.html" and request.user.is_authenticated:
            return redirect(_after_login(request))
        html = full.read_bytes()
        if APP_PAGE in html:
            return _app_page(request, rel, html)
    res = serve(request, rel, document_root=settings.SITE_ROOT)
    res["Cache-Control"] = "no-store" if settings.DEBUG else ("public, max-age=300" if rel.endswith(".html") else "public, max-age=86400")
    return res


def _after_login(request):
    """Where a logged-in visitor to login.html goes: the audit they asked for, ?next, or the dashboard."""
    site = request.GET.get("site", "").strip()
    if site:
        return "/audit.html?url=" + quote(site, safe="")
    nxt = request.GET.get("next", "")
    if nxt.startswith("/") and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return "/dashboard.html"


def _app_page(request, page, html):
    """App pages need a login, and start with the user's saved data embedded (workspace.boot)."""
    if not request.user.is_authenticated:
        return redirect("/login.html?mode=login&next=" + quote(request.get_full_path(), safe=""))
    from workspace.boot import page_boot

    tag = str(json_script(page_boot(request, page), "scoutly-boot")).encode()
    i = html.find(b"<head>")
    html = html[: i + 6] + b"\n" + tag + html[i + 6:] if i >= 0 else tag + html
    res = HttpResponse(html, content_type="text/html; charset=utf-8")
    res["Cache-Control"] = "private, no-store"  # it holds this user's data
    return res


def csrf_failure(request, reason=""):
    return respond({"error": "Your session expired or the page is out of date. Reload the page and try again.", "csrf": True}, 403)
