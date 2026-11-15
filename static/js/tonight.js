// Tonight (#/, the home screen): "where tonight?". Mode chips, who's going, a filter row and Shuffle above the cards
// GET /api/decide returns. The choices live in the address (#/?mode=any&town=Concord) so Back from a place returns to
// the same list; the last mode and audience are also remembered on this device (localStorage, which may be refused).
import { get } from "./api.js";
import { ago, esc, fmtDate, renderError, todayLA, toast, townLabel } from "./core.js";
import { register } from "./router.js";

export const MODES = [
  ["not_lately", "Not lately"], ["want", "Want to try"], ["never_been", "Never been"],
  ["new_in_town", "New in town"], ["occasion", "Occasion"], ["any", "Any"],
];
const MODE_KEYS = MODES.map(([m]) => m);
const AUDIENCES = [["everyone", "Everyone"], ["adults", "Adults"], ["kids", "Kids"]];
const PAGE = 10;
const KEYS = { mode: "hometown.tonight.mode", audience: "hometown.tonight.audience" };
// The filters the address carries, besides mode and audience.
const FILTERS = ["town", "cuisine", "tag", "list", "open_now", "liked_by_all", "near", "seed"];

const EMPTY = {
  not_lately: "Nothing has rested long enough. A place shows here once you've rated it 4 or more and haven't been for a while.",
  want: "Nothing on the want-to-try list that you haven't been to. Star a place to add it.",
  never_been: "You've been to every place on the list.",
  new_in_town: "Nothing new in town since the first scan.",
  occasion: "No place has an occasion tag yet. Mark a tag as an occasion in Settings, then tag a few places.",
  any: "No places yet.",
};

// ---------------------------------------------------------------- remembering (never required to work)
function recall(key) {
  try {
    return globalThis.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function remember(key, value) {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    // private browsing or storage turned off: the choice just isn't remembered
  }
}

// ---------------------------------------------------------------- plain-text helpers (tests/tonight.test.mjs)
// 4.5 -> "4.5", 4 -> "4", 4.333 -> "4.3"; "" for no score.
export function scoreText(score) {
  return Number.isFinite(score) ? String(Math.round(score * 10) / 10) : "";
}

// "3 months ago · Sam 5, Riley 4", or "Not been yet".
export function lastVisitLine(lastVisit, today = todayLA()) {
  if (!lastVisit) return "Not been yet";
  const when = ago(lastVisit.on, today) || fmtDate(lastVisit.on);
  const who = (lastVisit.people ?? []).map(p => (p.rating ? `${p.name} ${p.rating}` : p.name)).join(", ");
  return who ? `${when} · ${who}` : when;
}

// "Open now · closes 9 pm", "Open now", "Closed now" or "Hours unknown".
export function openLine(card) {
  if (card.open === true) return card.closes_at ? `Open now · closes ${card.closes_at}` : "Open now";
  if (card.open === false) return "Closed now";
  return "Hours unknown";
}

// 0.42 -> "420 m", 3.14 -> "3.1 km"; "" without a distance.
export function distanceText(km) {
  if (typeof km !== "number" || !Number.isFinite(km)) return "";
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}

// The query string for GET /api/decide (and the address) from the screen's state; empty values are left out.
export function queryFor(state, extra = {}) {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries({ mode: state.mode, audience: state.audience, ...pick(state), ...extra })) {
    if (value !== undefined && value !== null && value !== "" && value !== false) q.set(key, String(value));
  }
  return q.toString();
}

const pick = state => Object.fromEntries(FILTERS.map(k => [k, state[k]]));

// The starting state: the address first, then what this device remembers, then the defaults.
export function initialState(query, remembered = {}) {
  const state = { mode: "not_lately", audience: "everyone" };
  const mode = query.mode ?? remembered.mode;
  if (MODE_KEYS.includes(mode)) state.mode = mode;
  const audience = query.audience ?? remembered.audience;
  if (audience) state.audience = audience;
  for (const key of FILTERS) state[key] = query[key] ?? "";
  return state;
}

// ---------------------------------------------------------------- markup
const renderOption = (value, label, chosen) => `<option value="${esc(value)}"${renderSelected(String(value) === String(chosen))}>${esc(label)}</option>`;
const renderSelected = on => (on ? " selected" : "");
const renderChecked = on => (on ? " checked" : "");
const renderOptions = (pairs, chosen) => pairs.map(([v, l]) => renderOption(v, l, chosen)).join("");

const renderModeChip = ([mode, label], current) => `<button type="button" class="chip${esc(mode === current ? " on" : "")}" data-mode="${esc(mode)}" aria-pressed="${esc(mode === current)}">${esc(label)}</button>`;
const renderModeChips = current => MODES.map(m => renderModeChip(m, current)).join("");

function audiencePairs(choices, me) {
  const pairs = [...AUDIENCES];
  if (me) pairs.push(["me", `Me (${me.name})`]);
  for (const p of choices.people) pairs.push([p.name, p.name]);
  return pairs;
}

const renderCuisine = c => `<option value="${esc(c)}"></option>`;
const renderCuisines = list => list.map(renderCuisine).join("");

const renderListFilter = (choices, state) => (choices.lists.length
  ? `<label class="f">List<select name="list">${renderOptions([["", "Any list"], ...choices.lists.map(l => [l.id, l.name])], state.list)}</select></label>`
  : "");

const renderFilters = (choices, state) => `<label class="f">Town<select name="town">${renderOptions([["", "Any town"], ...choices.towns.map(t => [t.name, t.name])], state.town)}</select></label>
  <label class="f">Cuisine<input type="text" name="cuisine" list="tonight-cuisines" maxlength="100" value="${esc(state.cuisine)}" placeholder="Any cuisine" autocomplete="off"></label>
  <datalist id="tonight-cuisines">${renderCuisines(choices.cuisines)}</datalist>
  <label class="f">Tag<select name="tag">${renderOptions([["", "Any tag"], ...choices.tags.map(t => [t.id, t.label])], state.tag)}</select></label>
  ${renderListFilter(choices, state)}
  <label class="f">Hours<select name="open_now">${renderOptions([["", "Any time"], ["true", "Open now"], ["strict", "Open now, known hours only"]], state.open_now)}</select></label>`;

const filtersInUse = state => ["town", "cuisine", "tag", "list", "open_now"].some(k => state[k]);

const renderScreen = (choices, me, state) => `<h1>Where tonight?</h1>
  <div class="filters">
    <div class="chips" role="group" aria-label="Mode">${renderModeChips(state.mode)}</div>
    <div class="grid2">
      <label class="f">Who's going<select name="audience">${renderOptions(audiencePairs(choices, me), state.audience)}</select></label>
    </div>
    <details${renderOpen(filtersInUse(state))}><summary>More filters</summary><div class="grid2">${renderFilters(choices, state)}</div></details>
    <div class="row">
      <label class="check"><input type="checkbox" name="liked_by_all"${renderChecked(state.liked_by_all === "true")}> Everyone liked it</label>
      <label class="check"><input type="checkbox" name="near"${renderChecked(Boolean(state.near))}> Near me</label>
      <button type="button" class="primary" data-act="shuffle">Shuffle</button>
    </div>
  </div>
  <p class="count" id="tonight-count" aria-live="polite"></p>
  <div class="list" id="tonight-list"></div>
  <div class="row" id="tonight-more"></div>`;

const renderOpen = on => (on ? " open" : "");

const renderTagBadge = t => `<span class="badge">${esc(t.label)}</span>`;
const renderBadges = card => [
  card.unsure ? `<span class="badge unsure">Not sure it counts</span>` : "",
  card.want_to_try ? `<span class="badge accent">Want to try</span>` : "",
  ...card.tags.map(renderTagBadge),
].join(" ");
const renderBadgeRow = card => (card.unsure || card.want_to_try || card.tags.length ? `<span class="row">${renderBadges(card)}</span>` : "");
const renderScore = card => (card.score === null || card.score === undefined
  ? ""
  : `<span class="m">Score ${esc(scoreText(card.score))} of 5</span>`);

const where = card => [townLabel(card.town), card.cuisine, distanceText(card.distance_km)].filter(Boolean).join(" · ");

export const renderCard = (card, today) => `<a class="card${esc(card.unsure ? " marked" : "")}" href="#/p/${esc(card.id)}" data-id="${esc(card.id)}"><div class="b">
  <span class="t">${esc(card.name)}</span>
  <span class="m">${esc(where(card))}</span>
  ${renderBadgeRow(card)}
  <span class="m">${esc(lastVisitLine(card.last_visit, today))}</span>
  ${renderScore(card)}
  <span class="m">${esc(openLine(card))}</span>
</div></a>`;

const renderCards = (cards, today) => cards.map(c => renderCard(c, today)).join("");
const renderEmpty = (mode, filtered) => `<div class="empty"><p>${esc(EMPTY[mode])}</p>${renderTryFewer(filtered)}</div>`;
const renderTryFewer = filtered => (filtered ? `<p>Try fewer filters.</p>` : "");
const renderResults = (cards, today, state) => (cards.length
  ? renderCards(cards, today)
  : renderEmpty(state.mode, filtersInUse(state) || Boolean(state.liked_by_all)));
const renderMore = more => (more ? `<button type="button" data-act="more">Show more</button>` : "");

const countLine = (total, mode, seed) => {
  const label = MODES.find(([m]) => m === mode)?.[1] ?? mode;
  return `${label}: ${total} place${total === 1 ? "" : "s"}${seed ? ", shuffled" : ""}`;
};

// ---------------------------------------------------------------- the screen
async function render(ctx) {
  const [choices, me] = await Promise.all([get("/api/decide/choices"), get("/api/me")]);
  if (!ctx.alive()) return;
  const state = initialState(ctx.query, { mode: recall(KEYS.mode), audience: recall(KEYS.audience) });
  if (!audiencePairs(choices, me.person).some(([v]) => v === state.audience)) state.audience = "everyone";
  ctx.view.innerHTML = renderScreen(choices, me.person, state);
  const $ = sel => ctx.view.querySelector(sel);
  let shown = [];
  let asked = 0;

  async function load(append = false) {
    const mine = ++asked;
    const qs = queryFor(state);
    if (!append) {
      history.replaceState(history.state, "", `#/${qs ? `?${qs}` : ""}`);
      remember(KEYS.mode, state.mode);
      remember(KEYS.audience, state.audience);
    }
    let result;
    try {
      result = await get(`/api/decide?${queryFor(state, { limit: PAGE, offset: append ? shown.length : 0 })}`);
    } catch (e) {
      if (!ctx.alive() || mine !== asked) return;
      if (!e.error) throw e;
      $("#tonight-count").textContent = "";
      $("#tonight-list").innerHTML = renderError(e.error);
      $("#tonight-more").innerHTML = "";
      return;
    }
    if (!ctx.alive() || mine !== asked) return;
    shown = append ? shown.concat(result.places) : result.places;
    const today = todayLA();
    const list = $("#tonight-list");
    list.innerHTML = renderResults(shown, today, state);
    list.dataset.query = `${qs}#${shown.length}`;
    $("#tonight-count").textContent = countLine(result.total, state.mode, state.seed);
    $("#tonight-more").innerHTML = renderMore(shown.length < result.total);
  }

  function nearMe(box) {
    if (!box.checked) {
      state.near = "";
      load();
      return;
    }
    if (!navigator.geolocation) {
      box.checked = false;
      toast("This phone can't share its location.");
      return;
    }
    navigator.geolocation.getCurrentPosition(pos => {
      if (!ctx.alive()) return;
      state.near = `${pos.coords.latitude.toFixed(4)},${pos.coords.longitude.toFixed(4)}`;
      load();
    }, () => {
      if (!ctx.alive()) return;
      box.checked = false;
      toast("Couldn't get your location.");
    }, { timeout: 10000, maximumAge: 300000 });
  }

  ctx.view.addEventListener("click", e => {
    const chip = e.target.closest("[data-mode]");
    if (chip) {
      state.mode = chip.dataset.mode;
      state.seed = "";
      for (const c of ctx.view.querySelectorAll("[data-mode]")) {
        const on = c === chip;
        c.classList.toggle("on", on);
        c.setAttribute("aria-pressed", String(on));
      }
      load();
      return;
    }
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "shuffle") {
      state.seed = String(Math.floor(Math.random() * 1e9) + 1);
      load();
    } else if (act === "more") {
      load(true);
    }
  });

  ctx.view.addEventListener("change", e => {
    const el = e.target;
    if (el.name === "near") {
      nearMe(el);
      return;
    }
    if (el.name === "liked_by_all") state.liked_by_all = el.checked ? "true" : "";
    else if (el.name === "audience" || FILTERS.includes(el.name)) state[el.name] = el.value.trim();
    else return;
    load();
  });

  await load();
}

register("/", { label: "Tonight", nav: 10, render });
