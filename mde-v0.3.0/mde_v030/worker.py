import argparse
import posixpath
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .config import COLLECTION_POLL_SECONDS, MAX_COLLECTION_BYTES, UPSTREAM_TIMEOUT_SECONDS
from .database import connection, init_db
from .delivery import validate_public_url


@dataclass(frozen=True)
class PlaylistEntry:
    title: str
    uri: str
    group: str
    tvg_id: str


_ATTRIBUTE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


def parse_m3u(content: str) -> list[PlaylistEntry]:
    entries: list[PlaylistEntry] = []
    pending: tuple[str, str, str] | None = None
    for raw_line in content.lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            metadata, separator, title = line.partition(",")
            attrs = {key.lower(): value for key, value in _ATTRIBUTE.findall(metadata)}
            pending = (
                title.strip() if separator and title.strip() else attrs.get("tvg-name", "Sem título"),
                attrs.get("group-title", "Sem categoria").strip() or "Sem categoria",
                attrs.get("tvg-id", "").strip(),
            )
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            continue
        if "|" in line:
            pending = None
            continue
        title, group, tvg_id = pending
        entries.append(PlaylistEntry(title=title, uri=line, group=group, tvg_id=tvg_id))
        pending = None
    return entries


def _same_origin(left: str, right: str) -> bool:
    a, b = urlsplit(left), urlsplit(right)
    a_port = a.port or (443 if a.scheme == "https" else 80)
    b_port = b.port or (443 if b.scheme == "https" else 80)
    return (
        a.scheme.lower() == b.scheme.lower()
        and (a.hostname or "").lower() == (b.hostname or "").lower()
        and a_port == b_port
        and not b.username
        and not b.password
    )


def resolve_catalog_entry(base_url: str, uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme or parsed.netloc:
        candidate = uri
    else:
        candidate = urljoin(base_url.rstrip("/") + "/", uri)
    safe = validate_public_url(candidate)
    if not _same_origin(base_url, safe):
        raise ValueError("A entrada aponta para outro host ou porta; importação recusada")
    base = urlsplit(base_url)
    target = urlsplit(safe)
    base_path = posixpath.normpath(base.path or "/")
    target_path = posixpath.normpath(target.path or "/")
    prefix = base_path.rstrip("/")
    if prefix and prefix != "/" and not (target_path == prefix or target_path.startswith(prefix + "/")):
        raise ValueError("A entrada está fora do caminho cadastrado para a fonte")
    relative = posixpath.relpath(target_path, prefix if prefix else "/")
    if relative == ".":
        raise ValueError("Entrada sem caminho de mídia")
    return urlunsplit(("", "", relative, target.query, ""))


def fetch_catalog(source_base_url: str, manifest_path: str) -> str:
    manifest_url = validate_public_url(
        urljoin(source_base_url.rstrip("/") + "/", manifest_path.lstrip("/"))
    )
    if not _same_origin(source_base_url, manifest_url):
        raise ValueError("Manifesto deve estar no mesmo host da fonte cadastrada")
    timeout = httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS, connect=min(10.0, UPSTREAM_TIMEOUT_SECONDS))
    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    ) as client:
        current = manifest_url
        for _ in range(6):
            response = client.get(current, headers={"Accept": "application/vnd.apple.mpegurl, audio/x-mpegurl, text/plain"})
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirecionamento de manifesto sem destino")
                current = validate_public_url(urljoin(current, location))
                if not _same_origin(source_base_url, current):
                    raise ValueError("Redirecionamento saiu do host cadastrado")
                continue
            response.raise_for_status()
            body = response.content
            if len(body) > MAX_COLLECTION_BYTES:
                raise ValueError("Manifesto excede o tamanho máximo configurado")
            return body.decode("utf-8-sig")
    raise ValueError("Limite de redirecionamentos do manifesto excedido")


def process_job(job_id: int) -> dict:
    with connection() as db:
        job = db.execute(
            "SELECT id,source_id FROM collection_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if not job:
            raise ValueError("Job de coleta não encontrado")
        source = db.execute(
            """SELECT id,base_url,manifest_path,catalog_media_type,enabled
               FROM sources WHERE id=?""",
            (job["source_id"],),
        ).fetchone()
        if not source or not source["enabled"]:
            db.execute(
                "UPDATE collection_jobs SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                ("Fonte inexistente ou desativada", job_id),
            )
            return {"status": "failed", "error": "Fonte inexistente ou desativada"}
        if not source["manifest_path"]:
            db.execute(
                "UPDATE collection_jobs SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                ("Cadastre manifest_path na fonte para iniciar a coleta", job_id),
            )
            return {"status": "failed", "error": "Manifesto não configurado"}
        db.execute(
            "UPDATE collection_jobs SET status='processing',error='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )
        base_url = source["base_url"]
        manifest_path = source["manifest_path"]
        media_type = source["catalog_media_type"]

    try:
        content = fetch_catalog(base_url, manifest_path)
        entries = parse_m3u(content)
        imported = 0
        skipped = 0
        with connection() as db:
            for entry in entries:
                try:
                    source_path = resolve_catalog_entry(base_url, entry.uri)
                except Exception:
                    skipped += 1
                    continue
                category_name = entry.group[:120] or "Sem categoria"
                category = db.execute(
                    "SELECT id FROM categories WHERE name=? AND media_type=?",
                    (category_name, media_type),
                ).fetchone()
                if category is None:
                    cursor = db.execute(
                        "INSERT INTO categories(name,media_type) VALUES (?,?)",
                        (category_name, media_type),
                    )
                    category_id = cursor.lastrowid
                else:
                    category_id = category["id"]
                before = db.total_changes
                db.execute(
                    """INSERT OR IGNORE INTO media_items
                       (title,media_type,category_id,source_id,source_path,enabled,
                        epg_channel_id,tvg_id)
                       VALUES (?,?,?,?,?,1,?,?)""",
                    (
                        entry.title[:240],
                        media_type,
                        category_id,
                        source["id"],
                        source_path,
                        entry.tvg_id,
                        entry.tvg_id,
                    ),
                )
                imported += int(db.total_changes > before)
                skipped += int(db.total_changes == before)
            db.execute(
                """UPDATE collection_jobs SET status='completed',total_items=?,
                   imported_items=?,skipped_items=?,error='',updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (len(entries), imported, skipped, job_id),
            )
        return {
            "status": "completed",
            "total_items": len(entries),
            "imported_items": imported,
            "skipped_items": skipped,
        }
    except Exception as exc:
        with connection() as db:
            db.execute(
                "UPDATE collection_jobs SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(exc)[:1000], job_id),
            )
        return {"status": "failed", "error": str(exc)[:1000]}


def run_worker(once: bool = False):
    init_db()
    while True:
        with connection() as db:
            job = db.execute(
                "SELECT id FROM collection_jobs WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
        if job:
            process_job(job["id"])
            continue
        if once:
            break
        time.sleep(COLLECTION_POLL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description="MDE authorized catalog collection worker")
    parser.add_argument("--once", action="store_true", help="Process the queued jobs once and exit")
    args = parser.parse_args()
    run_worker(once=args.once)


if __name__ == "__main__":
    main()
