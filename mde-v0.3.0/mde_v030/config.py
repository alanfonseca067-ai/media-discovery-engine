import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATABASE_PATH = Path(os.getenv("MDE_DATABASE_PATH", "mde-v030.sqlite3"))
BOOTSTRAP_USER = os.getenv("MDE_BOOTSTRAP_USER", "demo")
BOOTSTRAP_PASSWORD = os.getenv("MDE_BOOTSTRAP_PASSWORD", "change-this-password")
ADMIN_KEY = os.getenv("MDE_ADMIN_KEY", "")
PROXY_TOKEN_TTL_SECONDS = max(60, int(os.getenv("MDE_PROXY_TOKEN_TTL_SECONDS", "21600")))
UPSTREAM_TIMEOUT_SECONDS = max(2.0, float(os.getenv("MDE_UPSTREAM_TIMEOUT_SECONDS", "20")))
MAX_PLAYLIST_BYTES = max(65536, int(os.getenv("MDE_MAX_PLAYLIST_BYTES", "2097152")))
PLAYBACK_SESSION_TTL_SECONDS = max(30, int(os.getenv("MDE_PLAYBACK_SESSION_TTL_SECONDS", "120")))
COLLECTION_POLL_SECONDS = max(1, int(os.getenv("MDE_COLLECTION_POLL_SECONDS", "5")))
MAX_COLLECTION_BYTES = max(65536, int(os.getenv("MDE_MAX_COLLECTION_BYTES", "10485760")))

ALLOWED_PRIVATE_HOSTS = frozenset(
    item.strip().lower().rstrip(".")
    for item in os.getenv("MDE_ALLOWED_PRIVATE_HOSTS", "").split(",")
    if item.strip()
)
