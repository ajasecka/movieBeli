import { api } from "./api.js";

// ---------- State ----------
let IMAGE_BASE = "https://image.tmdb.org/t/p";
const state = { view: "rankings", rankings: [], searchTimer: null };

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

// Score color scale: green (high) -> amber -> red (low).
function scoreColor(score) {
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
      $("#empty-add").onclick = () => switchView("search");
      return;
    }
    root.innerHTML = `<div class="section-title">Your rankings · ${rankings.length}</div>
      <div class="rank-list"></div>`;
    const list = root.querySelector(".rank-list");
    rankings.forEach((m) => list.appendChild(rankCard(m)));
  } catch (e) {
    root.innerHTML = `<div class="hint">Couldn't load rankings.<br/>${esc(e.message)}</div>`;
  }
}

function rankCard(m) {
  const sub = [m.release_year, m.director].filter(Boolean).join(" · ");
  const card = el(`
    <div class="rank-card">
      <div class="rank-num">${m.rank}</div>
      ${posterHtml(m, "w185")}
      <div class="rank-meta">
        <div class="rank-title">${esc(m.title)}</div>
        <div class="rank-sub">${esc(sub)}</div>
        ${genreChips(m.genres)}
      </div>
      <div class="score" style="background:${scoreColor(m.score)}">${m.score.toFixed(1)}</div>
    </div>`);
  // Long-press / double-tap to remove.
  card.addEventListener("dblclick", () => confirmRemove(m));
  return card;
}

async function confirmRemove(m) {
  if (!confirm(`Remove "${m.title}" from your rankings?`)) return;
  await api.remove(m.id);
  toast("Removed");
  renderRankings();
}

// ---------- Search ----------
function renderSearchShell() {
  const root = $("#view-search");
  root.innerHTML = `
    <div class="search-wrap">
      <input class="search-input" id="search-input" type="search"
             placeholder="Search a movie you've seen…" autocomplete="off" />
    </div>
    <div class="result-list" id="result-list"></div>
    <div class="hint" id="search-hint">Type a title to get started.</div>`;
  const input = $("#search-input");
  input.addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => doSearch(input.value), 220);
  });
}

async function doSearch(q) {
  const listEl = $("#result-list");
  const hint = $("#search-hint");
  q = q.trim();
  if (!q) { listEl.innerHTML = ""; hint.style.display = "block"; hint.textContent = "Type a title to get started."; return; }
  hint.style.display = "none";
  try {
    const { results } = await api.search(q);
    if (!results.length) {
      listEl.innerHTML = "";
      hint.style.display = "block";
      hint.textContent = `No matches for "${q}".`;
      return;
    }
    listEl.innerHTML = "";
    results.forEach((r) => {
      const row = el(`
        <div class="result-row ${r.already_ranked ? "ranked" : ""}">
          <div class="result-title">${esc(r.title)}</div>
          <div class="go">${r.already_ranked ? "Ranked ✓" : "Add →"}</div>
        </div>`);
      if (!r.already_ranked) row.onclick = () => openPreview(r.id);
      listEl.appendChild(row);
    });
  } catch (e) {
    hint.style.display = "block";
    hint.textContent = e.message;
  }
}

// ---------- Overlays ----------
function closeOverlay() { $("#overlay-root").innerHTML = ""; }

async function openPreview(movieId) {
  const root = $("#overlay-root");
  root.innerHTML = `<div class="overlay"><div class="overlay-head">
      <button class="close">✕</button></div>
      <div class="overlay-body"><div class="spinner"></div></div></div>`;
  root.querySelector(".close").onclick = closeOverlay;
  try {
    const { movie } = await api.movie(movieId);
    const body = root.querySelector(".overlay-body");
    body.innerHTML = `
      <div class="preview">
        ${posterHtml(movie, "w500", "poster lg")}
        <h2>${esc(movie.title)}</h2>
        <div class="year">${[movie.release_year, movie.director].filter(Boolean).map(esc).join(" · ")}</div>
        ${genreChips(movie.genres)}
        ${movie.overview ? `<p class="overview">${esc(movie.overview)}</p>` : ""}
        <button class="btn-primary" id="start-rank">Add &amp; Rank</button>
      </div>`;
    body.querySelector("#start-rank").onclick = () => startRanking(movie);
  } catch (e) {
    root.querySelector(".overlay-body").innerHTML = `<div class="hint">${esc(e.message)}</div>`;
  }
}

async function startRanking(movie) {
  try {
    const first = await api.startRanking(movie.id);
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
          </div>
          <div class="vs-card" data-prefer="against">
            <div class="badge-new" style="color:var(--text-dim)">Ranked</div>
            ${posterHtml(step.against, "w342")}
            <div class="vs-title">${esc(step.against.title)}</div>
            <div class="vs-year">${esc(step.against.release_year || "")}</div>
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
}

function finishPlacement(result, movie) {
  closeOverlay();
  if (result.already_ranked) {
    toast(`${movie.title} is already ranked`);
  } else {
    toast(`${movie.title} ranked #${result.position + 1} · ${result.score.toFixed(1)}`);
  }
  switchView("rankings");
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
  renderSearchShell();
  renderRankings();
  loadStatus();
  setInterval(loadStatus, 30000);

  // Register service worker (only succeeds over HTTPS or localhost).
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

init();
