# HomeoAI Clinical Recommender

Static site (no build step, no server code) hosted on GitHub Pages.

| Path | What it is |
|------|-----------|
| `/` | Redirect to the recommender |
| `/recommender.html` | HomeoAI clinical recommender — chat consultation or form → Top 5 remedies (weightage) |
| `/library.html` | Searchable browser view of the local SQLite database export |
| `/database/homeopathy.sqlite` | Structured SQLite source database (used to generate the browser search export) |
| `/data/homeopathy_search.json.gz` | Compressed read-only search index loaded by the static site |
| `/data/` | JS/JSON indexes the recommender loads (~45 MB) |
| `/data/db_rag.js`, `/data/db_mm.js`, `/data/db_candidates.js` | Generated from the SQLite DB by `scripts/build_recommender_db.py` (run automatically after each DB build): 238,160 ext-repertory rubrics for RAG retrieval, materia medica text for all 910 abbreviated remedies that have any, and the 49 SQLite-only remedy records that make **all 1,183 abbreviated remedies rankable** in the recommender (767 already in `embed.js`, 367 covered by `abc_index.js`, 49 added) |
| `/data/boericke_full.js` | Full Boericke Materia Medica, 689 remedies — merged from `HomeoDBs/boericke-alt` + `boericke-688`, scored into every rubric and shown as an expandable panel on each result |

### Database coverage

`database/homeopathy.sqlite` is rebuilt by `scripts/build_homeopathy_db.py` from local
inputs only (nothing is scraped at build time):

| Input | What is imported |
|-------|------------------|
| `data/*.js`, `data/*.json` | Original project datasets (Boericke, ABC index, OOREP, cases, etc.) |
| `../remedia-homeopathy-database.json` | Remedia shop catalog — 7,459 remedies (families, synonyms, potencies, price ranges), 686 products, 106 categories |
| `../ext_hommr_json/` | [drsamonline JSON dataset](https://github.com/drsamonline/homoeopathic-materia-medica-repertory-json) (CC0-1.0) — 72 public-domain books: repertories (Kent, Knerr, Boenninghausen, Lippe, Gentry, …), materia medica (Clarke, Kent, Hering, Allen, …) and treatises (Organon, Kent's lectures, …), driven by its `catalog.json` |
| `data/repository_catalog.json` | 17 verified repository/source pointers in `source_catalog` (GitHub repos, libraries, commercial tools — URLs checked, none ingested) |

Current counts: **7,845 remedies · 356,363 rubrics · 1,105,379 rubric–remedy links ·
5,239 materia medica rows · 6,916 reference documents · 85 sources · 362,923 search
records** (≈30 MB `homeopathy_search.json.gz`). Rubric texts from the external
repertories are stored chapter-prefixed (`Mind — ANGER…`); remedy links resolve by
exact name first, then a conservative fuzzy match (same first character, ratio ≥ 0.85),
and only curated-vocabulary names may create new remedy rows — OCR debris is skipped.

## Chat consultation

`recommender.html` opens on a **chat consultation** tab. The assistant interviews
the patient in plain language — name, age, gender, main complaint, duration,
sensation, modalities (what makes it better or worse), associated symptoms,
cause, mood and previous medicines — then ranks remedies.

- **Fully offline.** The interview is a small rule-based state machine in the page;
  no API key, no network call, nothing leaves the browser.
- **One engine.** `recommend()` is driven by filling the hidden form fields, so
  scores, potency, stock badges, relationship checks and the AI panels are the
  same as the form view.
- **Free text works.** `"My son Aarav is 6 years old, has cough and running nose
  for 3 days, worse at night"` prefills name, age, gender, complaint, duration and
  modality in one go; later steps accept sentences and pull out symptoms and
  modalities. Quick-reply chips cover the common answers.
- **Safety screen on every message.** Red-flag phrases (breathing difficulty,
  chest pain, unconsciousness, convulsions, severe bleeding, stiff neck, stroke
  signs, severe dehydration, allergic swelling, self-harm) raise an urgent card,
  and remedies are withheld behind a **Show remedies anyway (educational)** gate.
- **Refine later.** After a ranking you can add symptoms in the chat and it
  re-ranks, or open **Full reasoning** to see the full case sheet in the form view.

The **Form & case sheet** tab keeps the original manual workflow, case sheet,
saved history and export.

### Kojo in the chat

When online assist is on, anything the interview can't handle routes to **Kojo**
inside the consultation itself: typed questions ("what is aconite?", "why worse
at night?"), meta questions, and answers the symptom extractor can't parse (in
the complaint / sensation / modality steps a Kojo handoff is shown while the
question stays open). The interview state never advances on a Kojo reply, and
with online assist off (or the relay unreachable) the app stays fully offline
and rule-based as before.

### Deep AI analysis (web-verified re-rank)

Immediately after the offline Top 5 appears, the chat automatically runs a
**deep analysis** through the relay while online assist is on:

1. **Internet verification** — the relay (`/v1/web`, keyless) searches live
   sources (DuckDuckGo, Wikipedia fallback) for materia-medica / repertory
   pages matching the case's chief symptom and modality. First hits typically
   include Boericke entries (homeoint.org, materiamedica.info).
2. **Collective analysis** — Kojo reads the whole case as a totality (chief
   complaint, modalities, concomitants, cause, mind, age/sex) and interprets
   picture, miasm and keynote pattern.
3. **Individual repertorisation** — each recorded symptom is mapped to its
   Kent-style rubric with leading remedies per rubric.
4. **Full-corpus verification** — Kojo checks the offline Top 5 against the
   *entire* classical materia medica (every remedy on record, beyond the
   small offline library) and produces a **Final Top 5**, marking remedies
   absent from the offline database with an asterisk.
5. The verified list is stored on the case, so later chat questions and the
   AI-verification step reason from the same Final Top 5.

The offline ranking always remains displayed and stands as fallback if the
AI phase fails; failures never break the interview flow.

### Saved chats (continue / delete)

Every chat consultation is auto-saved to the browser (`homeo_chat_history`,
last 30 chats): the full transcript plus the interview state (step, symptoms,
modalities, flags, AI Top 5). The **🕘 Saved** button in the chat header lists
them — **Continue** restores the conversation exactly where it left off
(including recomputing the offline ranking so Full reasoning still works, and
keeping the AI-verified Top 5 for follow-up questions), **Delete** removes a
chat permanently. Continuing a restored chat updates the same entry rather
than creating duplicates. Storage is local to the browser only.

## AI settings

Online assist is **on by default**. The AI supervisor's operating stance is
*classical in-framework*, and is baked into every prompt (chat replies, AI
verification, and the exportable prompt for ChatGPT/Qwen): reason strictly from
materia medica (Boericke, Kent, Phatak), repertory logic and documented cured
cases; treat homeopathy as a **supportive companion taken alongside regular
medical care, not a replacement**; never append unsolicited evidence/placebo
disclaimers, and keep any direct question about evidence within the case
framework. Genuine emergency red flags are the only warning allowed to appear
unsolicited.

Setup can run on **OpenRouter** (free
models) or **Kojo** (`https://api.kojo.sh/v1`, model `kojo` — standard OpenAI
chat-completions API), chosen under Provider in **AI Settings**.

Calls go through a **built-in relay** — the Cloudflare Worker in
[`worker/`](worker/) holds the OpenRouter API key (and optionally `KOJO_API_KEY`)
as encrypted Worker secrets, so the page ships with **no key at all** and
visitors need none to use it. The relay refuses every OpenRouter model that
isn't `:free`, so it cannot be used to spend money (rate limits only).

Default model: `nvidia/nemotron-3.5-lightning:free` via OpenRouter. Pasting
your own key in **AI Settings** bypasses the relay and bills your account
(stored in your browser's `localStorage` only).

### Kojo provider

Pick **Kojo (kojo.sh)** in Settings. Kojo speaks the standard OpenAI
chat-completions API (`https://api.kojo.sh/v1/chat/completions`, model
`kojo`), but **api.kojo.sh does not send CORS headers**, so the page cannot
call it directly. The **built-in relay** is therefore always used: the Worker
detects `model: "kojo"` and forwards the request to Kojo using its
`KOJO_API_KEY` Worker secret (never committed, never sent to the browser).

One-time setup:

```sh
cd worker
npx wrangler secret put KOJO_API_KEY   # paste sk-kojo-... when prompted
npx wrangler deploy
```

Get the key at `kojo.sh` → API keys (keys are shown once at creation). **Test
Connection** makes a real, tiny chat-completion through the relay and shows the
model's reply, so you can confirm the key and connectivity in one click.

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

### Database API

The Worker exposes `GET /v1/health` and `GET /v1/search?q=cough&type=rubric&limit=20&offset=0`. To connect it to D1, create a database in your Cloudflare account, place the returned ID in `worker/wrangler.jsonc`, then run the migration and import:

```sh
cd worker
npx wrangler d1 create homeopathy-library
npx wrangler d1 migrations apply homeopathy-library --remote
cd ..
python scripts/import_d1_search.py --execute
cd worker
npx wrangler deploy
```

The importer creates SQL batches in ignored `worker/seed/` and uploads the current local search records. The library UI uses the API when available and falls back to the static search index otherwise. API setup requires Cloudflare authentication. Search exposes public reference material only; do not add patient records to this API.

## Updating

Edit `recommender.html` or files under `data/`, rebuild with `python scripts/build_homeopathy_db.py`, upload with `python scripts/import_d1_search.py --execute`, and deploy the Worker and GitHub Pages changes.
