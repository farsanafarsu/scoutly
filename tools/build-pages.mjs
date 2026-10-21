// Builds Scoutly's static inner pages, sitemap.xml and robots.txt.
// No dependencies: run `node tools/build-pages.mjs` from the repo root and commit the output.
// The homepage (index.html) is hand-edited; this script only syncs its production origin.
import { writeFileSync, mkdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SITE } from './site.config.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const TODAY = new Date().toISOString().slice(0, 10);

/* ---------- shared data ---------- */
export const FEATURES = [
  { slug: 'website-seo-audit', name: 'Website SEO Audit', short: 'Technical and on-page checks with prioritized, evidence-based findings.' },
  { slug: 'backlink-checker', name: 'Backlink Checker', footer: 'Backlink Explorer', short: 'Referring domains, lost links and competitor link gaps from an external backlink index.' },
  { slug: 'broken-link-checker', name: 'Broken Link Checker', short: '404 errors, redirect chains and dead ends, with the page each link sits on.' },
  { slug: 'keyword-research', name: 'Keyword Research', short: 'Keyword ideas grouped by search intent and topic, with labeled data sources.' },
  { slug: 'competitor-analysis', name: 'Competitor Analysis', short: 'Keyword, content and backlink gaps against the sites you compete with in search.' },
  { slug: 'ai-search-visibility', name: 'AI Search Visibility', short: 'Brand mentions and citations in AI-generated answers, where measurement is possible.' },
  { slug: 'aeo-geo', name: 'AEO & GEO', short: 'Make content easier for answer engines and generative search to understand.' },
];
const feat = (s) => FEATURES.find((f) => f.slug === s);

const esc = (s) => String(s).replace(/&(?!(amp|lt|gt|quot|#\d+);)/g, '&amp;').replace(/"/g, '&quot;');
const strip = (s) => String(s).replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
const decode = (s) => s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');

const I = {
  audit: '<path d="M9 4h6v3H9zM7 5.5H5.5v15h13v-15H17"/><path d="M8.5 13l2.5 2.5 4.5-5"/>',
  link: '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
  broken: '<path d="M9 7l-1.5-1.5a4 4 0 00-5.6 5.6L4 13M15 17l1.5 1.5a4 4 0 005.6-5.6L20 11"/><path d="M8 3v2M3 8h2M16 21v-2M21 16h-2"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5M8.5 11h5"/>',
  compare: '<path d="M5 20V10M10 20V4M15 20v-7M20 20V8"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M18.5 16.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  gauge: '<path d="M4 17a8 8 0 1116 0"/><path d="M12 17l4-5"/>',
  code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  tree: '<circle cx="12" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="19" r="2"/><path d="M12 7v5M12 12l-6 5M12 12l6 5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  check: '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12.2l2.4 2.4 4.6-5"/>',
  doc: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4M9 12h6M9 15.5h6"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.8 2.6 14.2 0 17M12 3.5c-2.6 2.8-2.6 14.2 0 17"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3 19c.6-3.2 3-5 6-5s5.4 1.8 6 5"/><path d="M15.5 5.2a3 3 0 010 5.6M17.5 14.3c1.8.7 3 2.3 3.4 4.7"/>',
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
};
const icon = (k) => `<span class="ic" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${I[k]}</svg></span>`;

/* ---------- building blocks ---------- */
const cards = (items, cls = '') => `<div class="cards ${cls}">${items.map((c) => `
  <article class="card">${c.icon ? icon(c.icon) : ''}${c.chip ? `<span class="chip ${c.chip[0]}">${c.chip[1]}</span>` : ''}<h3${c.chip ? ' style="margin-top:12px"' : ''}>${c.h}</h3><p>${c.p}</p></article>`).join('')}
</div>`;
const steps = (items) => `<ol class="steps">${items.map((s) => `<li><h3>${s.h}</h3><p>${s.p}</p></li>`).join('')}</ol>`;
const ticks = (items) => `<ul>${items.map((t) => `<li>${t}</li>`).join('')}</ul>`;
const sec = ({ id, kick, h, lead = '', body = '', cls = '' }) => `
<section class="sec ${cls}" id="${id}" aria-labelledby="${id}-h"><div class="w">
  ${kick ? `<p class="kick">${kick}</p>` : ''}
  <h2 id="${id}-h">${h}</h2>
  ${lead ? `<p class="lead">${lead}</p>` : ''}
  ${body}
</div></section>`;
const split = (left, right) => `<div class="split" style="margin-top:40px"><div class="prose">${left}</div><div>${right}</div></div>`;
const mock = (title, tag, inner, note = 'Illustrative example with fictional data, not a real audit result.') =>
  `<figure class="mock" style="margin:0"><figcaption class="mock-top"><b>${title}</b><span class="chip in">${tag}</span></figcaption>${inner}<p class="mock-note">${note}</p></figure>`;
const table = (head, rows) => `<div class="mock-scroll"><table><thead><tr>${head.map((h) => `<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
const Y = '<span class="yes">✓<span class="sr-only"> yes</span></span>', N = '<span class="no">—<span class="sr-only"> no</span></span>';

/* ---------- layout ---------- */
function nav(r, current, scanHref) {
  return `<a class="skip" href="#main">Skip to content</a>
<header class="nav" id="nav">
<div class="nav-top"><div class="w nav-top-in">
<a class="nav-promo" href="${r}login.html"><span class="nav-spark" aria-hidden="true">✦</span><span>Scan your website free</span><span aria-hidden="true">→</span></a>
<nav class="nav-util" aria-label="Resources and account"><a href="${r}seo-glossary/">SEO glossary</a><a href="${r}about/">About</a><a href="${r}login.html?mode=login">Login</a></nav>
</div></div>
<div class="nav-main"><div class="w nav-row">
<a href="${r}" class="logo nav-logo" aria-label="Scoutly home"><img src="${r}assets/scoutly-mark.svg" alt="" width="34" height="34"><span>Scout<b>ly</b></span></a>
<nav class="lk" id="navLinks" aria-label="Primary"><a href="${r}#features">Features</a><a href="${r}website-seo-audit/"${current === 'website-seo-audit' ? ' aria-current="page"' : ''}>SEO audit</a><a href="${r}backlink-checker/"${current === 'backlink-checker' ? ' aria-current="page"' : ''}>Backlinks</a><a href="${r}ai-search-visibility/"${current === 'ai-search-visibility' ? ' aria-current="page"' : ''}>AI search</a><a href="${r}#how">How it works</a><a href="${r}#faq">FAQ</a></nav>
<div class="rt"><a class="btn nav-login" href="${r}login.html?mode=login">Login</a><a class="btn nav-cta" href="${scanHref}">Scan your website</a><button class="nav-burger" id="navBurger" type="button" aria-label="Open menu" aria-expanded="false" aria-controls="navLinks"><i></i><i></i><i></i></button></div>
</div></div>
</header>`;
}

function footer(r) {
  return `<footer><div class="w"><div class="fg">
<div><a class="logo" href="${r}" aria-label="Scoutly home"><img src="${r}assets/scoutly-mark.svg" alt="" width="34" height="34" loading="lazy"><span>Scout<b>ly</b></span></a><p style="max-width:300px">Scoutly is your workspace for website SEO audits, backlink intelligence, and search visibility.</p></div>
<nav aria-labelledby="f-platform"><h2 id="f-platform">Platform</h2><ul>${FEATURES.map((f) => `<li><a href="${r}${f.slug}/">${f.footer || f.name}</a></li>`).join('')}</ul></nav>
<nav aria-labelledby="f-resources"><h2 id="f-resources">Resources</h2><ul><li><a href="${r}seo-glossary/">SEO glossary</a></li><li><a href="${r}website-seo-audit/#what-it-checks">Technical SEO checks</a></li><li><a href="${r}backlink-checker/#link-building">Link-building guidance</a></li><li><a href="${r}#faq">FAQ</a></li></ul></nav>
<nav aria-labelledby="f-company"><h2 id="f-company">Company</h2><ul><li><a href="${r}about/">About Scoutly</a></li><li><a href="${r}about/#product-status">Product status</a></li><li><a href="${r}about/#data-sources">Data sources</a></li><li><a href="${r}login.html?mode=login">Log in</a></li></ul></nav>
</div><div class="lgl"><p>© 2026 Scoutly. All rights reserved.</p><p>Scoutly is in early access. Product visuals on this site are illustrative examples, not real audit data.</p></div></div></footer>`;
}

const scanForm = (r, id, label = 'Scan Your Website Free') => `<form class="scan" id="${id}" action="${r}login.html" method="get" data-scan data-msg="${id}Msg" novalidate>
  <label class="sr-only" for="${id}Site">Enter your website URL</label>
  <input id="${id}Site" name="site" type="text" inputmode="url" autocomplete="url" autocapitalize="off" spellcheck="false" placeholder="Enter your website URL" aria-describedby="${id}Msg">
  <button class="btn p" type="submit">${label}</button>
</form><p class="msg" id="${id}Msg" role="status" aria-live="polite"></p>`;

function page(p) {
  const r = p.root ?? '../';
  const url = p.slug ? `${SITE}/${p.slug}/` : `${SITE}/`;
  const crumbs = p.crumbs ?? [{ name: 'Home', href: r }, { name: p.crumb }];
  const ld = [];
  if (!p.noindex) {
    ld.push({ '@context': 'https://schema.org', '@type': 'BreadcrumbList', itemListElement: crumbs.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: i === 0 ? `${SITE}/` : url })) });
    if (p.faq?.length) ld.push({ '@context': 'https://schema.org', '@type': 'FAQPage', mainEntity: p.faq.map(([q, a]) => ({ '@type': 'Question', name: decode(strip(q)), acceptedAnswer: { '@type': 'Answer', text: decode(strip(a)) } })) });
  }
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${p.title}</title>
<meta name="description" content="${esc(p.description)}">
${p.noindex ? '<meta name="robots" content="noindex">' : `<link rel="canonical" href="${url}">\n<meta name="robots" content="index, follow, max-image-preview:large">`}
<meta name="theme-color" content="#050a1f">
<link rel="icon" type="image/png" href="${r}assets/favicon.png">
<link rel="apple-touch-icon" href="${r}assets/apple-touch-icon.png">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Scoutly">
<meta property="og:title" content="${esc(p.ogTitle || p.title)}">
<meta property="og:description" content="${esc(p.description)}">
${p.noindex ? '' : `<meta property="og:url" content="${url}">\n`}<meta property="og:image" content="${SITE}/assets/og-image.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="The Scoutly logo, a search lens with a spark inside, next to the headline Get found on Google. Get discovered by AI.">
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap">
<link rel="stylesheet" href="${r}assets/site.css">
${ld.map((o) => `<script type="application/ld+json">${JSON.stringify(o)}</script>`).join('\n')}
</head>
<body>
${nav(r, p.slug, p.form !== false ? '#scan' : p.cta !== false ? '#scanEnd' : r + 'login.html')}
<main id="main">
<header class="ph"><div class="w ph-g"><div>
  <nav class="crumbs" aria-label="Breadcrumb"><ol>${crumbs.map((c, i) => i < crumbs.length - 1 ? `<li><a href="${c.href}">${c.name}</a></li>` : `<li aria-current="page">${c.name}</li>`).join('')}</ol></nav>
  <p class="eye"><i></i>${p.eyebrow}</p>
  <h1>${p.h1}</h1>
  ${p.intro.map((t) => `<p class="intro">${t}</p>`).join('\n  ')}
  ${p.form === false ? '' : scanForm(r, 'scan', p.formLabel)}
  ${p.status ? `<div class="status"><span class="chip">${p.status[0]}</span><p>${p.status[1]}</p></div>` : ''}
</div><div class="ph-art" aria-hidden="true"><img src="${r}assets/scoutly-mark.svg" alt="" width="240" height="240"></div></div></header>
${p.body}
${p.related ? sec({ id: 'related', kick: 'Related tools', h: p.relatedH || 'Keep investigating', cls: 'alt', body: `<div class="rel">${p.related.map((s) => { const f = feat(s); return `<a href="${r}${f.slug}/"><b>${f.name}</b><span>${f.short}</span></a>`; }).join('')}</div>` }) : ''}
${p.faq?.length ? sec({ id: 'faq', kick: 'FAQ', h: p.faqH, body: `<div class="faq">${p.faq.map(([q, a]) => `<details><summary><h3>${q}</h3></summary><div class="a">${a.split('\n\n').map((x) => `<p>${x}</p>`).join('')}</div></details>`).join('')}</div>` }) : ''}
${p.cta === false ? '' : `<section class="sec" aria-labelledby="cta-h"><div class="w"><div class="cta">
  <h2 id="cta-h">${p.cta?.h || 'Your next SEO opportunity is waiting to be found.'}</h2>
  <p>${p.cta?.p || 'Start with your website. Discover what to improve next.'}</p>
  ${scanForm(r, 'scanEnd', p.cta?.btn || 'Scan Your Website')}
</div></div></section>`}
</main>
${footer(r)}
<script src="${r}assets/site.js" defer></script>
</body>
</html>
`;
}

/* ---------- pages ---------- */
const PAGES = [];

PAGES.push({
  slug: 'website-seo-audit', crumb: 'Website SEO Audit',
  title: 'Website SEO Audit Tool: Technical & On-Page Checks | Scoutly',
  description: 'Check crawlability, indexability, metadata, broken links, internal linking, structured data and performance, then fix what matters first with Scoutly.',
  eyebrow: 'Website SEO audit',
  h1: 'A website SEO audit that shows what to fix, and why',
  intro: ['Scoutly’s website SEO audit crawls the pages search engines can reach, checks the technical and on-page signals that affect how those pages are discovered and understood, and turns the results into a prioritized list of findings. Each finding is designed to show the affected URLs, the evidence behind it, a plain-English explanation and a suggested fix.'],
  formLabel: 'Run a Website SEO Audit',
  status: ['In development', 'Scoutly is in early access and audits aren’t open to the public yet. This page explains what the audit is being built to check. Once audits launch, results will come only from a real crawl of your site.'],
  body: [
    sec({ id: 'what-it-checks', kick: 'What the audit checks', h: 'Six areas of technical and on-page SEO',
      lead: 'An audit first looks at your site the way a crawler does: can pages be found, fetched and indexed? Then it checks whether each page clearly communicates what it’s about.',
      body: cards([
        { icon: 'eye', h: 'Crawlability and indexability', p: 'Robots.txt rules, meta robots and X-Robots-Tag directives, XML sitemap availability, canonical tags and HTTP status codes. Together they decide whether a page can be crawled and whether it’s eligible to be indexed.' },
        { icon: 'doc', h: 'On-page SEO', p: 'Page titles, meta descriptions, heading structure, image alt attributes, and duplicate or missing metadata across pages. These help search engines and visitors understand each page’s topic.' },
        { icon: 'broken', h: 'Broken links and redirects', p: 'Links returning 4xx or 5xx errors, redirect chains with several hops, redirect loops, and internal links that point to a redirect instead of the final URL. See the <a class="tl" href="../broken-link-checker/">broken link checker</a> for detail.' },
        { icon: 'tree', h: 'Internal linking', p: 'Orphaned pages with no internal links pointing to them, important pages buried deep in the site, and pages with few internal connections. Internal links help crawlers find pages and show how your content fits together.' },
        { icon: 'gauge', h: 'Performance and Core Web Vitals', p: 'Largest Contentful Paint, Interaction to Next Paint and Cumulative Layout Shift, using the PageSpeed Insights API and Chrome field data where it exists for your URLs. This is a planned integration.' },
        { icon: 'code', h: 'Structured data', p: 'JSON-LD, Microdata and RDFa markup, checked for syntax errors and missing properties on supported schema.org types. Valid markup can make a page eligible for some rich results, but it doesn’t guarantee them.' },
      ]) }),
    sec({ id: 'prioritization', cls: 'alt', kick: 'Prioritized findings', h: 'Critical problems first, routine improvements after',
      lead: 'A large site can produce hundreds of findings. Scoutly sorts each one into three levels, so you start with whatever blocks discovery instead of whatever is merely untidy.',
      body: cards([
        { chip: ['cr', 'Critical issues'], h: 'Pages that may not be crawled or indexed', p: 'For example, an important page blocked in robots.txt, a stray noindex tag, or a canonical tag pointing to the wrong URL.' },
        { chip: ['wr', 'Improvements'], h: 'Problems that weaken navigation or clarity', p: 'Broken internal links, redirect chains, duplicate page titles, missing meta descriptions and weak internal connections.' },
        { chip: ['in', 'Optimization opportunities'], h: 'Changes that can make good pages better', p: 'Clearer headings, stronger internal links to important pages, expanded topic coverage and better-structured answers.' },
      ]) + split(`<h3 style="font:600 24px/1.2 Geist,system-ui,sans-serif;letter-spacing:-.03em;color:var(--ink)">Every finding answers four questions</h3>
        ${ticks(['<b>What was detected:</b> the exact issue and the URLs it affects.', '<b>Why it matters:</b> how it can affect crawling, indexing or visitors.', '<b>How to verify it:</b> a way to confirm the problem yourself, so you don’t have to take a score on trust.', '<b>What to do next:</b> a suggested fix, written for whoever will make the change.'])}
        <p>Severity reflects how directly an issue affects crawling, indexing and usability. It isn’t a prediction of ranking changes, because rankings depend on many factors no audit can measure.</p>`,
      mock('Example finding', 'Critical', `<dl>
        <dt>Detected</dt><dd><code>/pricing/</code> is disallowed in robots.txt by the rule <code>Disallow: /pricing</code>.</dd>
        <dt>Why it matters</dt><dd>Search engine crawlers can’t fetch the page, so its content can’t be read. The URL may still appear in results without a description.</dd>
        <dt>How to verify</dt><dd>Open <code>yourwebsite.com/robots.txt</code>, then test the URL with the URL Inspection tool in Google Search Console.</dd>
        <dt>Suggested action</dt><dd>Remove or narrow the rule if the page should be discoverable, then request a recrawl.</dd></dl>`)) }),
    sec({ id: 'how-it-works', kick: 'How an audit runs', h: 'From a URL to a fix list in four steps',
      body: steps([
        { h: 'Enter a URL', p: 'Start with a domain, a subfolder or a single page, depending on how much of the site you want to check.' },
        { h: 'Crawl', p: 'Scoutly reads robots.txt and your XML sitemap, then follows links the way a search engine crawler does, within your plan’s crawl limits.' },
        { h: 'Analyze', p: 'Each fetched page is checked against the audit rules. External data, such as PageSpeed Insights, is added when that integration is connected.' },
        { h: 'Review and compare', p: 'Findings are grouped by severity. Re-run the audit later to see what’s fixed, what’s new and what’s still open.' },
      ]) }),
    sec({ id: 'when-to-audit', cls: 'alt', kick: 'When to run an audit', h: 'Good moments to audit your website',
      body: cards([
        { icon: 'check', h: 'Before and after a launch', p: 'Catch blocked pages, missing titles and broken navigation before visitors and crawlers do.' },
        { icon: 'globe', h: 'During a migration or redesign', p: 'Domain moves, URL changes and new templates are where redirect chains and lost pages usually appear.' },
        { icon: 'doc', h: 'After big content changes', p: 'Removing, merging or reorganizing pages can leave broken links and orphaned content behind.' },
        { icon: 'clock', h: 'On a regular schedule', p: 'Sites that change often benefit from repeat audits, so new issues are caught while they’re still small.' },
      ], 'c4') }),
    sec({ id: 'limits', kick: 'Know the limits', h: 'What an SEO audit can and can’t tell you',
      body: `<div class="prose"><p>An audit is a diagnostic tool, not a ranking forecast. It can show you that a page is blocked, slow or missing a title. It can’t tell you exactly how a search engine weighs those issues against your content, your competitors or what searchers want.</p>
      ${ticks(['Pages that aren’t linked from anywhere and aren’t in your sitemap may not be discovered by any crawler.', 'Content that only appears after JavaScript runs needs rendering to be checked accurately.', 'Sites that block automated requests or rate-limit crawlers can produce incomplete results.', 'Real-world Core Web Vitals data only exists for URLs with enough Chrome traffic.'])}</div>` }),
  ].join(''),
  related: ['broken-link-checker', 'competitor-analysis', 'aeo-geo'],
  faqH: 'Website SEO audit questions',
  faq: [
    ['What’s the difference between a technical SEO audit and an on-page audit?', 'A technical audit looks at whether search engines can crawl, render and index your pages: status codes, robots directives, canonical tags, sitemaps, redirects and performance. An on-page audit looks at what each page communicates: titles, descriptions, headings, content structure and image alt text. Scoutly’s audit is designed to cover both in a single crawl.'],
    ['Will the audit change anything on my website?', 'No. An audit only reads publicly accessible pages, the same way a browser or search engine crawler does. You or your team make the fixes in your CMS or codebase.'],
    ['Does Scoutly respect robots.txt?', 'Scoutly’s crawler is being built to read robots.txt and follow the rules that apply to it. The audit will also report which pages are blocked for search engine crawlers, because that’s a common cause of indexing problems.'],
    ['How many pages can be audited?', 'Crawl limits will depend on the plan and will be published before audits open. Large sites can also be audited one section at a time, for example a single subfolder, to keep results focused.'],
    ['Will fixing every issue improve my rankings?', 'Not necessarily. Fixing technical problems removes obstacles to crawling and indexing, which matters. But rankings also depend on content quality and relevance, competition, and how search engines evaluate your site. No tool can guarantee specific rankings.'],
  ],
  cta: { h: 'Find the issues. Understand the impact. Fix what matters.', btn: 'Run a Website SEO Audit' },
});

PAGES.push({
  slug: 'backlink-checker', crumb: 'Backlink Checker',
  title: 'Backlink Checker: Referring Domains & Link Gaps | Scoutly',
  description: 'Review referring domains and backlinks, spot lost links, and find sites that link to competitors but not to you. Backlink data comes from an external index.',
  eyebrow: 'Backlink checker',
  h1: 'Backlink checker for referring domains, lost links and link gaps',
  intro: ['Backlinks are links from other websites to yours. They help search engines discover your pages and understand how sites relate to each other. Scoutly’s backlink checker is designed to show which domains link to you, which links have disappeared, and which relevant sites link to your competitors but not to you.'],
  formLabel: 'Explore Backlinks',
  status: ['Planned', 'Backlink data needs an external backlink index. No provider is connected in this early-access build, so no backlink data is shown yet.'],
  body: [
    sec({ id: 'data-source', kick: 'Where backlink data comes from', h: 'Why crawling your own site can’t find your backlinks',
      body: split(`<p>Crawling your website shows the links on your pages, which are the links going out. Backlinks live on other people’s websites. To find them, a tool needs an index built by crawling a large part of the web and recording which pages link where.</p>
        <p>Scoutly is being built to use an external backlink data provider for this. That has a few consequences worth knowing:</p>
        ${ticks(['<b>Coverage depends on the provider.</b> No index contains every link on the web, which is why different backlink tools report different totals.', '<b>Freshness depends on recrawls.</b> New and lost links show up after the provider revisits the linking page.', '<b>Search Console is a useful second source.</b> Its Links report shows top linking sites that Google has found for your property.'])}`,
      mock('Referring domains', 'Example', table(['Referring domain', 'Links', 'Status'], [
        ['<code>industry-news.example</code>', '3', '<span class="chip ok">Active</span>'],
        ['<code>partner-directory.example</code>', '1', '<span class="chip cr">Lost</span>'],
        ['<code>local-guide.example</code>', '2', '<span class="chip in">Active · nofollow</span>'],
        ['<code>old-review.example</code>', '1', '<span class="chip wr">Points to a 404</span>'],
      ]))) }),
    sec({ id: 'what-you-can-review', cls: 'alt', kick: 'What you can review', h: 'Understand your link profile, then act on it',
      body: cards([
        { icon: 'globe', h: 'Referring domains', p: 'The distinct websites that link to you. Many links from one site usually count for less than links from several relevant sites.' },
        { icon: 'link', h: 'Backlinks and anchor text', p: 'Which of your pages attract links, which pages link to them, and the clickable text those links use.' },
        { icon: 'broken', h: 'Lost and broken backlinks', p: 'Links that disappeared, or links pointing to URLs on your site that now return a 404. These can often be recovered with a redirect or a short email.' },
        { icon: 'compare', h: 'Competitor link gap', p: 'Domains that link to one or more competitors but not to you. These are often a good place to start relevant outreach.' },
        { icon: 'list', h: 'Outreach prospects', p: 'Organize relevant prospects with notes and a status, so outreach stays focused on sites where a link would make sense to their readers.' },
        { icon: 'clock', h: 'Change monitoring', p: 'Compare snapshots when fresh data is available to see new and lost referring domains over time.' },
      ]) }),
    sec({ id: 'link-gap', kick: 'Competitor backlink gap', h: 'Find sites that link to competitors but not to you',
      body: split(`<p>A link gap analysis compares referring domains across your site and a few competitors. Domains that link to several competitors, but not to you, are worth a closer look: they already cover your topic and link to sites like yours.</p>
        <p>Not every gap is an opportunity. A directory that links to everyone, or a site serving a different market, may not be relevant. Treat the list as research, not a to-do list. To choose which competitors to compare, start with <a href="../competitor-analysis/">SEO competitor analysis</a>.</p>`,
      mock('Link gap', 'Example', table(['Referring domain', 'Comp. A', 'Comp. B', 'You'], [
        ['<code>trade-association.example</code>', Y, Y, N],
        ['<code>how-to-weekly.example</code>', Y, Y, N],
        ['<code>regional-news.example</code>', Y, N, Y],
        ['<code>tools-roundup.example</code>', N, Y, N],
      ])))}),
    sec({ id: 'link-building', cls: 'dk', kick: 'Link-building guidance', h: 'Earn links that make sense to readers',
      body: `<div class="prose"><p>Search engines use links as signals, and they also have rules against manipulating them. Google’s spam policies, for example, list buying or selling links that pass ranking credit and excessive link exchanges as link spam.</p>
      ${ticks(['Create resources people want to cite: original data, useful tools and clear guides.', 'Reclaim lost links by fixing broken URLs or redirecting moved pages.', 'Ask sites that already mention your brand without linking whether they’d add a link.', 'Mark paid or sponsored placements with <code>rel="sponsored"</code> or <code>rel="nofollow"</code>.'])}
      <p>Scoutly helps you research and organize opportunities. It doesn’t buy, sell or place links.</p></div>` }),
  ].join(''),
  related: ['competitor-analysis', 'broken-link-checker', 'website-seo-audit'],
  faqH: 'Backlink checker questions',
  faq: [
    ['How does Scoutly find backlinks?', 'Backlinks are found on other websites, so Scoutly is designed to read them from an external backlink index rather than from a crawl of your own site. The provider and its coverage will be shown alongside the data.'],
    ['Why do backlink tools show different numbers?', 'Each backlink index crawls a different part of the web on a different schedule, and each one counts and filters links differently. Compare trends within one source rather than comparing totals across sources.'],
    ['What is a lost backlink?', 'A link that a data source found before but can’t find anymore. The linking page may have been removed or edited, or the URL it pointed to on your site may have changed. Some lost links can be recovered.'],
    ['Are nofollow links worthless?', 'Not necessarily. Google treats <code>rel="nofollow"</code>, <code>rel="sponsored"</code> and <code>rel="ugc"</code> as hints rather than strict rules, and these links can still send visitors and help people discover your site.'],
    ['Does Scoutly build links for me?', 'No. Scoutly is a research and organization tool. It helps you find relevant opportunities and keep track of outreach, but it doesn’t buy, sell or place links.'],
  ],
  cta: { h: 'Find backlink opportunities your competitors already know about.', btn: 'Explore Backlink Opportunities' },
});

PAGES.push({
  slug: 'broken-link-checker', crumb: 'Broken Link Checker',
  title: 'Broken Link Checker: Find 404s & Redirect Chains | Scoutly',
  description: 'Find broken internal and external links, 404 errors, redirect chains and loops. See the source page, destination URL, status code and a suggested fix.',
  eyebrow: 'Broken link checker',
  h1: 'Broken link checker for 404 errors, redirect chains and dead ends',
  intro: ['Broken links send visitors and crawlers to pages that no longer exist. Scoutly’s broken link checker is designed to crawl your site, test the links it finds, and show exactly where each problem is: the page containing the link, the URL it points to, the response it got, and what to do about it.'],
  formLabel: 'Find Broken Links',
  status: ['In development', 'The link checker is part of the website SEO audit, which isn’t open to the public yet. Link results will only appear after a real crawl of your site.'],
  body: [
    sec({ id: 'what-it-detects', kick: 'What it detects', h: 'The link problems worth fixing',
      body: cards([
        { icon: 'broken', h: 'Internal 404s and client errors', p: 'Links between your own pages that return 404 Not Found, 410 Gone or other 4xx responses.' },
        { icon: 'shield', h: 'Server errors', p: 'Links to pages that return 5xx errors, which can point to unstable pages or server problems rather than missing content.' },
        { icon: 'globe', h: 'Broken external links', p: 'Links to other websites that fail, including error responses, domains that no longer resolve, and requests that time out.' },
        { icon: 'link', h: 'Redirect chains', p: 'Links that pass through several redirects before reaching the final page. Each hop adds delay for visitors and work for crawlers.' },
        { icon: 'clock', h: 'Redirect loops', p: 'Redirects that send a request back where it started, so the page never loads for anyone.' },
        { icon: 'tree', h: 'Weakly linked pages', p: 'Important pages with few internal links pointing to them, and orphaned pages that only appear in your sitemap.' },
      ]) }),
    sec({ id: 'results', cls: 'alt', kick: 'What each result shows', h: 'Every broken link comes with its source and a next step',
      body: split(`<p>Knowing that a 404 exists is only half the job. You also need to know where the link is so you can fix it. When the data is available, each result is designed to show:</p>
        ${ticks(['<b>Source page:</b> the page that contains the link.', '<b>Destination URL:</b> where the link points.', '<b>Detected status:</b> the HTTP status code, redirect path or connection error.', '<b>Anchor text:</b> the clickable text, so the link is easy to find on the page.', '<b>Recommended next step:</b> update, redirect, restore or remove.'])}
        <p>Some fields depend on what the server returns. A link that times out, for example, has no status code to report.</p>`,
      mock('Broken links', 'Example', table(['Source page', 'Destination', 'Status', 'Next step'], [
        ['<code>/blog/seo-basics</code>', '<code>/guides/old-checklist</code>', '<span class="chip cr">404</span>', 'Update the link to the new guide'],
        ['<code>/</code>', '<code>/pricing</code>', '<span class="chip wr">3 redirects</span>', 'Link straight to the final URL'],
        ['<code>/resources</code>', '<code>partner.example/tools</code>', '<span class="chip cr">Timed out</span>', 'Re-test, then remove or replace'],
      ]))) }),
    sec({ id: 'how-to-fix', kick: 'How to fix broken links', h: 'Four ways to repair a dead end',
      body: cards([
        { icon: 'doc', h: 'Update the link', p: 'If the content moved, point the link straight at the new URL. It’s the cleanest fix for internal links.' },
        { icon: 'link', h: 'Add a redirect', p: 'If other sites or bookmarks use the old URL, a 301 redirect to the closest equivalent page keeps them working.' },
        { icon: 'check', h: 'Restore the page', p: 'If the page was removed by mistake, or still gets visits and links, bringing it back may be the best option.' },
        { icon: 'broken', h: 'Remove the link', p: 'If there’s no good replacement, removing the link is better than sending people to an error page.' },
      ], 'c4') + `<div class="note blue"><p><b>Redirects aren’t errors.</b> A single 301 redirect is a normal way to move a page. Chains and loops are the problem: every extra hop slows visitors down, and crawlers may stop following a long chain before they reach the end.</p></div>` }),
    sec({ id: 'internal-external', cls: 'dk', kick: 'Internal, external and inbound', h: 'Not all broken links are fixed the same way',
      body: `<div class="prose"><p><b style="color:#fff">Internal broken links</b> are fully in your control and usually the most urgent, because they break your own navigation and waste crawl activity on pages that don’t exist.</p>
      <p><b style="color:#fff">External broken links</b> point to other websites. You can’t fix the destination, but you can update the link to a working source or remove it.</p>
      <p><b style="color:#fff">Inbound broken links</b> are links from other sites to URLs on your site that no longer work. Finding them needs backlink data. See the <a href="../backlink-checker/">backlink checker</a>.</p></div>` }),
  ].join(''),
  related: ['website-seo-audit', 'backlink-checker', 'competitor-analysis'],
  faqH: 'Broken link checker questions',
  faq: [
    ['Why does a link work in my browser but show as broken in a checker?', 'Some servers respond differently to automated requests. Firewalls, bot protection, rate limits, login walls and location restrictions can all block a checker while the page still loads for you. Re-test those links manually before changing them.'],
    ['Is a 404 page bad for SEO?', 'A 404 is the correct response for a page that genuinely doesn’t exist, and Google has said 404s on invalid URLs don’t harm a site’s indexing or ranking. The problem is linking to them: visitors hit a dead end, and important content may be lost.'],
    ['Should I fix broken links to other websites?', 'Yes, when you can. They don’t break your own pages, but they send visitors to dead ends and make content look out of date. Replace them with a working source or remove them.'],
    ['What’s the difference between a 301 and a 302 redirect?', 'A 301 means the page moved permanently. A 302 means the move is temporary. Search engines treat a 301 as a strong signal that the new URL should be shown in results, so use it for permanent moves.'],
    ['How often should I check for broken links?', 'After content changes, redesigns and migrations, and on a regular schedule. Sites that publish or remove pages often benefit from scheduled crawls.'],
  ],
  cta: { h: 'Find broken links before visitors hit a dead end.', btn: 'Find Broken Links' },
});

PAGES.push({
  slug: 'keyword-research', crumb: 'Keyword Research',
  title: 'Keyword Research Tool: Ideas, Intent & Topics | Scoutly',
  description: 'Discover keyword ideas, understand search intent, evaluate opportunities and organize topics for your content plan, with every metric labeled by its data source.',
  eyebrow: 'Keyword research',
  h1: 'Keyword research for ideas, search intent and content planning',
  intro: ['Good keyword research starts with what your audience actually searches for, and why. Scoutly’s keyword research workflow is designed to help you collect keyword ideas, group them by search intent and topic, and decide which ones deserve a page, with every metric labeled by where it came from.'],
  formLabel: 'Explore Keyword Research',
  status: ['Planned', 'Search volume and difficulty need a third-party keyword data provider, which isn’t connected in this build. Scoutly will show the source of every metric and an estimated cost before any paid lookup.'],
  body: [
    sec({ id: 'search-intent', kick: 'Search intent', h: 'Start with what the searcher wants',
      lead: 'The same topic can be searched with very different goals. Matching the type of page to the intent matters more than matching exact words.',
      body: cards([
        { chip: ['in', 'Informational'], h: '“how to fix a redirect chain”', p: 'The searcher wants to learn. Guides, explanations and step-by-step tutorials fit best.' },
        { chip: ['in', 'Navigational'], h: '“scoutly login”', p: 'The searcher wants a specific site or page. Make sure that page is easy to find and clearly titled.' },
        { chip: ['wr', 'Commercial'], h: '“best broken link checker”', p: 'The searcher is comparing options. Comparisons, feature pages and honest reviews fit best.' },
        { chip: ['ok', 'Transactional'], h: '“seo audit tool pricing”', p: 'The searcher is ready to act. Pricing, sign-up and product pages should answer them directly.' },
      ], 'c4') }),
    sec({ id: 'evaluate', cls: 'alt', kick: 'Evaluating opportunities', h: 'Questions to ask before you target a keyword',
      body: split(`${ticks(['<b>Is it relevant?</b> Would someone searching this be glad to land on your site?', '<b>Can you match the intent?</b> Check what kind of pages appear in results today: guides, product pages, videos or tools.', '<b>Do you already have a page for it?</b> A second page on the same topic can end up competing with the first.', '<b>Is there demand?</b> Volume estimates help you compare, but treat them as ranges, not exact counts.', '<b>Can you add something?</b> First-hand experience, better examples or a clearer structure.'])}`,
      mock('Keyword list', 'Example', table(['Keyword', 'Intent', 'Topic', 'Volume'], [
        ['seo audit checklist', 'Informational', 'Technical SEO', '<span class="no">Needs a data source</span>'],
        ['website seo checker', 'Commercial', 'Site audits', '<span class="no">Needs a data source</span>'],
        ['fix redirect chain', 'Informational', 'Redirects', '<span class="no">Needs a data source</span>'],
      ]), 'Illustrative example. Volume stays empty until a keyword data provider is connected, rather than showing a guess.')) }),
    sec({ id: 'topics', kick: 'Topic clusters', h: 'Organize keywords into topics, not endless lists',
      body: split(`<p>A long keyword list is hard to act on. Grouping related keywords into topics helps you plan one strong page for each intent instead of many thin pages competing with each other.</p>
        <p>A topic usually has one main page covering the subject broadly, supported by more specific pages that answer narrower questions and link back to it. That structure helps visitors find what they need, and helps search engines understand how your content relates. It also connects naturally to <a href="../aeo-geo/">answer engine optimization</a>, where clear answers to specific questions matter.</p>`,
      mock('Topic: broken links', 'Example', `<div class="row"><span><b>Main page</b> · broken link checker</span><span class="chip ok">Commercial</span></div>
        <div class="row"><span>how to find broken links on a website</span><span class="chip in">Informational</span></div>
        <div class="row"><span>fix 404 errors from internal links</span><span class="chip in">Informational</span></div>
        <div class="row"><span>redirect chain vs redirect loop</span><span class="chip in">Informational</span></div>`)) }),
    sec({ id: 'data-sources', cls: 'dk', kick: 'Where the numbers come from', h: 'Keyword metrics are estimates. Scoutly labels them.',
      body: `<div class="prose"><p>Search volume, keyword difficulty and cost per click are modeled estimates from third-party providers, and different providers often disagree. Scoutly is designed to show where each number came from, so you know what you’re looking at.</p>
      ${ticks(['<b style="color:#fff">Your own queries:</b> Google Search Console reports the queries that actually led to impressions and clicks on your site. It’s the most reliable source for keywords you already appear for. Connecting your account is a planned integration.', '<b style="color:#fff">Keyword ideas and volumes:</b> from an external provider, shown with the source and date.', '<b style="color:#fff">Cost before you spend:</b> for paid lookups, Scoutly is designed to show an estimated cost first, so you decide.'])}</div>` }),
  ].join(''),
  related: ['competitor-analysis', 'aeo-geo', 'website-seo-audit'],
  faqH: 'Keyword research questions',
  faq: [
    ['Where does keyword search volume come from?', 'Search engines don’t publish complete search volumes. Keyword tools estimate them from sources such as advertising data and clickstream panels, so numbers differ between providers. Use them to compare keywords rather than as exact counts.'],
    ['What is keyword difficulty?', 'A provider’s estimate of how hard it may be to rank for a keyword, often based on the links pointing to the pages that currently rank. Every provider calculates it differently, so use it as a rough, relative guide.'],
    ['How many keywords should one page target?', 'Focus each page on one main topic and intent. A good page often appears for many related searches naturally. Creating separate pages for near-identical keywords usually spreads your effort thin.'],
    ['Can I use my Search Console data?', 'Connecting Google Search Console is a planned integration. Once connected, Scoutly will be able to read the queries, clicks and impressions for your verified sites, which is the most accurate picture of how people already find you.'],
  ],
  cta: { h: 'Find the topics your audience is already searching for.', btn: 'Explore Keyword Research' },
});

PAGES.push({
  slug: 'competitor-analysis', crumb: 'Competitor Analysis',
  title: 'SEO Competitor Analysis: Keyword & Content Gaps | Scoutly',
  description: 'Compare your site with search competitors to find keyword gaps, missing topics, strong pages and backlink prospects, then plan around real opportunities.',
  eyebrow: 'SEO competitor analysis',
  h1: 'SEO competitor analysis for keyword, content and backlink gaps',
  intro: ['The sites you compete with in search results aren’t always your business competitors. Scoutly’s competitor analysis is designed to help you find out who actually competes for your topics, compare what they cover, and spot gaps where your site could give a more useful answer.'],
  formLabel: 'Analyze Competitors',
  status: ['In development', 'Content and site-structure comparisons will use Scoutly’s own crawler. Keyword and backlink comparisons need external data providers, which aren’t connected in this build.'],
  body: [
    sec({ id: 'search-competitors', kick: 'Who you’re up against', h: 'Your search competitors may surprise you',
      body: split(`<p>A local accounting firm might compete in search with national publishers, software companies and government guidance pages, not just with other firms down the street. Start by looking at who appears for the topics that matter to you, then choose a small set of competitors to compare.</p>
        ${ticks(['<b>Business competitors</b> sell similar products or services.', '<b>Content competitors</b> are publishers, blogs and communities that rank for your topics.', '<b>Answer competitors</b> are the sources cited in featured snippets and AI-generated answers for your questions.'])}`,
      mock('Competitor set', 'Example', `<div class="row"><span><code>rival-tools.example</code></span><span class="chip ok">Business</span></div>
        <div class="row"><span><code>marketing-weekly.example</code></span><span class="chip in">Content</span></div>
        <div class="row"><span><code>web-standards.example</code></span><span class="chip wr">Answer source</span></div>`)) }),
    sec({ id: 'what-to-compare', cls: 'alt', kick: 'What to compare', h: 'Six ways to learn from the search landscape',
      body: cards([
        { icon: 'search', h: 'Keywords they target', p: 'Which keywords competitors appear for that you don’t, based on available keyword data from a connected provider.' },
        { icon: 'list', h: 'Topics they cover', p: 'Subjects and questions their content covers that are missing from yours, and topics they’ve missed too.' },
        { icon: 'doc', h: 'Pages with search visibility', p: 'The pages that seem to attract the most relevant visibility, and what makes them useful: format, depth and structure.' },
        { icon: 'link', h: 'Referring domains', p: 'Sites that link to competitors, which may be relevant prospects for you. See the <a class="tl" href="../backlink-checker/">backlink checker</a>.' },
        { icon: 'tree', h: 'Site structure', p: 'How competitors organize sections and internal links, and how that compares with your own navigation.' },
        { icon: 'check', h: 'Answer quality', p: 'Where their answers are thin, outdated or hard to follow, and where your site could offer a more useful answer.' },
      ]) }),
    sec({ id: 'content-gap', kick: 'Content gap example', h: 'Spot topics competitors cover that you don’t',
      body: split(`<p>A content gap is a topic that competitors cover and you don’t. Seeing gaps side by side makes it easier to decide what to write next.</p>
        <p>A gap isn’t automatically worth filling. Ask whether the topic fits your audience, and whether you can add something new, such as first-hand experience, better examples or a clearer structure, instead of producing another version of the same page.</p>`,
      mock('Topic coverage', 'Example', table(['Topic', 'Comp. A', 'Comp. B', 'You'], [
        ['Robots.txt basics', Y, Y, Y],
        ['Fixing redirect chains', Y, Y, N],
        ['Core Web Vitals for WordPress', Y, N, N],
        ['Structured data for local businesses', N, N, N],
      ]))) }),
    sec({ id: 'plan', cls: 'dk', kick: 'From research to plan', h: 'Build around opportunities, not imitation',
      body: `<div class="prose"><p>Build your strategy around relevant opportunities rather than copying what everyone else is doing. A practical way to work through competitor research:</p>
      ${ticks(['Shortlist three to five competitors that appear for your most important topics.', 'Compare topic coverage, strong pages and referring domains.', 'Prioritize gaps by relevance to your audience and the effort to do them well.', 'Revisit the comparison periodically, because search results change.'])}</div>` }),
  ].join(''),
  related: ['keyword-research', 'backlink-checker', 'ai-search-visibility'],
  faqH: 'Competitor analysis questions',
  faq: [
    ['How many competitors should I analyze?', 'Three to five is usually enough. More than that tends to produce long lists that are hard to act on. Choose sites that appear for the topics you care about most.'],
    ['Can Scoutly see my competitors’ traffic?', 'No tool has direct access to another site’s analytics. Third-party traffic figures are modeled estimates and can be far off. Where Scoutly shows estimates from a provider, it will label them as estimates, not measured data.'],
    ['Is it okay to crawl a competitor’s website?', 'Competitor comparisons only fetch publicly available pages. Scoutly’s crawler is being built to respect robots.txt and to crawl at a polite rate.'],
    ['What is a keyword gap?', 'A keyword gap is a set of keywords that one or more competitors appear for in search results, but your site doesn’t. It’s a starting point for research, not a list of keywords you must target.'],
  ],
  cta: { h: 'Discover the opportunities your competitors are capturing.', btn: 'Find Competitor Opportunities' },
});

PAGES.push({
  slug: 'ai-search-visibility', crumb: 'AI Search Visibility',
  title: 'AI Search Visibility: Track Mentions & Citations | Scoutly',
  description: 'Understand how your brand appears in AI-generated answers: mentions, cited pages and changes over time, measured with transparent methods and clear limits.',
  eyebrow: 'AI search visibility',
  h1: 'AI search visibility: see how your brand shows up in AI answers',
  intro: ['More people now ask AI assistants and AI-powered search features to explain topics, compare products and recommend options. Scoutly’s AI search visibility tracking is designed to show whether your brand is mentioned in those answers, which of your pages are cited, and how that changes over time, on platforms where reliable measurement is possible.'],
  formLabel: 'Explore AI Search Visibility',
  status: ['Planned', 'AI visibility tracking isn’t available in this build. Supported platforms and measurement methods will be listed here before launch.'],
  body: [
    sec({ id: 'signals', kick: 'What it measures', h: 'Four signals of AI search visibility',
      body: cards([
        { icon: 'eye', h: 'Brand mentions', p: 'Whether your brand or product is named in answers to the questions that matter to you.' },
        { icon: 'link', h: 'Citations', p: 'Whether your pages are linked or listed as sources, and which URLs are cited most often.' },
        { icon: 'check', h: 'Accuracy', p: 'Whether the description of your brand is correct and up to date, so you can fix the source content when it isn’t.' },
        { icon: 'compare', h: 'Competitive share', p: 'How often you appear compared with competitors for the same set of questions.' },
      ], 'c4') }),
    sec({ id: 'measurement', cls: 'alt', kick: 'How measurement works', h: 'AI answers change. Measure trends, not single answers.',
      body: split(`<p>Unlike a traditional ranking, an AI-generated answer can be different every time. It depends on the exact wording of the question, the user’s location and conversation, personalization and the model version.</p>
        <p>That’s why AI visibility tracking works from samples: a defined set of prompts, run repeatedly, with results recorded over time.</p>
        ${ticks(['A prompt set you define around your topics, products and competitors.', 'Repeated runs to smooth out differences between individual answers.', 'Recorded answers and sources, so every data point can be checked.', 'Clear labels for which platforms were measured, and when.'])}`,
      mock('Prompt tracking', 'Example', table(['Prompt', 'Mentioned', 'Cited page'], [
        ['what is a redirect chain', Y, '<code>/guides/redirects</code>'],
        ['best tools to find broken links', N, N],
        ['how to check if a page is indexed', Y, N],
      ]))) }),
    sec({ id: 'limits', kick: 'Limits we’re upfront about', h: 'What AI visibility tracking can’t do',
      body: `<div class="prose">${ticks(['It can’t see the answers every individual user receives, only the samples that were measured.', 'AI platforms don’t publish complete data about mentions or citations, so all tracking is an estimate.', 'Results reflect the prompts you track. Questions outside your prompt set aren’t covered.', 'It can’t guarantee, buy or force citations. Nobody can.'])}</div>` }),
    sec({ id: 'improve', cls: 'dk', kick: 'Improving visibility', h: 'Visibility starts with clear, crawlable, trustworthy content',
      body: `<div class="prose"><p>No switch makes AI systems cite you. Google says the same SEO best practices apply to its AI features in Search, with no extra technical requirements: a page needs to be indexed and eligible to appear with a snippet. Clear, well-structured content from a source people trust is a sound starting point everywhere.</p>
      <p>Learn what that looks like in practice in the <a href="../aeo-geo/">AEO and GEO readiness guide</a>, and make sure the basics are covered with a <a href="../website-seo-audit/">website SEO audit</a>.</p></div>` }),
  ].join(''),
  related: ['aeo-geo', 'competitor-analysis', 'website-seo-audit'],
  faqH: 'AI search visibility questions',
  faq: [
    ['Can Scoutly guarantee that AI tools will cite my website?', 'No. No tool can guarantee placement in AI-generated answers. Scoutly is designed to measure visibility where it can and to help you improve the content and technical foundations that make your pages easier to understand.'],
    ['Which AI platforms will Scoutly track?', 'Supported platforms will be announced once measurement methods are confirmed. Scoutly will only list platforms it can measure reliably and in line with each platform’s terms.'],
    ['How is AI visibility different from rank tracking?', 'Rank tracking records the position of a URL in a list of results. AI visibility looks at whether your brand is mentioned and your pages are cited in a generated answer, which has no fixed positions and changes between runs.'],
    ['Do I need to do anything special for Google’s AI features?', 'According to Google’s guidance, no. The same SEO best practices apply: pages need to be crawlable, indexed and eligible to appear with a snippet. There’s no special markup or file required.'],
  ],
  cta: { h: 'Don’t just optimize for rankings. Optimize for discoverability.', btn: 'Explore AI Search Visibility' },
});

PAGES.push({
  slug: 'aeo-geo', crumb: 'AEO & GEO',
  title: 'AEO & GEO: Answer & Generative Engine Optimization | Scoutly',
  description: 'Learn what answer engine optimization (AEO) and generative engine optimization (GEO) involve, and check if your content is clear, crawlable and AI-search ready.',
  eyebrow: 'AEO and GEO',
  h1: 'AEO and GEO: get your content ready for answer engines and AI search',
  intro: ['Answer engine optimization (AEO) and generative engine optimization (GEO) are about making useful content easy for search systems to find, understand and reference when they answer questions. They build on SEO rather than replacing it. Scoutly’s readiness checks are designed to show where your pages are already clear, and where they could be easier to understand.'],
  formLabel: 'Explore AI Search Readiness',
  status: ['In development', 'AEO and GEO readiness checks are planned as part of the website SEO audit. Nothing on this page implies guaranteed placement in AI-generated answers.'],
  body: [
    sec({ id: 'definitions', kick: 'Definitions', h: 'What AEO and GEO mean',
      body: cards([
        { icon: 'search', h: 'Answer engine optimization (AEO)', p: 'Structuring useful content so that questions are answered clearly, pages are easy to navigate, and relevant facts can be understood by search systems, including featured snippets, voice assistants and AI answers.' },
        { icon: 'spark', h: 'Generative engine optimization (GEO)', p: 'Improving content clarity, topical depth, source quality and entity information, to support discoverability in search experiences that generate answers instead of only listing links.' },
        { icon: 'tree', h: 'How they relate to SEO', p: 'Answer engines and generative search still depend on finding and understanding pages. Crawlability, indexing, clear structure and trustworthy content remain the foundation of all three.' },
      ]) }),
    sec({ id: 'checklist', cls: 'alt', kick: 'Readiness checklist', h: 'What makes content easier to understand and cite',
      body: cards([
        { icon: 'check', h: 'Questions answered directly', p: 'Lead with a short, direct answer, then add detail. Readers and systems can both find the answer without guessing.' },
        { icon: 'list', h: 'Clear structure', p: 'Descriptive headings, short sections, and lists or tables where they help. Each section should make sense on its own.' },
        { icon: 'eye', h: 'Crawlable and indexable pages', p: 'Many AI search features rely on search indexes. Check that important pages aren’t blocked and that key content is in the HTML.' },
        { icon: 'users', h: 'Clear entity information', p: 'Use a consistent brand name, a helpful About page, and organization details, so it’s clear who is behind the content.' },
        { icon: 'doc', h: 'Evidence and sources', p: 'Cite data and sources, show who wrote or reviewed the content, and keep dates accurate when information changes.' },
        { icon: 'code', h: 'Accurate structured data', p: 'Schema.org markup that matches the visible content helps machines interpret a page. It supports understanding but doesn’t guarantee placement.' },
      ]) }),
    sec({ id: 'ai-crawlers', kick: 'AI crawlers and robots.txt', h: 'Decide which AI crawlers can access your content',
      body: split(`<p>Several AI companies publish the user agents their crawlers use and say they respect robots.txt. Some separate crawling for model training from crawling for search features, so you can allow one and not the other.</p>
        <p>Google, for example, provides the <code>Google-Extended</code> token to control whether content is used for its Gemini models, and says it doesn’t affect inclusion or ranking in Google Search.</p>
        <p>Whether to allow AI crawlers is a business decision with trade-offs. Check each provider’s current documentation before changing your rules, because crawler names and behavior change.</p>`,
      mock('robots.txt', 'Example', `<pre style="margin:0;padding:18px;overflow-x:auto;font:400 13px/1.7 'Geist Mono',ui-monospace,monospace;color:var(--ink)"># Allow search engine crawlers
User-agent: Googlebot
Allow: /

# Example: opt out of Gemini model use
# without affecting Google Search
User-agent: Google-Extended
Disallow: /</pre>`, 'Example only. Review each crawler’s documentation before copying rules.')) }),
    sec({ id: 'myths', cls: 'dk', kick: 'Common myths', h: 'What AEO and GEO aren’t',
      body: cards([
        { h: 'A guaranteed citation', p: 'No technique guarantees that an AI system will mention or cite you. Anyone promising that is overselling.' },
        { h: 'A replacement for SEO', p: 'Answer and generative search still rely on discovering and understanding pages. Technical SEO and helpful content still come first.' },
        { h: 'A special file or tag', p: 'No single file or markup type makes a page appear in AI answers. Proposals such as llms.txt exist, but they aren’t an established standard and support varies.' },
      ]) }),
    sec({ id: 'scoutly-checks', kick: 'What Scoutly will check', h: 'Readiness checks built into the audit',
      body: `<div class="prose"><p>AEO and GEO readiness checks are planned as part of the <a href="../website-seo-audit/">website SEO audit</a>, so they sit alongside the technical issues that affect every kind of search.</p>
      ${ticks(['Headings that clearly describe each section, and question-style headings with direct answers.', 'Heading hierarchy and content structure on important pages.', 'Robots rules for search engine crawlers and the AI crawlers you choose to allow.', 'Validity of structured data, and whether it matches the visible content.', 'Organization and authorship information.', 'Dates and freshness signals on time-sensitive content.'])}
      <p>To measure how your brand actually appears in AI answers, see <a href="../ai-search-visibility/">AI search visibility tracking</a>.</p></div>` }),
  ].join(''),
  related: ['ai-search-visibility', 'website-seo-audit', 'keyword-research'],
  faqH: 'AEO and GEO questions',
  faq: [
    ['What’s the difference between AEO and GEO?', 'AEO focuses on answering questions clearly so answer-oriented features can use your content. GEO focuses more broadly on how generative search systems understand and reference content: clarity, depth, sources and entity information. In practice they overlap heavily.'],
    ['Do I need separate content for AI search?', 'Usually not. The same helpful, well-structured content serves readers, traditional search and AI-generated answers. Focus on answering real questions clearly instead of writing for a particular system.'],
    ['Does structured data help with AI answers?', 'Structured data helps machines understand what a page is about and can make it eligible for some rich results in search. It doesn’t guarantee that an AI system will use or cite the page.'],
    ['Should I block AI crawlers?', 'It depends on your goals. Blocking training crawlers keeps your content out of some model training. Blocking crawlers used for AI search features may reduce your visibility in those features. Review each crawler’s documentation and decide case by case.'],
  ],
  cta: { h: 'Get your content ready for the next layer of search.', btn: 'Explore AI Search Readiness' },
});

/* glossary */
const G = [
  ['AEO (answer engine optimization)', 'Structuring content so that questions are answered clearly and directly, making it easier for answer-oriented search features to understand and use. See <a href="../aeo-geo/">AEO and GEO</a>.'],
  ['Alt text', 'A text description of an image in the <code>alt</code> attribute. It’s read by screen readers and helps search engines understand images. Decorative images should use an empty alt attribute.'],
  ['Anchor text', 'The clickable text of a link. Descriptive anchor text helps visitors and search engines understand what the linked page is about.'],
  ['Backlink', 'A link from another website to yours. Backlinks help search engines discover pages and understand relationships between sites. See the <a href="../backlink-checker/">backlink checker</a>.'],
  ['Breadcrumb', 'A navigation trail showing where a page sits in a site’s structure, such as Home / Guides / Redirects. It can be marked up with BreadcrumbList structured data.'],
  ['Broken link', 'A link that leads to an error, such as a 404 page, a server error or a domain that no longer resolves. See the <a href="../broken-link-checker/">broken link checker</a>.'],
  ['Canonical tag', 'A <code>rel="canonical"</code> link that tells search engines which URL is the preferred version of a page when similar or duplicate versions exist. It’s a strong hint, not a directive.'],
  ['Core Web Vitals', 'Google’s user-experience metrics for loading (Largest Contentful Paint), responsiveness (Interaction to Next Paint) and visual stability (Cumulative Layout Shift).'],
  ['Crawl budget', 'Roughly, how many URLs a search engine can and wants to crawl on a site in a given period. It mainly matters for very large sites or sites with many low-value URLs.'],
  ['Crawler', 'A program that fetches web pages by following links, also called a bot or spider. Search engines use crawlers to discover and revisit pages.'],
  ['Cumulative Layout Shift (CLS)', 'A Core Web Vital that measures how much visible content moves unexpectedly while a page loads. A score of 0.1 or less is considered good.'],
  ['Duplicate content', 'Substantially similar content available at more than one URL. It’s usually handled with canonical tags, redirects or consolidation rather than being a penalty in itself.'],
  ['GEO (generative engine optimization)', 'Improving content clarity, depth, sources and entity information to support discoverability in search experiences that generate answers. See <a href="../aeo-geo/">AEO and GEO</a>.'],
  ['Heading tags', 'HTML elements from <code>h1</code> to <code>h6</code> that structure a page’s content. Clear, descriptive headings help readers scan and help search engines understand topics.'],
  ['Hreflang', 'An attribute that tells search engines which language or regional version of a page to show to which users.'],
  ['HTTP status code', 'A three-digit code a server returns for each request: 200 for success, 301 for a permanent redirect, 404 for not found and 5xx for server errors.'],
  ['Indexability', 'Whether a page can be added to a search engine’s index. It depends on factors such as status codes, robots directives and canonical tags.'],
  ['Interaction to Next Paint (INP)', 'A Core Web Vital that measures how quickly a page responds to user interactions. 200 milliseconds or less is considered good. It replaced First Input Delay in 2024.'],
  ['Internal link', 'A link from one page to another page on the same website. Internal links help visitors navigate and help crawlers discover and understand pages.'],
  ['Keyword difficulty', 'A third-party estimate of how hard it might be to rank for a keyword. Each provider calculates it differently. See <a href="../keyword-research/">keyword research</a>.'],
  ['Keyword gap', 'Keywords that competitors appear for in search results but your site doesn’t. See <a href="../competitor-analysis/">competitor analysis</a>.'],
  ['Largest Contentful Paint (LCP)', 'A Core Web Vital that measures how long the largest visible image or text block takes to render. 2.5 seconds or less is considered good.'],
  ['Link gap', 'Websites that link to one or more competitors but not to you. Often used to find relevant outreach prospects.'],
  ['Meta description', 'A short summary of a page in the HTML head. Search engines may show it as the snippet in results, though they often write their own.'],
  ['Meta robots', 'A tag or HTTP header (X-Robots-Tag) with instructions such as <code>noindex</code> or <code>nofollow</code> that control how search engines index a page and treat its links.'],
  ['Nofollow', 'A link attribute (<code>rel="nofollow"</code>) suggesting that search engines shouldn’t pass ranking credit through a link. Google treats it as a hint.'],
  ['Orphan page', 'A page with no internal links pointing to it. Orphan pages are hard for visitors and crawlers to find.'],
  ['Redirect chain', 'A sequence of more than one redirect between the requested URL and the final page. Chains slow pages down and should usually be collapsed into a single redirect.'],
  ['Referring domain', 'A unique website that links to yours. Ten links from one site count as one referring domain.'],
  ['Robots.txt', 'A file at the root of a site that tells crawlers which paths they may or may not crawl. Blocking crawling doesn’t always prevent a URL from being indexed.'],
  ['Search intent', 'The goal behind a search: to learn, find a specific site, compare options or take action.'],
  ['Search volume', 'An estimate of how often a keyword is searched in a period, usually per month. Volumes are modeled by third-party providers and vary between them.'],
  ['Structured data', 'Code, usually JSON-LD using the schema.org vocabulary, that describes a page’s content in a machine-readable way. It can make pages eligible for rich results but doesn’t guarantee them.'],
  ['Title tag', 'The <code>&lt;title&gt;</code> element of a page. It’s shown in browser tabs and often used as the clickable headline in search results.'],
  ['XML sitemap', 'A file listing the URLs you want search engines to discover, optionally with last-modified dates. It helps discovery but doesn’t guarantee indexing.'],
];
const slugify = (s) => s.toLowerCase().replace(/\(.*?\)/g, '').trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const letters = [...new Set(G.map(([t]) => t[0].toUpperCase()))];
PAGES.push({
  slug: 'seo-glossary', crumb: 'SEO Glossary',
  title: 'SEO Glossary: Technical, Link & AI Search Terms | Scoutly',
  description: 'Plain-English definitions of SEO terms, from canonical tags and Core Web Vitals to backlinks, search intent, AEO and GEO.',
  eyebrow: 'Resources',
  h1: 'SEO glossary: plain-English definitions',
  intro: ['SEO comes with a lot of jargon. This glossary explains the technical SEO, link, keyword and AI search terms used across Scoutly, in a sentence or two each.'],
  form: false,
  body: `<section class="sec" aria-label="Glossary terms"><div class="w"><div class="gl">
    <nav aria-label="Jump to letter">${letters.map((l) => `<a href="#letter-${l.toLowerCase()}">${l}</a>`).join('')}</nav>
    <div>${letters.map((l) => `<section id="letter-${l.toLowerCase()}" aria-labelledby="h-${l.toLowerCase()}"><h2 id="h-${l.toLowerCase()}">${l}</h2><dl>${G.filter(([t]) => t[0].toUpperCase() === l).map(([t, d]) => `<dt id="${slugify(t)}">${t}</dt><dd>${d}</dd>`).join('')}</dl></section>`).join('')}</div>
  </div></div></section>`,
  related: ['website-seo-audit', 'backlink-checker', 'aeo-geo'], relatedH: 'Put the terms to work',
  cta: { h: 'Ready to see these terms on your own site?', btn: 'Scan Your Website' },
});

/* about */
const ST = [
  ['Website SEO audit', 'In development', 'Scoutly crawler and audit rules; PageSpeed Insights for performance data'],
  ['Broken link checker', 'In development', 'Scoutly crawler'],
  ['AEO and GEO readiness checks', 'In development', 'Scoutly crawler and audit rules'],
  ['Competitor analysis', 'In development', 'Scoutly crawler for content and structure; external providers for keyword and backlink data'],
  ['Keyword research', 'Planned', 'External keyword data provider; Google Search Console for your own queries'],
  ['Backlink checker', 'Planned', 'External backlink index'],
  ['AI search visibility', 'Planned', 'Supported AI search measurement sources, to be confirmed'],
  ['Accounts, audit history and reports', 'In development', 'Scoutly workspace'],
];
const DS = [
  ['Scoutly crawler', 'Built in', 'In development'],
  ['Google Search Console', 'Connect your own account', 'Planned'],
  ['PageSpeed Insights', 'Free Google API', 'Planned'],
  ['Bing Webmaster Tools', 'Connect your own account', 'Planned'],
  ['Keyword data provider', 'Optional, paid, with a cost estimate first', 'Planned'],
  ['Backlink index provider', 'Optional, paid, with a cost estimate first', 'Planned'],
  ['AI search measurement', 'To be confirmed per platform', 'Researching'],
];
const chipFor = (s) => s === 'Planned' ? 'in' : s === 'Researching' ? 'wr' : 'ok';
PAGES.push({
  slug: 'about', crumb: 'About Scoutly',
  title: 'About Scoutly: Product Status & Data Sources | Scoutly',
  description: 'Scoutly is an SEO and search visibility platform in early access. See what each feature is for, where it stands today, and which data sources it relies on.',
  eyebrow: 'About Scoutly',
  h1: 'About Scoutly',
  intro: ['Scoutly is an SEO and search visibility platform in early development. The goal is simple: bring SEO work that’s usually spread across several tools (audits, link checks, backlink research, keyword research, competitor research and AI search visibility) into one workspace that explains what it finds.'],
  form: false,
  body: [
    sec({ id: 'the-mark', kick: 'The mark', h: 'A lens and a spark',
      body: split(`<p>SEO often feels like detective work: following link trails, checking clues in the page source, and working out why something isn’t showing up. The Scoutly mark is a search lens, the investigation itself, done patiently and explained clearly.</p>
        <p>Inside the lens is a spark: what the investigation turns up, and the AI answers a well-built site gets cited in. Search on the outside, discovery on the inside.</p>`,
      `<img src="../assets/scoutly-mark.svg" alt="The Scoutly mark: a white search lens with a four-point spark inside, on a blue rounded square" width="220" height="220" loading="lazy" style="display:block;margin:0 auto;width:min(220px,60%);height:auto;filter:drop-shadow(0 30px 40px rgba(47,91,255,.35))">`) }),
    sec({ id: 'principles', cls: 'alt', kick: 'How we build', h: 'Principles behind Scoutly',
      body: cards([
        { icon: 'doc', h: 'Evidence over scores', p: 'Every finding should show what was detected and how to verify it, not just a number to trust.' },
        { icon: 'list', h: 'Labeled data sources', p: 'Crawl findings, connected accounts and third-party estimates are kept clearly apart, so you know what you’re looking at.' },
        { icon: 'shield', h: 'No guarantees we can’t keep', p: 'No tool can guarantee rankings or AI citations. Scoutly won’t claim otherwise.' },
        { icon: 'check', h: 'Plain-English explanations', p: 'Findings should make sense to business owners as well as SEO specialists.' },
      ], 'c4') }),
    sec({ id: 'product-status', kick: 'Product status', h: 'Where each feature stands today',
      lead: 'Scoutly is in early access. The feature pages on this site describe how each tool is designed to work. This table shows what’s being built now and what’s planned.',
      body: `<div class="mock-scroll"><table class="st"><thead><tr><th scope="col">Feature</th><th scope="col">Status</th><th scope="col">Relies on</th></tr></thead><tbody>${ST.map(([a, b, c]) => `<tr><td>${a}</td><td><span class="chip ${chipFor(b)}">${b}</span></td><td>${c}</td></tr>`).join('')}</tbody></table></div>` }),
    sec({ id: 'data-sources', cls: 'alt', kick: 'Data sources', h: 'Better insights start with reliable data',
      lead: 'Depending on the integrations configured, Scoutly is designed to combine its own crawl findings with data from accounts you connect and optional third-party providers.',
      body: `<div class="mock-scroll"><table class="st"><thead><tr><th scope="col">Source</th><th scope="col">How it’s used</th><th scope="col">Status</th></tr></thead><tbody>${DS.map(([a, b, c]) => `<tr><td>${a}</td><td>${b}</td><td><span class="chip ${chipFor(c)}">${c}</span></td></tr>`).join('')}</tbody></table></div>
      <div class="note"><p><b>No integrations are live in this early-access build.</b> Integration names are listed to explain the product design. They don’t indicate a partnership or endorsement.</p></div>` }),
  ].join(''),
  related: ['website-seo-audit', 'backlink-checker', 'ai-search-visibility'], relatedH: 'Explore the platform',
});

/* 404 (served by GitHub Pages for any missing path, so links are root-relative) */
const notFound = {
  root: '/', noindex: true, slug: null, crumbs: [{ name: 'Home', href: '/' }, { name: 'Page not found' }],
  title: 'Page not found | Scoutly', description: 'The page you were looking for doesn’t exist or has moved.',
  eyebrow: '404 · Dead end', h1: 'This trail goes nowhere',
  intro: ['The page you were looking for doesn’t exist or has moved. Even Scoutly hits a broken link now and then.', 'Try the <a href="/">homepage</a>, browse the <a href="/#features">features</a>, or look up a term in the <a href="/seo-glossary/">SEO glossary</a>.'],
  form: false, cta: false,
  body: sec({ id: 'popular', kick: 'Popular pages', h: 'Pick up the trail here', body: `<div class="rel">${FEATURES.slice(0, 6).map((f) => `<a href="/${f.slug}/"><b>${f.name}</b><span>${f.short}</span></a>`).join('')}</div>` }),
};

/* ---------- write ---------- */
for (const p of PAGES) {
  const dir = join(ROOT, p.slug); mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'index.html'), page(p));
  if (p.title.length > 65) console.warn(`title long (${p.title.length}): ${p.slug}`);
  if (p.description.length > 165) console.warn(`description long (${p.description.length}): ${p.slug}`);
}
writeFileSync(join(ROOT, '404.html'), page(notFound));

const urls = ['', ...PAGES.map((p) => `${p.slug}/`)];
writeFileSync(join(ROOT, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((u) => `  <url><loc>${SITE}/${u}</loc><lastmod>${TODAY}</lastmod></url>`).join('\n')}
</urlset>
`);
writeFileSync(join(ROOT, 'robots.txt'), `User-agent: *
Allow: /

Sitemap: ${SITE}/sitemap.xml
`);

/* keep the homepage's absolute URLs on the configured origin */
const home = join(ROOT, 'index.html');
const html = readFileSync(home, 'utf8');
const m = html.match(/<link rel="canonical" href="(https?:\/\/[^"/]+)\/">/);
if (m && m[1] !== SITE) { writeFileSync(home, html.split(m[1]).join(SITE)); console.log(`index.html: ${m[1]} -> ${SITE}`); }

console.log(`Built ${PAGES.length} pages, 404.html, sitemap.xml (${urls.length} URLs) and robots.txt for ${SITE}`);
