# MDE 0.3.0 — foundation

This directory is the new 0.3.0 development line. The original 0.2.0 ZIP in the repository root is intentionally left untouched.

## Current scope
- Xtream-style account authentication surface: `player_api.php`
- M3U playlist endpoint: `get.php`
- SQLite catalog for users, categories, and media items
- Source registry with delivery policy configured per source: `redirect` or `proxy`
- Health endpoint and tests

Only add media sources that you own or are explicitly authorized to distribute. MDE stores catalog references; it does not discover or grant rights to third-party streams.

## Run on Windows
```powershell
cd mde-v0.3.0
py -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
$env:MDE_DATABASE_PATH = "mde-v030.sqlite3"
$env:MDE_BOOTSTRAP_USER = "demo"
$env:MDE_BOOTSTRAP_PASSWORD = "change-this-password"
uvicorn mde_v030.main:app --reload
```

Open `http://127.0.0.1:8000/health` and `http://127.0.0.1:8000/docs`.

The bootstrap account is created only when the database has no users. The first-stage M3U response is a catalog preview, not a playable playlist yet. Change the example password before exposing the service to a network. Do not commit real credentials, private stream URLs, database files, or API keys.

## API examples
- `GET /player_api.php?username=demo&password=...`
- `GET /get.php?username=demo&password=...&type=m3u_plus&output=ts`

Playback delivery is deliberately not enabled in this first foundation. Redirect/proxy selection is stored per media item and will be connected to the secure HLS delivery layer in the next stage.
