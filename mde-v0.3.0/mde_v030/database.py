import sqlite3
from contextlib import contextmanager

from .config import BOOTSTRAP_PASSWORD, BOOTSTRAP_USER, DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 username TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1,
 expires_at TEXT,
 max_connections INTEGER NOT NULL DEFAULT 1 CHECK(max_connections >= 0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS categories (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 media_type TEXT NOT NULL CHECK(media_type IN ('live','movie','series')),
 UNIQUE(name, media_type)
);
CREATE TABLE IF NOT EXISTS sources (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 base_url TEXT NOT NULL,
 delivery_mode TEXT NOT NULL DEFAULT 'redirect' CHECK(delivery_mode IN ('redirect','proxy')),
 enabled INTEGER NOT NULL DEFAULT 1,
 manifest_path TEXT NOT NULL DEFAULT '',
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
 sort_order INTEGER NOT NULL DEFAULT 0,
 parent_id INTEGER REFERENCES media_items(id) ON DELETE CASCADE,
 season_number INTEGER,
 episode_number INTEGER,
 plot TEXT NOT NULL DEFAULT '',
 cover_url TEXT NOT NULL DEFAULT '',
 release_date TEXT NOT NULL DEFAULT '',
 duration TEXT NOT NULL DEFAULT '',
 rating TEXT NOT NULL DEFAULT '',
 epg_channel_id TEXT NOT NULL DEFAULT '',
 tvg_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS proxy_resources (
 token TEXT PRIMARY KEY,
 media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
 upstream_url TEXT NOT NULL,
 expires_at INTEGER NOT NULL,
 owner_username TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_proxy_resources_expiry ON proxy_resources(expires_at);
CREATE INDEX IF NOT EXISTS idx_media_type_category ON media_items(media_type, category_id, enabled);
CREATE INDEX IF NOT EXISTS idx_media_parent_season ON media_items(parent_id, season_number, episode_number);
CREATE TABLE IF NOT EXISTS playback_sessions (
 username TEXT NOT NULL,
 client_key TEXT NOT NULL,
 last_seen INTEGER NOT NULL,
 PRIMARY KEY(username, client_key)
);
CREATE INDEX IF NOT EXISTS idx_playback_seen ON playback_sessions(last_seen);
CREATE TABLE IF NOT EXISTS epg_programmes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 channel_id TEXT NOT NULL,
 title TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '',
 start_at TEXT NOT NULL,
 end_at TEXT NOT NULL,
 category TEXT NOT NULL DEFAULT '',
 UNIQUE(channel_id, start_at, end_at)
);
CREATE INDEX IF NOT EXISTS idx_epg_channel_time ON epg_programmes(channel_id, start_at, end_at);
CREATE TABLE IF NOT EXISTS collection_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source_id INTEGER NOT NULL REFERENCES sources(id),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','completed','failed')),
 total_items INTEGER NOT NULL DEFAULT 0,
 imported_items INTEGER NOT NULL DEFAULT 0,
 skipped_items INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_collection_jobs_status ON collection_jobs(status, id);
"""

_MIGRATIONS = {
    "users": {
        "max_connections": "INTEGER NOT NULL DEFAULT 1",
        "created_at": "TEXT NOT NULL DEFAULT ''",
    },
    "sources": {
        "manifest_path": "TEXT NOT NULL DEFAULT ''",
    },
    "media_items": {
        "parent_id": "INTEGER REFERENCES media_items(id) ON DELETE CASCADE",
        "season_number": "INTEGER",
        "episode_number": "INTEGER",
        "plot": "TEXT NOT NULL DEFAULT ''",
        "cover_url": "TEXT NOT NULL DEFAULT ''",
        "release_date": "TEXT NOT NULL DEFAULT ''",
        "duration": "TEXT NOT NULL DEFAULT ''",
        "rating": "TEXT NOT NULL DEFAULT ''",
        "epg_channel_id": "TEXT NOT NULL DEFAULT ''",
        "tvg_id": "TEXT NOT NULL DEFAULT ''",
    },
    "proxy_resources": {
        "owner_username": "TEXT NOT NULL DEFAULT ''",
    },
}


@contextmanager
def connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    with connection() as db:
        db.executescript(SCHEMA)
        for table, columns in _MIGRATIONS.items():
            existing = {
                row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            from .security import hash_password
            db.execute(
                "INSERT INTO users(username,password_hash) VALUES (?,?)",
                (BOOTSTRAP_USER, hash_password(BOOTSTRAP_PASSWORD)),
            )
