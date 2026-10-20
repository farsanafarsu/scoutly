# Scoutly

Marketing site for Scoutly, an SEO, AEO and GEO intelligence platform. Static HTML with no build step at runtime, so it can be hosted on GitHub Pages as-is.

## Structure

| Path | What it is |
| --- | --- |
| `index.html` | Homepage (hand-edited; inline CSS and JS) |
| `login.html` | Account / audit setup preview (`noindex`) |
| `website-seo-audit/`, `backlink-checker/`, `broken-link-checker/`, `keyword-research/`, `competitor-analysis/`, `ai-search-visibility/`, `aeo-geo/` | Feature pages (generated) |
| `seo-glossary/`, `about/`, `404.html` | Resource, company and error pages (generated) |
| `sitemap.xml`, `robots.txt` | Generated |
| `assets/site.css`, `assets/site.js` | Shared styles and scripts for the generated pages. `site.js` also handles the homepage's mobile menu and URL scan forms |
| `tools/` | Page generator, site checker and domain config |

## Editing generated pages

Feature pages, the glossary, the about page, the 404 page, the sitemap and robots.txt are generated from `tools/build-pages.mjs`. Edit the content there, then run:

```sh
node tools/build-pages.mjs   # rebuild pages, sitemap.xml and robots.txt
node tools/check-site.mjs    # broken links/fragments, one h1 per page, unique titles, canonicals, JSON-LD
```

Commit the generated files. No dependencies are needed (Node 18+).

## Before deploying

1. Set the real domain in `tools/site.config.mjs` (it's currently the placeholder `https://scoutly.example`), then run `node tools/build-pages.mjs`. This also updates the canonical, Open Graph and JSON-LD URLs in `index.html`.
2. `404.html` uses root-relative links (`/…`), which assumes the site is served from the domain root (a custom domain). If you deploy to a project URL such as `user.github.io/scoutly/`, adjust those links.
