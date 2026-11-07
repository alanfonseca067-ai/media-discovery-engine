import os
from pathlib import Path

DATABASE_PATH = Path(os.getenv("MDE_DATABASE_PATH", "mde-v030.sqlite3"))
BOOTSTRAP_USER = os.getenv("MDE_BOOTSTRAP_USER", "demo")
BOOTSTRAP_PASSWORD = os.getenv("MDE_BOOTSTRAP_PASSWORD", "change-this-password")
