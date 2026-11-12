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

`recommender.html` opens on a **Clinical chat** tab. There is no interview: type
anything — a full case, a question about a remedy, feedback on a remedy that did
not work — and **Kojo** answers after live web research.

- **Web research first.** The relay's keyless `/v1/web` route runs its sources in
  parallel (DuckDuckGo lite, DuckDuckGo HTML, Wikipedia), merges and de-duplicates
  the hits, then fetches the readable text of the top pages so Kojo correlates real
  content rather than snippets. Two searches go out per turn: the message's
  meaning-bearing keywords, and the remedies currently ranked. Sources used are
  shown under the answer; when the web returns nothing, the reply says so and
  reasons from the classical corpus. Boericke / Kent / Phatak and institutional
  pages outrank content farms and forums.
- **Local repertorisation.** Every turn splits the message into symptom clauses
  and scans the bundled `homeopathy.sqlite` export (`data/db_rag.js`,
  `data/db_mm.js`, `data/boericke_full.js`): each clause gets its best local
  rubric(s) with graded remedies (IDF-weighted, so `cheek`/`teething` outrank
  `worse`/`night`; prefix matching so `red` finds `redness`), then Kojo receives
  the collective grade totals and symptom coverage. The prompt makes it weigh
  individual fit against coverage, name the best single remedy with its reasoning,
  and justify any departure from the totals. Grades may only be the ones printed
  in that block — never invented.
- **Visible check.** Each reply ends with `🗄️ Local database: n/5 remedies
  verified`, flagging any Top 5 remedy that exists only in the wider materia
  medica.
- **The conversation is the case.** Every turn is sent back as history, so later
  questions ("why is Bryonia second?") keep the patient picture without repeating
  it.
- **Final Top 5.** When a case is described, Kojo analyses the totality
  (collectively) and each symptom to its Kent-style rubric (individually), then
  closes with a numbered **Final Top 5** drawn from the entire classical materia
  medica — not just the offline library. Pure definition or comparison questions
  are answered without a ranking.
- **Stance.** Homeopathy is framed as a supportive companion alongside regular
  medical care, never a replacement; no placebo/evidence lectures even when
  asked directly.
- **Safety screen on every message.** Red-flag phrases (breathing difficulty,
  chest pain, unconsciousness, convulsions, severe bleeding, stiff neck, stroke
  signs, severe dehydration, allergic swelling, self-harm) raise an urgent card
  immediately; the answer still follows.
- **Degradation.** With online assist off the chat says so, and the **Form & case
  sheet** tab keeps the fully offline repertorisation.

The **Form & case sheet** tab keeps the original manual workflow, case sheet,
saved history and export — and its Top 5 is now repertorised against the whole
bundled database rather than remedy prose alone:

- **Every rubric is scanned.** Each symptom you type is one clause; a bare
  modality (`worse at night`, `from coffee`) folds into the symptom it modifies.
  All 312,589 rubrics (OOREP publicum + the 238,160-rubric ext-repertory export)
  are searched per clause, keeping the two best matches. Words are IDF-weighted
  and prefix-matched (`red` finds `redness`), a multi-word clause must match at
  least two of its words, and a chapter the case never mentions is penalised so
  a sore throat cannot match under *Eye*.
- **Grades are summed, coverage is tracked.** The grades printed on the winning
  rubrics (`Bry:3`) are summed per remedy, with the alternate rubric weighted
  0.6, giving each remedy a point total and a `symptoms matched` count. This
  becomes a component of the score out of 30 (24 for how strongly the case leads
  the database, 6 for coverage) and is shown on every card as
  `DB rubrics n pts on k/N symptoms`, plus a `🗄️ DB repertorisation` line naming
  the rubric chosen for each symptom.
- **Rubric-only remedies can win.** Remedies graded by the rubrics but missing
  from the 816-remedy catalogue and the ABC 763 index are added as candidates
  (badge **DB rubrics**), so the Top 5 follows the repertory mass.

The offline engine still verifies each candidate against Boericke/Kent/Phatak
text for the `✓ MM-verified` / `⚠ weight only` badge, and the relationship check
against already-given remedies still decides the final pick.

### Saved chats (continue / delete)

Every chat is auto-saved to the browser (`homeo_chat_history`, last 30 chats):
the full transcript plus the model message history and the last **Final Top 5**.
The **🕘 Saved** button in the chat header lists them — **Continue** restores the
conversation transcript, keeps the ranking for follow-up grounding, and puts the
cursor back in the composer; **Delete** removes a chat permanently. Continuing a
restored chat updates the same entry rather than creating duplicates. Storage is
local to the browser only.

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
