"""Emit the recommender's SQLite-derived data files so recommender.html can use
database/homeopathy.sqlite offline (file:// safe, plain <script> loads).

- db_rag.js        window.DB_RAG        ext repertory rubrics as [text, "Abbr:grade,..."] pairs
                                          (same shape as window.OOREP_RAG). Skips data/* sources
                                          already shipped as oorep_rag.js / oorep_index.js.
- db_mm.js         window.DB_MM         {normalizedAbbr: materia-medica text} for every remedy
                                          that has an abbreviation, capped at 6000 chars.
- db_candidates.js window.DB_CANDIDATES SQLite remedies with an abbreviation that are in NEITHER
                                          embed.js nor abc_index.js — shaped like embed records so
                                          recommender.html can concat them into its DB pool and
                                          make all 1,183 abbreviated remedies rankable.

Chapter prefixes on rubric text are the attribution for the public-domain classics.
"""
import io
import json
import pathlib
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = pathlib.Path(__file__).resolve().parents[1]
DB_PATH = BASE / "database" / "homeopathy.sqlite"
EMBED_PATH = BASE / "data" / "embed.js"
ABC_PATH = BASE / "data" / "abc_index.js"
MM_CAP = 6000
TOKEN_CAP = 30  # max Abbr:grade pairs kept per rubric (longest-standing rubrics have 300+)

import sqlite3

conn = sqlite3.connect(str(DB_PATH))


def norm(s):
    return "".join(c for c in str(s or "").lower() if "a" <= c <= "z" or "0" <= c <= "9")  # match JS ragNorm [^a-z0-9]


def js_array(path):
    """Parse the [...] payload of a window.X = [...] data file."""
    txt = path.read_text(encoding="utf-8")
    return json.loads(txt[txt.index("[") : txt.rindex("]") + 1])


# ---- DB_RAG: ext repertory rubrics with OOREP-compatible "Abbr:grade" tokens ----
# Tokens come from rubric_remedies (search_index bodies store a plain comma list
# without grades, which the recommender's bonus parser would discard).
rows = conn.execute(
    """
    SELECT r.id, r.rubric, s.origin_file
    FROM rubrics r
    JOIN sources s ON s.id = r.source_id
    WHERE s.origin_file LIKE 'ext_hommr_json/%'
    """
).fetchall()
books = len({o for _, _, o in rows})
toks_by_rubric = {}
for rid, ab, g in conn.execute(
    """
    SELECT rr.rubric_id, rem.abbreviation, rr.grade
    FROM rubric_remedies rr
    JOIN remedies rem ON rem.id = rr.remedy_id
    JOIN rubrics r ON r.id = rr.rubric_id
    JOIN sources s ON s.id = r.source_id
    WHERE s.origin_file LIKE 'ext_hommr_json/%'
      AND rem.abbreviation IS NOT NULL AND rem.abbreviation != ''
    """
):
    toks_by_rubric.setdefault(rid, []).append((ab, int(g) if g else 1))
rubrics = []
for rid, text, _origin in rows:
    text = (text or "").strip()
    tl = toks_by_rubric.get(rid)
    if len(text) >= 4 and tl:
        tl.sort(key=lambda t: -t[1])
        rubrics.append([text, ",".join(f"{a}:{g}" for a, g in tl[:TOKEN_CAP])])
remedies_in_rag = len({p.split(":", 1)[0] for _, t in rubrics for p in t.split(",") if ":" in p})
meta = {
    "source": "homeopathy.sqlite — public-domain classic repertories via drsamonline/homoeopathic-materia-medica-repertory-json (CC0-1.0 JSON); chapter prefix = book attribution",
    "books": books,
    "rubrics": len(rubrics),
    "remedies": remedies_in_rag,
}
rag_path = BASE / "data" / "db_rag.js"
rag_path.write_text(
    "window.DB_RAG=" + json.dumps({"meta": meta, "rubrics": rubrics}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + ";\n",
    encoding="utf-8",
)
print(f"db_rag.js: {len(rubrics)} rubrics from {books} books -> {rag_path.stat().st_size} bytes")

# ---- DB_MM: per-remedy materia medica for every abbreviated remedy ----
agg = {}
mm_rows = 0
for ab, txt in conn.execute(
    "SELECT rem.abbreviation, mm.text FROM materia_medica mm "
    "JOIN remedies rem ON rem.id = mm.remedy_id "
    "WHERE mm.remedy_id IS NOT NULL AND rem.abbreviation IS NOT NULL AND rem.abbreviation != ''"
):
    k = norm(ab)
    if k and txt:
        agg[k] = (agg.get(k, "") + "\n" + txt)[:MM_CAP]
        mm_rows += 1
mm_path = BASE / "data" / "db_mm.js"
mm_path.write_text(
    "window.DB_MM=" + json.dumps(agg, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + ";\n",
    encoding="utf-8",
)
print(f"db_mm.js: {len(agg)} remedies ({mm_rows} rows) -> {mm_path.stat().st_size} bytes")

# ---- DB_CANDIDATES: SQLite remedies not already rankable via embed/abc ----
embed_keys = {norm(e.get("abbr")) for e in js_array(EMBED_PATH) if e.get("abbr")}
abc_keys = set()
for a in js_array(ABC_PATH):
    abc_keys.add(norm(a.get("abbr")))
    abc_keys.add(norm(a.get("name")))
candidates = []
db_abbr_keys = set()
for cid, name, ab, common, cls, sd in conn.execute(
    "SELECT id, canonical_name, abbreviation, common_name, remedy_class, source_data "
    "FROM remedies WHERE abbreviation IS NOT NULL AND abbreviation != ''"
):
    k = norm(ab)
    db_abbr_keys.add(k)
    if k in embed_keys or k in abc_keys:
        continue
    candidates.append(
        {
            "id": None,
            "name": name,
            "abbr": ab,
            "common": common or "",
            "kingdom": (cls or "").capitalize(),
            "family": "",
            "miasm": "",
            "boericke": {"mind": "", "generals": "", "particulars": "", "modalities": "", "dose": ""},
            "kent": "",
            "phatak": "",
            "keySymptoms": [],
            "keynotes": [],
            "relationships": None,
            "clinical_tips": None,
            "kingdom_src": "remedies.remedy_class",
            "source": "homeopathy.sqlite",
        }
    )
conn.close()
cand_path = BASE / "data" / "db_candidates.js"
cand_path.write_text(
    "window.DB_CANDIDATES=" + json.dumps(candidates, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + ";\n",
    encoding="utf-8",
)
covered = len(db_abbr_keys & embed_keys) + len(db_abbr_keys & abc_keys - embed_keys) + len(candidates)
print(
    f"db_candidates.js: {len(candidates)} new rankable remedies -> {cand_path.stat().st_size} bytes | "
    f"coverage: {covered}/{len(db_abbr_keys)} abbreviated remedies rankable "
    f"(embed {len(db_abbr_keys & embed_keys)} + abc-only {len(db_abbr_keys & abc_keys - embed_keys)} + new {len(candidates)})"
)
