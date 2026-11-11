#!/usr/bin/env python3
"""Build an attribution-aware SQLite index from local datasets.

Inputs are local files only: ../data (project datasets), plus the sibling Remedia
catalog JSON and the sibling drsamonline JSON checkout when they are present.
Nothing is scraped; source_catalog rows are verified pointers, not ingested content.
"""
from __future__ import annotations

import argparse
import difflib
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
  url TEXT, metadata JSON NOT NULL DEFAULT '{}',
  remedy_id INTEGER REFERENCES remedies(id)
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
_FUZZY_CACHE: dict[str, int | None] = {}


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


REPO_JSON_URL = "https://github.com/drsamonline/homoeopathic-materia-medica-repertory-json"
EXT_LICENSE_NOTE = "CC0-1.0 per upstream README; repertorium-publicum-2008 (GPL-3.0) not imported"
FUZZY_CUTOFF = 0.85


def build_alias(conn: sqlite3.Connection) -> dict[str, int]:
    """Map every normalized name/abbreviation of known remedies to its id."""
    alias: dict[str, int] = {}
    for rid, key, name, abbr, common in conn.execute(
        "SELECT id, normalized_key, canonical_name, abbreviation, common_name FROM remedies"
    ):
        for value in (key, name, abbr, common):
            norm = normalized(value)
            if norm:
                alias.setdefault(norm, rid)
    return alias


def build_fuzzy_pool(alias: dict[str, int]) -> dict[str, list[str]]:
    """Bucket alias keys by first character so fuzzy lookup stays cheap."""
    pool: dict[str, list[str]] = {}
    for key, rid in alias.items():
        if rid is not None and len(key) >= 5:
            pool.setdefault(key[0], []).append(key)
    return pool


def fuzzy_remedy(key: str, alias: dict[str, int], pool: dict[str, list[str]]) -> int | None:
    if key in _FUZZY_CACHE:
        return _FUZZY_CACHE[key]
    # Same first character plus a high ratio keeps OCR-damaged names from
    # latching onto an unrelated remedy (e.g. "Acetic Acid" vs "lactic acid").
    candidates = pool.get(key[0])
    hit = None
    if candidates:
        match = difflib.get_close_matches(key, candidates, n=1, cutoff=FUZZY_CUTOFF)
        hit = alias.get(match[0]) if match else None
    _FUZZY_CACHE[key] = hit
    return hit


def resolve_remedy(conn: sqlite3.Connection, alias: dict[str, int], trusted: set[str],
                   pool: dict[str, list[str]], name: Any, abbr: Any, remedy_index: dict | None,
                   *, allow_create: bool, stats: dict | None = None,
                   source_id: int | None = None) -> int | None:
    """Resolve a source's remedy reference to a remedies.id, or None if untrustworthy.

    Order: exact name/abbrev match, then a conservative fuzzy match, then — only
    when allow_create and the name comes from a curated vocabulary (trusted) —
    a new remedy row. OCR debris with no trustworthy name resolves to None.
    """
    name = str(name or "").strip()
    abbr = str(abbr or "").strip()
    if not name and abbr and remedy_index:
        idx = remedy_index.get(abbr) or remedy_index.get(abbr.rstrip("."))
        if isinstance(idx, dict):  # some books store {"name": ..., "resolved": bool}
            idx = idx.get("name")
        name = str(idx or "").strip()
    nk, ak = normalized(name), normalized(abbr)
    for key in (nk, ak):
        if key and key in alias:
            return alias[key]
    if len(nk) >= 5:
        hit = fuzzy_remedy(nk, alias, pool)
        if hit is not None:
            return hit
    if allow_create and nk and nk in trusted:
        rid = ensure_remedy(conn, name, "")
        if rid:
            alias[nk] = rid
            if abbr and len(abbr) <= 8 and " " not in abbr and ak:
                alias.setdefault(ak, rid)
                conn.execute(
                    "UPDATE remedies SET abbreviation=? WHERE id=? AND (abbreviation IS NULL OR abbreviation='')",
                    (abbr, rid))
            if source_id is not None:
                link_remedy_source(conn, rid, source_id, abbr or name, name,
                                   attribution="curated vocabulary match", license_name="CC0-1.0")
            if stats is not None:
                stats["created_remedies"] = stats.get("created_remedies", 0) + 1
        return rid
    return None


def unique_source_name(conn: sqlite3.Connection, base: str) -> str:
    name, suffix = base, 2
    while conn.execute("SELECT 1 FROM sources WHERE name=?", (name,)).fetchone():
        name = f"{base} ({suffix})"
        suffix += 1
    return name


def ext_source(conn: sqlite3.Connection, book: dict) -> int:
    year = book.get("year")
    base = f"{book['title']} ({year})" if year else str(book["title"])
    notes = EXT_LICENSE_NOTE
    if book.get("quality"):
        notes += f"; quality={book['quality']}"
        if book.get("quality_note"):
            notes += f" — {book['quality_note']}"
    attribution = "; ".join(x for x in (str(book.get("author") or ""), str(book.get("publisher") or "")) if x)
    return ensure_source(conn, unique_source_name(conn, base), f"ext_hommr_json/{book['path']}",
                         attribution, url=f"{REPO_JSON_URL}/blob/main/{book['path']}",
                         license_name="CC0-1.0", notes=notes)


def import_remedia(conn: sqlite3.Connection, path: Path, alias: dict[str, int]) -> dict:
    """Import the Remedia shop catalog: remedy metadata, products, categories.

    The full price-variant matrix stays in the source JSON; each remedy row only
    gets its potency list and price range.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = payload.get("metadata") or {}
    source_url = str(meta.get("source") or "https://remedia-homeopathy.com/shop/")
    sid = ensure_source(conn, "Remedia Homeopathy Catalog", str(path), attribution=source_url,
                        url=source_url, license_name="catalog data; verify reuse rights",
                        notes=f"Catalog snapshot scraped {meta.get('scraped_at', 'unknown')}; "
                              f"prices excl. VAT. {meta.get('note', '')}")
    license_name = "catalog data; verify reuse rights"
    stats = {"remedies": 0, "matched": 0, "created": 0, "products": 0, "categories": 0}
    for item in payload.get("homeopathic_remedies", []):
        name = str(item.get("name") or item.get("main_name") or "").strip()
        if not name:
            continue
        synonyms = [str(s) for s in (item.get("synonyms") or []) if s]
        keys: list[str] = []
        for value in [item.get("main_name"), name, *synonyms]:
            key = normalized(value)
            if key and key not in keys:
                keys.append(key)
        rid = next((alias[k] for k in keys if k in alias), None)
        potencies: list[str] = []
        for variant in item.get("price_variants") or []:
            potency = variant.get("potency")
            if potency and potency not in potencies:
                potencies.append(str(potency))
        data = {k: v for k, v in {
            "code": item.get("code"), "url": item.get("url"), "main_name": item.get("main_name"),
            "synonyms": synonyms, "main_group": item.get("main_group"), "family": item.get("family"),
            "scholten_id": item.get("scholten_id"), "manufacturer_regulation": item.get("manufacturer_regulation"),
            "remedia_id": item.get("remedia_id"), "image": item.get("image"),
            "price_low": item.get("price_low"), "price_high": item.get("price_high"),
            "currency": item.get("currency"), "dosages": item.get("dosages"), "potencies": potencies,
        }.items() if v not in (None, "", [], {})}
        group = str(item.get("main_group") or "unspecified")
        if rid is None:
            rid = ensure_remedy(conn, name, "", remedy_class=group, source_data={"remedia": data})
            stats["created"] += 1
        else:
            row = conn.execute("SELECT source_data FROM remedies WHERE id=?", (rid,)).fetchone()
            try:
                merged = json.loads(row[0] or "{}")
            except (TypeError, json.JSONDecodeError):
                merged = {}
            merged["remedia"] = data
            conn.execute(
                "UPDATE remedies SET source_data=?, remedy_class=CASE WHEN remedy_class='unspecified' THEN ? ELSE remedy_class END WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), group, rid))
            stats["matched"] += 1
        if rid:
            for key in keys:
                alias.setdefault(key, rid)
            link_remedy_source(conn, rid, sid, item.get("code"), name, attribution=source_url,
                               license_name=license_name)
        stats["remedies"] += 1
    domain = "https://remedia-homeopathy.com"
    for product in payload.get("shop_products", []):
        title = str(product.get("name") or "Shop product")
        low, high = product.get("price_low"), product.get("price_high")
        price = f"{low} {product.get('currency', '')}".strip() if low == high else \
            f"{low}–{high} {product.get('currency', '')}".strip()
        categories = [str(c) for c in (product.get("categories") or [])]
        body = title + (f"\nPrice: {price}" if price else "")
        if categories:
            body += "\nCategories: " + ", ".join(categories)
        meta_row = {"code": product.get("code"), "url": product.get("url"), "price_low": low,
                    "price_high": high, "currency": product.get("currency"),
                    "categories": categories, "variant_count": len(product.get("variants") or [])}
        doc_id = conn.execute(
            "INSERT INTO reference_documents(source_id,document_type,title,body,url,metadata) VALUES(?,?,?,?,?,?)",
            (sid, "product", title, body, product.get("url") or None,
             json.dumps(meta_row, ensure_ascii=False))).lastrowid
        add_search(conn, "reference_document", doc_id, title, body)
        stats["products"] += 1
    for category in payload.get("categories", []):
        codes = [str(c) for c in (category.get("product_codes") or [])]
        rel = str(category.get("url") or "")
        url = domain + rel if rel.startswith("/") else rel
        title = str(category.get("name") or "Category")
        body = f"{title}\n{len(codes)} products\n{url}"
        meta_row = {"slug": category.get("slug"), "url": rel, "product_count": len(codes),
                    "product_codes": codes}
        doc_id = conn.execute(
            "INSERT INTO reference_documents(source_id,document_type,title,body,url,metadata) VALUES(?,?,?,?,?,?)",
            (sid, "product_category", title, body, url or None,
             json.dumps(meta_row, ensure_ascii=False))).lastrowid
        add_search(conn, "reference_document", doc_id, title, body)
        stats["categories"] += 1
    return stats


def import_repertory_book(conn: sqlite3.Connection, ext_root: Path, book: dict,
                          alias: dict[str, int], trusted: set[str], pool: dict[str, list[str]]) -> dict:
    path = ext_root / book["path"]
    data = json.loads(path.read_text(encoding="utf-8"))
    remedy_index = data.get("remedy_index") or {}
    for value in remedy_index.values():
        if isinstance(value, dict):  # {"name": ..., "resolved": bool} form
            value = value.get("name")
        key = normalized(value)
        if key:
            trusted.add(key)
    sid = ext_source(conn, book)
    label = str(book["title"])
    stats = {"rubrics": 0, "rubric_links": 0, "skipped_links": 0, "created_remedies": 0}

    def process(chapter: str, rub: dict) -> None:
        text = str(rub.get("text") or "").strip()
        if len(text) < 2:
            return
        # Chapter prefix keeps duplicate rubric texts inside one book unique
        # (the rubrics table is UNIQUE on source/repertory/text) and mirrors the
        # full-path style used by the OOREP rubrics already in this database.
        full = f"{chapter} — {text}" if chapter else text
        cur = conn.execute(
            "INSERT OR IGNORE INTO rubrics(source_id,repertory,rubric,source_record_id) VALUES(?,?,?,?)",
            (sid, label, full, str(rub.get("id") or "")))
        is_new = bool(cur.rowcount)
        if is_new:
            rubric_id = cur.lastrowid
        else:
            row = conn.execute("SELECT id FROM rubrics WHERE source_id=? AND repertory=? AND rubric=?",
                               (sid, label, full)).fetchone()
            if not row:
                return
            rubric_id = row[0]
        tokens = []
        for entry in rub.get("remedies") or []:
            rid = resolve_remedy(conn, alias, trusted, pool, entry.get("name"), entry.get("abbrev"),
                                 remedy_index, allow_create=True, stats=stats, source_id=sid)
            if rid is None:
                stats["skipped_links"] += 1
                continue
            grade = entry.get("grade")
            try:
                grade = float(grade) if grade is not None else None
            except (TypeError, ValueError):
                grade = None
            cur2 = conn.execute(
                "INSERT OR IGNORE INTO rubric_remedies(rubric_id,remedy_id,grade,source_text) VALUES(?,?,?,?)",
                (rubric_id, rid, grade, str(entry.get("abbrev") or entry.get("name") or "")))
            if cur2.rowcount:
                stats["rubric_links"] += 1
            token = str(entry.get("abbrev") or entry.get("name") or "")
            if token:
                tokens.append(token if grade is None else f"{token}:{grade:g}")
        if is_new:
            add_search(conn, "rubric", rubric_id, full, ",".join(tokens))
            stats["rubrics"] += 1

    if book.get("split"):
        seen: set[str] = set()
        for entry in data.get("chapters") or data.get("sections") or []:
            filename = entry.get("file")
            if not filename or filename in seen:
                continue
            seen.add(filename)
            chapter_path = path.parent / filename
            if not chapter_path.exists():
                continue
            chapter_data = json.loads(chapter_path.read_text(encoding="utf-8"))
            section = chapter_data.get("section") or chapter_data
            for rub in section.get("rubrics") or []:
                process(str(rub.get("chapter") or section.get("title") or entry.get("title") or ""), rub)
        # Chapter files present on disk but absent from the index (Kent's
        # extremities-2 continuation) still belong to the book.
        for chapter_path in sorted(path.parent.glob("*.json")):
            if chapter_path.name == "index.json" or chapter_path.name in seen:
                continue
            chapter_data = json.loads(chapter_path.read_text(encoding="utf-8"))
            section = chapter_data.get("section") or chapter_data
            for rub in section.get("rubrics") or []:
                process(str(rub.get("chapter") or section.get("title") or chapter_path.stem), rub)
    else:
        for section in data.get("sections") or data.get("chapters") or []:
            title = str(section.get("title") or "")
            for rub in section.get("rubrics") or []:
                process(str(rub.get("chapter") or title), rub)
    return stats


def import_mm_book(conn: sqlite3.Connection, ext_root: Path, book: dict,
                   alias: dict[str, int], trusted: set[str], pool: dict[str, list[str]]) -> dict:
    path = ext_root / book["path"]
    data = json.loads(path.read_text(encoding="utf-8"))
    sid = ext_source(conn, book)
    url = f"{REPO_JSON_URL}/blob/main/{book['path']}"
    attribution = "; ".join(x for x in (str(book.get("author") or ""),
                                        f"{book.get('title', '')} ({book.get('year')})") if x)
    stats = {"mm_entries": 0, "mm_rows": 0, "mm_linked": 0}
    for rem in data.get("remedies") or []:
        stats["mm_entries"] += 1
        rid = resolve_remedy(conn, alias, trusted, pool, rem.get("name"), rem.get("abbrev"),
                             None, allow_create=False)
        if rid is None:
            rid = resolve_remedy(conn, alias, trusted, pool, rem.get("canonical_name"),
                                 rem.get("abbrev"), None, allow_create=False)
        lines = []
        indications = rem.get("clinical_indications")
        if isinstance(indications, list) and indications:
            lines.append("Clinical indications: " + ", ".join(str(x) for x in indications))
        sections = rem.get("sections")
        if isinstance(sections, dict):
            for heading, body in sections.items():
                if body:
                    lines.append(f"{heading}\n{body}")
        for symptom in rem.get("symptoms") or []:
            body = str(symptom.get("text") or "").strip()
            if body:
                chapter = str(symptom.get("chapter") or "").strip()
                lines.append(f"{chapter}: {body}" if chapter else body)
        text = "\n\n".join(lines)
        if not text:
            continue
        title = str(rem.get("name") or rem.get("canonical_name") or rem.get("abbrev") or "Untitled remedy")
        add_mm(conn, sid, rid, title, text, url, attribution)
        stats["mm_rows"] += 1
        if rid:
            stats["mm_linked"] += 1
    return stats


def import_treatise_book(conn: sqlite3.Connection, ext_root: Path, book: dict,
                         alias: dict[str, int], trusted: set[str], pool: dict[str, list[str]]) -> dict:
    path = ext_root / book["path"]
    data = json.loads(path.read_text(encoding="utf-8"))
    sections = data.get("sections") or []
    sid = ext_source(conn, book)
    url = f"{REPO_JSON_URL}/blob/main/{book['path']}"
    attribution = "; ".join(x for x in (str(book.get("author") or ""),
                                        f"{book.get('title', '')} ({book.get('year')})") if x)
    # Pass 1: resolve section titles so we can tell a remedy-monograph book
    # (Clarke's Dictionary, Kent's Lectures, Allen's Keynotes, ...) from a
    # chapter-titled treatise. Match-only: titles never create remedies.
    resolved: list[int | None] = []
    titled = matched = 0
    for sec in sections:
        title = str(sec.get("title") or "").strip()
        rid = None
        if title:
            titled += 1
            rid = resolve_remedy(conn, alias, trusted, pool, title, "", None, allow_create=False)
            if rid:
                matched += 1
        resolved.append(rid)
    is_remedy_book = bool(titled) and matched / titled >= 0.5
    stats = {"treatise_sections": 0, "treatise_mm_books": int(is_remedy_book),
             "mm_rows": 0, "mm_linked": 0}
    for sec, rid in zip(sections, resolved):
        body = str(sec.get("text") or "")
        notes = sec.get("notes")
        if notes:
            body += "\n\nNotes:\n" + ("\n".join(str(x) for x in notes) if isinstance(notes, list) else str(notes))
        if not body:
            continue
        title = str(sec.get("title") or "").strip()
        if not title:
            number = sec.get("n")
            title = f"{book['title']} §{number}" if number is not None else str(book["title"])
        meta: dict[str, Any] = {"book": str(book.get("id") or "")}
        for key in ("n", "from", "corroboration"):
            if sec.get(key) is not None:
                meta[key] = sec[key]
        doc_id = conn.execute(
            "INSERT INTO reference_documents(source_id,document_type,title,body,url,metadata,remedy_id) VALUES(?,?,?,?,?,?,?)",
            (sid, "treatise_section", title, body, url, json.dumps(meta, ensure_ascii=False), rid)).lastrowid
        add_search(conn, "reference_document", doc_id, title, body)
        stats["treatise_sections"] += 1
        if is_remedy_book and rid:
            add_mm(conn, sid, rid, title, body, url, attribution)
            stats["mm_rows"] += 1
            stats["mm_linked"] += 1
    return stats


def import_ext_dataset(conn: sqlite3.Connection, ext_root: Path, alias: dict[str, int]) -> dict:
    """Import the drsamonline CC0 JSON dataset driven by its catalog.json."""
    catalog = json.loads((ext_root / "catalog.json").read_text(encoding="utf-8"))
    trusted = set(alias)
    pool = build_fuzzy_pool(alias)
    stats: dict[str, int] = {"books": 0, "catalog_books": len(catalog.get("books", []))}
    importers = {"repertory": import_repertory_book, "materia_medica": import_mm_book,
                 "treatise": import_treatise_book}
    for book in catalog.get("books", []):
        importer = importers.get(book.get("type"))
        if not importer:
            continue
        for key, value in importer(conn, ext_root, book, alias, trusted, pool).items():
            stats[key] = stats.get(key, 0) + value
        stats["books"] += 1
    return stats


def import_repository_catalog(conn: sqlite3.Connection, path: Path) -> int:
    """Load verified repository/source pointers into source_catalog."""
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    sid = ensure_source(conn, "Curated repository catalog", "data/repository_catalog.json",
                        attribution=f"Curated by this project; URLs verified {payload.get('generated', 'unknown date')}",
                        license_name="reference metadata only",
                        notes=str(payload.get("note", "")))
    count = 0
    for entry in payload.get("entries", []):
        link_id = None
        if entry.get("source"):
            row = conn.execute("SELECT id FROM sources WHERE name=?", (entry["source"],)).fetchone()
            if row:
                link_id = row[0]
            else:
                print(f"note: repository catalog entry '{entry.get('name')}' references unknown source "
                      f"'{entry['source']}'; left unlinked")
        conn.execute(
            "INSERT INTO source_catalog(name,source_type,url,license_claim,review_status,relevance,source_id) VALUES(?,?,?,?,?,?,?)",
            (entry.get("name", ""), entry.get("source_type", ""), entry.get("url", ""),
             entry.get("license_claim", ""), entry.get("review_status", "needs verification"),
             entry.get("relevance", ""), link_id))
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DB, help="SQLite output path")
    parser.add_argument("--search-export", type=Path, default=DEFAULT_SEARCH_EXPORT,
                        help="Compressed browser-search export path")
    parser.add_argument("--remedia", type=Path, default=ROOT.parent / "remedia-homeopathy-database.json",
                        help="Remedia catalog JSON (skipped when absent)")
    parser.add_argument("--ext-repo", type=Path, default=ROOT.parent / "ext_hommr_json",
                        help="drsamonline JSON checkout with catalog.json (skipped when absent)")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    global _REMEDY_CACHE, _FUZZY_CACHE
    _REMEDY_CACHE = {}
    _FUZZY_CACHE = {}
    conn = sqlite3.connect(args.output)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-200000")
    conn.executescript(SCHEMA)
    conn.execute("BEGIN")
    try:
        source_ids: dict[str, int] = {}
        import_stats: dict[str, int] = {}
        build_notes: list[str] = []
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
        # tautopathy "repositories" placeholders are replaced by data/repository_catalog.json below.

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

        # External local datasets: Remedia catalog, drsamonline repertory/MM/treatise
        # JSON, and the curated repository catalog. Each is skipped with a note
        # when its input file is absent so the build stays reproducible.
        alias = build_alias(conn)
        if args.remedia.exists():
            stats = import_remedia(conn, args.remedia, alias)
            for key, value in stats.items():
                import_stats[f"remedia_{key}"] = import_stats.get(f"remedia_{key}", 0) + value
        else:
            build_notes.append(f"Remedia catalog not found at {args.remedia}; skipped")
        if (args.ext_repo / "catalog.json").exists():
            stats = import_ext_dataset(conn, args.ext_repo, alias)
            for key, value in stats.items():
                import_stats[f"ext_{key}"] = import_stats.get(f"ext_{key}", 0) + value
        else:
            build_notes.append(f"drsamonline JSON checkout not found at {args.ext_repo}; skipped")
        import_stats["repository_catalog"] = import_repository_catalog(conn, DATA / "repository_catalog.json")

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
              for table in ("remedies", "materia_medica", "rubrics", "rubric_remedies", "modalities",
                            "reference_documents", "source_catalog", "sources", "search_index")}
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
                      "search_records": len(export), "counts": counts,
                      "import_stats": import_stats, "build_notes": build_notes}, indent=2))


if __name__ == "__main__":
    main()
    # refresh the recommender's data/db_rag.js + data/db_mm.js from the fresh DB
    import subprocess
    import sys
    subprocess.run([sys.executable, str(Path(__file__).with_name("build_recommender_db.py"))], check=True)
