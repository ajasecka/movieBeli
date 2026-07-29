"""End-to-end test of the genre-first binary ranking engine against a real
Postgres database. TMDB is stubbed by seeding the movie/movie_index tables
directly, since the ranking logic is what we most need to verify.

Run with:  python -m tests.test_ranking   (from the backend/ dir, DB running)
"""
import os
import sys

# Make `app` importable when run as a script from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import engine, init_db, session_scope  # noqa: E402
from app.models import Movie, MovieIndex, Ranking  # noqa: E402
from app import ranking  # noqa: E402
from sqlalchemy import text  # noqa: E402

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures = []


def check(cond, msg):
    print(f"  [{PASS if cond else FAIL}] {msg}")
    if not cond:
        _failures.append(msg)


def reset():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE ranking, movie, movie_index RESTART IDENTITY CASCADE"))


def seed_movie(mid, title, genres, year=2000):
    with session_scope() as s:
        s.merge(Movie(id=mid, title=title, genres=genres, release_year=year))
        s.merge(MovieIndex(id=mid, original_title=title, popularity=float(mid)))


def current_order():
    return [m["title"] for m in ranking.get_rankings()]


def place(mid, genres, answers):
    """Drive a placement. `answers` is a list of booleans (prefer_new) consumed
    in order. Returns the final result dict."""
    genre_of = genres
    step = ranking.start_placement(mid, genre_of)
    i = 0
    while not step.get("done"):
        prefer_new = answers[i]
        i += 1
        step = ranking.submit_comparison(step["placement_id"], prefer_new)
    return step


def main():
    init_db()

    # -------- Test 1: first movie needs zero comparisons --------
    print("\nTest 1: empty list places instantly")
    reset()
    seed_movie(1, "Alien", ["Sci-Fi", "Horror"])
    res = place(1, ["Sci-Fi", "Horror"], [])
    check(res["done"] and res["position"] == 0, "first movie lands at position 0")
    check(res["score"] == 10.0, "solo movie scores 10.0")

    # -------- Test 2: prefer-new inserts above --------
    print("\nTest 2: preferring the new movie ranks it higher")
    seed_movie(2, "Aliens", ["Sci-Fi", "Action"])
    # Compared against Alien; prefer new (Aliens) -> Aliens above Alien.
    res = place(2, ["Sci-Fi", "Action"], [True])
    check(current_order() == ["Aliens", "Alien"], f"order is Aliens, Alien (got {current_order()})")
    check(res["position"] == 0, "Aliens placed at top")

    # -------- Test 3: prefer-existing inserts below --------
    print("\nTest 3: preferring the existing movie ranks new one lower")
    seed_movie(3, "Prometheus", ["Sci-Fi"])
    # Genre pool [Aliens, Alien]. Binary search midpoint = index1 (Alien).
    # prefer existing each time -> Prometheus goes to the bottom.
    res = place(3, ["Sci-Fi"], [False, False])
    order = current_order()
    check(order[-1] == "Prometheus", f"Prometheus at bottom (got {order})")

    # -------- Test 4: scores are monotonically decreasing, top=10 bottom=0 --------
    print("\nTest 4: derived scores")
    ranks = ranking.get_rankings()
    scores = [m["score"] for m in ranks]
    check(scores[0] == 10.0, "top score is 10.0")
    check(scores[-1] == 0.0, "bottom score is 0.0")
    check(all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)),
          f"scores strictly non-increasing (got {scores})")

    # -------- Test 5: genre-first — cross-genre movie still finds its slot --------
    print("\nTest 5: cross-genre placement (no shared genre -> phase 2 only)")
    reset()
    seed_movie(10, "Heat", ["Crime", "Action"])
    seed_movie(11, "Casino", ["Crime", "Drama"])
    place(10, ["Crime", "Action"], [])
    place(11, ["Crime", "Drama"], [False])  # Casino below Heat
    seed_movie(12, "Amelie", ["Romance", "Comedy"])
    # No shared genre with anything -> straight to full-list binary search.
    # Full list [Heat, Casino]; midpoint index1 (Casino). Prefer new -> hi=1,
    # then midpoint index0 (Heat), prefer existing -> lo=1. Insert at 1 (middle).
    res = place(12, ["Romance", "Comedy"], [True, False])
    check(current_order() == ["Heat", "Amelie", "Casino"],
          f"Amelie placed in the middle (got {current_order()})")

    # -------- Test 6: full binary insertion sort correctness over many movies --------
    print("\nTest 6: 12-movie insertion keeps a consistent total order")
    reset()
    # Ground-truth ranking the 'user' has in mind: higher id = better.
    truth = {i: 100 - i for i in range(1, 13)}
    genres_cycle = [["Action"], ["Drama"], ["Action", "Drama"], ["Comedy"]]
    for i in range(1, 13):
        seed_movie(i, f"M{i:02d}", genres_cycle[i % len(genres_cycle)])

    import random
    random.seed(7)
    order_added = list(range(1, 13))
    random.shuffle(order_added)

    for mid in order_added:
        g = genres_cycle[mid % len(genres_cycle)]
        step = ranking.start_placement(mid, g)
        while not step.get("done"):
            # Answer honestly per ground truth: prefer new if it's truly better.
            new_id = mid
            against_id = step["against"]["id"]
            prefer_new = truth[new_id] > truth[against_id]
            step = ranking.submit_comparison(step["placement_id"], prefer_new)

    final = [int(t[1:]) for t in current_order()]
    expected = sorted(range(1, 13), key=lambda i: -truth[i])
    check(final == expected,
          f"honest answers reproduce the true order\n      expected {expected}\n      got      {final}")

    # -------- Test 7: removal renumbers and rescores --------
    print("\nTest 7: removal keeps positions dense and rescored")
    ranking.remove_ranking(expected[5])  # remove a middle movie
    ranks = ranking.get_rankings()
    positions = [m["position"] for m in ranks]
    check(positions == list(range(len(ranks))), f"positions dense 0..n-1 (got {positions})")
    check(ranks[0]["score"] == 10.0 and ranks[-1]["score"] == 0.0, "rescored after removal")

    print("\n" + ("=" * 40))
    if _failures:
        print(f"{FAIL}: {len(_failures)} check(s) failed")
        for f in _failures:
            print("   -", f)
        sys.exit(1)
    print(f"{PASS}: all checks passed")


if __name__ == "__main__":
    main()
