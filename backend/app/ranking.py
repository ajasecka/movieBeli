"""The hard-ranking engine — Beli-style pairwise placement.

Placing a new movie into your ordered list happens in two binary-search phases:

  Phase 1 (genre-anchored): binary-search the new movie against only the
  already-ranked movies that share a genre with it. This pins down which band
  of the global list it belongs in, using the most relevant comparisons first.

  Phase 2 (refine): binary-search within that band against the full list
  (including cross-genre neighbours) to land on the exact slot.

Each phase is a classic "jump to the midpoint, ask which you prefer, recurse on
the chosen half" search. Total questions ≈ log2(genre pool) + log2(band).

A placement is a short-lived server-side session; the frontend answers one
comparison at a time until the movie lands and scores are recomputed.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from .db import session_scope
from .models import Movie, Ranking

# In-memory placement sessions, keyed by placement_id. Fine for a single-user
# personal app; a server restart mid-placement just means re-adding the movie.
_PLACEMENTS: dict[str, "Placement"] = {}


@dataclass
class RankedEntry:
    movie_id: int
    genres: frozenset[str]


@dataclass
class Placement:
    id: str
    movie_id: int
    movie_genres: frozenset[str]
    ranked: list[RankedEntry]          # snapshot, best -> worst
    phase: int = 1
    lo: int = 0
    hi: int = 0
    genre_positions: list[int] = field(default_factory=list)  # global indices
    band_lo: int = 0
    band_hi: int = 0
    comparisons_done: int = 0
    estimated_total: int = 0


# --------------------------------------------------------------------------
# Score model — derived purely from rank position (tunable in one place).
# --------------------------------------------------------------------------
def score_for(position: int, total: int) -> float:
    """Map a 0-based rank position to a 0-10 score. Top = 10.0."""
    if total <= 1:
        return 10.0
    return round(10.0 * (total - 1 - position) / (total - 1), 1)


def _load_ranked() -> list[RankedEntry]:
    """Current ranked list, best -> worst, with each movie's genre set."""
    with session_scope() as s:
        rows = s.execute(
            select(Ranking.movie_id, Movie.genres)
            .join(Movie, Movie.id == Ranking.movie_id)
            .order_by(Ranking.position.asc())
        ).all()
    return [RankedEntry(movie_id=mid, genres=frozenset(genres or [])) for mid, genres in rows]


def _est(n: int) -> int:
    return max(1, math.ceil(math.log2(n + 1))) if n > 0 else 0


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def start_placement(movie_id: int, genres: list[str]) -> dict:
    """Begin placing `movie_id`. Returns either a comparison or an immediate done."""
    ranked = _load_ranked()

    # Already ranked? Nothing to do.
    if any(e.movie_id == movie_id for e in ranked):
        return {"done": True, "already_ranked": True, "position": _position_of(movie_id)}

    p = Placement(
        id=uuid.uuid4().hex,
        movie_id=movie_id,
        movie_genres=frozenset(genres or []),
        ranked=ranked,
    )

    # Empty list: straight to the top, no questions.
    if not ranked:
        return _finalize(p, 0)

    # Set up phase 1 over the same-genre sublist (falls through to phase 2 if none).
    p.genre_positions = [
        i for i, e in enumerate(ranked) if e.genres & p.movie_genres
    ]
    if p.genre_positions:
        p.phase = 1
        p.lo, p.hi = 0, len(p.genre_positions)
        p.estimated_total = _est(len(p.genre_positions)) + _est(len(ranked))
    else:
        p.phase = 2
        p.band_lo, p.band_hi = 0, len(ranked)
        p.lo, p.hi = 0, len(ranked)
        p.estimated_total = _est(len(ranked))

    _PLACEMENTS[p.id] = p
    return _next_comparison(p)


def submit_comparison(placement_id: str, prefer_new: bool) -> dict:
    """Record one answer and return the next comparison, or the final placement.

    prefer_new=True  -> the user prefers the NEW movie (it ranks higher).
    prefer_new=False -> the user prefers the movie already in the list.
    """
    p = _PLACEMENTS.get(placement_id)
    if p is None:
        raise KeyError("Unknown or expired placement session.")

    p.comparisons_done += 1
    mid = (p.lo + p.hi) // 2

    if prefer_new:
        # New movie is better than candidate -> search the higher (better) half.
        p.hi = mid
    else:
        # New movie is worse -> search the lower half.
        p.lo = mid + 1

    if p.lo < p.hi:
        return _next_comparison(p)

    # This phase converged.
    if p.phase == 1:
        return _begin_phase_two(p)
    return _finalize(p, p.lo)


def cancel_placement(placement_id: str) -> None:
    _PLACEMENTS.pop(placement_id, None)


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
def _candidate_index(p: Placement) -> int:
    """Global index of the movie to compare against at the current midpoint."""
    mid = (p.lo + p.hi) // 2
    if p.phase == 1:
        return p.genre_positions[mid]
    return mid


def _next_comparison(p: Placement) -> dict:
    against_id = p.ranked[_candidate_index(p)].movie_id
    with session_scope() as s:
        new_movie = s.get(Movie, p.movie_id)
        against = s.get(Movie, against_id)
        against_note = s.execute(
            select(Ranking.notes).where(Ranking.movie_id == against_id)
        ).scalar_one_or_none()
        payload = {
            "done": False,
            "placement_id": p.id,
            "phase": p.phase,
            "phase_label": "Same genre" if p.phase == 1 else "Fine-tuning",
            "comparisons_done": p.comparisons_done,
            "estimated_total": p.estimated_total,
            "new_movie": new_movie.as_dict(),
            "new_note": None,  # the movie being added isn't ranked yet
            "against": against.as_dict(),
            "against_note": against_note,
        }
    return payload


def _begin_phase_two(p: Placement) -> dict:
    """Translate the phase-1 result into a global band, then binary-search it."""
    g = p.lo  # insertion index among the genre sublist
    lower = p.genre_positions[g - 1] + 1 if g > 0 else 0
    upper = p.genre_positions[g] if g < len(p.genre_positions) else len(p.ranked)

    p.phase = 2
    p.band_lo, p.band_hi = lower, upper
    p.lo, p.hi = lower, upper

    if p.lo >= p.hi:
        # Genre neighbours are adjacent — the slot is already pinned.
        return _finalize(p, p.lo)
    return _next_comparison(p)


def _finalize(p: Placement, insert_at: int) -> dict:
    """Insert the movie at `insert_at`, renumber positions, recompute scores."""
    _PLACEMENTS.pop(p.id, None)
    with session_scope() as s:
        rows = s.execute(select(Ranking).order_by(Ranking.position.asc())).scalars().all()
        insert_at = max(0, min(insert_at, len(rows)))

        s.add(Ranking(movie_id=p.movie_id, position=insert_at, score=0.0))
        s.flush()  # make the new row visible to _renumber's re-query

        # Rebuild the ordered id list with the new movie spliced in.
        ordered_ids = [r.movie_id for r in rows]
        ordered_ids.insert(insert_at, p.movie_id)

        _renumber(s, ordered_ids)

    return {
        "done": True,
        "movie_id": p.movie_id,
        "position": insert_at,
        "score": score_for(insert_at, len(p.ranked) + 1),
        "comparisons": p.comparisons_done,
        "list": get_rankings(),
    }


def _renumber(session, ordered_ids: list[int]) -> None:
    total = len(ordered_ids)
    by_movie = {
        r.movie_id: r
        for r in session.execute(select(Ranking)).scalars().all()
    }
    for pos, mid in enumerate(ordered_ids):
        row = by_movie[mid]
        row.position = pos
        row.score = score_for(pos, total)


def _position_of(movie_id: int) -> int | None:
    with session_scope() as s:
        row = s.execute(
            select(Ranking).where(Ranking.movie_id == movie_id)
        ).scalar_one_or_none()
        return row.position if row else None


def get_rankings() -> list[dict]:
    """The full ordered list with movie details, best -> worst."""
    with session_scope() as s:
        rows = s.execute(
            select(Ranking, Movie)
            .join(Movie, Movie.id == Ranking.movie_id)
            .order_by(Ranking.position.asc())
        ).all()
        out = []
        for ranking, movie in rows:
            d = movie.as_dict()
            d["position"] = ranking.position
            d["rank"] = ranking.position + 1
            d["score"] = ranking.score
            d["note"] = ranking.notes
            out.append(d)
        return out


def set_note(movie_id: int, notes: str | None) -> dict | None:
    """Set (or clear) the personal note on a ranked movie. Returns the movie's
    updated ranking dict, or None if the movie isn't ranked."""
    cleaned = (notes or "").strip() or None
    with session_scope() as s:
        row = s.execute(
            select(Ranking).where(Ranking.movie_id == movie_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.notes = cleaned
    return {"movie_id": movie_id, "note": cleaned}


def remove_ranking(movie_id: int) -> list[dict]:
    with session_scope() as s:
        row = s.execute(
            select(Ranking).where(Ranking.movie_id == movie_id)
        ).scalar_one_or_none()
        if row is None:
            return get_rankings()
        s.delete(row)
        s.flush()
        remaining = s.execute(select(Ranking).order_by(Ranking.position.asc())).scalars().all()
        _renumber(s, [r.movie_id for r in remaining])
    return get_rankings()
