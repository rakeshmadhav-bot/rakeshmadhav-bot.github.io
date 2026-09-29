// HomeoAI OpenRouter relay.
//
// Holds the OpenRouter API key as a Worker secret (never in git, never in the
// browser) so the GitHub Pages site can run AI verification without visitors
// entering a key. Only free-tier models are allowed through, so an abusive
// visitor can burn rate limits but cannot spend money.

const UPSTREAM = 'https://openrouter.ai/api/v1/chat/completions';
const REFERER = 'https://rakeshmadhav-bot.github.io';
const MAX_TOKENS = 1400;

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, HTTP-Referer, X-Title',
  'Access-Control-Max-Age': '86400',
  Vary: 'Origin',
};

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });
    if (request.method !== 'POST' || url.pathname !== '/v1/chat/completions') {
      return json({ error: { message: 'Not found' } }, 404);
    }
    if (!env.OPENROUTER_API_KEY) {
      return json({ error: { message: 'Relay key not configured' } }, 500);
    }

    let payload;
    try {
      payload = await request.json();
    } catch (e) {
      return json({ error: { message: 'Invalid JSON body' } }, 400);
    }

    const model = typeof payload.model === 'string' ? payload.model : '';
    if (!model.endsWith(':free')) {
      return json({ error: { message: 'This relay only serves free models (:free)' } }, 400);
    }
    if (typeof payload.max_tokens === 'number') {
      payload.max_tokens = Math.min(Math.max(1, payload.max_tokens), MAX_TOKENS);
    }
    delete payload.user;

    let upstream;
    try {
      upstream = await fetch(UPSTREAM, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer ' + env.OPENROUTER_API_KEY,
          'X-Title': 'HomeoAI Clinical Recommender',
          'HTTP-Referer': REFERER,
        },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      return json({ error: { message: 'Upstream request failed: ' + (e && e.message) } }, 502);
    }

    const headers = new Headers(CORS);
    headers.set('Content-Type', upstream.headers.get('Content-Type') || 'application/json');
    return new Response(upstream.body, { status: upstream.status, headers });
  },
};
