import { api } from "./api.js";

// ---------- State ----------
let IMAGE_BASE = "https://image.tmdb.org/t/p";
const state = { view: "rankings", rankings: [], watchlist: [] };

// ---------- Helpers ----------
const $ = (sel) => document.querySelector(sel);
const el = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

const posterUrl = (path, size = "w500") => (path ? `${IMAGE_BASE}/${size}${path}` : null);

function posterHtml(movie, size = "w342", cls = "poster") {
  const url = posterUrl(movie.poster_path, size);
  if (url) return `<img class="${cls}" src="${url}" alt="${esc(movie.title)} poster" loading="lazy" />`;
  return `<div class="${cls}">🎬</div>`;
}

function scoreColor(score, tier) {
  if (tier === "loved")    return "#16a34a";  // green
  if (tier === "liked")    return "#ca8a04";  // amber
  if (tier === "disliked") return "#dc2626";  // red
  // Gradient fallback for legacy movies without a stored tier.
  const hue = Math.max(0, Math.min(120, (score / 10) * 120));
  return `hsl(${hue}, 62%, 42%)`;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2200);
}

// TMDB community rating badge (the rating the movie site itself provides).
function ratingHtml(m) {
  if (m.vote_average == null || m.vote_average === 0) return "";
  const votes = m.vote_count ? ` · ${m.vote_count.toLocaleString()} votes` : "";
  return `<div class="tmdb-rating">
    <span class="star">★</span>
    <span class="rating-val">${m.vote_average.toFixed(1)}</span>
    <span class="rating-max">/10</span>
    <span class="rating-src">TMDB${votes}</span>
  </div>`;
}

function runtimeText(min) {
  if (!min) return "";
  const h = Math.floor(min / 60), r = min % 60;
  return h ? `${h}h ${r}m` : `${r}m`;
}

function genreChips(genres = []) {
  if (!genres.length) return "";
  return `<div class="genre-chips">${genres
    .slice(0, 3)
    .map((g) => `<span class="chip">${esc(g)}</span>`)
    .join("")}</div>`;
}

// ---------- Views ----------
function switchView(name) {
  state.view = name;
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $(`#view-${name}`).classList.add("active");
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === name)
  );
  if (name === "rankings") renderRankings();
  else if (name === "watchlist") renderWatchlist();
}

// Re-render whatever list-bearing view is active (after add/remove/rank).
function refreshAfterListChange() {
  if (state.view === "watchlist") renderWatchlist();
  if (state.view === "rankings") renderRankings();
}

async function renderRankings() {
  const root = $("#view-rankings");
  root.innerHTML = `<div class="spinner"></div>`;
  try {
    const { rankings } = await api.rankings();
    state.rankings = rankings;
    if (!rankings.length) {
      root.innerHTML = `
        <div class="empty">
          <div class="big">🎬</div>
          <h2>No movies ranked yet</h2>
          <p>Search for a film you've seen and place it<br/>against the ones you already love.</p>
          <button class="cta" id="empty-add">Add your first movie</button>
        </div>`;
      $("#empty-add").onclick = () => openAddOverlay();
      return;
    }
    root.innerHTML = `
      <div class="list-filter-wrap">
        <input class="list-filter" id="rankings-filter" type="search" placeholder="Filter rankings…" autocomplete="off" />
      </div>
      <div class="section-title" id="rankings-title">Your rankings · ${rankings.length}</div>
      <div class="rank-list" id="rankings-list"></div>`;
    const list = root.querySelector("#rankings-list");
    const filterInput = root.querySelector("#rankings-filter");
    const titleEl = root.querySelector("#rankings-title");

    const applyFilter = (q) => {
      const filtered = q
        ? rankings.filter((m) =>
            m.title.toLowerCase().includes(q.toLowerCase()) ||
            (m.director && m.director.toLowerCase().includes(q.toLowerCase()))
          )
        : rankings;
      titleEl.textContent = q
        ? `${filtered.length} of ${rankings.length} match${filtered.length === 1 ? "" : "es"}`
        : `Your rankings · ${rankings.length}`;
      list.innerHTML = "";
      filtered.forEach((m) => list.appendChild(rankCard(m)));
      if (!q) enableDragAndDrop(list);
    };

    applyFilter("");
    filterInput.addEventListener("input", () => applyFilter(filterInput.value.trim()));
  } catch (e) {
    root.innerHTML = `<div class="hint">Couldn't load rankings.<br/>${esc(e.message)}</div>`;
  }
}

function rankCard(m) {
  const sub = [m.release_year, m.director].filter(Boolean).join(" · ");
  const card = el(`
    <div class="rank-card" draggable="true" data-movie-id="${m.id}">
      <div class="drag-handle" title="Drag to reorder">⠿</div>
      <div class="rank-num">${m.rank}</div>
      ${posterHtml(m, "w185")}
      <div class="rank-meta">
        <div class="rank-title">${esc(m.title)}</div>
        <div class="rank-sub">${esc(sub)}</div>
        ${genreChips(m.genres)}
        ${m.note ? `<div class="note-line">📝 ${esc(m.note)}</div>` : ""}
      </div>
      <div class="score" style="background:${scoreColor(m.score, m.tier)}">${m.score.toFixed(1)}</div>
    </div>`);
  card.addEventListener("click", () => openDetail(m));
  return card;
}

// ---------- Drag-and-drop reordering ----------
function clearDropIndicators(list) {
  list.querySelectorAll(".drag-over-top, .drag-over-bottom").forEach((c) => {
    c.classList.remove("drag-over-top", "drag-over-bottom");
  });
}

async function performDrop(list, dragSrc, target, clientY) {
  clearDropIndicators(list);
  const rect = target.getBoundingClientRect();
  if (clientY < rect.top + rect.height / 2) {
    list.insertBefore(dragSrc, target);
  } else {
    list.insertBefore(dragSrc, target.nextSibling);
  }
  // Update rank numbers immediately so the list feels snappy.
  [...list.querySelectorAll(".rank-card")].forEach((c, i) => {
    c.querySelector(".rank-num").textContent = i + 1;
  });
  const orderedIds = [...list.querySelectorAll(".rank-card")].map((c) =>
    parseInt(c.dataset.movieId)
  );

  // If the movie crossed into a different tier, adopt the target's tier.
  const draggedId = parseInt(dragSrc.dataset.movieId);
  const targetId = parseInt(target.dataset.movieId);
  const tierById = Object.fromEntries(state.rankings.map((m) => [m.id, m.tier]));
  const oldTier = tierById[draggedId] ?? null;
  const newTier = tierById[targetId] ?? null;
  const tierUpdates = newTier && newTier !== oldTier ? { [draggedId]: newTier } : {};

  try {
    const { rankings } = await api.reorder(orderedIds, tierUpdates);
    // Patch scores and tier colors in-place without a full re-render.
    rankings.forEach((m) => {
      const c = list.querySelector(`[data-movie-id="${m.id}"]`);
      if (!c) return;
      const scoreEl = c.querySelector(".score");
      scoreEl.style.background = scoreColor(m.score, m.tier);
      scoreEl.textContent = m.score.toFixed(1);
    });
    state.rankings = rankings;
  } catch (_) {
    toast("Failed to save new order");
    renderRankings();
  }
}

function enableDragAndDrop(list) {
  let dragSrc = null;

  // --- HTML5 drag (desktop/Chrome-Android) ---
  list.querySelectorAll(".rank-card").forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      dragSrc = card;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", card.dataset.movieId);
      requestAnimationFrame(() => card.classList.add("dragging"));
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      clearDropIndicators(list);
      dragSrc = null;
    });
  });

  list.addEventListener("dragover", (e) => {
    const target = e.target.closest(".rank-card");
    if (!target || target === dragSrc) return;
    e.preventDefault();
    clearDropIndicators(list);
    const rect = target.getBoundingClientRect();
    target.classList.add(
      e.clientY < rect.top + rect.height / 2 ? "drag-over-top" : "drag-over-bottom"
    );
  });

  list.addEventListener("dragleave", (e) => {
    if (!list.contains(e.relatedTarget)) clearDropIndicators(list);
  });

  list.addEventListener("drop", async (e) => {
    const target = e.target.closest(".rank-card");
    if (!target || !dragSrc || target === dragSrc) return;
    e.preventDefault();
    await performDrop(list, dragSrc, target, e.clientY);
  });

  // --- Touch drag (iOS/mobile) ---
  let touchSrc = null;
  let touchStartY = 0;
  let touchTimer = null;
  let touchActive = false;

  list.addEventListener("touchstart", (e) => {
    if (!e.target.closest(".drag-handle")) return;
    const card = e.target.closest(".rank-card");
    touchSrc = card;
    touchStartY = e.touches[0].clientY;
    touchActive = false;
    touchTimer = setTimeout(() => {
      touchActive = true;
      card.classList.add("dragging");
    }, 200);
  }, { passive: true });

  list.addEventListener("touchmove", (e) => {
    if (!touchSrc) return;
    const dy = Math.abs(e.touches[0].clientY - touchStartY);
    if (!touchActive) {
      if (dy > 8) { clearTimeout(touchTimer); touchSrc = null; }
      return;
    }
    e.preventDefault();
    const y = e.touches[0].clientY;
    clearDropIndicators(list);
    const target = [...list.querySelectorAll(".rank-card")].find((c) => {
      if (c === touchSrc) return false;
      const r = c.getBoundingClientRect();
      return y >= r.top && y <= r.bottom;
    });
    if (target) {
      const r = target.getBoundingClientRect();
      target.classList.add(y < r.top + r.height / 2 ? "drag-over-top" : "drag-over-bottom");
    }
  }, { passive: false });

  list.addEventListener("touchend", async (e) => {
    clearTimeout(touchTimer);
    if (!touchSrc || !touchActive) { touchSrc = null; touchActive = false; return; }
    const y = e.changedTouches[0].clientY;
    const target = [...list.querySelectorAll(".rank-card")].find((c) => {
      if (c === touchSrc) return false;
      const r = c.getBoundingClientRect();
      return y >= r.top && y <= r.bottom;
    });
    touchSrc.classList.remove("dragging");
    if (target) await performDrop(list, touchSrc, target, y);
    else clearDropIndicators(list);
    touchSrc = null;
    touchActive = false;
  }, { passive: true });
}

// ---------- Watchlist ----------
async function renderWatchlist() {
  const root = $("#view-watchlist");
  root.innerHTML = `<div class="spinner"></div>`;
  try {
    const { watchlist } = await api.watchlist();
    state.watchlist = watchlist;
    if (!watchlist.length) {
      root.innerHTML = `
        <div class="empty">
          <div class="big">🔖</div>
          <h2>Your watchlist is empty</h2>
          <p>Save movies you want to see. When you've<br/>watched one, rank it from here.</p>
          <button class="cta" id="empty-wl-add">Find something to watch</button>
        </div>`;
      $("#empty-wl-add").onclick = () => openAddOverlay();
      return;
    }
    root.innerHTML = `
      <div class="list-filter-wrap">
        <input class="list-filter" id="watchlist-filter" type="search" placeholder="Filter watchlist…" autocomplete="off" />
      </div>
      <div class="section-title" id="watchlist-title">Want to watch · ${watchlist.length}</div>
      <div class="rank-list" id="watchlist-list"></div>`;
    const list = root.querySelector("#watchlist-list");
    const filterInput = root.querySelector("#watchlist-filter");
    const titleEl = root.querySelector("#watchlist-title");

    const applyFilter = (q) => {
      const filtered = q
        ? watchlist.filter((m) =>
            m.title.toLowerCase().includes(q.toLowerCase()) ||
            (m.director && m.director.toLowerCase().includes(q.toLowerCase()))
          )
        : watchlist;
      titleEl.textContent = q
        ? `${filtered.length} of ${watchlist.length} match${filtered.length === 1 ? "" : "es"}`
        : `Want to watch · ${watchlist.length}`;
      list.innerHTML = "";
      filtered.forEach((m) => list.appendChild(watchCard(m)));
    };

    applyFilter("");
    filterInput.addEventListener("input", () => applyFilter(filterInput.value.trim()));
  } catch (e) {
    root.innerHTML = `<div class="hint">Couldn't load watchlist.<br/>${esc(e.message)}</div>`;
  }
}

function watchCard(m) {
  const sub = [m.release_year, m.director, runtimeText(m.runtime)].filter(Boolean).join(" · ");
  const rating = m.vote_average
    ? `<span class="wl-rating"><span class="star">★</span> ${m.vote_average.toFixed(1)}</span>`
    : "";
  const card = el(`
    <div class="rank-card">
      <div class="wl-mark">🔖</div>
      ${posterHtml(m, "w185")}
      <div class="rank-meta">
        <div class="rank-title">${esc(m.title)}</div>
        <div class="rank-sub">${esc(sub)}</div>
        <div class="genre-chips">${(m.genres || []).slice(0, 2).map((g) => `<span class="chip">${esc(g)}</span>`).join("")}${rating}</div>
      </div>
      <button class="wl-rank-btn">Rank</button>
    </div>`);
  // Tap the card -> detail; tap Rank -> jump straight into placement.
  card.addEventListener("click", () => openPreview(m.id, true));
  card.querySelector(".wl-rank-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    startRanking(m);
  });
  return card;
}

async function addToWatchlist(movie) {
  try {
    await api.addWatchlist(movie.id);
    toast(`Added ${movie.title} to watchlist`);
    closeOverlay();
    refreshAfterListChange();
  } catch (e) {
    toast(e.message);
  }
}

async function removeFromWatchlist(movie) {
  try {
    await api.removeWatchlist(movie.id);
    toast("Removed from watchlist");
    closeOverlay();
    refreshAfterListChange();
  } catch (e) {
    toast(e.message);
  }
}

// From a search result, look up the full ranked entry and open its detail.
async function openRankedDetail(id) {
  try {
    const { rankings } = await api.rankings();
    const m = rankings.find((x) => x.id === id);
    if (m) openDetail(m);
    else toast("That movie isn't in your rankings anymore.");
  } catch (e) {
    toast(e.message);
  }
}

// Detail sheet: edit the note on an already-ranked movie, or remove it.
function openDetail(m) {
  const root = $("#overlay-root");
  root.innerHTML = `
    <div class="overlay">
      <div class="overlay-head"><button class="close">✕</button></div>
      <div class="overlay-body">
        <div class="preview">
          ${posterHtml(m, "w500", "poster lg")}
          <h2>${esc(m.title)}</h2>
          <div class="year">${[m.release_year, m.director, runtimeText(m.runtime)].filter(Boolean).map(esc).join(" · ")}</div>
          <div class="rating-row">
            <div class="your-rank">
              <span class="mini-score" style="background:${scoreColor(m.score, m.tier)}">${m.score.toFixed(1)}</span>
              <span class="rank-hash">Your #${m.rank}</span>
            </div>
            ${ratingHtml(m)}
          </div>
          ${genreChips(m.genres)}
          ${m.overview ? `<p class="overview">${esc(m.overview)}</p>` : ""}
          <div class="note-editor">
            <label class="note-label">Your notes</label>
            <textarea class="note-input" id="note-input"
              placeholder="What did you think? (hidden by default on the compare screen)">${esc(m.note || "")}</textarea>
            <button class="btn-primary" id="save-note">Save notes</button>
            <button class="btn-danger" id="remove-movie">Remove from rankings</button>
          </div>
        </div>
      </div>
    </div>`;
  root.querySelector(".close").onclick = closeOverlay;
  root.querySelector("#save-note").onclick = async () => {
    try {
      await api.setNote(m.id, root.querySelector("#note-input").value);
      toast("Notes saved");
      closeOverlay();
      renderRankings();
    } catch (e) { toast(e.message); }
  };
  root.querySelector("#remove-movie").onclick = async () => {
    if (!confirm(`Remove "${m.title}" from your rankings?`)) return;
    try {
      await api.remove(m.id);
      toast("Removed");
      closeOverlay();
      renderRankings();
    } catch (e) { toast(e.message); }
  };
}

// ---------- Add-movie overlay ----------
function openAddOverlay() {
  const root = $("#overlay-root");
  root.innerHTML = `
    <div class="overlay">
      <div class="add-overlay-head">
        <input class="add-search-input" id="add-search-input" type="search"
               placeholder="Search a movie…" autocomplete="off" />
        <button class="close">✕</button>
      </div>
      <div class="overlay-body" style="padding-top: 8px;">
        <div class="result-list" id="add-result-list"></div>
        <div class="hint" id="add-search-hint">Type a title to get started.</div>
      </div>
    </div>`;
  root.querySelector(".close").onclick = closeOverlay;
  const input = root.querySelector("#add-search-input");
  const listEl = root.querySelector("#add-result-list");
  const hintEl = root.querySelector("#add-search-hint");
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => _doSearch(input.value, listEl, hintEl), 220);
  });
  requestAnimationFrame(() => input.focus());
}

async function _doSearch(q, listEl, hintEl) {
  q = q.trim();
  if (!q) {
    listEl.innerHTML = "";
    hintEl.style.display = "block";
    hintEl.textContent = "Type a title to get started.";
    return;
  }
  hintEl.style.display = "none";
  try {
    const { results } = await api.search(q);
    if (!results.length) {
      listEl.innerHTML = "";
      hintEl.style.display = "block";
      hintEl.textContent = `No matches for "${q}".`;
      return;
    }
    listEl.innerHTML = "";
    const preferWatchlist = state.view === "watchlist";
    results.forEach((r) => {
      let right;
      if (r.already_ranked) {
        right = `<div class="result-rank">
             <span class="mini-score" style="background:${scoreColor(r.score, r.tier)}">${r.score.toFixed(1)}</span>
             <span class="rank-hash">#${r.rank}</span>
           </div>`;
      } else if (r.in_watchlist) {
        right = `<div class="go wl">🔖 Watchlist</div>`;
      } else {
        right = preferWatchlist ? `<div class="go wl">🔖 Save</div>` : "";
      }
      const metaParts = [];
      if (r.release_year) metaParts.push(r.release_year);
      if (r.vote_average) {
        const count = r.vote_count ? ` (${r.vote_count.toLocaleString()})` : "";
        metaParts.push(`★ ${r.vote_average.toFixed(1)}${count}`);
      }
      if (r.genres && r.genres.length) metaParts.push(r.genres.slice(0, 2).join(", "));
      const metaHtml = metaParts.length
        ? `<div class="result-meta">${esc(metaParts.join(" · "))}</div>`
        : "";
      const row = el(`
        <div class="result-row ${r.already_ranked ? "ranked" : ""}">
          <div class="result-title-wrap">
            <div class="result-title">${esc(r.title)}</div>
            ${metaHtml}
          </div>
          ${right}
        </div>`);
      row.onclick = () =>
        r.already_ranked ? openRankedDetail(r.id) : openPreview(r.id, r.in_watchlist, preferWatchlist);
      listEl.appendChild(row);
    });
  } catch (e) {
    hintEl.style.display = "block";
    hintEl.textContent = e.message;
  }
}

// ---------- Overlays ----------
function closeOverlay() { $("#overlay-root").innerHTML = ""; }

async function openPreview(movieId, inWatchlist = false, preferWatchlist = false) {
  const root = $("#overlay-root");
  root.innerHTML = `<div class="overlay"><div class="overlay-head">
      <button class="close">✕</button></div>
      <div class="overlay-body"><div class="spinner"></div></div></div>`;
  root.querySelector(".close").onclick = closeOverlay;
  try {
    const { movie } = await api.movie(movieId);
    // In watchlist -> Rank it / Remove. Otherwise order buttons by context.
    let actions;
    if (inWatchlist) {
      actions = `<button class="btn-primary" id="start-rank">I watched it — Rank it</button>
                 <button class="btn-danger" id="wl-remove">Remove from watchlist</button>`;
    } else if (preferWatchlist) {
      actions = `<button class="btn-primary" id="wl-add">🔖 Add to Watchlist</button>
                 <button class="btn-secondary" id="start-rank">Add &amp; Rank</button>`;
    } else {
      actions = `<button class="btn-primary" id="start-rank">Add &amp; Rank</button>
                 <button class="btn-secondary" id="wl-add">🔖 Add to Watchlist</button>`;
    }
    const body = root.querySelector(".overlay-body");
    body.innerHTML = `
      <div class="preview">
        ${posterHtml(movie, "w500", "poster lg")}
        <h2>${esc(movie.title)}</h2>
        <div class="year">${[movie.release_year, movie.director, runtimeText(movie.runtime)].filter(Boolean).map(esc).join(" · ")}</div>
        ${ratingHtml(movie)}
        ${genreChips(movie.genres)}
        ${movie.overview ? `<p class="overview">${esc(movie.overview)}</p>` : ""}
        ${actions}
      </div>`;
    body.querySelector("#start-rank").onclick = () => startRanking(movie);
    const addBtn = body.querySelector("#wl-add");
    if (addBtn) addBtn.onclick = () => addToWatchlist(movie);
    const rmBtn = body.querySelector("#wl-remove");
    if (rmBtn) rmBtn.onclick = () => removeFromWatchlist(movie);
  } catch (e) {
    root.querySelector(".overlay-body").innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}

function startRanking(movie) {
  renderTierSelection(movie);
}

function renderTierSelection(movie) {
  const root = $("#overlay-root");
  root.innerHTML = `
    <div class="overlay">
      <div class="overlay-head"><button class="close">✕</button></div>
      <div class="overlay-body">
        <div class="tier-screen">
          ${posterHtml(movie, "w342", "poster lg")}
          <h2>${esc(movie.title)}</h2>
          <p class="tier-q">How did you feel about it?</p>
          <div class="tier-options">
            <button class="tier-btn" data-tier="loved">
              <span class="tier-icon">❤️</span>
              <div class="tier-text">
                <span class="tier-label">Loved it</span>
                <span class="tier-sub">One of my favourites</span>
              </div>
            </button>
            <button class="tier-btn" data-tier="liked">
              <span class="tier-icon">👍</span>
              <div class="tier-text">
                <span class="tier-label">Liked it</span>
                <span class="tier-sub">Solid, worth watching</span>
              </div>
            </button>
            <button class="tier-btn" data-tier="disliked">
              <span class="tier-icon">👎</span>
              <div class="tier-text">
                <span class="tier-label">Didn't like it</span>
                <span class="tier-sub">Not for me</span>
              </div>
            </button>
          </div>
        </div>
      </div>
    </div>`;
  root.querySelector(".close").onclick = closeOverlay;
  root.querySelectorAll(".tier-btn").forEach((btn) => {
    btn.onclick = () => startRankingWithTier(movie, btn.dataset.tier);
  });
}

async function startRankingWithTier(movie, tier) {
  try {
    const first = await api.startRanking(movie.id, tier);
    if (first.done) return finishPlacement(first, movie);
    renderComparison(first, movie);
  } catch (e) {
    toast(e.message);
  }
}

function renderComparison(step, movie) {
  const root = $("#overlay-root");
  const total = Math.max(step.estimated_total || 1, step.comparisons_done + 1);
  const dots = Array.from({ length: total }, (_, i) =>
    `<i class="${i < step.comparisons_done ? "on" : ""}"></i>`
  ).join("");

  root.innerHTML = `
    <div class="overlay">
      <div class="overlay-head"><button class="close">✕</button></div>
      <div class="compare-head">
        <span class="compare-phase">${esc(step.phase_label)}</span>
        <div class="compare-q">Which do you prefer?</div>
        <div class="progress-dots">${dots}</div>
      </div>
      <div class="versus-wrap">
        <div class="versus">
          <div class="vs-card" data-prefer="new">
            <div class="badge-new">Adding</div>
            ${posterHtml(step.new_movie, "w342")}
            <div class="vs-title">${esc(step.new_movie.title)}</div>
            <div class="vs-year">${esc(step.new_movie.release_year || "")}</div>
            ${noteToggleHtml(step.new_note)}
          </div>
          <div class="vs-card" data-prefer="against">
            <div class="badge-new" style="color:var(--text-dim)">Ranked</div>
            ${posterHtml(step.against, "w342")}
            <div class="vs-title">${esc(step.against.title)}</div>
            <div class="vs-year">${esc(step.against.release_year || "")}</div>
            ${noteToggleHtml(step.against_note)}
          </div>
          <div class="vs-mid">VS</div>
        </div>
      </div>
    </div>`;
  root.querySelector(".close").onclick = closeOverlay;
  root.querySelectorAll(".vs-card").forEach((card) => {
    card.onclick = async () => {
      const preferNew = card.dataset.prefer === "new";
      try {
        const next = await api.compare(step.placement_id, preferNew);
        if (next.done) finishPlacement(next, movie);
        else renderComparison(next, movie);
      } catch (e) {
        toast(e.message);
      }
    };
  });
  // "Show notes" toggles the note under a movie without triggering the pick.
  root.querySelectorAll(".show-notes-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const pop = btn.nextElementSibling;
      const nowHidden = !pop.hidden;
      pop.hidden = nowHidden;
      btn.textContent = nowHidden ? "📝 Show notes" : "📝 Hide notes";
    });
  });
  root.querySelectorAll(".note-pop").forEach((p) =>
    p.addEventListener("click", (e) => e.stopPropagation())
  );
}

// Renders a hidden note + a toggle button, or nothing if there's no note.
function noteToggleHtml(note) {
  if (!note) return "";
  return `<button class="show-notes-btn" type="button">📝 Show notes</button>
          <div class="note-pop" hidden>${esc(note)}</div>`;
}

function finishPlacement(result, movie) {
  if (result.already_ranked) {
    closeOverlay();
    toast(`${movie.title} is already ranked`);
    switchView("rankings");
    return;
  }
  renderNotePrompt(result, movie);
}

// After a movie lands, offer to jot a note (optional).
function renderNotePrompt(result, movie) {
  const root = $("#overlay-root");
  root.innerHTML = `
    <div class="overlay">
      <div class="overlay-head"><button class="close">×</button></div>
      <div class="overlay-body">
        <div class="preview">
          ${posterHtml(movie, "w342", "poster lg")}
          <h2>${esc(movie.title)}</h2>
          <div class="year">
            <span class="mini-score" style="background:${scoreColor(result.score, result.tier)}">${result.score.toFixed(1)}</span>
            Ranked #${result.position + 1} in your list
          </div>
          <div class="note-editor">
            <label class="note-label">Add a note <span>(optional)</span></label>
            <textarea class="note-input" id="note-input" placeholder="What stuck with you?"></textarea>
            <button class="btn-primary" id="save-note">Continue</button>
          </div>
        </div>
      </div>
    </div>`;
  root.querySelector(".close").onclick = async () => {
    try { await api.remove(movie.id); } catch (_) {}
    closeOverlay();
  };
  root.querySelector("#save-note").onclick = async () => {
    const val = root.querySelector("#note-input").value;
    try {
      if (val.trim()) await api.setNote(movie.id, val);
      closeOverlay();
      if (val.trim()) toast("Note saved");
      switchView("rankings");
    } catch (e) {
      toast(e.message);
    }
  };
}

// ---------- Status indicator ----------
async function loadStatus() {
  try {
    const s = await api.status();
    if (s.image_base) IMAGE_BASE = s.image_base;
    const count = s.index?.indexed_movies || 0;
    const ok = count > 0;
    const warn = !s.tmdb_configured;
    $("#index-status").innerHTML =
      `<span class="dot ${warn ? "warn" : ok ? "ok" : ""}"></span>` +
      (warn ? "No TMDB key" : ok ? `${count.toLocaleString()} movies` : "Indexing…");
  } catch (_) {
    $("#index-status").innerHTML = `<span class="dot"></span>offline`;
  }
}

// ---------- Boot ----------
function init() {
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => switchView(t.dataset.view);
  });
  $("#add-btn").onclick = () => openAddOverlay();
  renderRankings();
  loadStatus();
  setInterval(loadStatus, 30000);

  // Register service worker (only succeeds over HTTPS or localhost).
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

init();
