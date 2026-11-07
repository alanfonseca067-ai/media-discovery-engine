import re

import pytest
from fastapi import HTTPException

from mde_v030 import database, delivery


def test_private_ip_destinations_are_rejected():
    with pytest.raises(HTTPException) as error:
        delivery.validate_public_url("http://127.0.0.1/private")
    assert error.value.status_code == 400


def test_source_path_cannot_override_registered_host():
    with pytest.raises(HTTPException) as error:
        delivery.resolve_source_url("https://media.example", "https://attacker.example/stream.m3u8")
    assert error.value.status_code == 400


def test_source_path_rejects_network_path_reference():
    with pytest.raises(HTTPException):
        delivery.resolve_source_url("https://media.example", "//attacker.example/stream.m3u8")


def test_hls_rewrite_uses_persisted_opaque_resource_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "mde-test.sqlite3")
    database.init_db()
    with database.connection() as db:
        source = db.execute(
            "INSERT INTO sources(name,base_url,delivery_mode,enabled) VALUES (?,?,?,1)",
            ("test", "https://upstream.example", "proxy"),
        )
        db.execute(
            "INSERT INTO media_items(id,title,media_type,source_id,source_path,enabled) VALUES (42,?,?,?,?,1)",
            ("test media", "live", source.lastrowid, "master.m3u8"),
        )
    monkeypatch.setattr(delivery, "validate_public_url", lambda value: value)

    rewritten = delivery.rewrite_hls_playlist(
        '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="keys/key.bin"\nsegment001.ts\n',
        "https://upstream.example/live/master.m3u8",
        42,
        "https://mde.example",
        "demo",
        "secret",
    )

    assert "upstream.example" not in rewritten
    tokens = re.findall(r"/play-resource/demo/secret/42/([A-Za-z0-9_-]+)", rewritten)
    assert len(tokens) == 2
    assert len(set(tokens)) == 2
    for token in tokens:
        assert delivery.get_resource_token(token, 42, "demo").startswith("https://upstream.example/")


def test_source_path_rejects_parent_directory_escape():
    with pytest.raises(HTTPException) as error:
        delivery.resolve_source_url("https://media.example/base", "../outside/stream.m3u8")
    assert error.value.status_code == 400


def test_proxy_resource_token_is_bound_to_user(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "bound-token.sqlite3")
    database.init_db()
    with database.connection() as db:
        source = db.execute(
            "INSERT INTO sources(name,base_url,delivery_mode,enabled) VALUES (?,?,?,1)",
            ("test", "https://upstream.example", "proxy"),
        )
        db.execute(
            "INSERT INTO media_items(id,title,media_type,source_id,source_path,enabled) VALUES (7,?,?,?,?,1)",
            ("private token test", "live", source.lastrowid, "live/master.m3u8"),
        )
    monkeypatch.setattr(delivery, "validate_public_url", lambda value: value)
    token = delivery.create_resource_token(7, "https://upstream.example/segment.ts", "alice")
    assert delivery.get_resource_token(token, 7, "alice") == "https://upstream.example/segment.ts"
    with pytest.raises(HTTPException) as error:
        delivery.get_resource_token(token, 7, "bob")
    assert error.value.status_code == 404
