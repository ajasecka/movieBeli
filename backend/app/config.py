"""Configuration loaded from environment and the gitignored secrets/ folder.

Resolution order for every value: real environment variable first, then a
`.env` file in the repo root, then `secrets/.env`. This lets you keep your
TMDB key out of version control (secrets/ is gitignored) while still allowing
plain env vars for deployment.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# repo root = two levels up from this file (backend/app/config.py -> repo/)
REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_DIR = REPO_ROOT / "secrets"

# Load .env files without clobbering real environment variables.
load_dotenv(REPO_ROOT / ".env", override=False)
load_dotenv(SECRETS_DIR / ".env", override=False)


def _get(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val.strip()
    return default


# --- Database -------------------------------------------------------------
DATABASE_URL = _get(
    "DATABASE_URL",
    default="postgresql+psycopg2://moviebeli:moviebeli@localhost:5432/moviebeli",
)

# --- TMDB credentials -----------------------------------------------------
# Either a v4 read access token (preferred, sent as a Bearer header) or a
# classic v3 API key (sent as an api_key query param). Provide one.
TMDB_ACCESS_TOKEN = _get("TMDB_ACCESS_TOKEN", "TMDB_READ_ACCESS_TOKEN", "TMDB_V4_TOKEN")
TMDB_API_KEY = _get("TMDB_API_KEY", "TMDB_KEY", "TMDB_V3_KEY")

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
# Public bulk-export host (no auth needed).
TMDB_EXPORT_BASE = "https://files.tmdb.org/p/exports"

# --- Server ---------------------------------------------------------------
HOST = _get("HOST", default="0.0.0.0")
PORT = int(_get("PORT", default="8000"))

# Where the downloaded daily export lands.
DATA_DIR = Path(_get("DATA_DIR", default=str(REPO_ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# How often the background scheduler wakes to check for a fresh export (seconds).
EXPORT_CHECK_INTERVAL = int(_get("EXPORT_CHECK_INTERVAL", default="1800"))

FRONTEND_DIR = REPO_ROOT / "frontend"


def tmdb_configured() -> bool:
    return bool(TMDB_ACCESS_TOKEN or TMDB_API_KEY)
