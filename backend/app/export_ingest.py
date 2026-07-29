"""Download, decompress, and load the daily TMDB bulk export.

The export is a gzip-compressed NDJSON file (one JSON object per line) hosted
at files.tmdb.org, published ~07:00 UTC and guaranteed by 08:00 UTC. Each line
looks like:

    {"adult":false,"id":3924,"original_title":"Blondie","popularity":2.9,"video":false}

We stream-decompress it (never holding the whole ~80MB in memory), keep only
real movies (`not adult and not video`), and bulk-upsert into movie_index.
"""
from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from psycopg2.extras import execute_values

from . import config
from .db import engine, session_scope
from .models import Meta

log = logging.getLogger("moviebeli.ingest")

BATCH_SIZE = 10_000
# The export becomes available by 08:00 UTC; before that, use yesterday's file.
AVAILABLE_HOUR_UTC = 8


def _export_url(day: datetime) -> str:
    return f"{config.TMDB_EXPORT_BASE}/movie_ids_{day:%m_%d_%Y}.json.gz"


def _target_date(now: datetime | None = None) -> datetime:
    """The most recent date whose export should exist given the 08:00 UTC rule."""
    now = now or datetime.now(timezone.utc)
    if now.hour < AVAILABLE_HOUR_UTC:
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _get_meta(key: str) -> str | None:
    with session_scope() as s:
        row = s.get(Meta, key)
        return row.value if row else None


def _set_meta(key: str, value: str) -> None:
    with session_scope() as s:
        row = s.get(Meta, key)
        if row:
            row.value = value
        else:
            s.add(Meta(key=key, value=value))


def already_ingested(day: datetime) -> bool:
    return _get_meta("export_date") == f"{day:%Y-%m-%d}"


def _download(day: datetime) -> Path | None:
    """Download the export for `day` to DATA_DIR. Returns path or None if 404."""
    url = _export_url(day)
    dest = config.DATA_DIR / f"movie_ids_{day:%Y_%m_%d}.json.gz"
    log.info("Downloading TMDB export: %s", url)
    with requests.get(url, stream=True, timeout=60) as resp:
        if resp.status_code == 404:
            log.warning("Export not published yet (404): %s", url)
            return None
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
    return dest


def _iter_movies(path: Path):
    """Yield (id, original_title, popularity) for each real (non-adult, non-video) movie."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Real theatrical movies are adult=False and video=False. The video
            # flag marks trailer/short-type entries that we don't want.
            if row.get("adult") or row.get("video"):
                continue
            title = row.get("original_title")
            if not title:
                continue
            yield (int(row["id"]), title, float(row.get("popularity") or 0.0))


def _load_into_db(path: Path) -> int:
    """Bulk-upsert the export into movie_index. Returns rows processed."""
    raw = engine.raw_connection()
    count = 0
    try:
        cur = raw.cursor()
        batch: list[tuple] = []
        for rec in _iter_movies(path):
            batch.append(rec)
            if len(batch) >= BATCH_SIZE:
                _flush(cur, batch)
                count += len(batch)
                batch.clear()
        if batch:
            _flush(cur, batch)
            count += len(batch)
        raw.commit()
    finally:
        raw.close()
    return count


def _flush(cur, batch: list[tuple]) -> None:
    execute_values(
        cur,
        """
        INSERT INTO movie_index (id, original_title, popularity)
        VALUES %s
        ON CONFLICT (id) DO UPDATE
          SET original_title = EXCLUDED.original_title,
              popularity = EXCLUDED.popularity
        """,
        batch,
        page_size=BATCH_SIZE,
    )


def ingest_if_needed(force: bool = False) -> dict:
    """Ensure the freshest available export is loaded. Safe to call repeatedly.

    Falls back day-by-day if the target date's file isn't published yet.
    """
    target = _target_date()
    if not force and already_ingested(target):
        return {"status": "up_to_date", "export_date": f"{target:%Y-%m-%d}"}

    # Try target date, then walk back a few days in case of a publishing gap.
    for back in range(0, 4):
        day = target - timedelta(days=back)
        if not force and already_ingested(day):
            return {"status": "up_to_date", "export_date": f"{day:%Y-%m-%d}"}
        path = _download(day)
        if path is None:
            continue
        log.info("Loading export %s into movie_index...", path.name)
        rows = _load_into_db(path)
        _set_meta("export_date", f"{day:%Y-%m-%d}")
        _set_meta("export_rows", str(rows))
        _set_meta("export_ingested_at", datetime.now(timezone.utc).isoformat())
        log.info("Ingested %d movies from %s", rows, path.name)
        try:
            path.unlink()  # tidy up the .gz once loaded
        except OSError:
            pass
        return {"status": "ingested", "export_date": f"{day:%Y-%m-%d}", "rows": rows}

    return {"status": "unavailable", "message": "No export file could be downloaded."}


def index_count() -> int:
    from sqlalchemy import text

    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM movie_index")).scalar_one()


def status() -> dict:
    return {
        "export_date": _get_meta("export_date"),
        "export_rows": _get_meta("export_rows"),
        "ingested_at": _get_meta("export_ingested_at"),
        "indexed_movies": index_count(),
    }
