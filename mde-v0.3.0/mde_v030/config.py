import os
from pathlib import Path

DATABASE_PATH = Path(os.getenv("MDE_DATABASE_PATH", "mde-v030.sqlite3"))
BOOTSTRAP_USER = os.getenv("MDE_BOOTSTRAP_USER", "demo")
BOOTSTRAP_PASSWORD = os.getenv("MDE_BOOTSTRAP_PASSWORD", "change-this-password")
ADMIN_KEY = os.getenv("MDE_ADMIN_KEY", "")
PROXY_TOKEN_TTL_SECONDS = int(os.getenv("MDE_PROXY_TOKEN_TTL_SECONDS", "21600"))
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("MDE_UPSTREAM_TIMEOUT_SECONDS", "20"))
MAX_PLAYLIST_BYTES = int(os.getenv("MDE_MAX_PLAYLIST_BYTES", "2097152"))
