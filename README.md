# HomeoAI — Materia Medica DB + Clinical Recommender

Static site (no build step, no server code). Hosted on GitHub Pages.

| Path | What it is |
|------|-----------|
| `/` (`index.html`) | Materia Medica offline search — Boericke, Kent, Phatak, Tautodes |
| `/recommender.html` | HomeoAI clinical recommender — patient details → Top 5 remedies (weightage) |
| `/data/` | JSON + JS indexes loaded by both apps (~20 MB) |

## AI settings

The recommender calls OpenRouter / OpenAI-compatible APIs **from your browser**.
Open **AI Settings** in the app and paste your own key — it is stored in your
browser's `localStorage` only. No keys live in this repository.

## Updating data

Edit or replace files under `data/`. Schema notes are documented inside `data/db.json`.
