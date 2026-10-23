"""Action Center scans: run a source (Site Audit, PageSpeed, backlinks) for a project and record what it found.

Scans take from a few seconds to a couple of minutes, so they run in a background thread and the page polls
/api/tasks/job. Only one scan runs per project at a time, at most MAX_RUNNING run at once on the server, and
each project gets at most MAX_PER_HOUR scans an hour. Jobs live in memory: a server restart forgets running
jobs (their scans simply don't get recorded), never the tasks themselves.

Crawling reuses engine.py's fetcher, so it keeps its limits and protections: http(s) only, private, local and
cloud-metadata addresses refused (safe_host), robots.txt respected, 25 pages / 3 levels deep, 4 MB per response.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from server import AuditError, link_status, pagespeed, run_audit
import actions
import linkstore
import taskstore

MAX_RUNNING = 3
MAX_PER_HOUR = 12
KEEP_SECONDS = 3600

_lock = threading.Lock()
_jobs = {}
_history = {}  # project → start times in the last hour


def _prune():
    cut = time.time() - KEEP_SECONDS
    for jid in [j for j, v in _jobs.items() if v["state"] != "running" and v["finished"] < cut]:
        del _jobs[jid]


def start(project, kind, fn, by):
    """Start fn() in the background unless this project is already scanning or over its hourly limit."""
    project = taskstore.project_id(project)
    with _lock:
        _prune()
        running = [j for j in _jobs.values() if j["state"] == "running"]
        mine = next((j for j in running if j["project"] == project), None)
        if mine:
            return {"job": _public(mine), "already": True}
        if len(running) >= MAX_RUNNING:
            raise AuditError("The server is already running several scans. Try again in a minute.")
        recent = [t for t in _history.get(project, []) if t > time.time() - 3600]
        if len(recent) >= MAX_PER_HOUR:
            raise AuditError(f"This website has been scanned {MAX_PER_HOUR} times in the last hour. Try again later.")
        _history[project] = recent + [time.time()]
        jid = os.urandom(8).hex()
        job = {"id": jid, "project": project, "kind": kind, "state": "running", "started": time.time(), "finished": 0,
               "by": by, "step": "Starting…", "result": None, "error": ""}
        _jobs[jid] = job

    def run():
        try:
            job["result"] = fn(lambda s: job.__setitem__("step", s))
            job["state"] = "done"
        except AuditError as e:
            job.update(state="failed", error=str(e))
        except Exception as e:
            job.update(state="failed", error=f"The scan failed unexpectedly ({e.__class__.__name__}).")
        job["finished"] = time.time()
    threading.Thread(target=run, daemon=True, name=f"scan-{project}").start()
    return {"job": _public(job)}


def _public(j):
    return {k: j[k] for k in ("id", "project", "kind", "state", "step", "result", "error", "by")} | {"seconds": round((j["finished"] or time.time()) - j["started"])}


def status(jid):
    j = _jobs.get(str(jid or ""))
    if not j:
        raise AuditError("That scan isn't running any more. Reload the page to see its results.")
    return {"job": _public(j)}


# ------------------------------------------------------------------ the scans

def _psi_types(dev):
    return ({f"psi-score-{dev}"} | {f"cwv-{m}-{dev}" for m in ("lcp", "inp", "cls")} | {f"lab-{m}-{dev}" for m in ("lcp", "cls")}
            | {f"{a}-{dev}" for a in actions.PSI_OPPORTUNITIES})


def site_scan(project, url, keyword, with_psi, by):
    project = taskstore.project_id(project)
    url = (url or "").strip() or f"https://{project}/"
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    if actions.host_of(url) != project:
        raise AuditError(f"That page isn't on {project}. Pick a page on the selected website.")

    def go(step):
        out = {}
        step("Crawling the site (up to 25 pages)…")
        try:
            audit = run_audit(url, keyword)
        except AuditError as e:
            taskstore.record_failed_scan(project, "site-audit", by, url, str(e))
            raise
        final = audit.get("finalUrl") or url
        if actions.host_of(final) != project:
            raise AuditError(f"{url} redirects to another website ({actions.host_of(final)}), so its issues weren't added to {project}.")
        out["audit"] = taskstore.ingest(project, actions.from_audit(project, audit), "site-audit", by, final,
                                        evaluated=actions.audit_checked_types(audit), scopes={final, project})["counts"]
        if with_psi:
            out["pagespeed"] = {}
            for dev in ("mobile", "desktop"):
                step(f"Running PageSpeed Insights ({dev})…")
                psi = pagespeed(final, dev)
                err = psi.get("error")
                if err:
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    taskstore.record_failed_scan(project, "pagespeed", by, final, msg or "PageSpeed Insights failed")
                    out["pagespeed"][dev] = {"error": msg or "PageSpeed Insights didn't return a result."}
                    continue
                out["pagespeed"][dev] = taskstore.ingest(project, actions.from_pagespeed(project, final, psi, dev), "pagespeed", by, final,
                                                         evaluated=_psi_types(dev), scopes={final})["counts"]
        return out
    return go


def backlink_scan(project, by):
    project = taskstore.project_id(project)

    def go(step):
        step("Reading the team's backlinks…")
        links = linkstore.list_links()["items"]
        targets = sorted({b.get("target") for b in links if b.get("target") and actions.host_of(b["target"]) == project})[:25]
        step(f"Checking {len(targets)} page{'s' if len(targets) != 1 else ''} that receive backlinks…")
        with ThreadPoolExecutor(max_workers=6) as pool:
            status = dict(zip(targets, (s["status"] for s in pool.map(link_status, targets))))
        issues = actions.from_backlinks(project, links, status)
        return {"backlinks": taskstore.ingest(project, issues, "backlink-manager", by, f"{len(links)} logged backlinks")["counts"],
                "links": sum(1 for b in links if actions.host_of(b.get("target") or "") == project)}
    return go


def search_console_import(project, snapshots, by):
    """Opportunities from Search Console exports the user imported into Position Tracking (fast, so no job)."""
    project = taskstore.project_id(project)
    if not isinstance(snapshots, list) or not snapshots:
        raise AuditError("Import a Search Console export in Position Tracking first, then send it here.")
    issues = actions.from_search_console(project, snapshots[-2:])
    dates = sorted(str(s.get("date")) for s in snapshots[-2:] if isinstance(s, dict))
    return taskstore.ingest(project, issues, "search-console", by, "Search Console export " + " and ".join(dates))


def verify(tid, by):
    """Re-run the scan behind a task so its result shows whether the issue is still there."""
    t = taskstore.get_task(tid)["item"]
    src = t["source"]
    if src == "site-audit":
        url = (t.get("evidence") or {}).get("auditedUrl") or (t["scope"] if t["scope"].startswith("http") else f"https://{t['project']}/")
        return start(t["project"], "verify", site_scan(t["project"], url, "", False, by), by)
    if src == "pagespeed":
        return start(t["project"], "verify", site_scan(t["project"], t["scope"], "", True, by), by)
    if src == "backlink-manager":
        return start(t["project"], "verify", backlink_scan(t["project"], by), by)
    if src == "search-console":
        raise AuditError("Search Console data can't be re-checked automatically. Import a newer export in Position Tracking, then click Import from Position Tracking.")
    raise AuditError("Tasks you added by hand have no scan to re-run. Check the fix yourself and mark the task resolved.")
