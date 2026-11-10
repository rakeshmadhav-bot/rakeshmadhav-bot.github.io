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
    if (url.pathname === '/v1/health') {
      if (request.method !== 'GET') return json({ error: { message: 'Method not allowed' } }, 405, request);
      return json({ ok: !!env.HOMEOPATHY_DB, database: env.HOMEOPATHY_DB ? 'configured' : 'missing', openrouter: env.OPENROUTER_API_KEY ? 'configured' : 'missing', kojo: env.KOJO_API_KEY ? 'configured' : 'missing' }, 200, request);
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
      payload.max_tokens = Math.min(Math.max(1, payload.max_tokens), MAX_TOKENS);
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
