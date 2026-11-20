# HomeoAI Clinical Recommender

Static site (no build step, no server code) hosted on GitHub Pages.

| Path | What it is |
|------|-----------|
| `/` | Redirect to the recommender |
| `/recommender.html` | HomeoAI clinical recommender — chat consultation or form → Top 5 remedies (weightage) |
| `/library.html` | Searchable browser view of the local SQLite database export |
| `/styles/app.css` | Shared design system for both pages — tokens, type, cards, repertory grades, chat, dark mode, print |
| `/database/homeopathy.sqlite` | Structured SQLite source database (used to generate the browser search export) |
| `/data/homeopathy_search.json.gz` | Compressed read-only search index loaded by the static site |
| `/data/` | JS/JSON indexes the recommender loads (~45 MB) |
| `/data/db_rag.js`, `/data/db_mm.js`, `/data/db_candidates.js` | Generated from the SQLite DB by `scripts/build_recommender_db.py` (run automatically after each DB build): 238,160 ext-repertory rubrics for RAG retrieval, materia medica text for all 910 abbreviated remedies that have any, and the 49 SQLite-only remedy records that make **all 1,183 abbreviated remedies rankable** in the recommender (767 already in `embed.js`, 367 covered by `abc_index.js`, 49 added) |
| `/data/boericke_full.js` | Full Boericke Materia Medica, 689 remedies — merged from `HomeoDBs/boericke-alt` + `boericke-688`, scored into every rubric and shown as an expandable panel on each result |

The ~31 MB repertory payload (`abc_index`, `oorep_index`, `oorep_rag`,
`boericke_full`, `db_rag`, `db_mm`) is **not downloaded on load**: the chat shell
and the constitution questionnaire paint immediately, and switching tabs never
starts the fetch. The corpora load on the first action that actually needs them —
**AI Find Top 5** or the first chat message — with the button showing
`Loading repertory data…` while it happens. Both paths wait for the load before
ranking, and a failed fetch is reported and retried on the next click instead of
being remembered as done.

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
- **Dictate instead of typing.** 🎤 starts the browser's own speech recognition
  into the composer: words are appended to whatever is already typed, the box
  stays editable, and a second tap stops the session with the text left in place
  ready to send. Chrome closes a dictation session after a pause, so it reopens
  itself (up to 10 times in three minutes) until you stop it. Blocked permission,
  a missing microphone and an unsupported browser each get their own message
  instead of a silent failure. Chrome, Edge and Chrome for Android support it;
  Firefox does not. The audio goes to the browser's speech service, so dictation
  needs a connection.
- **Medical reports can be attached.** 📎 in the composer takes JPEG, PNG or
  PDF up to 15 MB, three reports per message. Kojo reads images natively
  (OpenAI `image_url` parts), so a photo of a lab slip goes straight to the
  model. PDFs are rendered to page images in the browser with pdf.js from
  jsDelivr (first 4 pages), and large photos are downscaled to 1400 px before
  sending, which keeps the printed values legible for a fraction of the tokens.
  The prompt then asks for every value, unit and reference range to be read
  before the reasoning, and still closes with the Final Top 5. A message can be a
  report on its own, with no typed text. Nothing is uploaded anywhere: the file
  is read on the device and only the message you send leaves it. Saved chats keep
  a `📄 Report image` placeholder instead of the pixels, so history stays small.
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

The **Form & case sheet** tab keeps the original manual workflow — patient
details in, Top 5 out, every visit saved to the case history (export it from the
sidebar's **More** menu) — and its Top 5 is repertorised against the whole
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

Every chat is auto-saved **on the device it was written on** (`homeo_chat_history`,
up to 200 chats) — and, once you sign in, to your account too (see *Online chat
sync* below): the full transcript plus the model message history and the last
**Final Top 5**. Reopening the page resumes the most recent conversation
automatically — on the same browser profile, on Chrome for Android and on a
Windows PC alike — and **New chat** starts a fresh one without deleting the old
record. Writes are flushed when the tab is hidden or closed, and if storage is
full the oldest chats are dropped (then the saved HTML) so the current chat still
saves. The rail lists every one of them under **Pinned / Today / Yesterday /
Previous 7 days / Older** — clicking a row restores the transcript, keeps the
ranking for follow-up grounding, and puts the cursor back in the composer;
**Rename** gives a chat your own title in place (Enter saves, Escape
cancels, an empty name falls back to the first question, and the name survives
later auto-saves); **Delete** removes a chat permanently, and the **⋯** menu on any row offers
**Export chat**. **Export all** in the panel header writes every chat at once.
Either way the download is JSON — id, title, date, the message list and the
readable transcript — named `homeoai-chat-<title>-YYYY-MM-DD.json` for one chat
or `homeoai-chats-YYYY-MM-DD.json` for all, so a conversation can be moved to
another device or kept outside the browser.

Storage is `localStorage` first: it lives in that browser profile for that site's
origin, so the rail is instant and works offline. Clearing site data, private
windows, or opening the page from a different URL or `file://` starts empty.

<a id="online-chat-sync" name="online-chat-sync"></a>

### Online chat sync (sign in, chats follow you)

The rail's footer button **Sign in to sync** turns the saved chats into an online
account, made right in the panel: type an email address and a password and press
**Create account**, then **Sign in** with the same two things on any other device.
**Continue with Google** does the same in one tap once that provider is set up, and
the one-time email link stays as a fallback. Every chat you write is mirrored to a
`homeo_chats` table that only your account can read, so on your phone or another
computer the same chats appear in the rail — with rename, pin, delete, search,
import and export unchanged, because they all work on the local cache and the
cache is refreshed from the account. Without a connection, or when signed out, the
app keeps saving to the browser exactly as before.

The **anon key is public** (it ships in the page); **row-level security** is what
keeps rows private. Each row is stamped with `auth.uid()` on the server, and every
policy checks `auth.uid() = user_id`, so one account can never list or delete
another's chats — even with the key in hand. Report images are stripped of their
pixels before upload to keep payloads small.

**Setup (once):**

1. Create a project at [supabase.com](https://supabase.com/dashboard) and open
   **SQL Editor** → run:

   ```sql
   create table if not exists public.homeo_chats (
     user_id    uuid not null references auth.users(id) on delete cascade,
     id         text not null,
     saved_at   bigint not null default 0,
     title      text,
     pinned     boolean not null default false,
     step       text not null default 'chat',
     msgs       jsonb not null default '[]'::jsonb,
     ai_top5    jsonb not null default '[]'::jsonb,
     log        text not null default '',
     updated_at timestamptz not null default now(),
     primary key (user_id, id)
   );

   alter table public.homeo_chats enable row level security;

   drop policy if exists "own chats read"   on public.homeo_chats;
   drop policy if exists "own chats insert" on public.homeo_chats;
   drop policy if exists "own chats update" on public.homeo_chats;
   drop policy if exists "own chats delete" on public.homeo_chats;

   create policy "own chats read"   on public.homeo_chats for select using (auth.uid() = user_id);
   create policy "own chats insert" on public.homeo_chats for insert with check (auth.uid() = user_id);
   create policy "own chats update" on public.homeo_chats for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
   create policy "own chats delete" on public.homeo_chats for delete using (auth.uid() = user_id);

   create index if not exists homeo_chats_recent_idx
     on public.homeo_chats (user_id, saved_at desc);
   ```

2. **Authentication → URL Configuration**: set *Site URL* to the site
   (`https://<user>.github.io/<repo>/`) and add it, plus
   `http://localhost:3000/recommender.html`, to *Redirect URLs*. That list is
   the only place the page may return to after signing in or confirming an
   email.
3. **Accounts**: nothing to turn on. *Authentication → Sign In / Providers →
   Email* is enabled by default, which is what **Create account** and **Sign in**
   use. Leave **Confirm email** on and each new account opens one link from its
   inbox before signing in; switch it off to let people in the moment they press
   *Create account*. Passwords must be six characters or more. Anyone who forgets
   one can press *Email me a sign-in link instead* and get back in without it.
4. **Project Settings → API**: copy the *Project URL* and the *anon public* key
   into the sync panel — open **Sign in to sync → Use a different project** (or
   the **Set up** panel on a page that has no project yet), paste both, press
   **Connect**, then create an account. The values stay in that browser, so no
   file edit is needed; they can instead be written into `HOMEOSYNC_URL` and
   `HOMEOSYNC_KEY` in `recommender.html` (search for `HOMEOSYNC_URL`). The first
   sign-in uploads the chats already on that device.
5. **Google sign-in (optional)**: only needed if you want the Google button to
   work. Create an OAuth client in the [Google Cloud
   console](https://console.cloud.google.com/apis/credentials) — *Create
   credentials → OAuth client ID → Web application*, with
   `https://<project-ref>.supabase.co/auth/v1/callback` as an *Authorized
   redirect URI*. Then in Supabase open **Authentication → Sign In / Providers →
   Google**, enable it and paste the client ID and client secret. While the
   Google consent screen is still in *Testing*, only the test users you list can
   sign in; publish the app to allow any Google account. Email accounts work
   whether or not this step is done.

Signing out removes the synced chats from that browser (they stay in the
account) — useful on a shared computer.

## Know your Constitution

The sidebar's **Know your Constitution** tab (below *Chat consultation*) is a
self-scoring 12-question questionnaire, written in everyday language: body and
skin, thirst and food, hurt and anger, energy, decisions, sleep, what a patient
wants when ill, work and family, heat and sweating, cravings, weather, and
company. Each of the twelve remedies is offered in exactly four of the twelve
questions, so every remedy is measured on the same number of questions and none
is favoured by the shorter form. The patient clicks one answer per question and
gets the
**top three constitutional remedies** from twelve frequently prescribed ones
(Calc-c, Lycopodium, Sulphur, Silicea, Nux-v, Pulsatilla, Phosphorus, Nat-m,
Sepia, Graphites, Staphysagria, Argentum-n), each with its typical picture, its
points and its share of the questions that offered it.

- **One point per answer**, totalled per remedy and shown against how often that
  remedy was offered, so a remedy that appears in fewer questions is not
  penalised. Ties break on that percentage; the explanation of the scoring is
  collapsed under the result and appears only after submission.
- **None of these — write your answer** is offered under every question, for a
  patient whose picture no printed option fits. The words are carried into the
  result and the PDF verbatim and are left out of the scoring, since a written
  answer neither fits nor contradicts a printed option; the question still
  counts as answered, and the progress line says when a chosen text box is still
  empty.
- **Nothing leaves the browser** — no storage, no request; *Start over* clears
  the answers, the written answers and the result.
- It is a **screening aid**: the result narrows the field for the practitioner,
  and the reminder on the page says the remedy is confirmed from the whole case
  and the materia medica, alongside regular medical care.
- **Save / export as PDF** uses the browser's own print dialog, so no library or
  network call is involved. A print stylesheet drops the app chrome, the
  questions and the buttons, and prints the three ranked remedies, the verdict
  and a numbered list of the answers given, headed with the date and the leading
  remedy. The file never leaves the device.

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

Everything AI — chat answers and the **Run AI Verification** pass — runs on
**Kojo** (`https://api.kojo.sh/v1`, model `kojo`, standard OpenAI
chat-completions API). It is the only provider in **AI Settings**.

Calls go through a **built-in relay** — the Cloudflare Worker in
[`worker/`](worker/) holds `KOJO_API_KEY` as an encrypted Worker secret, so the
page ships with **no key at all** and visitors need none to use it. The relay
still recognises OpenRouter free models for anyone calling it directly, and
refuses every model that isn't `:free`.

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
