// HomeoAI OpenRouter / Kojo relay.
//
// Holds the OpenRouter API key (and optionally a Kojo key) as Worker secrets
// (never in git, never in the browser) so the GitHub Pages site can run AI
// verification without visitors entering a key. OpenRouter traffic is limited
// to free-tier models, and model "kojo" forwards to the Kojo OpenAI-compatible
// endpoint at https://api.kojo.sh/v1 — so an abusive
// visitor can burn rate limits but cannot spend money.

const UPSTREAM = 'https://openrouter.ai/api/v1/chat/completions';
const REFERER = 'https://homeoai.github.io';
const MAX_TOKENS = 1400;
const ALLOWED_ORIGINS = new Set([
  'https://homeoai.github.io',
  'http://localhost:8000',
  'http://127.0.0.1:8000',
]);
// Loopback on any port and cloud preview hosts, so the site can be tested
// locally or in a preview build without editing this list. Both are the
// owner's own pages; nothing here reaches a third party.
const ALLOWED_ORIGIN_PATTERNS = [
  /^http:\/\/localhost(:\d{1,5})?$/,
  /^http:\/\/127\.0\.0\.1(:\d{1,5})?$/,
  /^https:\/\/[a-z0-9-]+\.style\.dev$/,
];
function originAllowed(origin) {
  if (!origin) return false;
  if (ALLOWED_ORIGINS.has(origin)) return true;
  return ALLOWED_ORIGIN_PATTERNS.some(re => re.test(origin));
}

const CORS = {
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, HTTP-Referer, X-Title',
  'Access-Control-Max-Age': '86400',
  Vary: 'Origin',
};

function corsHeaders(request) {
  const headers = new Headers(CORS);
  const origin = request.headers.get('Origin');
  if (origin && originAllowed(origin)) headers.set('Access-Control-Allow-Origin', origin);
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
// Readable text of a result page, so the model correlates real content instead
// of search snippets. Scripts, styling, navigation and footers carry no signal.
function readable(html) {
  return decodeEntities(String(html)
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<nav[\s\S]*?<\/nav>/gi, ' ')
    .replace(/<header[\s\S]*?<\/header>/gi, ' ')
    .replace(/<footer[\s\S]*?<\/footer>/gi, ' ')
    .replace(/<[^>]+>/g, ' '))
    .replace(/\s+/g, ' ')
    .trim();
}
const PAGE_UA = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', Accept: 'text/html,text/plain' };
async function readPage(url) {
  const r = await fetch(url, { headers: PAGE_UA, redirect: 'follow', signal: AbortSignal.timeout(5000) });
  if (!r.ok) return '';
  const ct = (r.headers.get('content-type') || '').toLowerCase();
  if (ct && !/text\/html|text\/plain/.test(ct)) return '';
  const html = await r.text();
  if (html.length > 600000) return '';
  return readable(html).slice(0, 1800);
}

async function webSearch(request) {
  let payload;
  try { payload = await request.json(); } catch (e) { return json({ error: { message: 'Invalid JSON body' } }, 400, request); }
  const q = typeof payload.q === 'string' ? payload.q.trim().slice(0, 240) : '';
  if (q.length < 3) return json({ error: { message: 'Query too short' } }, 400, request);
  const n = Math.min(8, Math.max(1, Number(payload.n) || 5));
  const read = Math.min(3, Math.max(0, Number(payload.read) || 0));
  const ua = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', Accept: 'text/html' };
  const dbg = {};
  const tmo = (ms) => AbortSignal.timeout(ms);
  const keywords = q.toLowerCase().split(/\s+/).filter(t => t.replace(/[^a-z0-9]/g, '').length > 3);

  // DuckDuckGo lite (href precedes class, protocol-relative /l/?uddg= redirects).
  const ddgLite = async () => {
    const r = await fetch('https://lite.duckduckgo.com/lite/?q=' + encodeURIComponent(q), { headers: ua, signal: tmo(3500), cf: { cacheTtl: 300, cacheEverything: true } });
    dbg.lite = r.status;
    if (!r.ok) return [];
    const html = await r.text();
    const linkRe = /<a\b([^>]*class='result-link'[^>]*|[^>]*class="result-link"[^>]*)>([\s\S]*?)<\/a>/g;
    const snipRe = /<td[^>]*class=['"]result-snippet['"][^>]*>([\s\S]*?)<\/td>/g;
    const hits = []; let m;
    while ((m = linkRe.exec(html)) && hits.length < n) {
      let href = (m[1].match(/href=['"]([^'"]+)['"]/) || [])[1] || '';
      if (href.includes('duckduckgo.com/y.js')) continue;
      const uddg = href.match(/[?&]uddg=([^&'"]+)/);
      if (uddg) href = decodeURIComponent(uddg[1]);
      if (href.startsWith('//')) href = 'https:' + href;
      if (!/^https?:/i.test(href)) continue;
      hits.push({ url: href, title: stripTags(decodeEntities(m[2])), snippet: '' });
    }
    let si = 0;
    while ((m = snipRe.exec(html)) && si < hits.length) { hits[si].snippet = stripTags(decodeEntities(m[1])).slice(0, 320); si++; }
    return hits;
  };

  // DuckDuckGo HTML endpoint (result__a / result__snippet markup).
  const ddgHtml = async () => {
    const r = await fetch('https://duckduckgo.com/html/?q=' + encodeURIComponent(q), { headers: ua, signal: tmo(3500), cf: { cacheTtl: 300, cacheEverything: true } });
    dbg.html = r.status;
    if (!r.ok) return [];
    const html = await r.text();
    const linkRe = /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g;
    const snipRe = /class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g;
    const hits = []; let m;
    while ((m = linkRe.exec(html)) && hits.length < n) {
      let href = decodeEntities(m[1]);
      if (href.includes('duckduckgo.com/y.js')) continue;
      const uddg = href.match(/uddg=([^&]+)/);
      if (uddg) href = decodeEntities(uddg[1]);
      if (!/^https?:/i.test(href)) continue;
      hits.push({ url: href, title: stripTags(decodeEntities(m[2])), snippet: '' });
    }
    let si = 0;
    while ((m = snipRe.exec(html)) && si < hits.length) { hits[si].snippet = stripTags(decodeEntities(m[1])).slice(0, 320); si++; }
    return hits;
  };

  // Wikipedia API: full query first, then the first 3 significant words.
  const wikiTry = async (terms, label) => {
    if (!terms) return [];
    const r = await fetch('https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=' + encodeURIComponent(terms) + '&format=json&srlimit=' + n, { headers: { 'User-Agent': 'HomeoAI-relay/1.0 (educational; contact rakeshmadhav111@gmail.com)' }, signal: tmo(5000) });
    dbg['wiki_' + label] = r.status;
    if (!r.ok) return [];
    const j = await r.json();
    return ((j.query || {}).search || []).map(x => ({
      url: 'https://en.wikipedia.org/wiki/' + encodeURIComponent(String(x.title).replace(/ /g, '_')),
      title: x.title,
      snippet: stripTags(decodeEntities(x.snippet || '')).slice(0, 320),
    })).filter(x => {
      if (!keywords.length) return true;
      const hay = (x.title + ' ' + x.snippet).toLowerCase();
      return keywords.some(t => hay.includes(t));
    });
  };
  // Wikipedia full-text search is AND-ish: a natural-language clinical query
  // ("Chamomilla irritability teething infant") returns zero hits. So also try
  // the individual keywords, in parallel, and take the first group with hits.
  const wiki = async () => {
    const short = q.split(/\s+/).filter(t => t.replace(/[^a-zA-Z0-9]/g, '').length > 2).slice(0, 3).join(' ');
    const terms = [...new Set([q, short, ...keywords.slice(0, 4)].filter(Boolean))];
    const tries = await Promise.allSettled(terms.map((t, i) => wikiTry(t, i === 0 ? 'full' : 'k' + i)));
    for (const t of tries) if (t.status === 'fulfilled' && t.value.length) return t.value;
    return [];
  };

  // All sources in parallel — the answer waits on the slowest, not the sum.
  const settled = await Promise.allSettled([ddgLite(), ddgHtml(), wiki()]);
  const labels = ['duckduckgo', 'duckduckgo', 'wikipedia'];
  const merged = [];
  settled.forEach((s, i) => {
    if (s.status === 'rejected') { dbg['src' + i] = String(s.reason && s.reason.message || s.reason); return; }
    (s.value || []).forEach(x => {
      if (!x || !x.url || merged.some(y => y.url === x.url)) return;
      merged.push(Object.assign({}, x, { via: labels[i] }));
    });
  });
  if (!merged.length) return json({ error: { message: 'Web search unavailable' }, debug: dbg }, 502, request);

  // Classical/reference pages first, so full-text reading is worth the latency.
  const preferred = /homeoint\.org|materiamedica|homeopath|wikipedia\.org|nih\.gov|ncbi\.nlm\.nih\.gov|pubmed|sciencedirect/i;
  const ordered = merged.map((x, i) => ({ x, i }))
    .sort((a, b) => (preferred.test(a.x.url) ? 0 : 1) - (preferred.test(b.x.url) ? 0 : 1) || a.i - b.i)
    .map(o => o.x)
    .slice(0, n);

  if (read) {
    const targets = ordered.slice(0, read).filter(x => /^https?:/i.test(x.url));
    const pages = await Promise.allSettled(targets.map(x => readPage(x.url)));
    pages.forEach((p, i) => {
      if (p.status === 'fulfilled' && p.value) targets[i].content = p.value;
      else if (p.status === 'rejected') dbg['read' + i] = String(p.reason && p.reason.message || p.reason);
    });
  }

  const sources = [...new Set(ordered.map(x => x.via))];
  return json({ query: q, source: sources.join(' + '), sources, results: ordered, debug: dbg }, 200, request);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') return new Response(null, { headers: corsHeaders(request) });
    if (url.pathname === '/v1/search') {
      if (request.method !== 'GET') return json({ error: { message: 'Method not allowed' } }, 405, request);
      const origin = request.headers.get('Origin');
      if (origin && !originAllowed(origin)) return json({ error: { message: 'Origin not allowed' } }, 403, request);
      return searchDatabase(url, env, request);
    }
    if (url.pathname === '/v1/web') {
      if (request.method !== 'POST') return json({ error: { message: 'Method not allowed' } }, 405, request);
      const origin = request.headers.get('Origin');
      if (origin && !originAllowed(origin)) return json({ error: { message: 'Origin not allowed' } }, 403, request);
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
    if (origin && !originAllowed(origin)) return json({ error: { message: 'Origin not allowed' } }, 403, request);
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
