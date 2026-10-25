"""Integrations: what this Scoutly server is connected to, for the Integrations page.

  * status(refresh): the API server itself (local or hosted behind the Cloudflare Worker), DataForSEO,
    the team database (MongoDB) and whether a team key guards it, and the PageSpeed Insights key

Only whether each one is set up and answering comes back: never a credential, connection string or key.
"""
import os
import time

import server as engine


def _database(refresh):
    uri = os.environ.get("MONGODB_URI", "").strip()
    out = {"configured": bool(uri), "teamKey": bool(engine.TEAM_KEY), "ok": False, "ms": None, "message": ""}
    if not uri:
        out["message"] = "Not set up. Backlinks and SEO Campaigns are kept in each browser; the Action Center needs it."
        return out
    if engine.API_ONLY and not engine.TEAM_KEY:
        out["message"] = "MONGODB_URI is set, but the hosted server keeps the database off until SCOUTLY_TEAM_KEY is set too."
        return out
    try:
        import linkstore
        t0 = time.perf_counter()
        linkstore._collection().database.command("ping")
        out.update(ok=True, ms=round((time.perf_counter() - t0) * 1000), message="Connected")
    except linkstore.NotConfigured as e:
        out["message"] = str(e)
    except Exception as e:
        out["message"] = f"Set up, but the database didn't answer ({e.__class__.__name__}). Check the connection string and that Atlas allows access from anywhere (0.0.0.0/0)."
    return out


def status(refresh=False):
    import provider
    return {
        "server": {"hosted": engine.API_ONLY, "proxyKey": bool(engine.PROXY_KEY), "allowPrivate": engine.ALLOW_PRIVATE},
        "dataforseo": provider.status(refresh),
        "database": _database(refresh),
        "pagespeed": {"ownKey": bool(engine.PSI_KEY)},
    }
