import ipaddress
import re
import secrets
import socket
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from .config import ALLOWED_PRIVATE_HOSTS, MAX_PLAYLIST_BYTES, PROXY_TOKEN_TTL_SECONDS, UPSTREAM_TIMEOUT_SECONDS
from .database import connection

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_URI_ATTRIBUTE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)
_ALLOWED_SCHEMES = {"http", "https"}
_FORWARD_HEADERS = ("range", "if-range", "user-agent", "accept", "accept-language")
_RESPONSE_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag")


def _public_addresses(host: str, port: int) -> set[str]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Não foi possível resolver o host: {host}") from exc
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    if not addresses:
        raise ValueError("Host sem endereço IP")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"Destino não público bloqueado: {address}")
    return addresses


def validate_public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ValueError("Somente HTTP e HTTPS são permitidos")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("Host inválido ou credenciais embutidas")
        if parsed.fragment:
            parsed = parsed._replace(fragment="")
        host = parsed.hostname.rstrip(".").lower()
        explicitly_allowed_private = host in ALLOWED_PRIVATE_HOSTS
        if (
            not explicitly_allowed_private
            and (
                host in {"localhost", "localhost.localdomain"}
                or host.endswith((".localhost", ".local", ".internal", ".lan", ".home"))
            )
        ):
            raise ValueError("Host local não permitido; configure MDE_ALLOWED_PRIVATE_HOSTS apenas para testes controlados")
        port = parsed.port or (443 if scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError("Porta inválida")
        try:
            addresses = _public_addresses(host, port)
        except ValueError:
            if not explicitly_allowed_private:
                raise
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = {record[4][0].split("%", 1)[0] for record in records}
            if not addresses:
                raise ValueError("Host permitido sem endereço IP")
            for address in addresses:
                ip = ipaddress.ip_address(address)
                if (
                    not (ip.is_private or ip.is_loopback or ip.is_link_local)
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    raise ValueError("Allowlist privada não permite endereços reservados ou públicos")
        return urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
        return urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"URL de mídia bloqueada ou inválida: {exc}"
        ) from exc


def resolve_source_url(base_url: str, source_path: str) -> str:
    path = source_path.strip()
    parsed_path = urlsplit(path)
    decoded_path = unquote(parsed_path.path)
    if (
        not path
        or parsed_path.scheme
        or parsed_path.netloc
        or path.startswith(("//", "\\\\"))
        or "\x00" in path
        or "\\" in decoded_path
        or any(part == ".." for part in decoded_path.split("/"))
    ):
        raise HTTPException(
            status_code=400,
            detail="Caminho da mídia deve ser relativo e não pode sair da fonte cadastrada",
        )
    return validate_public_url(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    )


def create_resource_token(media_id: int, upstream_url: str, owner_username: str = "") -> str:
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + PROXY_TOKEN_TTL_SECONDS
    with connection() as db:
        db.execute(
            "DELETE FROM proxy_resources WHERE expires_at < ?",
            (int(time.time()),),
        )
        db.execute(
            """INSERT INTO proxy_resources(token,media_id,upstream_url,expires_at,owner_username)
               VALUES (?,?,?,?,?)""",
            (token, media_id, upstream_url, expires, owner_username),
        )
    return token


def get_resource_token(token: str, media_id: int, owner_username: str | None = None) -> str:
    with connection() as db:
        row = db.execute(
            """SELECT upstream_url,expires_at,owner_username
               FROM proxy_resources WHERE token=? AND media_id=?""",
            (token, media_id),
        ).fetchone()
    if (
        not row
        or row["expires_at"] < int(time.time())
        or (row["owner_username"] and row["owner_username"] != (owner_username or ""))
    ):
        raise HTTPException(status_code=404, detail="Recurso de proxy expirado ou inexistente")
    return validate_public_url(row["upstream_url"])


def rewrite_hls_playlist(
    text: str,
    base_url: str,
    media_id: int,
    request_base: str,
    username: str,
    password: str,
) -> str:
    output = []
    safe_user = quote(username, safe="")
    safe_password = quote(password, safe="")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            output.append(line)
            continue

        def proxy_url(reference: str) -> str:
            absolute = validate_public_url(urljoin(base_url, reference.strip()))
            token = create_resource_token(media_id, absolute, username)
            return (
                f"{request_base}/play-resource/{safe_user}/{safe_password}/"
                f"{media_id}/{token}"
            )

        if stripped.startswith("#"):
            output.append(
                _URI_ATTRIBUTE.sub(lambda match: f'URI="{proxy_url(match.group(1))}"', line)
            )
        else:
            output.append(proxy_url(stripped))
    return "\n".join(output) + "\n"


async def _open_upstream(
    url: str,
    headers: dict[str, str],
    method: str = "GET",
):
    timeout = httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS, connect=min(10.0, UPSTREAM_TIMEOUT_SECONDS))
    client = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        trust_env=False,
    )
    current = validate_public_url(url)
    try:
        for _ in range(6):
            request = client.build_request(method, current, headers=headers)
            response = await client.send(request, stream=True)
            if response.status_code in _REDIRECT_CODES:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise HTTPException(
                        status_code=502,
                        detail="Redirecionamento upstream sem destino",
                    )
                current = validate_public_url(urljoin(current, location))
                continue
            return client, response, current
        raise HTTPException(status_code=502, detail="Limite de redirecionamentos upstream excedido")
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Falha ao acessar a fonte: {exc}") from exc
    except Exception:
        await client.aclose()
        raise


async def deliver_redirect(upstream_url: str):
    safe_url = validate_public_url(upstream_url)
    return RedirectResponse(url=safe_url, status_code=302)


async def deliver_proxy(
    upstream_url: str,
    media_id: int,
    request_base: str,
    username: str,
    password: str,
    incoming_headers: dict[str, str],
    method: str = "GET",
):
    forward_headers = {
        name: incoming_headers[name]
        for name in _FORWARD_HEADERS
        if name in incoming_headers
    }
    client, upstream, final_url = await _open_upstream(
        upstream_url, forward_headers, method
    )
    content_type = (
        upstream.headers.get("content-type", "application/octet-stream")
        .split(";", 1)[0].strip().lower()
    )

    if method == "HEAD":
        headers = {
            name: upstream.headers[name]
            for name in _RESPONSE_HEADERS
            if name in upstream.headers
        }
        status_code = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        return Response(status_code=status_code, headers=headers)

    is_playlist = (
        "mpegurl" in content_type
        or final_url.lower().split("?", 1)[0].endswith((".m3u8", ".m3u"))
    )
    if is_playlist:
        try:
            chunks = []
            size = 0
            async for chunk in upstream.aiter_bytes():
                size += len(chunk)
                if size > MAX_PLAYLIST_BYTES:
                    raise HTTPException(
                        status_code=502,
                        detail="Playlist upstream excede o limite permitido",
                    )
                chunks.append(chunk)
            body = b"".join(chunks)
            try:
                source_text = body.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    status_code=502, detail="Playlist upstream não está em UTF-8"
                ) from exc
            rewritten = rewrite_hls_playlist(
                source_text,
                final_url,
                media_id,
                request_base,
                username,
                password,
            )
            return Response(
                content=rewritten,
                status_code=upstream.status_code,
                headers={"Cache-Control": "no-store"},
                media_type="application/vnd.apple.mpegurl",
            )
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {
        name: upstream.headers[name]
        for name in _RESPONSE_HEADERS
        if name in upstream.headers
    }
    response_headers["Cache-Control"] = "no-store"

    async def stream_body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_body(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
