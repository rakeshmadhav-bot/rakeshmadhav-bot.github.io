#!/usr/bin/env python3
"""Build an attribution-aware SQLite index from the project's existing HomeoAI datasets.

This imports only files already present in ../data. It does not scrape or copy online
content; source_catalog rows are references for future, license-reviewed ingestion.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_DB = ROOT / "database" / "homeopathy.sqlite"
DEFAULT_SEARCH_EXPORT = DATA / "homeopathy_search.json.gz"

DATASETS = {
    "embed.js": ("HomeoAI detailed remedy index", "project dataset; upstream attributions vary"),
    "abc_index.js": ("ABC Homeopathy remedy index", "source attribution retained per record; verify reuse rights"),
    "boericke_full.js": ("Boericke Materia Medica", "public-domain source asserted by project; verify jurisdiction and edition"),
    "oorep_rag.js": ("OOREP Publicum repertory", "GPL-3.0 per embedded metadata; preserve license obligations"),
    "oorep_index.js": ("OOREP remedy index", "GPL-3.0 per embedded metadata; preserve license obligations"),
    "tautopathy_data.js": ("HomeoAI tautopathy reference", "compiled references; claims and licensing require source-level review"),
    "specialized_repertories.json": ("HomeoAI specialized repertories", "compiled dataset; source and reuse rights require review"),
    "patient_instructions.json": ("HomeoAI patient instructions", "project dataset; not clinical advice"),
    "cured_cases.json": ("HomeoAI illustrative cases", "project data labels these as illustrative seeds, not verified outcomes"),
    "stock.js": ("HomeoAI stock sheet", "user/project inventory data"),
}

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, origin_file TEXT,
  url TEXT, license TEXT NOT NULL DEFAULT 'unknown', attribution TEXT,
  review_status TEXT NOT NULL DEFAULT 'unreviewed', notes TEXT
);
CREATE TABLE IF NOT EXISTS remedies (
  id INTEGER PRIMARY KEY, canonical_name TEXT NOT NULL, abbreviation TEXT,
  normalized_key TEXT NOT NULL UNIQUE, common_name TEXT, remedy_class TEXT,
  source_data JSON NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remedies_name ON remedies(canonical_name);
CREATE INDEX IF NOT EXISTS idx_remedies_abbr ON remedies(abbreviation);
CREATE TABLE IF NOT EXISTS remedy_sources (
  remedy_id INTEGER NOT NULL REFERENCES remedies(id) ON DELETE CASCADE,
  source_id INTEGER NOT NULL REFERENCES sources(id), source_record_id TEXT,
  source_name TEXT, attribution TEXT, license TEXT,
  PRIMARY KEY(remedy_id, source_id, source_record_id)
);
CREATE TABLE IF NOT EXISTS materia_medica (
  id INTEGER PRIMARY KEY, remedy_id INTEGER REFERENCES remedies(id) ON DELETE SET NULL,
  source_id INTEGER NOT NULL REFERENCES sources(id), title TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '', url TEXT, attribution TEXT,
  UNIQUE(source_id, title, url)
);
CREATE TABLE IF NOT EXISTS modalities (
  id INTEGER PRIMARY KEY, remedy_id INTEGER REFERENCES remedies(id) ON DELETE CASCADE,
  source_id INTEGER NOT NULL REFERENCES sources(id), direction TEXT NOT NULL,
  description TEXT NOT NULL, source_text TEXT
);
CREATE TABLE IF NOT EXISTS rubrics (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
  repertory TEXT, rubric TEXT NOT NULL, source_record_id TEXT,
  UNIQUE(source_id, repertory, rubric)
);
CREATE TABLE IF NOT EXISTS rubric_remedies (
  rubric_id INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
  remedy_id INTEGER NOT NULL REFERENCES remedies(id) ON DELETE CASCADE,
  grade REAL, source_text TEXT,
  PRIMARY KEY(rubric_id, remedy_id)
);
CREATE TABLE IF NOT EXISTS reference_documents (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
  document_type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  url TEXT, metadata JSON NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS source_catalog (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, source_type TEXT, url TEXT,
  license_claim TEXT, review_status TEXT NOT NULL DEFAULT 'needs verification',
  relevance TEXT, source_id INTEGER REFERENCES sources(id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
  item_type UNINDEXED, item_id UNINDEXED, title, body, tokenize='unicode61'
);
"""


def normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def load_js_assignment(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig").strip()
    if "=" not in text:
        raise ValueError(f"Expected a JS global assignment in {path}")
    payload = text.split("=", 1)[1].strip()
    if payload.endswith(";"):
        payload = payload[:-1].rstrip()
    return json.loads(payload)


def load_data(filename: str) -> Any:
    path = DATA / filename
    if filename.endswith(".js"):
        return load_js_assignment(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def ensure_source(conn: sqlite3.Connection, name: str, filename: str, attribution: str, *,
                  url: str | None = None, license_name: str = "unknown", notes: str = "") -> int:
    conn.execute(
        "INSERT INTO sources(name,origin_file,url,license,attribution,review_status,notes) VALUES(?,?,?,?,?,'unreviewed',?) "
        "ON CONFLICT(name) DO UPDATE SET origin_file=excluded.origin_file, url=COALESCE(excluded.url,sources.url), "
        "license=excluded.license, attribution=excluded.attribution, notes=excluded.notes",
        (name, filename, url, license_name, attribution, notes),
    )
    return conn.execute("SELECT id FROM sources WHERE name=?", (name,)).fetchone()[0]


_REMEDY_CACHE: dict[str, int] = {}


def ensure_remedy(conn: sqlite3.Connection, name: Any, abbr: Any = "", common: Any = "",
                  remedy_class: str = "unspecified", source_data: dict[str, Any] | None = None) -> int | None:
    name = str(name or "").strip()
    abbr = str(abbr or "").strip()
    key = normalized(abbr) or normalized(name)
    if not key or not name:
        return None
    cached_id = _REMEDY_CACHE.get(key)
    data = source_data or {}
    if cached_id and not data:
        return cached_id
    row = conn.execute("SELECT id, source_data FROM remedies WHERE normalized_key=?", (key,)).fetchone()
    if row:
        _REMEDY_CACHE[key] = row[0]
        if data:
            try:
                merged = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                merged = {}
            for k, v in data.items():
                if v not in (None, "", [], {}):
                    merged[k] = v
            conn.execute(
                "UPDATE remedies SET common_name=COALESCE(NULLIF(common_name,''),?), remedy_class=CASE WHEN remedy_class='unspecified' THEN ? ELSE remedy_class END, source_data=? WHERE id=?",
                (str(common or ""), remedy_class, json.dumps(merged, ensure_ascii=False), row[0]),
            )
        return row[0]
    cur = conn.execute(
        "INSERT INTO remedies(canonical_name,abbreviation,normalized_key,common_name,remedy_class,source_data,created_at) VALUES(?,?,?,?,?,?,?)",
        (name, abbr, key, str(common or ""), remedy_class, json.dumps(data, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
    )
    _REMEDY_CACHE[key] = cur.lastrowid
    return cur.lastrowid


def link_remedy_source(conn: sqlite3.Connection, remedy_id: int | None, source_id: int,
                       source_record_id: Any, source_name: Any = "", attribution: Any = "", license_name: str = "") -> None:
    if remedy_id is None:
        return
    conn.execute(
        "INSERT OR IGNORE INTO remedy_sources(remedy_id,source_id,source_record_id,source_name,attribution,license) VALUES(?,?,?,?,?,?)",
        (remedy_id, source_id, str(source_record_id or ""), str(source_name or ""), str(attribution or ""), license_name),
    )


def add_search(conn: sqlite3.Connection, item_type: str, item_id: int, title: str, body: str) -> None:
    conn.execute("INSERT INTO search_index(item_type,item_id,title,body) VALUES(?,?,?,?)",
                 (item_type, str(item_id), str(title or ""), str(body or "")))


def add_mm(conn: sqlite3.Connection, source_id: int, remedy_id: int | None, title: str,
           text: str, url: str = "", attribution: str = "") -> None:
    if not text and not url:
        return
    cur = conn.execute(
        "INSERT OR IGNORE INTO materia_medica(remedy_id,source_id,title,text,url,attribution) VALUES(?,?,?,?,?,?)",
        (remedy_id, source_id, title or "Untitled remedy text", text or "", url or None, attribution or None),
    )
    mm_id = cur.lastrowid if cur.rowcount else None
    if not mm_id:
        mm_id = conn.execute("SELECT id FROM materia_medica WHERE source_id=? AND title=? AND url IS ?",
                             (source_id, title or "Untitled remedy text", url or None)).fetchone()[0]
    add_search(conn, "materia_medica", mm_id, title, text)
    for match in re.finditer(r"(?im)^\s*Modalities\s*:\s*(.*?)(?=\n\s*(?:Relationship|Dose|$))", text or ""):
        clause = match.group(1)
        for direction, pattern in (("worse", r"(?:worse|aggravated by|aggravation)\s*[,;:]?\s*(.*?)(?=\s*(?:\.\s*)?(?:Better|ameliorated by|$))"),
                                   ("better", r"(?:better|ameliorated by|amelioration)\s*[,;:]?\s*(.*?)(?=\s*(?:\.\s*)?(?:Worse|aggravated by|$))")):
            m = re.search(pattern, clause, re.I)
            if m and m.group(1).strip(" .;,"):
                conn.execute("INSERT INTO modalities(remedy_id,source_id,direction,description,source_text) VALUES(?,?,?,?,?)",
                             (remedy_id, source_id, direction, m.group(1).strip(" .;,"), clause.strip()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DB, help="SQLite output path")
    parser.add_argument("--search-export", type=Path, default=DEFAULT_SEARCH_EXPORT,
                        help="Compressed browser-search export path")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    global _REMEDY_CACHE
    _REMEDY_CACHE = {}
    conn = sqlite3.connect(args.output)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-200000")
    conn.executescript(SCHEMA)
    conn.execute("BEGIN")
    try:
        source_ids: dict[str, int] = {}
        for filename, (label, attribution) in DATASETS.items():
            path = DATA / filename
            if path.exists():
                license_name = "GPL-3.0" if filename.startswith("oorep_") else "unknown; verify before redistribution"
                notes = "Only existing local project data imported; internet-wide completeness is not claimed."
                source_ids[filename] = ensure_source(conn, label, f"data/{filename}", attribution,
                                                      license_name=license_name, notes=notes)

        # Structured remedy records and core remedy metadata.
        db_records = load_data("embed.js")
        db_source = source_ids["embed.js"]
        for item in db_records:
            rid = ensure_remedy(conn, item.get("name"), item.get("abbr"), item.get("common"),
                                source_data=item)
            link_remedy_source(conn, rid, db_source, item.get("id"), item.get("name"))
            if rid:
                blob = item.get("boericke") or {}
                mm_text = "\n".join(str(blob.get(k) or "") for k in ("mind", "generals", "particulars", "modalities", "dose"))
                add_mm(conn, db_source, rid, item.get("name") or item.get("abbr"), mm_text,
                       attribution="HomeoAI detailed remedy record; original field attribution may vary")
                add_search(conn, "remedy", rid, item.get("name"), json.dumps(item, ensure_ascii=False))

        # Auxiliary catalogue can include remedies absent from the primary index.
        abc_records = load_data("abc_index.js")
        abc_source = source_ids["abc_index.js"]
        for item in abc_records:
            rid = ensure_remedy(conn, item.get("name"), item.get("abbr"), (item.get("aka") or [""])[0],
                                item.get("category") or "unspecified", item)
            link_remedy_source(conn, rid, abc_source, item.get("abbr"), item.get("name"), item.get("url"),
                               "source attribution retained per record")
            if rid:
                add_search(conn, "remedy", rid, item.get("name"), json.dumps(item, ensure_ascii=False))

        # Full Boericke text and extracted modalities.
        mm_source = source_ids["boericke_full.js"]
        for item in load_data("boericke_full.js"):
            rid = ensure_remedy(conn, item.get("name"), item.get("abbr"), item.get("common"), "unspecified", item)
            link_remedy_source(conn, rid, mm_source, item.get("abbr"), item.get("name"), item.get("url"),
                               "Boericke Materia Medica record; verify edition rights")
            add_mm(conn, mm_source, rid, item.get("name") or item.get("abbr"), item.get("text") or "",
                   item.get("url") or "", "Boericke Materia Medica; source URL retained")

        # OOREP repertory rubrics and remedy grades.
        rag_source = source_ids["oorep_rag.js"]
        for rubric_text, remedies_text in load_data("oorep_rag.js").get("rubrics", []):
            cur = conn.execute("INSERT OR IGNORE INTO rubrics(source_id,repertory,rubric) VALUES(?,?,?)",
                               (rag_source, "OOREP Publicum", rubric_text))
            rubric_id = cur.lastrowid if cur.rowcount else None
            if not rubric_id:
                rubric_id = conn.execute("SELECT id FROM rubrics WHERE source_id=? AND repertory=? AND rubric=?",
                                         (rag_source, "OOREP Publicum", rubric_text)).fetchone()[0]
            for entry in str(remedies_text).split(","):
                if ":" not in entry:
                    continue
                abbr, grade = entry.rsplit(":", 1)
                remedy_id = conn.execute("SELECT id FROM remedies WHERE normalized_key=?", (normalized(abbr),)).fetchone()
                if remedy_id:
                    conn.execute("INSERT OR IGNORE INTO rubric_remedies(rubric_id,remedy_id,grade,source_text) VALUES(?,?,?,?)",
                                 (rubric_id, remedy_id[0], float(grade) if grade else None, entry))
            add_search(conn, "rubric", rubric_id, rubric_text, str(remedies_text))

        oorep_source = source_ids["oorep_index.js"]
        for abbr, entries in load_data("oorep_index.js").get("byRemedy", {}).items():
            rid = ensure_remedy(conn, abbr, abbr)
            link_remedy_source(conn, rid, oorep_source, abbr, abbr, attribution="OOREP index")
            for entry in entries:
                rubric_text = entry.get("t", "")
                cur = conn.execute("INSERT OR IGNORE INTO rubrics(source_id,repertory,rubric) VALUES(?,?,?)",
                                   (oorep_source, entry.get("rep", "OOREP"), rubric_text))
                rubric_id = cur.lastrowid if cur.rowcount else None
                if not rubric_id:
                    rubric_id = conn.execute("SELECT id FROM rubrics WHERE source_id=? AND repertory=? AND rubric=?",
                                             (oorep_source, entry.get("rep", "OOREP"), rubric_text)).fetchone()[0]
                if rubric_id and rid:
                    conn.execute("INSERT OR IGNORE INTO rubric_remedies(rubric_id,remedy_id,grade,source_text) VALUES(?,?,?,?)",
                                 (rubric_id, rid, entry.get("w"), json.dumps(entry, ensure_ascii=False)))

        # HomeoAI tautopathy agents are catalogued as reference records, not prescriptions.
        tauto = load_data("tautopathy_data.js")
        tauto_source = source_ids["tautopathy_data.js"]
        for agent in tauto.get("agents", []):
            name = agent.get("remedy") or agent.get("source_substance") or "Unnamed agent"
            rid = ensure_remedy(conn, name, remedy_class=agent.get("category") or "tautopathy reference", source_data=agent)
            link_remedy_source(conn, rid, tauto_source, name, name, attribution="HomeoAI compiled traditional reference; not evidence of efficacy")
            doc_id = conn.execute("INSERT INTO reference_documents(source_id,document_type,title,body,metadata) VALUES(?,?,?,?,?)",
                                  (tauto_source, "tautopathy_agent", name, str(agent.get("use", "")) + "\n" + str(agent.get("note", "")), json.dumps(agent, ensure_ascii=False))).lastrowid
            add_search(conn, "reference_document", doc_id, name, json.dumps(agent, ensure_ascii=False))
        for repo in tauto.get("repositories", []):
            conn.execute("INSERT INTO source_catalog(name,source_type,url,license_claim,relevance,source_id) VALUES(?,?,?,?,?,?)",
                         (repo.get("name", "Unnamed source"), repo.get("type", ""), repo.get("url", ""),
                          repo.get("license", "unknown"), repo.get("relevance", ""), tauto_source))

        # Remaining local JSON datasets retained with their complete metadata/body.
        for filename, doc_type, root_key, title_field, body_field in (
            ("specialized_repertories.json", "specialized_repertory", "repertories", "name", "rubrics"),
            ("patient_instructions.json", "patient_instructions", "conditions", "condition", "instructions"),
            ("cured_cases.json", "illustrative_case", "cases", "remedy", "outcome"),
        ):
            payload = load_data(filename)
            src = source_ids[filename]
            for i, item in enumerate(payload.get(root_key, [])):
                title = str(item.get(title_field) or f"{doc_type} {i+1}")
                body = json.dumps(item.get(body_field, ""), ensure_ascii=False) if isinstance(item.get(body_field), (dict, list)) else str(item.get(body_field, ""))
                did = conn.execute("INSERT INTO reference_documents(source_id,document_type,title,body,metadata) VALUES(?,?,?,?,?)",
                                   (src, doc_type, title, body, json.dumps(item, ensure_ascii=False))).lastrowid
                add_search(conn, "reference_document", did, title, body + " " + json.dumps(item, ensure_ascii=False))
                if filename == "specialized_repertories.json":
                    for rubric in item.get("rubrics", []):
                        text = rubric.get("rubric", "")
                        cur = conn.execute("INSERT OR IGNORE INTO rubrics(source_id,repertory,rubric) VALUES(?,?,?)",
                                           (src, item.get("id", item.get("name", "specialized")), text))
                        rr = conn.execute("SELECT id FROM rubrics WHERE source_id=? AND repertory=? AND rubric=?",
                                          (src, item.get("id", item.get("name", "specialized")), text)).fetchone()
                        if rr:
                            for abbr in rubric.get("remedies", []):
                                rid = ensure_remedy(conn, abbr, abbr)
                                if rid:
                                    conn.execute("INSERT OR IGNORE INTO rubric_remedies(rubric_id,remedy_id,grade,source_text) VALUES(?,?,?,?)",
                                                 (rr[0], rid, None, text))
                            add_search(conn, "rubric", rr[0], text, json.dumps(rubric, ensure_ascii=False))

        # Inventory records are data, not remedy evidence.
        stock_source = source_ids["stock.js"]
        for item in load_data("stock.js"):
            title = str(item.get("n", "Stock entry"))
            did = conn.execute("INSERT INTO reference_documents(source_id,document_type,title,body,metadata) VALUES(?,?,?,?,?)",
                               (stock_source, "inventory", title, json.dumps(item, ensure_ascii=False), json.dumps(item, ensure_ascii=False))).lastrowid
            add_search(conn, "inventory", did, title, json.dumps(item, ensure_ascii=False))

        conn.execute("INSERT INTO sources(name,license,attribution,review_status,notes) VALUES(?,?,?,?,?)",
                     ("Internet source coverage", "varies", "External sources are pointers only; import text only after checking copyright, license, attribution, and medical accuracy.",
                      "not ingested", "No claim of containing everything available online; this build contains project-local data only."))
        conn.commit()
        conn.execute("PRAGMA optimize")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    verify = sqlite3.connect(args.output)
    counts = {table: verify.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("remedies", "materia_medica", "rubrics", "rubric_remedies", "modalities", "reference_documents", "source_catalog")}
    args.search_export.parent.mkdir(parents=True, exist_ok=True)
    rows = verify.execute("""
      SELECT s.item_type, s.item_id, s.title, s.body,
        COALESCE(src.name, '') AS source,
        COALESCE(mm.url, rd.url, rs.url, '') AS url,
        COALESCE(src.license, '') AS license
      FROM search_index s
      LEFT JOIN materia_medica mm ON s.item_type='materia_medica' AND CAST(s.item_id AS INTEGER)=mm.id
      LEFT JOIN rubrics r ON s.item_type='rubric' AND CAST(s.item_id AS INTEGER)=r.id
      LEFT JOIN reference_documents rd ON s.item_type IN ('reference_document','inventory') AND CAST(s.item_id AS INTEGER)=rd.id
      LEFT JOIN remedies rem ON s.item_type='remedy' AND CAST(s.item_id AS INTEGER)=rem.id
      LEFT JOIN remedy_sources rsrc ON rem.id=rsrc.remedy_id
      LEFT JOIN sources rs ON rsrc.source_id=rs.id
      LEFT JOIN sources src ON src.id=COALESCE(mm.source_id, r.source_id, rd.source_id, rs.id)
      ORDER BY s.rowid
    """).fetchall()
    export = [{"type": row[0], "id": row[1], "title": row[2], "body": row[3],
               "source": row[4], "url": row[5], "license": row[6]} for row in rows]
    with args.search_export.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=7, mtime=0) as compressed:
            compressed.write(json.dumps(export, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    verify.close()
    print(json.dumps({"database": str(args.output), "search_export": str(args.search_export),
                      "search_export_bytes": args.search_export.stat().st_size,
                      "search_records": len(export), "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
