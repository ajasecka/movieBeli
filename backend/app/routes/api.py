"""All HTTP endpoints, grouped under /api."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text

from .. import config, export_ingest, ranking, tmdb
from ..db import session_scope
from ..models import Movie, Ranking

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------
# Search — served entirely from the local export index (no API calls).
# --------------------------------------------------------------------------
@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 20):
    term = q.strip()
    if not term:
        return {"results": []}
    sql = text(
        """
        SELECT id, original_title, popularity
        FROM movie_index
        WHERE lower(original_title) LIKE lower(:pat)
        ORDER BY popularity DESC
        LIMIT :lim
        """
    )
    with session_scope() as s:
        rows = s.execute(sql, {"pat": f"%{term}%", "lim": min(limit, 50)}).all()
        ranked_ids = set(s.execute(select(Ranking.movie_id)).scalars().all())

    results = [
        {
            "id": r.id,
            "title": r.original_title,
            "popularity": round(r.popularity, 1),
            "already_ranked": r.id in ranked_ids,
        }
        for r in rows
    ]
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
