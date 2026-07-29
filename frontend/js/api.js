// Tiny fetch wrapper around the backend API.

async function req(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  status: () => req("/api/status"),
  search: (q) => req(`/api/search?q=${encodeURIComponent(q)}`),
  movie: (id) => req(`/api/movies/${id}`),
  rankings: () => req("/api/rankings"),
  startRanking: (movieId) =>
    req("/api/rankings/start", { method: "POST", body: JSON.stringify({ movie_id: movieId }) }),
  compare: (placementId, preferNew) =>
    req("/api/rankings/compare", {
      method: "POST",
      body: JSON.stringify({ placement_id: placementId, prefer_new: preferNew }),
    }),
  remove: (movieId) => req(`/api/rankings/${movieId}`, { method: "DELETE" }),
  setNote: (movieId, notes) =>
    req(`/api/rankings/${movieId}/note`, { method: "PUT", body: JSON.stringify({ notes }) }),
  watchlist: () => req("/api/watchlist"),
  addWatchlist: (movieId) =>
    req("/api/watchlist", { method: "POST", body: JSON.stringify({ movie_id: movieId }) }),
  removeWatchlist: (movieId) => req(`/api/watchlist/${movieId}`, { method: "DELETE" }),
};
