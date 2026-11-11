#!/usr/bin/env python3
"""Create small SQL batches for uploading the local search index to Cloudflare D1.

Run from the repository root after creating/configuring the D1 database:
  python scripts/import_d1_search.py --execute
Without --execute, SQL batches are written under worker/seed/ for inspection.
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database" / "homeopathy.sqlite"
OUT = ROOT / "worker" / "seed"
TARGET_BYTES = 1_000_000


def quote(value: object) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Run generated batches against the configured remote D1 database")
    parser.add_argument("--database", default="homeopathy-library", help="D1 database name in wrangler config")
    args = parser.parse_args()
    if not DB.exists():
        raise SystemExit(f"Missing {DB}; build the local database first.")
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("batch-*.sql"):
        old.unlink()
    import sqlite3
    conn = sqlite3.connect(DB)
    cursor = conn.execute("""
      SELECT s.item_type,s.item_id,s.title,s.body,
        COALESCE(src.name,''),COALESCE(mm.url,rd.url,''),COALESCE(src.license,'')
      FROM search_index s
      LEFT JOIN materia_medica mm ON s.item_type='materia_medica' AND CAST(s.item_id AS INTEGER)=mm.id
      LEFT JOIN rubrics r ON s.item_type='rubric' AND CAST(s.item_id AS INTEGER)=r.id
      LEFT JOIN reference_documents rd ON s.item_type IN ('reference_document','inventory') AND CAST(s.item_id AS INTEGER)=rd.id
      LEFT JOIN remedies rem ON s.item_type='remedy' AND CAST(s.item_id AS INTEGER)=rem.id
      LEFT JOIN sources src ON src.id=COALESCE(mm.source_id,r.source_id,rd.source_id,
        (SELECT MIN(rs.source_id) FROM remedy_sources rs WHERE rs.remedy_id=rem.id))
      ORDER BY s.rowid
    """)
    batch, size, index = ["DELETE FROM search_documents;\n"], len("DELETE FROM search_documents;\n"), 0
    paths = []
    for row in cursor:
        vals = ",".join(quote(v) for v in row)
        statement = f"INSERT INTO search_documents(item_type,item_id,title,body,source,url,license) VALUES({vals});\n"
        if batch and size + len(statement.encode("utf-8")) > TARGET_BYTES:
            index += 1
            path = OUT / f"batch-{index:04d}.sql"
            path.write_text("".join(batch), encoding="utf-8")
            paths.append(path)
            batch, size = [], 0
        batch.append(statement)
        size += len(statement.encode("utf-8"))
    if batch:
        index += 1
        path = OUT / f"batch-{index:04d}.sql"
        path.write_text("".join(batch), encoding="utf-8")
        paths.append(path)
    conn.close()
    print(f"Prepared {len(paths)} SQL batches for {cursor.rowcount if cursor.rowcount >= 0 else 'search'} rows under {OUT}")
    if args.execute:
        if not shutil.which("npx"):
            raise SystemExit("npx was not found; install Node.js/npm and rerun.")
        for i, path in enumerate(paths, 1):
            cmd = ["npx", "wrangler", "d1", "execute", args.database, "--remote", "--file", str(path)]
            print(f"Uploading batch {i}/{len(paths)}: {path.name}", flush=True)
            subprocess.run(cmd, cwd=ROOT / "worker", check=True)

if __name__ == "__main__":
    main()
