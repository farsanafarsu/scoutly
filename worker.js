/* Scoutly on Cloudflare: pages and assets are served as static files; only /api/* reaches this Worker
   (run_worker_first in wrangler.jsonc). It forwards those requests to the Python server (seo/engine.py)
   hosted at API_ORIGIN, adding the shared API_KEY so the hosted server knows the request came from here. */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request);
    if (!env.API_ORIGIN) return fail('The Scoutly tools aren’t connected on this site yet.', 503);

    const headers = new Headers(request.headers);
    ['host', 'cookie', 'cf-connecting-ip', 'x-forwarded-for'].forEach(h => headers.delete(h));
    headers.set('X-Scoutly-Key', env.API_KEY || '');
    const hasBody = request.method !== 'GET' && request.method !== 'HEAD';
    try {
      const res = await fetch(new URL(url.pathname + url.search, env.API_ORIGIN), {
        method: request.method, headers, body: hasBody ? await request.arrayBuffer() : undefined, redirect: 'manual'
      });
      const out = new Response(res.body, res);
      out.headers.set('Cache-Control', 'no-store');
      return out;
    } catch (e) {
      return fail('The Scoutly server didn’t respond. It may be waking up; try again in a minute.', 502);
    }
  }
};

/* the app pages show {error} messages as they are */
function fail(message, status) {
  return Response.json({ error: message }, { status, headers: { 'Cache-Control': 'no-store' } });
}
