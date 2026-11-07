import ipaddress
import re
import secrets
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse, Response

from .config import MAX_PLAYLIST_BYTES, PROXY_TOKEN_TTL_SECONDS, UPSTREAM_TIMEOUT_SECONDS
from .database import connection

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_HLS_CONTENT_TYPES = {"application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl"}
_URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)
_ALLOWED_SCHEMES = {"http", "https"}


def validate_public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
            raise ValueError("Protocolo não permitido")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Host inválido ou credenciais embutidas")
        if parsed.port and not 1 <= parsed.port <= 65535:
            raise ValueError("Porta inválida")
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal", ".lan")):
            raise ValueError("Host local não permitido")
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
        if not addresses:
            raise ValueError("Host sem endereço IP")
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
            if not ip.is_global:
                raise ValueError("Destino não público bloqueado")
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"URL de mídia bloqueada ou inválida: {exc}") from exc


def resolve_source_url(base_url: str, source_path: str) -> str:
    path = source_path.strip()
    parsed_path = urlsplit(path)
    if not path or parsed_path.scheme or parsed_path.netloc or path.startswith(("//", "\\\\")) or "\x00" in path:
        raise HTTPException(status_code=400, detail="Caminho da mídia deve ser relativo à fonte cadastrada")
    return validate_public_url(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")))


def create_resource_token(media_id: int, upstream_url: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + PROXY_TOKEN_TTL_SECONDS
    with connection() as db:
        db.execute("DELETE FROM proxy_resources WHERE expires_at < ?", (int(time.time()),))
        db.execute("INSERT INTO proxy_resources(token,media_id,upstream_url,expires_at) VALUES (?,?,?,?)",
                   (token, media_id, upstream_url, expires))
    return token


def get_resource_token(token: str, media_id: int) -> str:
    with connection() as db:
        row = db.execute("SELECT upstream_url,expires_at FROM proxy_resources WHERE token=? AND media_id=?",
                         (token, media_id)).fetchone()
    if not row or row["expires_at"] < int(time.time()):
        raise HTTPException(status_code=404, detail="Recurso de proxy expirado ou inexistente")
    return validate_public_url(row["upstream_url"])


def rewrite_hls_playlist(text: str, base_url: str, media_id: int, request_base: str,
                          username: str, password: str) -> str:
    output = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            output.append(line)
            continue
        if stripped.startswith("#"):
            def replace_uri(match):
                absolute = validate_public_url(urljoin(base_url, match.group(1)))
                token = create_resource_token(media_id, absolute)
                return f'URI="{request_base}/play-resource/{username}/{password}/{media_id}/{token}"'
            output.append(_URI_ATTRIBUTE.sub(replace_uri, line))
        else:
            absolute = validate_public_url(urljoin(base_url, stripped))
            token = create_resource_token(media_id, absolute)
            output.append(f"{request_base}/play-resource/{username}/{password}/{media_id}/{token}")
    return "\n".join(output) + "\n"


async def _open_upstream(url: str, headers: dict[str, str]):
    client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS, follow_redirects=False)
    current = validate_public_url(url)
    try:
        for _ in range(6):
            request = client.build_request("GET", current, headers=headers)
            response = await client.send(request, stream=True)
            if response.status_code in _REDIRECT_CODES:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise HTTPException(status_code=502, detail="Redirecionamento upstream sem destino")
                current = validate_public_url(urljoin(current, location))
                continue
            return client, response, current
        raise HTTPException(status_code=502, detail="Limite de redirecionamentos upstream excedido")
    except Exception:
        await client.aclose()
        raise


async def deliver_redirect(upstream_url: str):
    safe_url = validate_public_url(upstream_url)
    return RedirectResponse(url=safe_url, status_code=302)


async def deliver_proxy(upstream_url: str, media_id: int, request_base: str,
                        username: str, password: str, incoming_headers: dict[str, str]):
    forward_headers = {}
    for name in ("range", "if-range", "user-agent", "accept", "accept-language"):
        if name in incoming_headers:
            forward_headers[name] = incoming_headers[name]
    client, upstream, final_url = await _open_upstream(upstream_url, forward_headers)
    content_type = upstream.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
    is_playlist = ("mpegurl" in content_type or final_url.lower().split("?", 1)[0].endswith((".m3u8", ".m3u")))
    if is_playlist:
        try:
            body = await upstream.aread()
            if len(body) > MAX_PLAYLIST_BYTES:
                raise HTTPException(status_code=502, detail="Playlist upstream excede o limite permitido")
            rewritten = rewrite_hls_playlist(body.decode("utf-8-sig"), final_url, media_id,
                                               request_base, username, password)
            headers = {"Cache-Control": "no-store", "Content-Type": "application/vnd.apple.mpegurl"}
            return Response(content=rewritten, status_code=upstream.status_code, headers=headers,
                            media_type="application/vnd.apple.mpegurl")
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {}
    for name in ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag"):
        if name in upstream.headers:
            response_headers[name] = upstream.headers[name]
    response_headers["Cache-Control"] = "no-store"

    async def stream_body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream_body(), status_code=upstream.status_code, headers=response_headers)
