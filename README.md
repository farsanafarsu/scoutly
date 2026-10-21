# Scoutly

Scoutly is an SEO, AEO and GEO intelligence platform. The marketing site and app pages are static HTML. The backend is a Django project that serves those pages, runs website audits and the research tools, and stores each user's account and saved work in MongoDB.

## Run it locally

Python 3.12+ and MongoDB: either a local server ([MongoDB Community Server](https://www.mongodb.com/try/download/community)) or a free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster. Put its connection string in `.env` as `MONGODB_URI`.

```sh
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
python manage.py migrate
python manage.py createsuperuser  # optional: for the admin at /admin/
python manage.py runserver        # http://localhost:8000
```

`python manage.py test` runs the test suite.

## Structure

| Path | What it is |
| --- | --- |
| `config/` | Django settings, URL routes, WSGI/ASGI entry points |
| `core/` | Shared JSON API helpers, rate limiting, and the view that serves the static pages |
| `accounts/` | User accounts (email + password, session login) |
| `seo/` | The audit engine (`engine.py`), free research tools (`tools.py`), optional DataForSEO data (`provider.py`) and their API |
| `workspace/` | A user's saved sites, audits, keyword lists, rank snapshots, campaigns, preferences and tool state |
| `index.html` | Homepage (hand-edited; inline CSS and JS) |
| `*.html` in the root | App pages: audit, dashboard, keyword tools, settings and so on |
| `website-seo-audit/`, `backlink-checker/`, `broken-link-checker/`, `keyword-research/`, `competitor-analysis/`, `ai-search-visibility/`, `aeo-geo/` | Feature pages (generated) |
| `seo-glossary/`, `about/`, `404.html` | Resource, company and error pages (generated) |
| `sitemap.xml`, `robots.txt` | Generated |
| `assets/` | Shared styles, scripts, icons and images |
| `tools/` | Page generator, site checker and domain config |

Django serves the pages straight from the repository root. Only page and asset file types are served, so code and `.env` never are.

`mongo_migrations/` holds migrations for Django's admin, auth and contenttypes apps, generated for MongoDB ObjectId keys (see `MIGRATION_MODULES` in `config/settings.py`).

## API

Every endpoint answers JSON. Failures carry `{"error": "..."}` with a message meant for the person using the page.

**SEO tools.** These have the same request and response formats as the old `server.py`. Expected failures, such as a site that can't be reached, answer `200` with `{"error"}`.

| Endpoint | What it does |
| --- | --- |
| `GET /api/audit?url=&kw=` | Full website audit. For a logged-in user it's also saved, and the response includes `auditId` |
| `GET /api/pagespeed?url=` | PageSpeed Insights proxy (keeps `PAGESPEED_API_KEY` on the server) |
| `GET /api/keywords`, `/api/topics`, `/api/intent`, `/api/page`, `/api/domain`, `/api/ai-ready`, `/api/aeo` | Free research tools |
| `POST /api/backlinks` | Checks that given pages link to a target |
| `GET /api/provider/status`, `/usage`, `/serp`, `/organic`, `/traffic`, `/backlinks`, `/gap`; `POST /api/provider/keywords`, `/clear-cache` | DataForSEO data. Without `run=1`, only the 24-hour cache answers |
| `GET /api/health` | Server and database check |

**Accounts** (`/api/auth/`). These use session cookies. Requests that change data need the CSRF token: call `GET /api/auth/csrf` once, then send the `csrftoken` cookie's value as the `X-CSRFToken` header.

| Endpoint | What it does |
| --- | --- |
| `GET /api/auth/csrf` | Sets the CSRF cookie |
| `POST /api/auth/signup` `{name, email, password}` | Creates an account and logs in |
| `POST /api/auth/login` `{email, password}` / `POST /api/auth/logout` | Login is locked for 15 minutes after 8 failed tries |
| `GET /api/auth/me` · `PATCH {name, email}` · `DELETE {password}` | The current user (or `null`), profile edits, account deletion (removes all their data) |
| `POST /api/auth/password` `{current, new}` | Changes the password and stays logged in |

**Workspace.** These need a login and only ever touch the current user's data.

| Endpoint | Methods |
| --- | --- |
| `/api/sites`, `/api/sites/<id>` | List (with each site's latest audit), add `{url, name}`, rename, delete |
| `/api/audits?site=&limit=`, `/api/audits/<id>` | Saved audit summaries; one audit with its full `result`; delete |
| `/api/keyword-lists`, `/api/keyword-lists/<id>` | Keyword Manager lists: create `{name}`, rename, delete, read with items |
| `/api/keyword-lists/<id>/keywords` | `POST {items: [{kw, ...metrics}]}` adds new keywords (duplicates skipped); `DELETE {kws}` |
| `/api/rank-snapshots`, `/api/rank-snapshots/<YYYY-MM-DD>` | Position Tracking imports `{date, queries, pages}`, one per date; delete by date |
| `/api/campaigns`, `/api/campaigns/<id>` | `{name, brand, loc, url, end, status}` with status `Active` or `Paused` |
| `/api/preferences` | `GET` / `PUT` the Settings defaults, e.g. `{"gl": "in"}` |
| `/api/state`, `/api/state/<key>` | Per-tool saved state under the keys the pages use (`scoutly.draft`, `scoutly.aivis`, …): `GET` / `PUT {data}` / `DELETE`, up to 512 KB each |
| `GET /api/export` | Everything stored for the account, as one JSON file |
| `/api/store`, `/api/store/<key>` | The page store the app pages use (below): `GET ?keys=`, `PUT {data}` per key, `DELETE` to clear all saved work |

## How the app pages save data

App pages (every root page that loads `assets/app.js`) need a login. Anonymous visitors are redirected to `login.html` and come back afterwards. When Django serves an app page, it embeds the user and their saved data in a `<script id="scoutly-boot" type="application/json">` block. `Scoutly.read(key)` then answers straight away, with no loading flash. `Scoutly.write(key, value)` saves to the account shortly after (`PUT /api/store/<key>`), and anything unsaved is flushed when the page closes.

The keys and data shapes are the ones the pages used in browser storage (`scoutly.campaigns`, `scoutly.kwlists`, `scoutly.ranks`, …). `workspace/store.py` maps the structured ones onto their tables: campaigns, keyword lists and keywords, rank snapshots, preferences, sites. Any other `scoutly.*` key is kept as-is. Large keys (rank snapshots) are embedded only on the pages that use them. Other pages can't overwrite them. When someone first logs in on a browser that used Scoutly before accounts, that browser's saved data moves into their account.

## Configuration

Everything comes from environment variables. Locally, `.env` holds them (see `.env.example` for the full list):

- `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`
- `MONGODB_URI`, `MONGODB_NAME`: the MongoDB connection string and database name (tests use `test_<name>`)
- `PAGESPEED_API_KEY`: your own PageSpeed quota (Google's shared quota is often used up)
- `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`: search volume, rankings, traffic and backlinks (paid)
- `SCOUTLY_AUDITS_PER_HOUR`, `SCOUTLY_AUDITS_PER_HOUR_USER`: audit rate limits
- `SCOUTLY_PAID_REQUIRES_LOGIN`: on by default outside DEBUG, so anonymous visitors can't spend DataForSEO credit

## Deploying the backend

1. Set `DJANGO_DEBUG=0`, a long random `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`. Set `DJANGO_BEHIND_PROXY=1` if a proxy terminates HTTPS. Point `MONGODB_URI` at your production cluster. Use a replica set, as MongoDB Atlas always does: account changes are then saved in transactions, which a standalone MongoDB server can't run.
2. Run `python manage.py migrate`, `python manage.py collectstatic` (the admin's CSS and JS go to `staticfiles/`; serve that folder at `/static/`), and `python manage.py check --deploy`.
3. Run it with a WSGI server and a long timeout, because audits can take up to a minute: `gunicorn config.wsgi --workers 3 --timeout 120`.
4. Rate limits and login lockouts use Django's cache. With more than one worker process, set `CACHES` to Redis or Memcached so the workers share counts.

## Editing generated pages

Feature pages, the glossary, the about page, the 404 page, the sitemap and robots.txt are generated from `tools/build-pages.mjs`. Edit the content there, then run:

```sh
node tools/build-pages.mjs   # rebuild pages, sitemap.xml and robots.txt
node tools/check-site.mjs    # broken links/fragments, one h1 per page, unique titles, canonicals, JSON-LD
```

Commit the generated files. No dependencies are needed (Node 18+).

## Before deploying the site

1. Set the real domain in `tools/site.config.mjs` (it's currently the placeholder `https://scoutly.example`), then run `node tools/build-pages.mjs`. This also updates the canonical, Open Graph and JSON-LD URLs in `index.html`.
2. `404.html` uses root-relative links (`/…`), which assumes the site is served from the domain root (a custom domain). If you deploy to a project URL such as `user.github.io/scoutly/`, adjust those links.
