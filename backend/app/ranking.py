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

from sqlalchemy import delete, select

from .db import session_scope
from .models import Movie, Ranking, Watchlist

# In-memory placement sessions, keyed by placement_id. Fine for a single-user
# personal app; a server restart mid-placement just means re-adding the movie.
_PLACEMENTS: dict[str, "Placement"] = {}


@dataclass
class RankedEntry:
    movie_id: int
    genres: frozenset[str]
    tier: str | None = None


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
    tier_lo: int = 0                   # tier band lower bound (inclusive)
    tier_hi: int = 0                   # tier band upper bound (exclusive); 0 = unconstrained
    tier_str: str | None = None        # "loved" | "liked" | "disliked"


# --------------------------------------------------------------------------
# Score model — derived purely from rank position (tunable in one place).
# --------------------------------------------------------------------------
def score_for(position: int, total: int) -> float:
    """Map a 0-based rank position to a 0-10 score. Top = 10.0."""
    if total <= 1:
        return 10.0
    return round(10.0 * (total - 1 - position) / (total - 1), 1)


def _load_ranked() -> list[RankedEntry]:
    """Current ranked list, best -> worst, with each movie's genre set and tier."""
    with session_scope() as s:
        rows = s.execute(
            select(Ranking.movie_id, Movie.genres, Ranking.tier)
            .join(Movie, Movie.id == Ranking.movie_id)
            .order_by(Ranking.position.asc())
        ).all()
    return [
        RankedEntry(movie_id=mid, genres=frozenset(genres or []), tier=tier)
        for mid, genres, tier in rows
    ]


def _est(n: int) -> int:
    return max(1, math.ceil(math.log2(n + 1))) if n > 0 else 0


_TIER_ORDER: dict[str, int] = {"loved": 0, "liked": 1, "disliked": 2}
# Where to insert the first movie of a tier when no tier data exists at all.
_TIER_DEFAULT_INSERT = {"loved": "start", "liked": "middle", "disliked": "end"}


def _tier_band(ranked: list[RankedEntry], tier: str | None) -> tuple[int, int]:
    """Return (lo, hi) position range for the tier.

    When there are existing same-tier movies, returns the contiguous slice they
    occupy so comparisons stay strictly within that tier.

    When the tier has no existing members, returns a single-point band (x, x)
    so the caller inserts at position x without asking any comparison questions.
    Tiers are ALWAYS isolated — a "liked" movie is never shown against a
    "loved" movie, even during the first placement.
    """
    n = len(ranked)
    if not tier or n == 0:
        return 0, n

    same = [i for i, e in enumerate(ranked) if e.tier == tier]
    if same:
        return same[0], same[-1] + 1

    my_order = _TIER_ORDER.get(tier, 1)

    # Find adjacent tiers to locate the insertion boundary.
    lower = [i for i, e in enumerate(ranked)
             if e.tier is not None and _TIER_ORDER.get(e.tier, 1) < my_order]
    higher = [i for i, e in enumerate(ranked)
              if e.tier is not None and _TIER_ORDER.get(e.tier, 1) > my_order]

    if lower or higher:
        insert_at = (max(lower) + 1) if lower else min(higher)
        return insert_at, insert_at

    # No tier data exists at all (e.g., all legacy null-tier movies, or empty
    # list). Place at a sensible default — no comparison needed.
    slot = _TIER_DEFAULT_INSERT.get(tier, "middle")
    if slot == "start":
        insert_at = 0
    elif slot == "end":
        insert_at = n
    else:
        insert_at = n // 2
    return insert_at, insert_at


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def start_placement(movie_id: int, genres: list[str], tier: str | None = None) -> dict:
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
        tier_str=tier,
    )

    # Empty list: straight to the top, no questions.
    if not ranked:
        return _finalize(p, 0)

    t_lo, t_hi = _tier_band(ranked, tier)
    p.tier_lo, p.tier_hi = t_lo, t_hi

    # No existing movies in this tier — slot in at the boundary without comparison.
    if t_lo >= t_hi:
        return _finalize(p, t_lo)

    # Phase 1: binary-search only same-tier, same-genre movies.
    p.genre_positions = [
        i for i, e in enumerate(ranked)
        if e.genres & p.movie_genres and e.tier == tier
    ]
    if p.genre_positions:
        p.phase = 1
        p.lo, p.hi = 0, len(p.genre_positions)
        p.estimated_total = _est(len(p.genre_positions)) + _est(t_hi - t_lo)
    else:
        p.phase = 2
        p.band_lo, p.band_hi = t_lo, t_hi
        p.lo, p.hi = t_lo, t_hi
        p.estimated_total = _est(t_hi - t_lo)

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

    # Clip the genre-derived band to the tier band.
    if p.tier_hi > p.tier_lo:
        lower = max(lower, p.tier_lo)
        upper = min(upper, p.tier_hi)

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

        s.add(Ranking(movie_id=p.movie_id, position=insert_at, score=0.0, tier=p.tier_str))
        # A ranked movie is no longer "want to watch".
        s.execute(delete(Watchlist).where(Watchlist.movie_id == p.movie_id))
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
        "tier": p.tier_str,
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
            d["tier"] = ranking.tier
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


_VALID_TIERS = {"loved", "liked", "disliked"}


def reorder_rankings(
    ordered_ids: list[int],
    tier_updates: dict[str, str] | None = None,
) -> list[dict]:
    """Reorder the ranked list; optionally change tiers for specific movies."""
    with session_scope() as s:
        rows = {r.movie_id: r for r in s.execute(select(Ranking)).scalars().all()}
        valid = [mid for mid in ordered_ids if mid in rows]
        seen = set(valid)
        for mid in rows:
            if mid not in seen:
                valid.append(mid)
        _renumber(s, valid)
        if tier_updates:
            for id_str, new_tier in tier_updates.items():
                try:
                    mid = int(id_str)
                except (ValueError, TypeError):
                    continue
                if mid in rows and new_tier in _VALID_TIERS:
                    rows[mid].tier = new_tier
    return get_rankings()


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
