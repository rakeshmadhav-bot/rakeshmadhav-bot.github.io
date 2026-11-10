CREATE TABLE IF NOT EXISTS search_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_type TEXT NOT NULL,
  item_id TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  license TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_documents_fts USING fts5(
  title, body, source, content='search_documents', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS search_documents_ai AFTER INSERT ON search_documents BEGIN
  INSERT INTO search_documents_fts(rowid,title,body,source) VALUES(new.id,new.title,new.body,new.source);
END;
CREATE TRIGGER IF NOT EXISTS search_documents_ad AFTER DELETE ON search_documents BEGIN
  INSERT INTO search_documents_fts(search_documents_fts,rowid,title,body,source)
    VALUES('delete',old.id,old.title,old.body,old.source);
END;
CREATE INDEX IF NOT EXISTS idx_search_documents_type ON search_documents(item_type);
