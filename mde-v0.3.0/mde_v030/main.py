import hashlib
import hmac
from contextlib import asynccontextmanager
import html
import time
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field, HttpUrl, model_validator

from .config import ADMIN_KEY, PLAYBACK_SESSION_TTL_SECONDS
from .database import connection, init_db
from .delivery import (
    deliver_proxy,
    deliver_redirect,
    get_resource_token,
    resolve_source_url,
)
from .security import hash_password, verify_password

@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="MDE 0.3.0",
    version="0.3.0",
    description="Catálogo, API Xtream, coleta autorizada e entrega de mídia.",
    lifespan=lifespan,
)


def authenticate(username: str, password: str):
    with connection() as db:
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not row["enabled"] or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    if row["expires_at"]:
        try:
            expiry = datetime.fromisoformat(row["expires_at"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Validade da conta está configurada incorretamente") from exc
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Conta expirada")
    return row


def require_admin(key: str | None):
    if not ADMIN_KEY:
        raise HTTPException(status_code=503, detail="Configure MDE_ADMIN_KEY para habilitar a administração")
    if key is None or not hmac.compare_digest(key, ADMIN_KEY):
        raise HTTPException(status_code=403, detail="Chave administrativa inválida")


def _client_key(request: Request) -> str:
    address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")[:256]
    return hashlib.sha256(f"{address}|{user_agent}".encode()).hexdigest()


def touch_playback_session(username: str, user, request: Request) -> int:
    now = int(time.time())
    client_key = _client_key(request)
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "DELETE FROM playback_sessions WHERE last_seen < ?",
            (now - PLAYBACK_SESSION_TTL_SECONDS,),
        )
        existing = db.execute(
            "SELECT 1 FROM playback_sessions WHERE username=? AND client_key=?",
            (username, client_key),
        ).fetchone()
        active = db.execute(
            "SELECT COUNT(*) FROM playback_sessions WHERE username=?",
            (username,),
        ).fetchone()[0]
        limit = int(user["max_connections"] or 0)
        if not existing and limit > 0 and active >= limit:
            raise HTTPException(status_code=429, detail="Limite de conexões simultâneas atingido")
        db.execute(
            """INSERT INTO playback_sessions(username,client_key,last_seen) VALUES (?,?,?)
               ON CONFLICT(username,client_key) DO UPDATE SET last_seen=excluded.last_seen""",
            (username, client_key, now),
        )
        return db.execute(
            "SELECT COUNT(*) FROM playback_sessions WHERE username=?",
            (username,),
        ).fetchone()[0]


def clean_m3u(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").replace('"', "'").strip()


def parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _xmltv_time(value: str) -> str:
    return parse_utc_datetime(value).strftime("%Y%m%d%H%M%S +0000")


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: HttpUrl
    delivery_mode: str = Field(default="redirect", pattern="^(redirect|proxy)$")
    manifest_path: str = Field(default="", max_length=2048)
    catalog_media_type: str = Field(default="live", pattern="^(live|movie)$")
    enabled: bool = True

    @model_validator(mode="after")
    def validate_source_paths(self):
        parsed = urlsplit(str(self.base_url))
        decoded_base_path = unquote(parsed.path)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Credenciais devem ser configuradas fora da URL base")
        if (
            parsed.query
            or parsed.fragment
            or "\\" in decoded_base_path
            or "\x00" in decoded_base_path
            or any(part == ".." for part in decoded_base_path.split("/"))
        ):
            raise ValueError("URL base não pode conter query, fragmento ou caminho fora da raiz cadastrada")
        manifest = urlsplit(self.manifest_path.strip())
        decoded = unquote(manifest.path)
        if (
            manifest.scheme
            or manifest.netloc
            or self.manifest_path.strip().startswith("//")
            or "\\" in decoded
            or "\x00" in decoded
            or any(part == ".." for part in decoded.split("/"))
        ):
            raise ValueError("manifest_path deve ser relativo e permanecer dentro da fonte")
        return self


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    delivery_mode: str | None = Field(default=None, pattern="^(redirect|proxy)$")
    manifest_path: str | None = Field(default=None, max_length=2048)
    catalog_media_type: str | None = Field(default=None, pattern="^(live|movie)$")
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_source_update(self):
        required_when_set = {"name", "base_url", "delivery_mode", "manifest_path", "catalog_media_type", "enabled"}
        for field in required_when_set.intersection(self.model_fields_set):
            if getattr(self, field) is None:
                raise ValueError(f"{field} não pode ser null")
        if self.base_url is not None:
            parsed = urlsplit(str(self.base_url))
            decoded = unquote(parsed.path)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("URL base não pode conter credenciais, query ou fragmento")
            if "\\" in decoded or "\x00" in decoded or any(part == ".." for part in decoded.split("/")):
                raise ValueError("Caminho base inválido")
        if self.manifest_path is not None:
            parsed = urlsplit(self.manifest_path.strip())
            decoded = unquote(parsed.path)
            if parsed.scheme or parsed.netloc or self.manifest_path.strip().startswith("//"):
                raise ValueError("manifest_path deve ser relativo")
            if "\\" in decoded or "\x00" in decoded or any(part == ".." for part in decoded.split("/")):
                raise ValueError("manifest_path não pode sair da fonte")
        return self


class MediaUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    source_id: int | None = Field(default=None, gt=0)
    source_path: str | None = Field(default=None, min_length=1, max_length=2048)
    category_id: int | None = None
    enabled: bool | None = None
    sort_order: int | None = None
    plot: str | None = Field(default=None, max_length=10000)
    cover_url: str | None = Field(default=None, max_length=2048)
    release_date: str | None = Field(default=None, max_length=32)
    duration: str | None = Field(default=None, max_length=32)
    rating: str | None = Field(default=None, max_length=16)
    epg_channel_id: str | None = Field(default=None, max_length=256)
    tvg_id: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_relative_source_path(self):
        non_nullable = self.model_fields_set - {"category_id"}
        for field in non_nullable:
            if getattr(self, field) is None:
                raise ValueError(f"{field} não pode ser null")
        if self.source_path is not None:
            parsed = urlsplit(self.source_path)
            decoded = unquote(parsed.path)
            if parsed.scheme or parsed.netloc or self.source_path.startswith("//"):
                raise ValueError("source_path deve ser relativo")
            if "\\" in decoded or "\x00" in decoded or any(part == ".." for part in decoded.split("/")):
                raise ValueError("source_path não pode sair da fonte")
        return self


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    media_type: str = Field(pattern="^(live|movie|series)$")


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern="^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=256)
    max_connections: int = Field(default=1, ge=0, le=100)
    expires_at: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def validate_expiry(self):
        if self.expires_at:
            datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        return self


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=10, max_length=256)
    max_connections: int | None = Field(default=None, ge=0, le=100)
    expires_at: str | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_non_nullable_fields(self):
        for field in {"password", "max_connections", "enabled"}.intersection(self.model_fields_set):
            if getattr(self, field) is None:
                raise ValueError(f"{field} não pode ser null")
        return self


class MediaIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    media_type: str = Field(pattern="^(live|movie|series|episode)$")
    source_id: int = Field(gt=0)
    source_path: str = Field(min_length=1, max_length=2048)
    category_id: int | None = None
    enabled: bool = True
    sort_order: int = 0
    series_id: int | None = None
    season_number: int | None = Field(default=None, ge=0, le=1000)
    episode_number: int | None = Field(default=None, ge=0, le=10000)
    plot: str = Field(default="", max_length=10000)
    cover_url: str = Field(default="", max_length=2048)
    release_date: str = Field(default="", max_length=32)
    duration: str = Field(default="", max_length=32)
    rating: str = Field(default="", max_length=16)
    epg_channel_id: str = Field(default="", max_length=256)
    tvg_id: str = Field(default="", max_length=256)

    @model_validator(mode="after")
    def validate_source_path(self):
        parsed = urlsplit(self.source_path)
        decoded = unquote(parsed.path)
        if (
            parsed.scheme
            or parsed.netloc
            or self.source_path.startswith("//")
            or "\\" in decoded
            or "\x00" in decoded
            or any(part == ".." for part in decoded.split("/"))
        ):
            raise ValueError("source_path deve ser relativo e permanecer dentro da fonte cadastrada")
        return self


class EpgProgrammeIn(BaseModel):
    channel_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=10000)
    start_at: str
    end_at: str
    category: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def validate_range(self):
        start = parse_utc_datetime(self.start_at)
        end = parse_utc_datetime(self.end_at)
        if end <= start:
            raise ValueError("end_at precisa ser posterior a start_at")
        return self

class EpgImportIn(BaseModel):
    xml_content: str = Field(min_length=1, max_length=5_000_000)


@app.get("/health")
def health():
    with connection() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok", "version": "0.3.0", "database": "ok"}


@app.get("/v1/stats")
def stats(x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        return {
            "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "sources": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "categories": db.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
            "media_items": db.execute("SELECT COUNT(*) FROM media_items").fetchone()[0],
            "active_playback_sessions": db.execute(
                "SELECT COUNT(*) FROM playback_sessions WHERE last_seen>=?",
                (int(time.time()) - PLAYBACK_SESSION_TTL_SECONDS,),
            ).fetchone()[0],
            "queued_collection_jobs": db.execute(
                "SELECT COUNT(*) FROM collection_jobs WHERE status='queued'"
            ).fetchone()[0],
        }


@app.post("/v1/admin/sources")
def create_source(payload: SourceIn, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        cur = db.execute(
            """INSERT INTO sources(name,base_url,delivery_mode,enabled,manifest_path,catalog_media_type)
               VALUES (?,?,?,?,?,?)""",
            (
                payload.name.strip(),
                str(payload.base_url).rstrip("/"),
                payload.delivery_mode,
                int(payload.enabled),
                payload.manifest_path.strip(),
                payload.catalog_media_type,
            ),
        )
        return {"id": cur.lastrowid, "name": payload.name, "delivery_mode": payload.delivery_mode}


@app.get("/v1/admin/sources")
def list_sources(x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        rows = db.execute(
            """SELECT id,name,base_url,delivery_mode,enabled,manifest_path,catalog_media_type
               FROM sources ORDER BY id"""
        ).fetchall()
    return [dict(row) for row in rows]


@app.patch("/v1/admin/sources/{source_id}")
def update_source(
    source_id: int,
    payload: SourceUpdate,
    x_mde_admin_key: str | None = Header(default=None),
):
    require_admin(x_mde_admin_key)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Informe ao menos um campo")
    with connection() as db:
        source = db.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Fonte não encontrada")
        assignments = []
        values = []
        for key, value in updates.items():
            if key == "base_url" and value is not None:
                value = str(value).rstrip("/")
            if key == "enabled" and value is not None:
                value = int(value)
            assignments.append(f"{key}=?")
            values.append(value)
        values.append(source_id)
        db.execute(
            f"UPDATE sources SET {', '.join(assignments)} WHERE id=?",
            values,
        )
    return {"updated": True, "source_id": source_id}


@app.delete("/v1/admin/sources/{source_id}")
def delete_source(source_id: int, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        source = db.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Fonte não encontrada")
        used = db.execute(
            "SELECT 1 FROM media_items WHERE source_id=? LIMIT 1",
            (source_id,),
        ).fetchone()
        if used:
            raise HTTPException(
                status_code=409,
                detail="A fonte possui mídias vinculadas; desative-a ou remova as mídias primeiro",
            )
        db.execute("DELETE FROM collection_jobs WHERE source_id=?", (source_id,))
        db.execute("DELETE FROM sources WHERE id=?", (source_id,))
    return {"deleted": True, "source_id": source_id}


@app.post("/v1/admin/categories")
def create_category(payload: CategoryIn, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        existing = db.execute(
            "SELECT id FROM categories WHERE name=? AND media_type=?",
            (payload.name.strip(), payload.media_type),
        ).fetchone()
        if existing:
            return {"id": existing["id"], "name": payload.name, "media_type": payload.media_type}
        cur = db.execute(
            "INSERT INTO categories(name,media_type) VALUES (?,?)",
            (payload.name.strip(), payload.media_type),
        )
        return {"id": cur.lastrowid, "name": payload.name, "media_type": payload.media_type}


@app.get("/v1/admin/categories")
def list_categories(x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        rows = db.execute("SELECT id,name,media_type FROM categories ORDER BY media_type,name").fetchall()
    return [dict(row) for row in rows]


@app.post("/v1/admin/users")
def create_user(payload: UserIn, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        try:
            cur = db.execute(
                """INSERT INTO users(username,password_hash,enabled,expires_at,max_connections)
                   VALUES (?,?,?,?,?)""",
                (
                    payload.username,
                    hash_password(payload.password),
                    int(payload.enabled),
                    payload.expires_at,
                    payload.max_connections,
                ),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="Nome de usuário já existe") from exc
            raise
        return {"id": cur.lastrowid, "username": payload.username, "max_connections": payload.max_connections}


@app.get("/v1/admin/users")
def list_users(x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        rows = db.execute(
            """SELECT id,username,enabled,expires_at,max_connections,created_at
               FROM users ORDER BY id"""
        ).fetchall()
    return [dict(row) for row in rows]


@app.patch("/v1/admin/users/{username}")
def update_user(
    username: str,
    payload: UserUpdate,
    x_mde_admin_key: str | None = Header(default=None),
):
    require_admin(x_mde_admin_key)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Informe ao menos um campo para alterar")
    if "expires_at" in updates and updates["expires_at"]:
        try:
            datetime.fromisoformat(updates["expires_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Data de expiração inválida") from exc
    allowed = {"password", "max_connections", "expires_at", "enabled"}
    with connection() as db:
        user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "password":
                key, value = "password_hash", hash_password(value)
            elif key == "enabled":
                value = int(value)
            db.execute(f"UPDATE users SET {key}=? WHERE username=?", (value, username))
        if updates.get("enabled") is False:
            db.execute("DELETE FROM playback_sessions WHERE username=?", (username,))
    return {"updated": True, "username": username}


@app.delete("/v1/admin/users/{username}")
def delete_user(username: str, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        db.execute("DELETE FROM playback_sessions WHERE username=?", (username,))
        db.execute("DELETE FROM users WHERE username=?", (username,))
    return {"deleted": True, "username": username}


@app.get("/v1/admin/media")
def list_media(x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        rows = db.execute(
            """SELECT id,title,media_type,category_id,source_id,source_path,enabled,
                      parent_id,season_number,episode_number,epg_channel_id
               FROM media_items ORDER BY media_type,sort_order,title"""
        ).fetchall()
    return [dict(row) for row in rows]


@app.patch("/v1/admin/media/{media_id}")
def update_media(
    media_id: int,
    payload: MediaUpdate,
    x_mde_admin_key: str | None = Header(default=None),
):
    require_admin(x_mde_admin_key)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Informe ao menos um campo")
    with connection() as db:
        current = db.execute(
            "SELECT id,media_type,category_id FROM media_items WHERE id=?",
            (media_id,),
        ).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Mídia não encontrada")
        if "source_id" in updates and updates["source_id"] is not None:
            source = db.execute(
                "SELECT id FROM sources WHERE id=? AND enabled=1",
                (updates["source_id"],),
            ).fetchone()
            if not source:
                raise HTTPException(status_code=404, detail="Fonte não encontrada ou desativada")
        category_id = updates.get("category_id", current["category_id"])
        if category_id is not None:
            category = db.execute(
                "SELECT media_type FROM categories WHERE id=?",
                (category_id,),
            ).fetchone()
            expected = "live" if current["media_type"] == "live" else (
                "movie" if current["media_type"] == "movie" else "series"
            )
            if not category or category["media_type"] != expected:
                raise HTTPException(status_code=422, detail="Categoria incompatível com a mídia")
        assignments = []
        values = []
        for key, value in updates.items():
            if key == "enabled" and value is not None:
                value = int(value)
            if key == "title" and value is not None:
                value = value.strip()
            if key == "source_path" and value is not None:
                value = value.strip()
            assignments.append(f"{key}=?")
            values.append(value)
        values.append(media_id)
        db.execute(
            f"UPDATE media_items SET {', '.join(assignments)} WHERE id=?",
            values,
        )
    return {"updated": True, "media_id": media_id}


@app.delete("/v1/admin/media/{media_id}")
def delete_media(media_id: int, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        media = db.execute("SELECT id FROM media_items WHERE id=?", (media_id,)).fetchone()
        if not media:
            raise HTTPException(status_code=404, detail="Mídia não encontrada")
        db.execute("DELETE FROM media_items WHERE id=?", (media_id,))
    return {"deleted": True, "media_id": media_id}


@app.post("/v1/admin/media")
def create_media(payload: MediaIn, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        source = db.execute(
            "SELECT id FROM sources WHERE id=? AND enabled=1",
            (payload.source_id,),
        ).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Fonte não encontrada ou desativada")

        expected_category = "live" if payload.media_type == "live" else (
            "movie" if payload.media_type == "movie" else "series"
        )
        if payload.category_id is not None:
            category = db.execute(
                "SELECT media_type FROM categories WHERE id=?",
                (payload.category_id,),
            ).fetchone()
            if not category or category["media_type"] != expected_category:
                raise HTTPException(status_code=422, detail="Categoria incompatível com o tipo de mídia")

        parent_id = None
        if payload.media_type == "episode":
            if payload.series_id is None or payload.season_number is None or payload.episode_number is None:
                raise HTTPException(
                    status_code=422,
                    detail="Episódios exigem series_id, season_number e episode_number",
                )
            parent = db.execute(
                "SELECT id,category_id FROM media_items WHERE id=? AND media_type='series' AND enabled=1",
                (payload.series_id,),
            ).fetchone()
            if not parent:
                raise HTTPException(status_code=404, detail="Série vinculada não encontrada")
            parent_id = parent["id"]
            if payload.category_id is None:
                payload_category_id = parent["category_id"]
            else:
                payload_category_id = payload.category_id
        else:
            if payload.series_id is not None:
                raise HTTPException(status_code=422, detail="series_id só pode ser usado em episódios")
            payload_category_id = payload.category_id

        cur = db.execute(
            """INSERT INTO media_items
               (title,media_type,category_id,source_id,source_path,enabled,sort_order,
                parent_id,season_number,episode_number,plot,cover_url,release_date,
                duration,rating,epg_channel_id,tvg_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                payload.title.strip(),
                payload.media_type,
                payload_category_id,
                payload.source_id,
                payload.source_path.strip(),
                int(payload.enabled),
                payload.sort_order,
                parent_id,
                payload.season_number if payload.media_type == "episode" else None,
                payload.episode_number if payload.media_type == "episode" else None,
                payload.plot,
                payload.cover_url,
                payload.release_date,
                payload.duration,
                payload.rating,
                payload.epg_channel_id,
                payload.tvg_id,
            ),
        )
        return {
            "id": cur.lastrowid,
            "title": payload.title,
            "media_type": payload.media_type,
            "source_id": payload.source_id,
            "series_id": parent_id,
        }


@app.post("/v1/admin/sources/{source_id}/collect")
def queue_collection(source_id: int, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        source = db.execute(
            "SELECT id,enabled,manifest_path FROM sources WHERE id=?",
            (source_id,),
        ).fetchone()
        if not source or not source["enabled"]:
            raise HTTPException(status_code=404, detail="Fonte inexistente ou desativada")
        if not source["manifest_path"]:
            raise HTTPException(status_code=422, detail="Cadastre manifest_path na fonte antes da coleta")
        cur = db.execute(
            "INSERT INTO collection_jobs(source_id,status) VALUES (?,'queued')",
            (source_id,),
        )
        return {"job_id": cur.lastrowid, "status": "queued"}


@app.get("/v1/admin/jobs")
def list_collection_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    x_mde_admin_key: str | None = Header(default=None),
):
    require_admin(x_mde_admin_key)
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM collection_jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/v1/admin/jobs/{job_id}")
def get_collection_job(job_id: int, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        row = db.execute("SELECT * FROM collection_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return dict(row)


@app.post("/v1/admin/epg/programmes")
def create_epg_programme(
    payload: EpgProgrammeIn,
    x_mde_admin_key: str | None = Header(default=None),
):
    require_admin(x_mde_admin_key)
    with connection() as db:
        known = db.execute(
            """SELECT 1 FROM media_items
               WHERE media_type='live' AND enabled=1 AND epg_channel_id=?""",
            (payload.channel_id,),
        ).fetchone()
        if not known:
            raise HTTPException(status_code=404, detail="Canal EPG não cadastrado em uma mídia ao vivo")
        db.execute(
            """INSERT INTO epg_programmes(channel_id,title,description,start_at,end_at,category)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(channel_id,start_at,end_at) DO UPDATE SET
               title=excluded.title,description=excluded.description,category=excluded.category""",
            (
                payload.channel_id, payload.title, payload.description,
                payload.start_at, payload.end_at, payload.category,
            ),
        )
    return {"saved": True, "channel_id": payload.channel_id}


@app.post("/v1/admin/epg/import")
def import_epg_xmltv(
    payload: EpgImportIn,
    x_mde_admin_key: str | None = Header(default=None),
):
    require_admin(x_mde_admin_key)
    upper = payload.xml_content.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise HTTPException(status_code=422, detail="DOCTYPE e entidades XML não são permitidos")
    try:
        root = fromstring(payload.xml_content)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Arquivo XMLTV inválido") from exc
    if root.tag != "tv":
        raise HTTPException(status_code=422, detail="O XML enviado não é um documento XMLTV")
    with connection() as db:
        known = {
            row["channel_id"]
            for row in db.execute(
                """SELECT DISTINCT COALESCE(NULLIF(epg_channel_id,''),tvg_id) AS channel_id
                   FROM media_items WHERE media_type='live' AND enabled=1"""
            ).fetchall()
            if row["channel_id"]
        }
        imported = 0
        ignored = 0
        for node in root.findall("programme"):
            channel_id = node.attrib.get("channel", "")
            start_raw = node.attrib.get("start", "")
            stop_raw = node.attrib.get("stop", "")
            title_node = node.find("title")
            if channel_id not in known or not start_raw or not stop_raw or title_node is None:
                ignored += 1
                continue
            try:
                start_dt = datetime.strptime(start_raw, "%Y%m%d%H%M%S %z").astimezone(timezone.utc)
                stop_dt = datetime.strptime(stop_raw, "%Y%m%d%H%M%S %z").astimezone(timezone.utc)
            except ValueError:
                ignored += 1
                continue
            if stop_dt <= start_dt:
                ignored += 1
                continue
            desc_node = node.find("desc")
            category_node = node.find("category")
            db.execute(
                """INSERT INTO epg_programmes(channel_id,title,description,start_at,end_at,category)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(channel_id,start_at,end_at) DO UPDATE SET
                   title=excluded.title,description=excluded.description,category=excluded.category""",
                (
                    channel_id,
                    (title_node.text or "Sem título")[:512],
                    (desc_node.text or "")[:10000] if desc_node is not None else "",
                    start_dt.isoformat(),
                    stop_dt.isoformat(),
                    (category_node.text or "")[:120] if category_node is not None else "",
                ),
            )
            imported += 1
    return {"imported_programmes": imported, "ignored_programmes": ignored, "known_channels": len(known)}


@app.get("/v1/admin/epg/programmes")
def list_epg_programmes(x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM epg_programmes ORDER BY channel_id,start_at"
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/player_api.php")
def player_api(
    request: Request,
    username: str,
    password: str,
    action: str | None = None,
    category_id: int | None = None,
    series_id: int | None = None,
    vod_id: int | None = None,
    stream_id: int | None = None,
):
    user = authenticate(username, password)
    category_actions = {
        "get_live_categories": "live",
        "get_vod_categories": "movie",
        "get_series_categories": "series",
    }
    stream_actions = {
        "get_live_streams": "live",
        "get_vod_streams": "movie",
        "get_series": "series",
    }

    if action in category_actions:
        kind = category_actions[action]
        with connection() as db:
            rows = db.execute(
                "SELECT id,name FROM categories WHERE media_type=? ORDER BY name",
                (kind,),
            ).fetchall()
        return [
            {"category_id": str(row["id"]), "category_name": row["name"], "parent_id": 0}
            for row in rows
        ]

    if action in stream_actions:
        kind = stream_actions[action]
        with connection() as db:
            sql = """SELECT m.id,m.title,m.category_id,m.epg_channel_id,m.tvg_id,m.cover_url,m.plot,
                            m.release_date,m.rating
                     FROM media_items m JOIN sources s ON s.id=m.source_id
                     WHERE m.media_type=? AND m.enabled=1 AND s.enabled=1"""
            params: list[object] = [kind]
            if category_id is not None:
                sql += " AND m.category_id=?"
                params.append(category_id)
            sql += " ORDER BY m.sort_order,m.title"
            rows = db.execute(sql, params).fetchall()
        entries = []
        for index, row in enumerate(rows):
            if kind == "series":
                entries.append({
                    "num": index + 1,
                    "name": row["title"],
                    "series_id": row["id"],
                    "cover": row["cover_url"],
                    "plot": row["plot"],
                    "cast": "",
                    "director": "",
                    "genre": "",
                    "releaseDate": row["release_date"],
                    "last_modified": "0",
                    "rating": row["rating"],
                    "rating_5based": 0,
                    "backdrop_path": [row["cover_url"]] if row["cover_url"] else [],
                    "youtube_trailer": "",
                    "episode_run_time": "",
                    "category_id": str(row["category_id"] or 0),
                    "category_ids": [row["category_id"]] if row["category_id"] else [],
                })
            else:
                extension = "ts" if kind == "live" else "mp4"
                entries.append({
                    "num": index + 1,
                    "name": row["title"],
                    "title": row["title"],
                    "stream_type": kind,
                    "stream_id": row["id"],
                    "category_id": str(row["category_id"] or 0),
                    "category_ids": [row["category_id"]] if row["category_id"] else [],
                    "container_extension": extension,
                    "stream_icon": row["cover_url"],
                    "epg_channel_id": row["epg_channel_id"] or row["tvg_id"] or "",
                    "added": "0",
                    "is_adult": 0,
                    "custom_sid": "",
                    "direct_source": "",
                    "tv_archive": 0,
                    "tv_archive_duration": 0,
                })
        return entries

    if action == "get_vod_info":
        if vod_id is None:
            raise HTTPException(status_code=422, detail="Informe vod_id")
        with connection() as db:
            item = db.execute(
                """SELECT id,title,media_type,category_id,plot,cover_url,release_date,
                          duration,rating
                   FROM media_items WHERE id=? AND media_type='movie' AND enabled=1""",
                (vod_id,),
            ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Filme não encontrado")
        info = {
            "name": item["title"], "title": item["title"],
            "category_id": str(item["category_id"] or 0),
            "cover": item["cover_url"], "movie_image": item["cover_url"],
            "plot": item["plot"], "description": item["plot"],
            "releaseDate": item["release_date"], "releasedate": item["release_date"],
            "duration": item["duration"], "rating": item["rating"],
            "backdrop_path": [item["cover_url"]] if item["cover_url"] else [],
        }
        return {
            "info": info,
            "movie_data": {
                "stream_id": item["id"], "name": item["title"],
                "container_extension": "mp4",
            },
        }

    if action == "get_series_info":
        if series_id is None:
            raise HTTPException(status_code=422, detail="Informe series_id")
        with connection() as db:
            series = db.execute(
                """SELECT id,title,category_id,plot,cover_url,release_date,rating
                   FROM media_items WHERE id=? AND media_type='series' AND enabled=1""",
                (series_id,),
            ).fetchone()
            if not series:
                raise HTTPException(status_code=404, detail="Série não encontrada")
            episodes = db.execute(
                """SELECT id,title,season_number,episode_number,plot,cover_url,
                          release_date,duration,rating
                   FROM media_items
                   WHERE parent_id=? AND media_type='episode' AND enabled=1
                   ORDER BY season_number,episode_number,id""",
                (series_id,),
            ).fetchall()
        by_season: dict[str, list[dict]] = {}
        season_counts: dict[int, int] = {}
        for episode in episodes:
            season_number = int(episode["season_number"] or 0)
            season_key = str(season_number)
            season_counts[season_number] = season_counts.get(season_number, 0) + 1
            by_season.setdefault(season_key, []).append({
                "id": episode["id"],
                "episode_num": episode["episode_number"],
                "title": episode["title"],
                "container_extension": "mp4",
                "info": {
                    "movie_image": episode["cover_url"],
                    "plot": episode["plot"],
                    "releasedate": episode["release_date"],
                    "duration": episode["duration"],
                    "rating": episode["rating"],
                    "duration_secs": 0,
                },
                "custom_sid": "",
                "added": "0",
            })
        seasons = [
            {
                "air_date": "",
                "episode_count": count,
                "id": int(season),
                "name": f"Temporada {season}",
                "overview": "",
                "season_number": int(season),
                "cover": "",
                "cover_big": "",
            }
            for season, count in sorted(season_counts.items(), key=lambda item: int(item[0]))
        ]
        info = {
            "name": series["title"], "title": series["title"],
            "category_id": str(series["category_id"] or 0),
            "cover": series["cover_url"], "plot": series["plot"],
            "releaseDate": series["release_date"], "rating": series["rating"],
            "backdrop_path": [series["cover_url"]] if series["cover_url"] else [],
        }
        return {"seasons": seasons, "info": info, "episodes": by_season}

    if action in {"get_short_epg", "get_simple_data_table"}:
        selected_id = stream_id
        if selected_id is None:
            raise HTTPException(status_code=422, detail="Informe stream_id")
        with connection() as db:
            channel = db.execute(
                "SELECT epg_channel_id,tvg_id FROM media_items WHERE id=? AND media_type='live'",
                (selected_id,),
            ).fetchone()
            if not channel:
                raise HTTPException(status_code=404, detail="Canal não encontrado")
            channel_id = channel["epg_channel_id"] or channel["tvg_id"]
            programmes = db.execute(
                """SELECT * FROM epg_programmes
                   WHERE channel_id=? AND end_at>? ORDER BY start_at LIMIT 20""",
                (channel_id, datetime.now(timezone.utc).isoformat()),
            ).fetchall()
        return {"epg_listings": [
            {
                "id": str(row["id"]),
                "epg_id": channel_id,
                "title": row["title"],
                "lang": "pt",
                "start": row["start_at"],
                "end": row["end_at"],
                "description": row["description"],
                "start_timestamp": str(int(parse_utc_datetime(row["start_at"]).timestamp())),
                "stop_timestamp": str(int(parse_utc_datetime(row["end_at"]).timestamp())),
                "now_playing": 0,
            }
            for row in programmes
        ]}

    with connection() as db:
        active = db.execute(
            "SELECT COUNT(*) FROM playback_sessions WHERE username=? AND last_seen>=?",
            (username, int(time.time()) - PLAYBACK_SESSION_TTL_SECONDS),
        ).fetchone()[0]
    scheme = request.url.scheme
    host = request.url.hostname or "localhost"
    port = request.url.port or (443 if scheme == "https" else 80)
    return {
        "user_info": {
            "username": username,
            "auth": 1,
            "status": "Active",
            "exp_date": user["expires_at"],
            "is_trial": "0",
            "active_cons": str(active),
            "max_connections": str(user["max_connections"]),
            "allowed_output_formats": ["ts", "m3u8", "mp4"],
        },
        "server_info": {
            "url": host,
            "port": str(port),
            "https_port": str(port) if scheme == "https" else "",
            "server_protocol": scheme,
            "timezone": "UTC",
        },
    }


@app.get("/get.php", response_class=PlainTextResponse)
def get_playlist(
    request: Request,
    username: str,
    password: str,
    type: str = Query(default="m3u_plus", pattern="^(m3u_plus|m3u|live|movie)$"),
    output: str = Query(default="ts", pattern="^(ts|m3u8|mp4|mkv)$"),
):
    authenticate(username, password)
    with connection() as db:
        sql = """SELECT m.id,m.title,m.media_type,m.category_id,m.tvg_id,m.epg_channel_id
                 FROM media_items m JOIN sources s ON s.id=m.source_id
                 WHERE m.enabled=1 AND s.enabled=1 AND m.media_type IN ('live','movie')"""
        if type in {"live", "movie"}:
            sql += " AND m.media_type=?"
            rows = db.execute(sql + " ORDER BY m.sort_order,m.title", (type,)).fetchall()
        else:
            rows = db.execute(sql + " ORDER BY m.sort_order,m.title").fetchall()
        categories = {
            row["id"]: row["name"]
            for row in db.execute("SELECT id,name FROM categories").fetchall()
        }
    lines = ["#EXTM3U"]
    base = str(request.base_url).rstrip("/")
    safe_user, safe_pass = quote(username, safe=""), quote(password, safe="")
    for row in rows:
        kind = row["media_type"]
        extension = output if kind == "live" else ("mp4" if output in {"ts", "m3u8"} else output)
        group = clean_m3u(categories.get(row["category_id"], kind.title()))
        title = clean_m3u(row["title"])
        tvg_id = clean_m3u(row["epg_channel_id"] or row["tvg_id"])
        if type == "m3u":
            lines.append(f"#EXTINF:-1,{title}")
        else:
            lines.append(
                f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{title}" group-title="{group}",{title}'
            )
        lines.append(f"{base}/play/{kind}/{safe_user}/{safe_pass}/{row['id']}.{extension}")
    return "\n".join(lines) + "\n"


async def _play_item(
    request: Request,
    media_type: str,
    username: str,
    password: str,
    media_id: int,
):
    user = authenticate(username, password)
    touch_playback_session(username, user, request)
    with connection() as db:
        row = db.execute(
            """SELECT m.id,m.media_type,m.source_path,s.base_url,s.delivery_mode
               FROM media_items m JOIN sources s ON s.id=m.source_id
               WHERE m.id=? AND m.enabled=1 AND s.enabled=1""",
            (media_id,),
        ).fetchone()
    if not row or row["media_type"] != media_type:
        raise HTTPException(status_code=404, detail="Mídia não encontrada ou desativada")
    upstream_url = resolve_source_url(row["base_url"], row["source_path"])
    if row["delivery_mode"] == "redirect":
        return await deliver_redirect(upstream_url)
    return await deliver_proxy(
        upstream_url,
        media_id,
        str(request.base_url).rstrip("/"),
        username,
        password,
        dict(request.headers),
        method=request.method,
    )


@app.api_route(
    "/play/{media_type}/{username}/{password}/{media_file}",
    methods=["GET", "HEAD"],
)
async def play_media(
    request: Request,
    media_type: str,
    username: str,
    password: str,
    media_file: str,
):
    if media_type not in {"live", "movie"}:
        raise HTTPException(status_code=404, detail="Tipo de reprodução inválido")
    try:
        media_id_text, extension = media_file.rsplit(".", 1)
        media_id = int(media_id_text)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Mídia não encontrada")
    if extension.lower() not in {"ts", "mp4", "mkv", "m3u8", "m3u", "aac", "mp3"}:
        raise HTTPException(status_code=404, detail="Formato não permitido")
    return await _play_item(request, media_type, username, password, media_id)


@app.api_route("/series/{username}/{password}/{media_file}", methods=["GET", "HEAD"])
async def play_episode(request: Request, username: str, password: str, media_file: str):
    try:
        media_id_text, extension = media_file.rsplit(".", 1)
        media_id = int(media_id_text)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Episódio não encontrado")
    if extension.lower() not in {"mp4", "mkv", "m3u8", "ts"}:
        raise HTTPException(status_code=404, detail="Formato não permitido")
    return await _play_item(request, "episode", username, password, media_id)


@app.api_route(
    "/play-resource/{username}/{password}/{media_id}/{token}",
    methods=["GET", "HEAD"],
)
async def play_proxy_resource(
    request: Request,
    username: str,
    password: str,
    media_id: int,
    token: str,
):
    user = authenticate(username, password)
    touch_playback_session(username, user, request)
    with connection() as db:
        media = db.execute(
            """SELECT m.id,s.delivery_mode FROM media_items m
               JOIN sources s ON s.id=m.source_id
               WHERE m.id=? AND m.enabled=1 AND s.enabled=1""",
            (media_id,),
        ).fetchone()
    if not media or media["delivery_mode"] != "proxy":
        raise HTTPException(status_code=404, detail="Recurso não encontrado")
    upstream_url = get_resource_token(token, media_id, username)
    return await deliver_proxy(
        upstream_url,
        media_id,
        str(request.base_url).rstrip("/"),
        username,
        password,
        dict(request.headers),
        method=request.method,
    )


@app.get("/xmltv.php")
def xmltv(request: Request, username: str, password: str):
    authenticate(username, password)
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        channels = db.execute(
            """SELECT DISTINCT epg_channel_id,tvg_id,title
               FROM media_items
               WHERE media_type='live' AND enabled=1
                 AND COALESCE(NULLIF(epg_channel_id,''),tvg_id)<> ''
               ORDER BY title"""
        ).fetchall()
        programmes = db.execute(
            "SELECT * FROM epg_programmes WHERE end_at>? ORDER BY channel_id,start_at",
            (now,),
        ).fetchall()
    root = Element("tv", {"generator-info-name": "Media Discovery Engine"})
    known_channels = set()
    for channel in channels:
        channel_id = channel["epg_channel_id"] or channel["tvg_id"]
        if channel_id in known_channels:
            continue
        known_channels.add(channel_id)
        node = SubElement(root, "channel", {"id": channel_id})
        SubElement(node, "display-name").text = channel["title"]
    for programme in programmes:
        if programme["channel_id"] not in known_channels:
            continue
        node = SubElement(
            root,
            "programme",
            {
                "channel": programme["channel_id"],
                "start": _xmltv_time(programme["start_at"]),
                "stop": _xmltv_time(programme["end_at"]),
            },
        )
        SubElement(node, "title", {"lang": "pt"}).text = programme["title"]
        if programme["description"]:
            SubElement(node, "desc", {"lang": "pt"}).text = programme["description"]
        if programme["category"]:
            SubElement(node, "category", {"lang": "pt"}).text = programme["category"]
    data = tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(content=data, media_type="application/xml")


ADMIN_PANEL = """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MDE Admin</title><style>
body{font:15px system-ui;background:#111827;color:#e5e7eb;margin:0;padding:24px}main{max-width:1000px;margin:auto}
section{background:#1f2937;padding:18px;border-radius:12px;margin:14px 0}input,select,button{padding:10px;margin:4px;border-radius:6px;border:1px solid #4b5563;background:#111827;color:#f9fafb}
button{background:#2563eb;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1220;padding:12px;border-radius:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.grid form{display:flex;flex-direction:column;align-items:stretch}
small{color:#9ca3af}
</style></head><body><main><h1>Media Discovery Engine</h1>
<p>Administração local — cadastre apenas fontes e conteúdos autorizados.</p>
<section><label>Chave administrativa <input id="key" type="password" autocomplete="off"></label>
<button onclick="refreshAll()">Atualizar painel</button><span id="status"></span></section>
<div class="grid">
<section><h2>Fonte</h2><form id="sourceForm">
<input name="name" placeholder="Nome da fonte" required>
<input name="base_url" placeholder="URL base HTTPS" required>
<input name="manifest_path" placeholder="Caminho da playlist M3U (opcional)">
<select name="delivery_mode"><option value="proxy">Proxy</option><option value="redirect">Redirect</option></select>
<select name="catalog_media_type"><option value="live">Canais ao vivo</option><option value="movie">Filmes</option></select>
<button>Cadastrar fonte</button></form></section>
<section><h2>Categoria</h2><form id="categoryForm">
<input name="name" placeholder="Nome" required>
<select name="media_type"><option value="live">Live</option><option value="movie">Filme</option><option value="series">Série</option></select>
<button>Cadastrar categoria</button></form></section>
<section><h2>Usuário</h2><form id="userForm">
<input name="username" placeholder="Usuário" minlength="3" required>
<input name="password" type="password" placeholder="Senha (mínimo 10 caracteres)" minlength="10" required>
<input name="max_connections" type="number" min="0" max="100" value="1">
<input name="expires_at" placeholder="Expiração ISO (opcional)">
<button>Cadastrar usuário</button></form></section>
<section><h2>Mídia</h2><form id="mediaForm">
<input name="title" placeholder="Título" required>
<select name="media_type"><option value="live">Live</option><option value="movie">Filme</option><option value="series">Série</option><option value="episode">Episódio</option></select>
<input name="source_id" type="number" placeholder="ID da fonte" required>
<input name="source_path" placeholder="Caminho relativo" required>
<input name="category_id" type="number" placeholder="ID da categoria (opcional)">
<input name="series_id" type="number" placeholder="ID da série (episódios)">
<input name="season_number" type="number" placeholder="Temporada">
<input name="episode_number" type="number" placeholder="Número do episódio">
<input name="epg_channel_id" placeholder="ID EPG (canais)">
<input name="tvg_id" placeholder="TVG ID">
<button>Cadastrar mídia</button></form></section>
</div>
<section><h2>Coleta de catálogo</h2><form id="collectForm">
<input name="source_id" type="number" placeholder="ID da fonte" required>
<button>Enfileirar coleta</button></form><small>Execute o processo worker em outro terminal para processar a fila.</small></section>
<section><h2>Importar guia XMLTV</h2><form id="epgImportForm">
<textarea id="epgXml" rows="6" placeholder="Cole o conteúdo XMLTV autorizado aqui" required></textarea>
<button>Importar XMLTV</button></form><small>Somente canais com EPG ID já cadastrado serão importados.</small></section>
<section><h2>Visão geral</h2><pre id="output">Clique em Atualizar painel.</pre></section>
</main><script>
const keyInput=document.getElementById("key"),statusNode=document.getElementById("status"),output=document.getElementById("output");
function auth(){return {"X-MDE-Admin-Key":keyInput.value,"Content-Type":"application/json"}}
async function api(path,method="GET",body=null){const r=await fetch(path,{method,headers:auth(),body:body?JSON.stringify(body):null});const t=await r.text();let data;try{data=JSON.parse(t)}catch{data=t}if(!r.ok)throw new Error(typeof data==="string"?data:JSON.stringify(data));return data}
function formData(form){const obj=Object.fromEntries(new FormData(form).entries());for(const k of Object.keys(obj)){if(obj[k]==="")delete obj[k]}for(const k of ["source_id","category_id","series_id","season_number","episode_number","max_connections"]){if(obj[k]!==undefined)obj[k]=Number(obj[k])}return obj}
function bind(id,path,method="POST"){document.getElementById(id).addEventListener("submit",async e=>{e.preventDefault();try{const result=await api(path,method,formData(e.target));statusNode.textContent="Salvo: "+JSON.stringify(result);e.target.reset();}catch(err){statusNode.textContent="Erro: "+err.message}})}
bind("sourceForm","/v1/admin/sources");bind("categoryForm","/v1/admin/categories");bind("userForm","/v1/admin/users");bind("mediaForm","/v1/admin/media");
document.getElementById("collectForm").addEventListener("submit",async e=>{e.preventDefault();try{const d=formData(e.target);const r=await api("/v1/admin/sources/"+d.source_id+"/collect","POST",{});statusNode.textContent="Coleta: "+JSON.stringify(r)}catch(err){statusNode.textContent="Erro: "+err.message}});
document.getElementById("epgImportForm").addEventListener("submit",async e=>{e.preventDefault();try{const xml=document.getElementById("epgXml").value;const r=await api("/v1/admin/epg/import","POST",{xml_content:xml});statusNode.textContent="EPG: "+JSON.stringify(r)}catch(err){statusNode.textContent="Erro: "+err.message}});
async function refreshAll(){try{const results={stats:await api("/v1/stats"),sources:await api("/v1/admin/sources"),categories:await api("/v1/admin/categories"),users:await api("/v1/admin/users"),media:await api("/v1/admin/media"),epg:await api("/v1/admin/epg/programmes")};output.textContent=JSON.stringify(results,null,2);statusNode.textContent="Painel atualizado"}catch(err){statusNode.textContent="Erro: "+err.message}}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
def admin_panel():
    return HTMLResponse(ADMIN_PANEL)
