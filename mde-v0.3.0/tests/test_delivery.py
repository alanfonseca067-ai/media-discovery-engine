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


def test_proxy_stream_preserves_range_response_headers(monkeypatch):
    import anyio
    from fastapi.responses import StreamingResponse

    class FakeUpstream:
        status_code = 206
        headers = {
            "content-type": "video/mp2t",
            "content-length": "4",
            "content-range": "bytes 10-13/100",
            "accept-ranges": "bytes",
        }

        async def aiter_bytes(self):
            yield b"TS"
            yield b"01"

        async def aclose(self):
            return None

    class FakeClient:
        async def aclose(self):
            return None

    async def fake_open(url, headers, method="GET"):
        assert headers["range"] == "bytes=10-13"
        return FakeClient(), FakeUpstream(), url

    monkeypatch.setattr(delivery, "_open_upstream", fake_open)
    response = anyio.run(
        lambda: delivery.deliver_proxy(
            "https://media.example/video.ts",
            1,
            "https://mde.example",
            "demo",
            "secret",
            {"range": "bytes=10-13"},
        )
    )
    assert isinstance(response, StreamingResponse)
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 10-13/100"
    body = anyio.run(lambda: _consume_stream(response))
    assert body == b"TS01"


async def _consume_stream(response):
    return b"".join([chunk async for chunk in response.body_iterator])


def test_proxy_hls_response_rewrites_segments_and_key_urls(tmp_path, monkeypatch):
    import anyio

    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "proxy-hls.sqlite3")
    database.init_db()
    with database.connection() as db:
        source = db.execute(
            "INSERT INTO sources(name,base_url,delivery_mode,enabled) VALUES (?,?,?,1)",
            ("proxy test", "https://upstream.example", "proxy"),
        )
        db.execute(
            "INSERT INTO media_items(id,title,media_type,source_id,source_path,enabled) VALUES (99,?,?,?,?,1)",
            ("HLS test", "live", source.lastrowid, "master.m3u8"),
        )
    monkeypatch.setattr(delivery, "validate_public_url", lambda value: value)

    class FakeUpstream:
        status_code = 200
        headers = {"content-type": "application/vnd.apple.mpegurl"}

        async def aiter_bytes(self):
            yield b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nsegment.ts\n'

        async def aclose(self):
            return None

    class FakeClient:
        async def aclose(self):
            return None

    async def fake_open(url, headers, method="GET"):
        return FakeClient(), FakeUpstream(), url

    monkeypatch.setattr(delivery, "_open_upstream", fake_open)
    response = anyio.run(
        lambda: delivery.deliver_proxy(
            "https://upstream.example/master.m3u8",
            99,
            "https://mde.example",
            "demo",
            "secret",
            {},
        )
    )
    assert response.media_type == "application/vnd.apple.mpegurl"
    assert "upstream.example" not in response.body.decode()
    assert response.body.decode().count("/play-resource/demo/secret/99/") == 2
