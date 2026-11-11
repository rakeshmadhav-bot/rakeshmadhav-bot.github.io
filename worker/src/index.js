// HomeoAI OpenRouter / Kojo relay.
//
// Holds the OpenRouter API key (and optionally a Kojo key) as Worker secrets
// (never in git, never in the browser) so the GitHub Pages site can run AI
// verification without visitors entering a key. OpenRouter traffic is limited
// to free-tier models, and model "kojo" forwards to the Kojo OpenAI-compatible
// endpoint at https://api.kojo.sh/v1 — so an abusive
// visitor can burn rate limits but cannot spend money.

const UPSTREAM = 'https://openrouter.ai/api/v1/chat/completions';
const REFERER = 'https://rakeshmadhav-bot.github.io';
const MAX_TOKENS = 1400;
const ALLOWED_ORIGINS = new Set([
  'https://rakeshmadhav-bot.github.io',
  'http://localhost:8000',
  'http://127.0.0.1:8000',
]);

const CORS = {
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, HTTP-Referer, X-Title',
  'Access-Control-Max-Age': '86400',
  Vary: 'Origin',
};

function corsHeaders(request) {
  const headers = new Headers(CORS);
  const origin = request.headers.get('Origin');
  if (origin && ALLOWED_ORIGINS.has(origin)) headers.set('Access-Control-Allow-Origin', origin);
  return headers;
}

function json(obj, status, request) {
  const headers = corsHeaders(request);
  headers.set('Content-Type', 'application/json; charset=utf-8');
  headers.set('Cache-Control', 'public, max-age=30, stale-while-revalidate=120');
  return new Response(JSON.stringify(obj), { status, headers });
}

function safeLimit(value, fallback = 20) {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? Math.min(50, Math.max(1, n)) : fallback;
}

async function searchDatabase(url, env, request) {
  if (!env.HOMEOPATHY_DB) return json({ error: { message: 'Database binding is not configured' } }, 503, request);
  const query = (url.searchParams.get('q') || '').trim().slice(0, 160);
  const type = (url.searchParams.get('type') || 'all').trim();
  const allowedTypes = new Set(['all', 'remedy', 'materia_medica', 'rubric', 'reference_document', 'inventory']);
  if (!allowedTypes.has(type)) return json({ error: { message: 'Invalid type filter' } }, 400, request);
  const limit = safeLimit(url.searchParams.get('limit'), 20);
  const offset = Math.max(0, Math.min(100000, Number.parseInt(url.searchParams.get('offset') || '0', 10) || 0));
  if (query.length < 2) return json({ error: { message: 'Search query must contain at least 2 characters' } }, 400, request);
  const terms = query.split(/\s+/).map(term => term.replace(/[^\p{L}\p{N}_-]/gu, '')).filter(Boolean).slice(0, 8);
  if (!terms.length) return json({ error: { message: 'Search query contains no searchable terms' } }, 400, request);
  const match = terms.map(term => '"' + term + '"*').join(' AND ');
  try {
    const rows = await env.HOMEOPATHY_DB.prepare(
      `SELECT d.item_type AS type, d.item_id AS id, d.title, snippet(search_documents_fts, 1, '', '', ' ? ', 20) AS body,
              d.source, d.url, d.license, bm25(search_documents_fts) AS score
       FROM search_documents_fts f JOIN search_documents d ON d.id=f.rowid
       WHERE search_documents_fts MATCH ? AND (? = 'all' OR d.item_type = ?)
       ORDER BY score LIMIT ? OFFSET ?`
    ).bind(match, type, type, limit + 1, offset).all();
    const results = rows.results || [];
    const hasMore = results.length > limit;
    if (hasMore) results.pop();
    return json({ query, type, limit, offset, count: results.length, hasMore, results }, 200, request);
  } catch (error) {
    return json({ error: { message: 'Database search failed', detail: String(error && error.message || error) } }, 500, request);
  }
}

function notFound(request) {
  return json({ error: { message: 'Not found' } }, 404, request);
}

// Keyless web search for AI grounding: DuckDuckGo lite results with a
// Wikipedia API fallback. No upstream key required — results are public.
function stripTags(html) {
  return String(html).replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
}
function decodeEntities(text) {
  const map = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#x27;': "'", '&#39;': "'", '&apos;': "'", '&nbsp;': ' ' };
  let out = String(text);
  for (const [ent, ch] of Object.entries(map)) out = out.split(ent).join(ch);
  return out.replace(/&#(\d+);/g, (_, d) => { try { return String.fromCharCode(Number(d)); } catch { return ' '; } });
}
async function webSearch(request) {
  let payload;
  try { payload = await request.json(); } catch (e) { return json({ error: { message: 'Invalid JSON body' } }, 400, request); }
  const q = typeof payload.q === 'string' ? payload.q.trim().slice(0, 240) : '';
  if (q.length < 3) return json({ error: { message: 'Query too short' } }, 400, request);
  const n = Math.min(6, Math.max(1, Number(payload.n) || 4));
  const ua = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', Accept: 'text/html' };
  const dbg = {};
  // Attempt 1: DuckDuckGo lite results (href precedes class="", single quotes,
  // links are protocol-relative //duckduckgo.com/l/?uddg=<encoded> redirects).
  try {
    const ddg = await fetch('https://lite.duckduckgo.com/lite/?q=' + encodeURIComponent(q), { headers: ua, cf: { cacheTtl: 300, cacheEverything: true } });
    dbg.lite = ddg.status;
    if (ddg.ok) {
      const html = await ddg.text();
      const linkRe = /<a\b([^>]*class='result-link'[^>]*|[^>]*class="result-link"[^>]*)>([\s\S]*?)<\/a>/g;
      const snipRe = /<td[^>]*class=['"]result-snippet['"][^>]*>([\s\S]*?)<\/td>/g;
      const hits = [];
      let m;
      while ((m = linkRe.exec(html)) && hits.length < n) {
        const attrs = m[1];
        let href = (attrs.match(/href=['"]([^'"]+)['"]/) || [])[1] || '';
        if (href.includes('duckduckgo.com/y.js')) continue; // ads
        const uddg = href.match(/[?&]uddg=([^&'"]+)/);
        if (uddg) href = decodeURIComponent(uddg[1]);
        if (href.startsWith('//')) href = 'https:' + href;
        if (!/^https?:/i.test(href)) continue;
        hits.push({ url: href, title: stripTags(decodeEntities(m[2])), snippet: '' });
      }
      let si = 0;
      while ((m = snipRe.exec(html)) && si < hits.length) { hits[si].snippet = stripTags(decodeEntities(m[1])).slice(0, 320); si++; }
      if (hits.length) return json({ query: q, source: 'duckduckgo', results: hits }, 200, request);
    }
  } catch (e) { dbg.lite = String(e && e.message || e); }
  // Attempt 2: DuckDuckGo HTML results (result__a / result__snippet markup).
  try {
    const ddg2 = await fetch('https://duckduckgo.com/html/?q=' + encodeURIComponent(q), { headers: ua, cf: { cacheTtl: 300, cacheEverything: true } });
    dbg.html = ddg2.status;
    if (ddg2.ok) {
      const html = await ddg2.text();
      const linkRe = /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g;
      const snipRe = /class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g;
      const hits = [];
      let m;
      while ((m = linkRe.exec(html)) && hits.length < n) {
        let href = decodeEntities(m[1]);
        if (href.includes('duckduckgo.com/y.js')) continue; // ads
        const uddg = href.match(/uddg=([^&]+)/);
        if (uddg) href = decodeEntities(uddg[1]);
        if (!/^https?:/i.test(href)) continue;
        hits.push({ url: href, title: stripTags(decodeEntities(m[2])), snippet: '' });
      }
      let si = 0;
      while ((m = snipRe.exec(html)) && si < hits.length) { hits[si].snippet = stripTags(decodeEntities(m[1])).slice(0, 320); si++; }
      if (hits.length) return json({ query: q, source: 'duckduckgo', results: hits }, 200, request);
    }
  } catch (e) { dbg.html = String(e && e.message || e); }
  // Attempt 3: Wikipedia API — first with the full query, then trimmed to the
  // first 3 significant words (Wikipedia full-text search is AND-ish; long
  // natural-language queries can return zero hits).
  const wikiTry = async (terms, label) => {
    if (!terms.length) return null;
    const wq = await fetch('https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=' + encodeURIComponent(terms) + '&format=json&srlimit=' + n, { headers: { 'User-Agent': 'HomeoAI-relay/1.0 (educational; contact rakeshmadhav111@gmail.com)' } });
    dbg['wiki_' + label] = wq.status;
    if (!wq.ok) return null;
    const j = await wq.json();
    const results = ((j.query || {}).search || []).map(r => ({
      url: 'https://en.wikipedia.org/wiki/' + encodeURIComponent(String(r.title).replace(/ /g, '_')),
      title: r.title,
      snippet: stripTags(decodeEntities(r.snippet || '')).slice(0, 320),
    }));
    return results.length ? results : null;
  };
  try {
    const short = q.split(/\s+/).filter(t => t.replace(/[^a-zA-Z0-9]/g, '').length > 2).slice(0, 3).join(' ');
    const wikiHit = (await wikiTry(q, 'full')) || (short && short !== q ? await wikiTry(short, 'trim') : null);
    if (wikiHit) return json({ query: q, source: 'wikipedia', results: wikiHit }, 200, request);
  } catch (e) { dbg.wiki_err = String(e && e.message || e); }
  return json({ error: { message: 'Web search unavailable' }, debug: dbg }, 502, request);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') return new Response(null, { headers: corsHeaders(request) });
    if (url.pathname === '/v1/search') {
      if (request.method !== 'GET') return json({ error: { message: 'Method not allowed' } }, 405, request);
      const origin = request.headers.get('Origin');
      if (origin && !ALLOWED_ORIGINS.has(origin)) return json({ error: { message: 'Origin not allowed' } }, 403, request);
      return searchDatabase(url, env, request);
    }
    if (url.pathname === '/v1/web') {
      if (request.method !== 'POST') return json({ error: { message: 'Method not allowed' } }, 405, request);
      const origin = request.headers.get('Origin');
      if (origin && !ALLOWED_ORIGINS.has(origin)) return json({ error: { message: 'Origin not allowed' } }, 403, request);
      return webSearch(request);
    }
    if (url.pathname === '/v1/health') {
      if (request.method !== 'GET') return json({ error: { message: 'Method not allowed' } }, 405, request);
      return json({ ok: !!env.HOMEOPATHY_DB, database: env.HOMEOPATHY_DB ? 'configured' : 'missing', openrouter: env.OPENROUTER_API_KEY ? 'configured' : 'missing', kojo: env.KOJO_API_KEY ? 'configured' : 'missing', websearch: 'keyless' }, 200, request);
    }
    if (request.method !== 'POST' || url.pathname !== '/v1/chat/completions') {
      return notFound(request);
    }
    const origin = request.headers.get('Origin');
    if (origin && !ALLOWED_ORIGINS.has(origin)) return json({ error: { message: 'Origin not allowed' } }, 403, request);
    if (!env.OPENROUTER_API_KEY && !env.KOJO_API_KEY) {
      return json({ error: { message: 'Relay key not configured' } }, 500, request);
    }

    let payload;
    try {
      payload = await request.json();
    } catch (e) {
      return json({ error: { message: 'Invalid JSON body' } }, 400, request);
    }

    const model = typeof payload.model === 'string' ? payload.model : '';
    // Route by provider: model "kojo" (or provider flag "kojo") forwards to the
    // Kojo API; everything else keeps the OpenRouter free-tier rules.
    const provider = (payload.provider === 'kojo' || model === 'kojo') ? 'kojo' : 'openrouter';
    let upstreamURL, apiKey;
    if (provider === 'kojo') {
      if (!env.KOJO_API_KEY) {
        return json({ error: { message: 'Kojo relay key not configured — run: npx wrangler secret put KOJO_API_KEY' } }, 500, request);
      }
      upstreamURL = 'https://api.kojo.sh/v1/chat/completions';
      apiKey = env.KOJO_API_KEY;
    } else {
      if (!env.OPENROUTER_API_KEY) {
        return json({ error: { message: 'Relay key not configured' } }, 500, request);
      }
      if (!model.endsWith(':free')) {
        return json({ error: { message: 'This relay only serves free models (:free)' } }, 400, request);
      }
      upstreamURL = UPSTREAM;
      apiKey = env.OPENROUTER_API_KEY;
    }
    delete payload.provider;
    if (typeof payload.max_tokens === 'number') {
      // Free OpenRouter models stay sharply capped; model "kojo" runs on the
      // owner's own secret key, so it gets a taller ceiling for the deep
      // analysis protocol (reasoning + structured output).
      const cap = provider === 'kojo' ? 2600 : MAX_TOKENS;
      payload.max_tokens = Math.min(Math.max(1, payload.max_tokens), cap);
    }
    delete payload.user;

    let upstream;
    try {
      upstream = await fetch(upstreamURL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + apiKey,
          'X-Title': 'HomeoAI Clinical Recommender',
          'HTTP-Referer': REFERER,
        },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      return json({ error: { message: 'Upstream request failed: ' + (e && e.message) } }, 502, request);
    }

    const headers = corsHeaders(request);
    headers.set('Content-Type', upstream.headers.get('Content-Type') || 'application/json');
    return new Response(upstream.body, { status: upstream.status, headers });
  },
};
