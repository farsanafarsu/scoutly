// Static checks for the built site: broken internal links/fragments, missing assets,
// one <h1> per page, unique titles/descriptions, canonical tags and valid JSON-LD.
// Run: node tools/check-site.mjs   (exits 1 on any error)
import { readFileSync, existsSync, statSync, readdirSync } from 'node:fs';
import { join, dirname, resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pages = [];
(function walk(d) {
  for (const f of readdirSync(d)) {
    if (f.startsWith('.') || f === 'node_modules' || f === 'tools') continue;
    const p = join(d, f);
    if (statSync(p).isDirectory()) walk(p); else if (f.endsWith('.html')) pages.push(p);
  }
})(ROOT);

const errors = [], warns = [], seen = { title: {}, desc: {} };
const ids = (html) => new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
const cache = {};
const load = (f) => (cache[f] ??= readFileSync(f, 'utf8'));

for (const file of pages) {
  const rel = relative(ROOT, file).replace(/\\/g, '/');
  const html = load(file);
  const noindex = /<meta name="robots" content="noindex/.test(html);
  // ignore code inside <script> (except JSON-LD) when looking at markup
  const markup = html.replace(/<script(?![^>]*ld\+json)[\s\S]*?<\/script>/g, '');
  const h1 = (markup.match(/<h1[\s>]/g) || []).length;
  if (h1 !== 1) errors.push(`${rel}: expected 1 <h1>, found ${h1}`);
  const title = html.match(/<title>([^<]*)<\/title>/)?.[1];
  const desc = html.match(/<meta name="description" content="([^"]*)"/)?.[1];
  if (!title) errors.push(`${rel}: missing <title>`); else (seen.title[title] ??= []).push(rel);
  if (!desc) errors.push(`${rel}: missing meta description`); else (seen.desc[desc] ??= []).push(rel);
  if (!noindex && !/<link rel="canonical" href="https?:\/\/[^"]+">/.test(html)) errors.push(`${rel}: missing canonical`);
  for (const m of html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)) {
    try { JSON.parse(m[1]); } catch (e) { errors.push(`${rel}: invalid JSON-LD (${e.message})`); }
  }
  const base = rel === '404.html' ? ROOT : dirname(file);
  for (const m of markup.matchAll(/\s(?:href|src)="([^"]*)"/g)) {
    let u = m[1];
    if (!u) { errors.push(`${rel}: empty link`); continue; }
    if (/^(https?:|mailto:|tel:|data:)/.test(u)) continue;
    if (u === '#') { warns.push(`${rel}: bare "#" link`); continue; }
    const [pathPart, frag] = u.split('#');
    const pathOnly = pathPart.split('?')[0];
    let target = file;
    if (pathOnly) {
      target = pathOnly.startsWith('/') ? join(ROOT, pathOnly) : resolve(base, pathOnly);
      if (pathOnly.endsWith('/') || (existsSync(target) && statSync(target).isDirectory())) target = join(target, 'index.html');
      if (!existsSync(target)) { errors.push(`${rel}: broken link ${u}`); continue; }
    }
    if (frag && target.endsWith('.html') && !ids(load(target)).has(frag)) errors.push(`${rel}: missing fragment ${u}`);
  }
}
for (const [k, map] of Object.entries(seen)) for (const [v, files] of Object.entries(map)) if (files.length > 1) errors.push(`duplicate ${k} on ${files.join(', ')}: ${v}`);

warns.forEach((w) => console.warn('warn ', w));
errors.forEach((e) => console.error('ERROR', e));
console.log(`${pages.length} pages checked, ${errors.length} errors, ${warns.length} warnings`);
process.exit(errors.length ? 1 : 0);
