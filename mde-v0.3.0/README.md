# MDE 0.3.0 — catalog and Xtream API foundation

The 0.2.0 ZIP in the repository root is preserved. This is a separate development line.

## Implemented in this stage
- SQLite tables for users, categories, media, and sources.
- Per-source delivery policy: `redirect` or `proxy`.
- Xtream-style `player_api.php` authentication, category and catalog actions.
- M3U generation at `get.php`, with stable MDE playback URLs.
- Admin endpoints to register sources, categories, and media items.
- Admin endpoints require the `X-MDE-Admin-Key` header and a configured secret.

## Run on Windows
```powershell
cd mde-v0.3.0
py -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
$env:MDE_DATABASE_PATH = "mde-v030.sqlite3"
$env:MDE_BOOTSTRAP_USER = "demo"
$env:MDE_BOOTSTRAP_PASSWORD = "replace-with-a-long-password"
$env:MDE_ADMIN_KEY = "replace-with-a-long-random-admin-key"
uvicorn mde_v030.main:app --reload
```

Open `http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/docs`.

## Add a source
In Swagger (`/docs`), call `POST /v1/admin/sources` and set header `X-MDE-Admin-Key`.
Example JSON:
```json
{"name":"Minha fonte autorizada","base_url":"https://media.example.net","delivery_mode":"proxy","enabled":true}
```
Use `redirect` or `proxy` per source.

Then create a category with `POST /v1/admin/categories`, and add an item with `POST /v1/admin/media`:
```json
{"title":"Canal de teste","media_type":"live","source_id":1,"source_path":"channel/master.m3u8","category_id":1}
```

Only use streams you own or are explicitly authorized to distribute. Do not commit real credentials, private stream URLs, database files, or API keys.

## Not complete yet
The generated M3U contains stable MDE playback URLs, but the `/play` delivery handler is not enabled yet. Redirect/proxy transport, secure HLS playlist rewriting, segment delivery, range handling, concurrent-session enforcement, and real-client compatibility tests are the next stage. Do not expose this development server publicly yet.
