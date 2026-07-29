def is_nonadult(movie: map) -> bool:
    return not movie['adult']

def is_movie(movie: map) -> bool:
    return movie['video']