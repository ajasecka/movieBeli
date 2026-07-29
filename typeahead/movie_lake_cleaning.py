import json
from typing import List


def filter_lake(file_name: str, funcs: list):
    movie_list = []
    global_funcs = globals()

    with open(f'./{file_name}', 'r') as fp:
        for movie in fp:
            movie.strip('\n')
            movie_json = json.loads(movie)

            # filters
            if not movie_json['adult'] \
                and movie_json['video']:
                movie_list.append(movie_json)

    return movie_list


out = filter_lake('movie_ids_10_25_2025.json', ['movies_nonadult'])
print(out)
