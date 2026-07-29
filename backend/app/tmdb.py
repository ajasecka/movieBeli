"""Thin TMDB API client used only to enrich a movie the first time we add it.

Search never comes through here — that runs entirely off the local export
index. This module is called once per movie, then the result is cached in
Postgres and never fetched again.
"""
from __future__ import annotations

import requests

from . import config

_session = requests.Session()


class TMDBError(RuntimeError):
    pass


def _auth() -> tuple[dict, dict]:
    """Return (headers, params) carrying credentials, preferring the v4 token."""
    if config.TMDB_ACCESS_TOKEN:
        return {"Authorization": f"Bearer {config.TMDB_ACCESS_TOKEN}"}, {}
    if config.TMDB_API_KEY:
        return {}, {"api_key": config.TMDB_API_KEY}
    raise TMDBError(
        "No TMDB credentials configured. Put TMDB_ACCESS_TOKEN or TMDB_API_KEY "
        "in secrets/.env"
    )


def _get(path: str, params: dict | None = None) -> dict:
    headers, auth_params = _auth()
    query = {**(params or {}), **auth_params}
    url = f"{config.TMDB_API_BASE}{path}"
    resp = _session.get(url, headers=headers, params=query, timeout=15)
    if resp.status_code == 404:
        raise TMDBError(f"TMDB movie not found: {path}")
    if resp.status_code != 200:
        raise TMDBError(f"TMDB error {resp.status_code}: {resp.text[:200]}")
    return resp.json()


_GENRE_MAP: dict[int, str] = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance",
    878: "Sci-Fi", 10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
}


def search_movies(query: str, limit: int = 20) -> list[dict]:
    """Search TMDB for movies matching query. Returns enriched result dicts."""
    data = _get("/search/movie", params={"query": query, "language": "en-US", "page": 1})
    results = []
    for r in (data.get("results") or [])[:limit]:
        release_date = r.get("release_date") or ""
        release_year = int(release_date[:4]) if len(release_date) >= 4 and release_date[:4].isdigit() else None
        vote_average = r.get("vote_average") or None
        vote_count = r.get("vote_count") or None
        genres = [_GENRE_MAP[gid] for gid in (r.get("genre_ids") or []) if gid in _GENRE_MAP]
        results.append({
            "id": r["id"],
            "title": r.get("title") or r.get("original_title") or "Untitled",
            "popularity": round(float(r.get("popularity") or 0.0), 1),
            "release_year": release_year,
            "vote_average": round(vote_average, 1) if vote_average else None,
            "vote_count": vote_count,
            "genres": genres,
        })
    return results


def fetch_movie_details(movie_id: int) -> dict:
    """Fetch full details for a movie and normalise into our cached shape.

    One request thanks to append_to_response: details + credits together.
    """
    data = _get(
        f"/movie/{movie_id}",
        params={"append_to_response": "credits", "language": "en-US"},
    )

    release_date = data.get("release_date") or None
    release_year = None
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        release_year = int(release_date[:4])

    director = None
    crew = (data.get("credits") or {}).get("crew") or []
    for member in crew:
        if member.get("job") == "Director":
            director = member.get("name")
            break

    return {
        "id": data["id"],
        "title": data.get("title") or data.get("original_title") or "Untitled",
        "original_title": data.get("original_title"),
        "release_year": release_year,
        "release_date": release_date,
        "poster_path": data.get("poster_path"),
        "backdrop_path": data.get("backdrop_path"),
        "overview": data.get("overview"),
        "runtime": data.get("runtime"),
        "vote_average": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "genres": [g["name"] for g in (data.get("genres") or []) if g.get("name")],
        "director": director,
    }
