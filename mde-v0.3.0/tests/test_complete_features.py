from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from fastapi.testclient import TestClient

from mde_v030 import database, main, worker


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "complete-test.sqlite3")
    monkeypatch.setattr(main, "ADMIN_KEY", "test-admin-secret")
    database.init_db()
    with TestClient(main.app) as test_client:
        yield test_client


def admin_headers():
    return {"X-MDE-Admin-Key": "test-admin-secret"}


def create_source(client, media_type="live"):
    response = client.post(
        "/v1/admin/sources",
        headers=admin_headers(),
        json={
            "name": "Test source",
            "base_url": "https://media.example.test/catalog",
            "delivery_mode": "proxy",
            "manifest_path": "channels.m3u",
            "catalog_media_type": media_type,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_series_seasons_and_episode_routes(client):
    source_id = create_source(client)
    category = client.post(
        "/v1/admin/categories",
        headers=admin_headers(),
        json={"name": "Drama", "media_type": "series"},
    ).json()
    series = client.post(
        "/v1/admin/media",
        headers=admin_headers(),
        json={
            "title": "A Test Series",
            "media_type": "series",
            "source_id": source_id,
            "source_path": "metadata/series",
            "category_id": category["id"],
            "plot": "Series synopsis",
        },
    )
    assert series.status_code == 200, series.text
    series_id = series.json()["id"]

    episode = client.post(
        "/v1/admin/media",
        headers=admin_headers(),
        json={
            "title": "Pilot",
            "media_type": "episode",
            "source_id": source_id,
            "source_path": "series/1/1.m3u8",
            "series_id": series_id,
            "season_number": 1,
            "episode_number": 1,
            "plot": "First episode",
        },
    )
    assert episode.status_code == 200, episode.text

    listed = client.get(
        "/player_api.php",
        params={
            "username": "demo",
            "password": "change-this-password",
            "action": "get_series",
        },
    )
    assert listed.status_code == 200
    assert listed.json()[0]["stream_id"] == series_id

    details = client.get(
        "/player_api.php",
        params={
            "username": "demo",
            "password": "change-this-password",
            "action": "get_series_info",
            "series_id": series_id,
        },
    )
    assert details.status_code == 200
    assert details.json()["seasons"][0]["season_number"] == 1
    assert details.json()["episodes"]["1"][0]["title"] == "Pilot"
    assert details.json()["episodes"]["1"][0]["id"] == episode.json()["id"]


def test_admin_user_creation_expiry_and_connection_limit(client):
    user = client.post(
        "/v1/admin/users",
        headers=admin_headers(),
        json={
            "username": "device-user",
            "password": "a-strong-test-password",
            "max_connections": 1,
        },
    )
    assert user.status_code == 200, user.text
    assert client.get(
        "/player_api.php",
        params={"username": "device-user", "password": "a-strong-test-password"},
    ).status_code == 200

    account = main.authenticate("device-user", "a-strong-test-password")
    scope_a = {
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": [(b"user-agent", b"Player A")],
        "server": ("testserver", 80), "client": ("10.0.0.1", 1234),
    }
    scope_b = dict(scope_a)
    scope_b["client"] = ("10.0.0.2", 1234)
    main.touch_playback_session("device-user", account, Request(scope_a))
    with pytest.raises(HTTPException) as error:
        main.touch_playback_session("device-user", account, Request(scope_b))
    assert error.value.status_code == 429

    updated = client.patch(
        "/v1/admin/users/device-user",
        headers=admin_headers(),
        json={"max_connections": 2},
    )
    assert updated.status_code == 200
    updated_account = main.authenticate("device-user", "a-strong-test-password")
    main.touch_playback_session("device-user", updated_account, Request(scope_b))


def test_epg_xmltv_and_short_epg(client):
    source_id = create_source(client)
    category = client.post(
        "/v1/admin/categories",
        headers=admin_headers(),
        json={"name": "News", "media_type": "live"},
    ).json()
    channel = client.post(
        "/v1/admin/media",
        headers=admin_headers(),
        json={
            "title": "News Channel",
            "media_type": "live",
            "source_id": source_id,
            "source_path": "live/news/master.m3u8",
            "category_id": category["id"],
            "epg_channel_id": "news.test",
            "tvg_id": "news.test",
        },
    )
    assert channel.status_code == 200, channel.text

    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    end = start + timedelta(hours=1)
    payload = {
        "channel_id": "news.test",
        "title": "Test bulletin",
        "description": "Programme description",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "category": "News",
    }
    saved = client.post("/v1/admin/epg/programmes", headers=admin_headers(), json=payload)
    assert saved.status_code == 200, saved.text

    xml = client.get(
        "/xmltv.php",
        params={"username": "demo", "password": "change-this-password"},
    )
    assert xml.status_code == 200
    assert "news.test" in xml.text
    assert "Test bulletin" in xml.text

    short = client.get(
        "/player_api.php",
        params={
            "username": "demo",
            "password": "change-this-password",
            "action": "get_short_epg",
            "stream_id": channel.json()["id"],
        },
    )
    assert short.status_code == 200
    assert short.json()["epg_listings"][0]["title"] == "Test bulletin"


def test_collection_worker_imports_only_same_origin_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "worker-test.sqlite3")
    monkeypatch.setattr(
        worker,
        "validate_public_url",
        lambda value: value,
    )
    database.init_db()
    manifest = """#EXTM3U
#EXTINF:-1 tvg-id="channel.one" group-title="News",Channel One
https://media.example.test/catalog/live/channel-one/master.m3u8
#EXTINF:-1 group-title="External",Blocked channel
https://other.example.test/live/blocked.m3u8
"""
    monkeypatch.setattr(
        worker,
        "fetch_catalog",
        lambda base, path: (
            manifest,
            "https://media.example.test/catalog/channels.m3u",
        ),
    )
    with database.connection() as db:
        source = db.execute(
            """INSERT INTO sources(name,base_url,delivery_mode,enabled,manifest_path,catalog_media_type)
               VALUES (?,?, 'proxy',1,?,'live')""",
            ("worker source", "https://media.example.test/catalog", "channels.m3u"),
        )
        job = db.execute(
            "INSERT INTO collection_jobs(source_id,status) VALUES (?,'queued')",
            (source.lastrowid,),
        )
        job_id = job.lastrowid
        retry_job = db.execute(
            "INSERT INTO collection_jobs(source_id,status) VALUES (?,'queued')",
            (source.lastrowid,),
        )
        retry_job_id = retry_job.lastrowid

    result = worker.process_job(job_id)
    assert result["status"] == "completed"
    assert result["total_items"] == 2
    assert result["imported_items"] == 1
    assert result["skipped_items"] == 1

    retry_result = worker.process_job(retry_job_id)
    assert retry_result["status"] == "completed"
    assert retry_result["imported_items"] == 0
    assert retry_result["skipped_items"] == 2

    with database.connection() as db:
        items = db.execute(
            "SELECT title,source_path,epg_channel_id FROM media_items"
        ).fetchall()
    assert len(items) == 1
    assert items[0]["title"] == "Channel One"
    assert items[0]["source_path"] == "live/channel-one/master.m3u8"
    assert items[0]["epg_channel_id"] == "channel.one"


def test_database_migration_adds_columns_to_old_schema(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old-version.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT
        );
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            delivery_mode TEXT NOT NULL DEFAULT 'redirect',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE media_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            media_type TEXT NOT NULL,
            category_id INTEGER,
            source_id INTEGER,
            source_path TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE proxy_resources (
            token TEXT PRIMARY KEY,
            media_id INTEGER NOT NULL,
            upstream_url TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        );
        """
    )
    old.close()
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    database.init_db()
    with database.connection() as db:
        user_cols = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        source_cols = {row["name"] for row in db.execute("PRAGMA table_info(sources)")}
        media_cols = {row["name"] for row in db.execute("PRAGMA table_info(media_items)")}
    assert "max_connections" in user_cols
    assert "catalog_media_type" in source_cols
    assert "season_number" in media_cols


def test_xmltv_import_accepts_registered_channels_only(client):
    source_id = create_source(client)
    client.post(
        "/v1/admin/media",
        headers=admin_headers(),
        json={
            "title": "Import EPG channel",
            "media_type": "live",
            "source_id": source_id,
            "source_path": "live/import/master.m3u8",
            "epg_channel_id": "known.channel",
        },
    )
    start = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y%m%d%H%M%S +0000")
    stop = (datetime.now(timezone.utc) + timedelta(minutes=70)).strftime("%Y%m%d%H%M%S +0000")
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
    <tv>
      <channel id="known.channel"><display-name>Known</display-name></channel>
      <programme channel="known.channel" start="{start}" stop="{stop}">
        <title>Imported show</title><desc>Imported description</desc><category>News</category>
      </programme>
      <programme channel="not.registered" start="{start}" stop="{stop}">
        <title>Must be ignored</title>
      </programme>
    </tv>'''
    response = client.post(
        "/v1/admin/epg/import",
        headers=admin_headers(),
        json={"xml_content": xml},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported_programmes"] == 1
    assert response.json()["ignored_programmes"] == 1


def test_admin_panel_is_available(client):
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Media Discovery Engine" in response.text
    assert "X-MDE-Admin-Key" in response.text


def test_admin_rejects_non_relative_source_and_manifest_paths(client):
    bad_source = client.post(
        "/v1/admin/sources",
        headers=admin_headers(),
        json={
            "name": "Bad source",
            "base_url": "https://media.example.test",
            "manifest_path": "../outside/catalog.m3u",
            "delivery_mode": "proxy",
        },
    )
    assert bad_source.status_code == 422

    source_id = create_source(client)
    bad_media = client.post(
        "/v1/admin/media",
        headers=admin_headers(),
        json={
            "title": "Bad path",
            "media_type": "live",
            "source_id": source_id,
            "source_path": "%2e%2e/private/secret.m3u8",
        },
    )
    assert bad_media.status_code == 422


def test_worker_rejects_encoded_parent_directory_escape(monkeypatch):
    monkeypatch.setattr(worker, "validate_public_url", lambda value: value)
    with pytest.raises(ValueError):
        worker.resolve_catalog_entry(
            "https://media.example.test/catalog",
            "%2e%2e/private/secret.m3u8",
            "https://media.example.test/catalog/list.m3u",
        )
