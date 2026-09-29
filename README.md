# HomeoAI Clinical Recommender

Static site (no build step, no server code) hosted on GitHub Pages.

| Path | What it is |
|------|-----------|
| `/` | Redirect to the recommender |
| `/recommender.html` | HomeoAI clinical recommender — patient details → Top 5 remedies (weightage) |
| `/data/` | JS/JSON indexes the recommender loads (~19 MB) |
| `/data/boericke_full.js` | Full Boericke Materia Medica, 689 remedies — merged from `HomeoDBs/boericke-alt` + `boericke-688`, scored into every rubric and shown as an expandable panel on each result |

## AI settings

Online assist is **on by default**, and the site ships pre-configured:
OpenRouter provider, model `nvidia/nemotron-3.5-lightning:free`, and a
pre-filled API key — so anyone with the link can run AI verification without
pasting anything.

**⚠ That key is visible in the page source to everyone.** Its balance is
effectively public: keep a hard credit limit at
[openrouter.ai/settings/credits](https://openrouter.ai/settings/credits) and
rotate the key if it is ever abused. Pasting your own key in **AI Settings**
bills your own account instead (stored in your browser's `localStorage` only).

## Updating

Edit `recommender.html` or files under `data/`, then `git push`.
