# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start everything (Postgres via Docker + backend + frontend)
./start.sh

# Stop Postgres (backend stops on Ctrl-C)
docker compose down

# Manual start
docker compose up -d
source .venv/bin/activate
cd backend && python -m app

# Tests (run from backend/, requires Postgres running)
python -m tests.test_ranking   # ranking engine against real DB
python -m tests.test_api       # API routes + search (TMDB calls are monkeypatched)
```

## Backend (`backend/app/`)

- **`main.py`** — FastAPI app setup. Mounts the API router and serves `frontend/` as static files at `/`. Startup hook calls `init_db()` and starts the scheduler.
- **`config.py`** — All env/secrets loading. Reads from real env vars, then `.env`, then `secrets/.env`. Defines `DATABASE_URL`, `TMDB_ACCESS_TOKEN`, `TMDB_API_KEY`, `HOST`, `PORT`, `FRONTEND_DIR`, `DATA_DIR`, etc.
- **`db.py`** — SQLAlchemy engine, `SessionLocal`, and `session_scope()` context manager. `init_db()` creates tables and runs idempotent `ALTER TABLE ADD COLUMN IF NOT EXISTS` for schema changes (no migration framework).
- **`models.py`** — Four tables: `movie_index` (search index, id/title/popularity only), `movie` (full TMDB details, cached forever), `ranking` (personal ordered list with position/score/tier/notes), `watchlist` (want-to-watch). `Movie.as_dict()` is the canonical serialization used by all endpoints.
- **`ranking.py`** — The ranking engine. `_PLACEMENTS` is a module-level in-memory dict of active placement sessions (keyed by hex UUID). `start_placement()` / `submit_comparison()` drive the two-phase binary search. `score_for(position, total)` and `_renumber()` recompute all scores after every change — position is the source of truth, not the stored score.
- **`routes/api.py`** — All `/api/*` endpoints. `_get_or_fetch_movie()` is the shared cache-or-fetch helper used by ranking, watchlist, and detail endpoints before touching TMDB.
- **`tmdb.py`** — TMDB API client. `fetch_movie_details(movie_id)` makes exactly one call per movie and returns a dict matching `Movie` column names.
- **`export_ingest.py`** — Downloads and bulk-loads the daily TMDB gzipped export into `movie_index`. `ingest_if_needed(force=False)` is idempotent; `status()` returns current index state.
- **`scheduler.py`** — Background thread that calls `export_ingest.ingest_if_needed()` on `EXPORT_CHECK_INTERVAL` (default 30 min).

## Frontend (`frontend/`)

No build step — plain HTML/CSS/vanilla JS served directly by FastAPI.

- **`js/api.js`** — All `fetch()` calls to `/api/*`. One function per endpoint.
- **`js/app.js`** — All UI logic: tab switching, rendering, placement flow (drives the comparison loop by calling `api.js`).
- **`index.html`** — Single HTML file; loads both JS files and `styles.css`.

## Secrets / config

`secrets/.env` is gitignored. Copy `env.example` to create it. Required: `TMDB_ACCESS_TOKEN` (v4 bearer, preferred) or `TMDB_API_KEY` (v3 key). `config.py` accepts several alias names for each — see its `_get()` calls.
