from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from .database import init_db, connection
from .security import verify_password

app = FastAPI(title="MDE 0.3.0", version="0.3.0",
              description="Independent catalog and authorized media delivery foundation")

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

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.3.0"}

@app.get("/player_api.php")
def player_api(username: str, password: str, action: str | None = None):
    authenticate(username, password)
    if action in ("get_live_categories", "get_vod_categories", "get_series_categories"):
        kind = {"get_live_categories":"live","get_vod_categories":"movie","get_series_categories":"series"}[action]
        with connection() as db:
            rows = db.execute("SELECT id, name FROM categories WHERE media_type=? ORDER BY name", (kind,)).fetchall()
        return [{"category_id": str(r["id"]), "category_name": r["name"], "parent_id": 0} for r in rows]
    if action in ("get_live_streams", "get_vod_streams", "get_series"):
        kind = {"get_live_streams":"live","get_vod_streams":"movie","get_series":"series"}[action]
        with connection() as db:
            rows = db.execute("SELECT id,title,category_id FROM media_items WHERE media_type=? AND enabled=1 ORDER BY sort_order,title", (kind,)).fetchall()
        return [{"num": i+1, "name": r["title"], "stream_type": kind,
                 "stream_id": r["id"], "category_id": str(r["category_id"] or 0),
                 "container_extension": "ts" if kind == "live" else "mp4"} for i,r in enumerate(rows)]
    return {
        "user_info": {"username": username, "auth": 1, "status": "Active",
                      "exp_date": None, "is_trial": "0", "active_cons": "0",
                      "max_connections": "1", "allowed_output_formats": ["ts","m3u8"]},
        "server_info": {"url": "127.0.0.1", "port": "8000", "https_port": "",
                        "server_protocol": "http", "timezone": "UTC"}
    }

@app.get("/get.php", response_class=PlainTextResponse)
def get_playlist(username: str, password: str, type: str = Query(default="m3u_plus"),
                 output: str = Query(default="ts")):
    authenticate(username, password)
    with connection() as db:
        rows = db.execute("""SELECT id,title,media_type,category_id FROM media_items
                             WHERE enabled=1 AND media_type IN ('live','movie','series')
                             ORDER BY sort_order,title""").fetchall()
        categories = {r["id"]: r["name"] for r in db.execute("SELECT id,name FROM categories")}
    lines = ["#EXTM3U"]
    for row in rows:
        group = categories.get(row["category_id"], row["media_type"].title())
        lines.append(f'#EXTINF:-1 tvg-id="{row["id"]}" group-title="{group}",{row["title"]}')
        lines.append(f'# MDE item id: {row["id"]} (playback endpoint pending)')
    return "\n".join(lines) + "\n"
