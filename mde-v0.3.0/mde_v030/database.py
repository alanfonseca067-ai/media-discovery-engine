import sqlite3
from contextlib import contextmanager
from .config import DATABASE_PATH, BOOTSTRAP_USER, BOOTSTRAP_PASSWORD

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 username TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1,
 expires_at TEXT
);
CREATE TABLE IF NOT EXISTS categories (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 media_type TEXT NOT NULL CHECK(media_type IN ('live','movie','series'))
);
CREATE TABLE IF NOT EXISTS sources (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 base_url TEXT NOT NULL,
 delivery_mode TEXT NOT NULL DEFAULT 'redirect' CHECK(delivery_mode IN ('redirect','proxy')),
 enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS media_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 title TEXT NOT NULL,
 media_type TEXT NOT NULL CHECK(media_type IN ('live','movie','series','episode')),
 category_id INTEGER REFERENCES categories(id),
 source_id INTEGER REFERENCES sources(id),
 source_path TEXT NOT NULL DEFAULT '',
 enabled INTEGER NOT NULL DEFAULT 1,
 sort_order INTEGER NOT NULL DEFAULT 0
);
"""

@contextmanager
def connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()

def init_db():
    with connection() as db:
        db.executescript(SCHEMA)
        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            from .security import hash_password
            db.execute("INSERT INTO users(username,password_hash) VALUES (?,?)",
                       (BOOTSTRAP_USER, hash_password(BOOTSTRAP_PASSWORD)))
