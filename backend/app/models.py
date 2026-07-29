"""SQLAlchemy models.

Three concerns, three tables:
  * movie_index — the daily TMDB bulk export (id/title/popularity only). Powers
    typeahead search. Rebuilt every morning; never hits the API.
  * movie       — full details for a movie, fetched from the API the first time
    you interact with it and cached forever after. Powers ranking + display.
  * ranking     — your ordered personal list with rank-derived scores.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MovieIndex(Base):
    """One row per movie in the daily export. Search index only."""

    __tablename__ = "movie_index"

    id = Column(BigInteger, primary_key=True)  # TMDB movie id
    original_title = Column(Text, nullable=False)
    popularity = Column(Float, nullable=False, default=0.0)


class Movie(Base):
    """Full cached TMDB details for a movie we've enriched."""

    __tablename__ = "movie"

    id = Column(BigInteger, primary_key=True)  # TMDB movie id
    title = Column(Text, nullable=False)
    original_title = Column(Text)
    release_year = Column(Integer)
    release_date = Column(String(10))
    poster_path = Column(String(255))
    backdrop_path = Column(String(255))
    overview = Column(Text)
    runtime = Column(Integer)
    vote_average = Column(Float)  # TMDB community rating, 0-10
    vote_count = Column(Integer)  # number of TMDB votes behind that rating
    genres = Column(JSONB, nullable=False, default=list)  # list[str] of names
    director = Column(Text)
    cached_at = Column(DateTime(timezone=True), default=_utcnow)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "original_title": self.original_title,
            "release_year": self.release_year,
            "release_date": self.release_date,
            "poster_path": self.poster_path,
            "backdrop_path": self.backdrop_path,
            "overview": self.overview,
            "runtime": self.runtime,
            "vote_average": self.vote_average,
            "vote_count": self.vote_count,
            "genres": self.genres or [],
            "director": self.director,
        }


class Ranking(Base):
    """A movie's slot in your personal ranked list.

    position: 0 = best. score: 0-10, derived from position (see ranking.py).
    """

    __tablename__ = "ranking"
    __table_args__ = (UniqueConstraint("movie_id", name="uq_ranking_movie"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    movie_id = Column(BigInteger, ForeignKey("movie.id"), nullable=False)
    position = Column(Integer, nullable=False)
    score = Column(Float, nullable=False, default=0.0)
    notes = Column(Text)  # your personal note about this movie
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Watchlist(Base):
    """Movies you want to watch but haven't ranked yet (Beli's "want to try").

    A movie leaves the watchlist automatically once it gets ranked.
    """

    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    movie_id = Column(BigInteger, ForeignKey("movie.id"), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Meta(Base):
    """Small key/value store for ingest state (last export date, etc.)."""

    __tablename__ = "meta"

    key = Column(String(64), primary_key=True)
    value = Column(Text)
