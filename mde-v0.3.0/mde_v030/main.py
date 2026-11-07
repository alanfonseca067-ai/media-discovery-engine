from datetime import datetime, timezone
from urllib.parse import quote
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, HttpUrl
from .database import init_db, connection
from .security import verify_password
from .config import ADMIN_KEY

app = FastAPI(title="MDE 0.3.0", version="0.3.0",
              description="Independent catalog and authorized media delivery engine")

@app.on_event("startup")
def startup():
    init_db()

def authenticate(username: str, password: str):
    with connection() as db:
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not row["enabled"] or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    if row["expires_at"]:
        expiry = datetime.fromisoformat(row["expires_at"])
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Conta expirada")
    return row

def require_admin(key: str | None):
    if not ADMIN_KEY:
        raise HTTPException(status_code=503, detail="Configure MDE_ADMIN_KEY para habilitar a administração")
    if key is None or key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Chave administrativa inválida")

def clean_m3u(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").replace('"', "'").strip()

class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: HttpUrl
    delivery_mode: str = Field(default="redirect", pattern="^(redirect|proxy)$")
    enabled: bool = True

class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    media_type: str = Field(pattern="^(live|movie|series)$")

class MediaIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    media_type: str = Field(pattern="^(live|movie|series|episode)$")
    source_id: int
    source_path: str = Field(min_length=1, max_length=2048)
    category_id: int | None = None
    enabled: bool = True
    sort_order: int = 0

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.3.0"}

@app.post("/v1/admin/sources")
def create_source(payload: SourceIn, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        cur = db.execute("INSERT INTO sources(name,base_url,delivery_mode,enabled) VALUES (?,?,?,?)",
                         (payload.name, str(payload.base_url).rstrip("/"), payload.delivery_mode, int(payload.enabled)))
        return {"id": cur.lastrowid, "name": payload.name, "delivery_mode": payload.delivery_mode}

@app.get("/v1/admin/sources")
def list_sources(x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        rows = db.execute("SELECT id,name,base_url,delivery_mode,enabled FROM sources ORDER BY id").fetchall()
    return [dict(r) for r in rows]

@app.post("/v1/admin/categories")
def create_category(payload: CategoryIn, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        cur = db.execute("INSERT INTO categories(name,media_type) VALUES (?,?)", (payload.name, payload.media_type))
        return {"id": cur.lastrowid, "name": payload.name, "media_type": payload.media_type}

@app.post("/v1/admin/media")
def create_media(payload: MediaIn, x_mde_admin_key: str | None = Header(default=None)):
    require_admin(x_mde_admin_key)
    with connection() as db:
        source = db.execute("SELECT id FROM sources WHERE id=? AND enabled=1", (payload.source_id,)).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Fonte não encontrada ou desativada")
        if payload.category_id is not None:
            category = db.execute("SELECT media_type FROM categories WHERE id=?", (payload.category_id,)).fetchone()
            if not category or category["media_type"] != ("live" if payload.media_type == "live" else payload.media_type if payload.media_type in ("movie","series") else "series"):
                raise HTTPException(status_code=422, detail="Categoria incompatível com o tipo de mídia")
        cur = db.execute("""INSERT INTO media_items(title,media_type,category_id,source_id,source_path,enabled,sort_order)
                            VALUES (?,?,?,?,?,?,?)""",
                         (payload.title, payload.media_type, payload.category_id, payload.source_id,
                          payload.source_path.lstrip("/"), int(payload.enabled), payload.sort_order))
        return {"id": cur.lastrowid, "title": payload.title, "media_type": payload.media_type,
                "source_id": payload.source_id}

@app.get("/player_api.php")
def player_api(username: str, password: str, action: str | None = None):
    authenticate(username, password)
    category_actions = {"get_live_categories":"live", "get_vod_categories":"movie", "get_series_categories":"series"}
    stream_actions = {"get_live_streams":"live", "get_vod_streams":"movie", "get_series":"series"}
    if action in category_actions:
        kind = category_actions[action]
        with connection() as db:
            rows = db.execute("SELECT id,name FROM categories WHERE media_type=? ORDER BY name", (kind,)).fetchall()
        return [{"category_id": str(r["id"]), "category_name": r["name"], "parent_id": 0} for r in rows]
    if action in stream_actions:
        kind = stream_actions[action]
        with connection() as db:
            rows = db.execute("SELECT id,title,category_id FROM media_items WHERE media_type=? AND enabled=1 ORDER BY sort_order,title", (kind,)).fetchall()
        return [{"num": i+1, "name": r["title"], "stream_type": kind, "stream_id": r["id"],
                 "category_id": str(r["category_id"] or 0),
                 "container_extension": "ts" if kind == "live" else "mp4"} for i,r in enumerate(rows)]
    return {"user_info": {"username": username, "auth": 1, "status": "Active",
                          "exp_date": None, "is_trial": "0", "active_cons": "0",
                          "max_connections": "1", "allowed_output_formats": ["ts","m3u8"]},
            "server_info": {"url": "127.0.0.1", "port": "8000", "https_port": "",
                            "server_protocol": "http", "timezone": "UTC"}}

@app.get("/get.php", response_class=PlainTextResponse)
def get_playlist(request: Request, username: str, password: str,
                 type: str = Query(default="m3u_plus"), output: str = Query(default="ts")):
    authenticate(username, password)
    with connection() as db:
        rows = db.execute("""SELECT id,title,media_type,category_id FROM media_items
                             WHERE enabled=1 AND media_type IN ('live','movie','series')
                             ORDER BY sort_order,title""").fetchall()
        categories = {r["id"]: r["name"] for r in db.execute("SELECT id,name FROM categories")}
    lines = ["#EXTM3U"]
    base = str(request.base_url).rstrip("/")
    safe_user, safe_pass = quote(username), quote(password)
    for row in rows:
        group = clean_m3u(categories.get(row["category_id"], row["media_type"].title()))
        title = clean_m3u(row["title"])
        ext = "ts" if row["media_type"] == "live" else "mp4"
        lines.append(f'#EXTINF:-1 tvg-id="{row["id"]}" group-title="{group}",{title}')
        # This is the stable MDE playback URL; actual delivery route is the next implementation step.
        lines.append(f"{base}/play/{row['media_type']}/{safe_user}/{safe_pass}/{row['id']}.{ext}")
    return "\n".join(lines) + "\n"
