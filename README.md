# HomeoAI Clinical Recommender

Static site (no build step, no server code) hosted on GitHub Pages.

| Path | What it is |
|------|-----------|
| `/` | Redirect to the recommender |
| `/recommender.html` | HomeoAI clinical recommender — patient details → Top 5 remedies (weightage) |
| `/data/` | JS/JSON indexes the recommender loads (~19 MB) |
| `/data/boericke_full.js` | Full Boericke Materia Medica, 689 remedies — merged from `HomeoDBs/boericke-alt` + `boericke-688`, scored into every rubric and shown as an expandable panel on each result |

## AI settings

Online assist is **on by default**. Calls go through a **built-in relay** — the
Cloudflare Worker in [`worker/`](worker/) holds the OpenRouter API key as an
encrypted Worker secret, so the page ships with **no key at all** and visitors
need none to use it. The relay refuses every model that isn't `:free`, so it
cannot be used to spend money (rate limits only).

Default model: `nvidia/nemotron-3.5-lightning:free` via OpenRouter. Pasting
your own key in **AI Settings** bypasses the relay and bills your account
(stored in your browser's `localStorage` only).

Free-tier latency swings hard — answers can take 1–4 minutes at peak times;
the UI shows an elapsed timer and a Cancel button, and gives up after 5 minutes.

### Relay

```sh
cd worker
npx wrangler deploy
echo -n "sk-or-..." | npx wrangler secret put OPENROUTER_API_KEY   # never committed
```

Then point `AI_RELAY_URL` in `recommender.html` at the deployed
`/v1/chat/completions` URL. The relay's key lives only in Cloudflare.

## Updating

Edit `recommender.html` or files under `data/`, then `git push`.
