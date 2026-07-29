"""API smoke test via FastAPI TestClient. TMDB network calls are monkeypatched
(they're blocked in CI and irrelevant to routing/DB/search correctness)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from app import tmdb  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Movie, MovieIndex  # noqa: E402
from app.db import session_scope  # noqa: E402

# Stub TMDB before importing the app so no real network is attempted.
_FAKE = {
    777: {
        "id": 777, "title": "The Matrix", "original_title": "The Matrix",
        "release_year": 1999, "release_date": "1999-03-31", "poster_path": "/matrix.jpg",
        "backdrop_path": None, "overview": "A hacker learns the truth.", "runtime": 136,
        "vote_average": 8.2, "vote_count": 26000,
        "genres": ["Action", "Sci-Fi"], "director": "The Wachowskis",
    },
    778: {
        "id": 778, "title": "The Matrix Reloaded", "original_title": "The Matrix Reloaded",
        "release_year": 2003, "release_date": "2003-05-15", "poster_path": "/reloaded.jpg",
        "backdrop_path": None, "overview": "Neo fights on.", "runtime": 138,
        "vote_average": 7.0, "vote_count": 10000,
        "genres": ["Action", "Sci-Fi"], "director": "The Wachowskis",
    },
}
tmdb.fetch_movie_details = lambda mid: _FAKE[mid]

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
import app.config as config  # noqa: E402
config.TMDB_API_KEY = config.TMDB_API_KEY or "test-key"  # pretend configured

PASS = "\033[92mPASS\033[0m"; FAIL = "\033[91mFAIL\033[0m"
fails = []
def check(c, m):
    print(f"  [{PASS if c else FAIL}] {m}");
    if not c: fails.append(m)


def seed():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE ranking, movie, movie_index RESTART IDENTITY CASCADE"))
    with session_scope() as s:
        for mid, title, pop in [
            (777, "The Matrix", 90.0),
            (778, "The Matrix Reloaded", 70.0),
            (999, "Matrix Revolutions", 40.0),
            (5, "Unrelated Film", 5.0),
        ]:
            s.merge(MovieIndex(id=mid, original_title=title, popularity=pop))


def main():
    with TestClient(app) as client:  # triggers startup: init_db + scheduler
        seed()

        print("\nStatus endpoint")
        r = client.get("/api/status").json()
        check("index" in r and r["tmdb_configured"], "status returns index + tmdb flag")

        print("\nSearch (local index, popularity-ordered, ILIKE)")
        res = client.get("/api/search?q=matrix").json()["results"]
        titles = [x["title"] for x in res]
        check(titles[0] == "The Matrix", f"most popular match first (got {titles})")
        check(all("matrix" in t.lower() for t in titles), "all results contain query")
        check("Unrelated Film" not in titles, "non-matches excluded")

        print("\nSearch: multi-word, order-independent, partial tokens")
        res = client.get("/api/search?q=reloaded matrix").json()["results"]
        check([x["title"] for x in res] == ["The Matrix Reloaded"],
              "out-of-order words match (reloaded matrix -> Reloaded)")
        res = client.get("/api/search?q=mat rel").json()["results"]
        check(any(x["title"] == "The Matrix Reloaded" for x in res),
              "partial tokens match (mat rel -> Reloaded)")

        print("\nStart ranking (enrich-on-add caches details, first movie instant)")
        start = client.post("/api/rankings/start", json={"movie_id": 777}).json()
        check(start["done"] and start["position"] == 0, "first movie ranked instantly at 0")
        with session_scope() as s:
            cached = s.get(Movie, 777)
            check(cached is not None and cached.genres == ["Action", "Sci-Fi"],
                  "movie details cached in DB after add")

        print("\nSearch now flags already-ranked + shows rank/score")
        res = client.get("/api/search?q=matrix").json()["results"]
        matrix = next(x for x in res if x["id"] == 777)
        check(matrix["already_ranked"] is True, "ranked movie flagged in search")
        check(matrix.get("rank") == 1 and matrix.get("score") == 10.0,
              "search result carries personal rank + score")
        unranked = next(x for x in res if x["id"] == 778)
        check("rank" not in unranked and "score" not in unranked,
              "unranked results omit rank/score")

        print("\nRankings list + score")
        ranks = client.get("/api/rankings").json()["rankings"]
        check(len(ranks) == 1 and ranks[0]["score"] == 10.0, "list shows the movie at 10.0")
        check(ranks[0]["poster_path"] == "/matrix.jpg", "poster_path present for saved movie")
        check(ranks[0]["vote_average"] == 8.2 and ranks[0]["vote_count"] == 26000,
              "TMDB rating (vote_average + vote_count) cached and returned")

        print("\nNotes: set, update, appears in list, 404 when unranked")
        r = client.put("/api/rankings/777/note", json={"notes": "  Loved the bullet-time  "})
        check(r.status_code == 200 and r.json()["note"] == "Loved the bullet-time",
              "PUT note trims + stores")
        note = client.get("/api/rankings").json()["rankings"][0]["note"]
        check(note == "Loved the bullet-time", "note surfaces in rankings list")
        client.put("/api/rankings/777/note", json={"notes": "Changed my mind"})
        check(client.get("/api/rankings").json()["rankings"][0]["note"] == "Changed my mind",
              "PUT note updates in place")
        client.put("/api/rankings/777/note", json={"notes": ""})
        check(client.get("/api/rankings").json()["rankings"][0]["note"] is None,
              "empty note clears to null")
        r404 = client.put("/api/rankings/424242/note", json={"notes": "x"})
        check(r404.status_code == 404, "note on unranked movie -> 404")

        print("\nWatchlist: add, list, search flag, 409 if ranked, auto-remove on rank")
        r = client.post("/api/watchlist", json={"movie_id": 778})
        check(r.status_code == 200, "add unranked movie to watchlist")
        wl = client.get("/api/watchlist").json()["watchlist"]
        check(len(wl) == 1 and wl[0]["id"] == 778, "watchlist lists the movie with details")
        res = client.get("/api/search?q=matrix").json()["results"]
        r778 = next(x for x in res if x["id"] == 778)
        check(r778["in_watchlist"] is True and not r778["already_ranked"],
              "search flags watchlisted (not ranked) movie")
        conflict = client.post("/api/watchlist", json={"movie_id": 777})  # already ranked
        check(conflict.status_code == 409, "cannot watchlist an already-ranked movie")
        # Rank the watchlisted movie -> it should leave the watchlist automatically.
        step = client.post("/api/rankings/start", json={"movie_id": 778}).json()
        while not step.get("done"):
            step = client.post("/api/rankings/compare",
                               json={"placement_id": step["placement_id"], "prefer_new": False}).json()
        check(client.get("/api/watchlist").json()["watchlist"] == [],
              "ranking a watchlisted movie auto-removes it from the watchlist")
        # Add then delete
        client.post("/api/watchlist", json={"movie_id": 778})  # 778 is ranked now -> 409, ignore
        added = client.get("/api/watchlist").json()["watchlist"]
        check(added == [], "ranked movie stays out of watchlist")

        print("\nStatic frontend served at /")
        html = client.get("/")
        check(html.status_code == 200 and "movie" in html.text.lower(), "index.html served at /")
        man = client.get("/manifest.json")
        check(man.status_code == 200 and man.json()["name"] == "movieBeli", "manifest served")

    print("\n" + "=" * 40)
    if fails:
        print(f"{FAIL}: {len(fails)} failed"); [print("  -", f) for f in fails]; sys.exit(1)
    print(f"{PASS}: all API checks passed")


if __name__ == "__main__":
    main()
