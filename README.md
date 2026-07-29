# movieBeli

Hard-rank the movies you've seen, Beli-style. You don't type a score — you
place each film through head-to-head comparisons and the app derives a 0–10
rating from where it lands in your list.

The signature mechanic: when you add a movie, it's first compared against the
movies you've already ranked **that share a genre with it** (a binary search —
jump to the midpoint, pick the one you prefer, recurse on that half), then
fine-tuned against the full list. A handful of taps places a film among
hundreds.

It's a **PWA**: run it on your computer and open it from your iPhone on the same
Wi-Fi, then *Add to Home Screen* for a full-screen app.

---

## How it works

Two TMDB data sources, each doing the job it's good at:

- **Daily bulk export** (`files.tmdb.org/.../movie_ids_*.json.gz`) — a gzipped
  NDJSON list of every movie id + title. It's downloaded once each morning and
  loaded into Postgres to power **instant typeahead search with zero API calls
  while you type.** A background thread refreshes it (the export is published by
  08:00 UTC daily); downloads never happen on your request path.
- **The live API** (`/movie/{id}`) — called **exactly once per movie**, the
  moment you add it, to fetch genres, poster, year, cast, etc. The full record
  is then **cached in Postgres forever**, so browsing, ranking, and re-display
  never hit the network again. Posters shown on your saved list come from this
  cached `poster_path` via `image.tmdb.org`.

```
 ┌─ movie_index ─┐   daily export      search box (local, no API)
 │ id · title    │◀── background ──────────────────────────────
 │ popularity    │    scheduler
 └───────────────┘
 ┌─ movie ───────┐   TMDB /movie/{id}   one call on first add,
 │ genres·poster │◀── enrich-on-add ─── cached permanently
 │ year·overview │
 └───────────────┘
 ┌─ ranking ─────┐   genre-first binary comparison → position → 0–10 score
 │ position·score│
 └───────────────┘
```

---

## Prerequisites

- **Docker** (for Postgres) — or any Postgres 14+ if you set `DATABASE_URL`.
- **Python 3.11+**
- A **TMDB API credential** — a v4 *API Read Access Token* (preferred) or a v3
  API key, from <https://www.themoviedb.org/settings/api>.

## Setup & run

```bash
# 1. Add your TMDB key (kept out of git — secrets/ is gitignored)
cp env.example secrets/.env        # then paste your token into secrets/.env

# 2. Start everything (Postgres + backend + PWA) with one command
./start.sh
```

`start.sh` boots Postgres, creates a `.venv`, installs deps, and starts the
server — then prints the URL to open on your phone, e.g.:

```
• On this computer:  http://localhost:8000
• On your iPhone:    http://192.168.1.42:8000   (same Wi-Fi)
```

Open that phone URL in **Safari → Share → Add to Home Screen**. The first launch
downloads the daily movie index in the background (takes a minute or two); the
header shows a live count when it's ready.

Stop with `Ctrl-C`. Postgres keeps running in the background — shut it down with
`docker compose down`.

### Manual steps (if you'd rather not use start.sh)

```bash
docker compose up -d
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && python -m app
```

### Prefer a native Postgres (no Docker)?

Create a db/user and point the app at it:

```bash
export DATABASE_URL="postgresql+psycopg2://USER:PASS@localhost:5432/DBNAME"
```

---

## Notes on iPhone PWA

iOS only registers a service worker over **HTTPS** (or `localhost`). Over plain
LAN HTTP your phone still gets a full-screen installable web app — it just won't
cache for offline use. That's fine for a personal app on your own Wi-Fi. To get
true offline later, serve over local HTTPS (e.g. with
[`mkcert`](https://github.com/FiloSottile/mkcert)).

## Tests

With Postgres running and `DATABASE_URL` set:

```bash
cd backend
python -m tests.test_ranking   # genre-first binary ranking engine
python -m tests.test_api       # API + search + enrich-on-add + static serving
```

## Project layout

```
backend/app/
  main.py           FastAPI app; serves the API and the PWA on one port
  config.py         env + secrets/.env loading
  db.py, models.py  Postgres schema (movie_index, movie, ranking, meta)
  export_ingest.py  download + gunzip + bulk-load the daily TMDB export
  scheduler.py      background thread that keeps the index fresh
  tmdb.py           enrich-on-add API client (one call per movie, then cached)
  ranking.py        the genre-first binary comparison + score model
  routes/api.py     all /api endpoints
frontend/           no-build PWA (HTML/CSS/vanilla JS) served by the backend
docker-compose.yml  Postgres
start.sh            one-command local runner
```

> The original `typeahead/` prototype is superseded by
> `backend/app/export_ingest.py`, which loads the same export into Postgres
> (and keeps only real movies: `adult=false` **and** `video=false`).

---

## TMDB reference

- Docs: <https://developer.themoviedb.org/docs/getting-started>
- Daily export example: `https://files.tmdb.org/p/exports/movie_ids_10_25_2025.json.gz`
  — the export job runs ~07:00 UTC and is available by 08:00 UTC.
