import pytest
from fastapi.testclient import TestClient

from mde_v030 import database, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "catalog-test.sqlite3")
    monkeypatch.setattr(main, "ADMIN_KEY", "test-admin-secret")
    database.init_db()
    with TestClient(main.app) as test_client:
        yield test_client


def test_xtream_login_categories_streams_and_m3u(client):
    headers = {"X-MDE-Admin-Key": "test-admin-secret"}
    source = client.post(
        "/v1/admin/sources",
        headers=headers,
        json={
            "name": "Authorized test source",
            "base_url": "https://media.example.net",
            "delivery_mode": "proxy",
            "enabled": True,
        },
    )
    assert source.status_code == 200
    source_id = source.json()["id"]

    category = client.post(
        "/v1/admin/categories",
        headers=headers,
        json={"name": "Test channels", "media_type": "live"},
    )
    assert category.status_code == 200
    category_id = category.json()["id"]

    media = client.post(
        "/v1/admin/media",
        headers=headers,
        json={
            "title": "Authorized HLS test channel",
            "media_type": "live",
            "source_id": source_id,
            "source_path": "live/test/master.m3u8",
            "category_id": category_id,
        },
    )
    assert media.status_code == 200
    media_id = media.json()["id"]

    login = client.get(
        "/player_api.php",
        params={"username": "demo", "password": "change-this-password"},
    )
    assert login.status_code == 200
    assert login.json()["user_info"]["auth"] == 1

    categories = client.get(
        "/player_api.php",
        params={
            "username": "demo",
            "password": "change-this-password",
            "action": "get_live_categories",
        },
    )
    assert categories.status_code == 200
    assert categories.json()[0]["category_id"] == str(category_id)

    streams = client.get(
        "/player_api.php",
        params={
            "username": "demo",
            "password": "change-this-password",
            "action": "get_live_streams",
        },
    )
    assert streams.status_code == 200
    assert streams.json()[0]["stream_id"] == media_id

    playlist = client.get(
        "/get.php",
        params={"username": "demo", "password": "change-this-password"},
    )
    assert playlist.status_code == 200
    assert playlist.text.startswith("#EXTM3U")
    assert f"/live/demo/change-this-password/{media_id}.ts" in playlist.text


def test_admin_key_is_required_for_catalog_mutations(client):
    response = client.post(
        "/v1/admin/categories",
        json={"name": "No access", "media_type": "live"},
    )
    assert response.status_code == 403


def test_expired_user_is_rejected(client):
    with database.connection() as db:
        db.execute(
            "UPDATE users SET expires_at='2000-01-01T00:00:00+00:00' WHERE username='demo'"
        )
    response = client.get(
        "/player_api.php",
        params={"username": "demo", "password": "change-this-password"},
    )
    assert response.status_code == 401


def test_xtream_vod_info_and_category_filter(client):
    headers = {"X-MDE-Admin-Key": "test-admin-secret"}
    source = client.post(
        "/v1/admin/sources",
        headers=headers,
        json={"name": "VOD source", "base_url": "https://vod.example.net", "delivery_mode": "redirect"},
    ).json()
    category = client.post(
        "/v1/admin/categories",
        headers=headers,
        json={"name": "Movies", "media_type": "movie"},
    ).json()
    movie = client.post(
        "/v1/admin/media",
        headers=headers,
        json={
            "title": "Test movie", "media_type": "movie",
            "source_id": source["id"], "source_path": "movie/test.mp4",
            "category_id": category["id"],
        },
    ).json()

    filtered = client.get(
        "/player_api.php",
        params={
            "username": "demo", "password": "change-this-password",
            "action": "get_vod_streams", "category_id": category["id"],
        },
    )
    assert filtered.status_code == 200
    assert [entry["stream_id"] for entry in filtered.json()] == [movie["id"]]

    info = client.get(
        "/player_api.php",
        params={
            "username": "demo", "password": "change-this-password",
            "action": "get_vod_info", "vod_id": movie["id"],
        },
    )
    assert info.status_code == 200
    assert info.json()["movie_data"]["stream_id"] == movie["id"]
