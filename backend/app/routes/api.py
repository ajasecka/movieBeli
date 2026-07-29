"""All HTTP endpoints, grouped under /api."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text

from .. import config, export_ingest, ranking, tmdb
from ..db import session_scope
from ..models import Movie, Ranking, Watchlist

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------
# Search — served entirely from the local export index (no API calls).
# --------------------------------------------------------------------------
@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 20):
    term = q.strip()
    if not term:
        return {"results": []}
    # Match each whitespace-separated word independently, in any order:
    # "dark knight", "knight dark", and "dar kni" all find "The Dark Knight".
    tokens = term.split()[:6]
    conds = " AND ".join(
        f"lower(original_title) LIKE lower(:t{i})" for i in range(len(tokens))
    )
    params = {f"t{i}": f"%{tok}%" for i, tok in enumerate(tokens)}
    params["lim"] = min(limit, 50)
    sql = text(
        f"""
        SELECT id, original_title, popularity
        FROM movie_index
        WHERE {conds}
        ORDER BY popularity DESC
        LIMIT :lim
        """
    )
    with session_scope() as s:
        rows = s.execute(sql, params).all()
        # movie_id -> (position, score) for anything already in your rankings
        ranked = {
            r.movie_id: (r.position, r.score)
            for r in s.execute(select(Ranking)).scalars().all()
        }
        watchlisted = set(s.execute(select(Watchlist.movie_id)).scalars().all())

    results = []
    for r in rows:
        item = {
            "id": r.id,
            "title": r.original_title,
            "popularity": round(r.popularity, 1),
            "already_ranked": r.id in ranked,
            "in_watchlist": r.id in watchlisted,
        }
        if r.id in ranked:
            position, score = ranked[r.id]
            item["rank"] = position + 1
            item["score"] = score
        results.append(item)
    return {"results": results}


# --------------------------------------------------------------------------
# Movie details — fetch from TMDB on first touch, then cache forever.
# --------------------------------------------------------------------------
@router.get("/movies/{movie_id}")
def get_movie(movie_id: int):
    return {"movie": _get_or_fetch_movie(movie_id)}


def _get_or_fetch_movie(movie_id: int) -> dict:
    with session_scope() as s:
        cached = s.get(Movie, movie_id)
        if cached:
            return cached.as_dict()

    if not config.tmdb_configured():
        raise HTTPException(
            status_code=503,
            detail="TMDB credentials not configured. Add TMDB_ACCESS_TOKEN or "
            "TMDB_API_KEY to secrets/.env.",
        )
    try:
        details = tmdb.fetch_movie_details(movie_id)
    except tmdb.TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    with session_scope() as s:
        movie = s.get(Movie, movie_id)
        if movie is None:
            movie = Movie(id=movie_id)
            s.add(movie)
        for key, value in details.items():
            setattr(movie, key, value)
        s.flush()
        return movie.as_dict()


# --------------------------------------------------------------------------
# Rankings + placement flow
# --------------------------------------------------------------------------
class StartBody(BaseModel):
    movie_id: int


class CompareBody(BaseModel):
    placement_id: str
    prefer_new: bool


class NoteBody(BaseModel):
    notes: str | None = None


@router.get("/rankings")
def list_rankings():
    return {"rankings": ranking.get_rankings()}


@router.post("/rankings/start")
def start_ranking(body: StartBody):
    movie = _get_or_fetch_movie(body.movie_id)  # ensures details are cached
    result = ranking.start_placement(body.movie_id, movie.get("genres") or [])
    return result


@router.post("/rankings/compare")
def compare(body: CompareBody):
    try:
        return ranking.submit_comparison(body.placement_id, body.prefer_new)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/rankings/{movie_id}/note")
def set_note(movie_id: int, body: NoteBody):
    result = ranking.set_note(movie_id, body.notes)
    if result is None:
        raise HTTPException(status_code=404, detail="That movie isn't in your rankings.")
    return result


@router.delete("/rankings/{movie_id}")
def delete_ranking(movie_id: int):
    return {"rankings": ranking.remove_ranking(movie_id)}


# --------------------------------------------------------------------------
# Watchlist ("want to watch")
# --------------------------------------------------------------------------
class WatchBody(BaseModel):
    movie_id: int


@router.get("/watchlist")
def list_watchlist():
    with session_scope() as s:
        rows = s.execute(
            select(Movie)
            .join(Watchlist, Watchlist.movie_id == Movie.id)
            .order_by(Watchlist.created_at.desc())
        ).scalars().all()
        return {"watchlist": [m.as_dict() for m in rows]}


@router.post("/watchlist")
def add_watchlist(body: WatchBody):
    movie = _get_or_fetch_movie(body.movie_id)  # ensure details are cached
    with session_scope() as s:
        if s.execute(
            select(Ranking).where(Ranking.movie_id == body.movie_id)
        ).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="That movie is already ranked.")
        exists = s.execute(
            select(Watchlist).where(Watchlist.movie_id == body.movie_id)
        ).scalar_one_or_none()
        if not exists:
            s.add(Watchlist(movie_id=body.movie_id))
    return {"ok": True, "movie": movie}


@router.delete("/watchlist/{movie_id}")
def remove_watchlist(movie_id: int):
    with session_scope() as s:
        row = s.execute(
            select(Watchlist).where(Watchlist.movie_id == movie_id)
        ).scalar_one_or_none()
        if row:
            s.delete(row)
    return {"ok": True}


# --------------------------------------------------------------------------
# Status / ops
# --------------------------------------------------------------------------
@router.get("/status")
def status():
    return {
        "tmdb_configured": config.tmdb_configured(),
        "image_base": config.TMDB_IMAGE_BASE,
        "index": export_ingest.status(),
    }


@router.post("/admin/refresh-export")
def refresh_export():
    return export_ingest.ingest_if_needed(force=True)
