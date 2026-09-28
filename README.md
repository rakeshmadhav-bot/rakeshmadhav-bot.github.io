# HomeoAI Clinical Recommender

Static site (no build step, no server code) hosted on GitHub Pages.

| Path | What it is |
|------|-----------|
| `/` | Redirect to the recommender |
| `/recommender.html` | HomeoAI clinical recommender — patient details → Top 5 remedies (weightage) |
| `/data/` | JS/JSON indexes the recommender loads (~18 MB) |

## AI settings

The recommender calls OpenRouter / OpenAI-compatible APIs **from your browser**.
Open **AI Settings** in the app and paste your own key — it is stored in your
browser's `localStorage` only. No keys live in this repository.

## Updating

Edit `recommender.html` or files under `data/`, then `git push`.
