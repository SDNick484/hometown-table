// The inbox (#/inbox): what the scan, the lists and the Where to Eat import found, waiting for a verdict. Tabs New,
// Backlog and Flags (places Google lists as closed), and a collapsed "Hidden as chains" section at the bottom.
//
// Each candidate shows where its values come from. OSM's and Where to Eat's are the ones a place is built from;
// Google's are labelled "Google Maps" and reach a place only through "Use this" (the owner's GOOGLE_PREFILL setting),
// which the server checks. Counts / Not sure / Doesn't count open the accept form with that verdict chosen; the reason
// is prefilled "on <list>" when the candidate came from a list.
import { get, send, ApiError } from "./api.js";
import { esc, townLabel, renderError, toast } from "./core.js";
import { register } from "./router.js";
import { FIELDS, loadTags, readPlaceForm, renderOsmCredit, renderPlaceForm, showFormError, wireGoogleHints } from "./add.js";

const TABS = [["new", "New"], ["backlog", "Backlog"], ["flags", "Flags"]];
const FLAG_TEXT = { CLOSED_PERMANENTLY: "closed for good", CLOSED_TEMPORARILY: "closed for now" };
const PAGE = 20;

// The header link reads "Inbox (3)" while there are new candidates.
function setNavCount(n) {
  const link = globalThis.document?.querySelector("#nav a[data-path='/inbox']");
  if (link) link.textContent = n > 0 ? `Inbox (${n})` : "Inbox";
}

export function refreshNavCount() {
  return get("/api/inbox?tab=new&limit=0").then(r => setNavCount(r.counts.new)).catch(() => {});
}

// ---------------------------------------------------------------- values
// The values a place would be built from: Where to Eat's own first for its candidates, then OSM's. Never Google's.
export function baseValues(r) {
  const sources = r.origin === "where_to_eat" ? [r.wte, r.osm] : [r.osm, r.wte];
  const values = {};
  for (const [key] of FIELDS) {
    const src = sources.find(s => s && s[key] !== null && s[key] !== undefined && s[key] !== "");
    values[key] = src ? String(src[key]) : "";
  }
  if (!values.town && r.address_from !== "google" && r.town) values.town = r.town;
  return values;
}

export const listReason = r => (r.lists?.length ? `on ${r.lists.map(l => l.name).join(", ")}`.slice(0, 200) : "");

// ---------------------------------------------------------------- markup
const renderGoogleMark = from => (from === "google" ? `<span class="badge">Google Maps</span>` : "");
const renderTitle = r => `<div class="t">${esc(r.name || "Name not known yet")} ${renderGoogleMark(r.name_from)}</div>`;
const renderWhere = r => `<div class="m">${esc([r.address, townLabel(r.town)].filter(Boolean).join(" · "))} ${renderGoogleMark(r.address_from)}</div>`;
const renderType = r => (r.type ? `<span class="badge">Google Maps: ${esc(r.type.replaceAll("_", " "))}</span>` : "");
const renderOsmMark = r => `<span class="badge">${esc(r.osm_match ? "OSM match" : "No OSM match")}</span>`;
const renderChain = r => (r.chain_hint ? `<span class="badge unsure">Chain hint</span>` : "");
const renderOrigin = r => (r.origin === "where_to_eat" ? `<span class="badge">Where to Eat</span>` : "");
const renderLists = r => (r.lists.length ? `<p class="small muted">From: ${esc(r.lists.map(l => l.name).join(", "))}</p>` : "");
const renderNote = r => (r.note ? `<p class="note">${esc(r.note)}</p>` : "");
const renderUnhide = r => (r.status === "chain_hidden" ? `<button type="button" data-act="unhide">Not a chain: move to New</button>` : "");

const renderActions = r => `<div class="row">
    <button type="button" data-verdict="counts">Counts</button>
    <button type="button" data-verdict="unsure">Not sure</button>
    <button type="button" data-verdict="no">Doesn't count</button>
    <button type="button" data-act="dismiss">Not a restaurant / not ours</button>
    ${renderUnhide(r)}
  </div>`;

const renderAccept = (r, state) => (state.form?.id === r.id
  ? renderPlaceForm({ values: baseValues(r), google: r.google, prefill: state.prefill, verdict: state.form.verdict,
    reason: listReason(r), tags: state.tags, submit: "Add to the list" })
  : "");

const renderCandidate = (r, state) => `<div class="card" data-id="${esc(r.id)}"><div class="b">
    ${renderTitle(r)}
    ${renderWhere(r)}
    <div class="row">${renderOrigin(r)}${renderOsmMark(r)}${renderChain(r)}${renderType(r)}</div>
    ${renderLists(r)}
    ${renderNote(r)}
    ${renderActions(r)}
    ${renderAccept(r, state)}
  </div></div>`;

const renderFlag = f => `<div class="card" data-place="${esc(f.id)}"><div class="b">
    <a class="t" href="#/p/${esc(f.id)}">${esc(f.name)}</a>
    <div class="m">${esc([f.address, townLabel(f.town)].filter(Boolean).join(" · "))}</div>
    <p class="small"><span class="badge">Google Maps</span> lists it as ${esc(FLAG_TEXT[f.closure_flag] ?? f.closure_flag)}.</p>
    <div class="row">
      <button type="button" data-act="flag-confirm">Confirm: ${esc(FLAG_TEXT[f.closure_flag] ?? "closed")}</button>
      <button type="button" data-act="flag-dismiss">Dismiss: it's open</button>
    </div>
  </div></div>`;

const renderRow = (r, state, tab) => (tab === "flags" ? renderFlag(r) : renderCandidate(r, state));
const renderRows = (part, state, tab) => part.rows.map(r => renderRow(r, state, tab)).join("");
const renderMore = (part, which) => (part.rows.length < part.total
  ? `<p><button type="button" data-more="${esc(which)}">Show more</button></p>`
  : "");
const EMPTY = { new: "Nothing new. The weekly scan adds what it finds.", backlog: "The backlog is empty.",
  flags: "No closure flags.", chain_hidden: "Nothing hidden as a chain." };
const renderEmpty = (part, tab) => (part.rows.length ? "" : `<p class="empty">${esc(EMPTY[tab])}</p>`);
const renderPart = (part, state, tab, which) => `${renderEmpty(part, tab)}<div class="list">${renderRows(part, state, tab)}</div>${renderMore(part, which)}`;

const renderTabButton = ([tab, label], state) => `<button type="button" class="chip ${esc(state.tab === tab ? "on" : "")}" data-tab="${esc(tab)}" aria-pressed="${esc(state.tab === tab)}">${esc(label)} (${esc(state.counts[tab])})</button>`;
const renderTabs = state => TABS.map(t => renderTabButton(t, state)).join("");
const renderChains = state => (state.chains.loaded ? renderPart(state.chains, state, "chain_hidden", "chains") : `<p class="muted">Loading…</p>`);
const renderOpen = on => (on ? "open" : "");
const renderPrefillOff = state => (state.prefill || !state.google ? "" : `<p class="small muted">Google values can't be used here (GOOGLE_PREFILL is off): type what OSM lacks.</p>`);

function renderInbox(state) {
  return `<div class="spread"><h1>Inbox</h1><a class="btn" href="#/add">Add place</a></div>
    <div class="chips" role="group" aria-label="Tabs">${renderTabs(state)}</div>
    <div id="inbox-msg"></div>
    ${renderPrefillOff(state)}
    <div>${renderPart(state.main, state, state.tab, "main")}</div>
    <details id="chains" ${renderOpen(state.chainsOpen)}>
      <summary class="section-h"><span>Hidden as chains (${esc(state.counts.chain_hidden)})</span><span class="small">Show or hide</span></summary>
      ${renderChains(state)}
    </details>
    ${renderOsmCredit()}`;
}

// ---------------------------------------------------------------- the screen
async function render(ctx) {
  const { view } = ctx;
  const tab = TABS.some(([t]) => t === ctx.query.tab) ? ctx.query.tab : "new";
  const [page, tags] = await Promise.all([get(`/api/inbox?tab=${tab}`), loadTags()]);
  if (!ctx.alive()) return;
  const state = {
    tab, tags, counts: page.counts, prefill: Boolean(page.prefill), google: Boolean(page.google), form: null, chainsOpen: false, busy: false,
    main: { rows: page.rows, total: page.total }, chains: { rows: [], total: page.counts.chain_hidden, loaded: false },
  };
  const draw = () => {
    view.innerHTML = renderInbox(state);
    setNavCount(state.counts.new);
  };
  const showError = err => {
    const box = view.querySelector("#inbox-msg");
    box.innerHTML = renderError(err instanceof ApiError ? err.error : "Something went wrong. Try again.");
    box.scrollIntoView?.({ block: "nearest" });
  };
  const candidate = id => [...state.main.rows, ...state.chains.rows].find(r => r.id === id);
  // Take a sorted row off the screen and out of the counts.
  const drop = (status, id) => {
    for (const part of [state.main, state.chains]) {
      const before = part.rows.length;
      part.rows = part.rows.filter(r => r.id !== id);
      if (part.rows.length < before) part.total -= 1;
    }
    if (status in state.counts) state.counts[status] = Math.max(0, state.counts[status] - 1);
    if (state.form?.id === id) state.form = null;
  };
  const loadChains = async (offset = 0) => {
    const r = await get(`/api/inbox?tab=chain_hidden&offset=${offset}`);
    if (!ctx.alive()) return;
    state.chains = { rows: offset ? [...state.chains.rows, ...r.rows] : r.rows, total: r.total, loaded: true };
    state.counts = r.counts;
    draw();
  };
  const loadMore = async which => {
    if (which === "chains") return loadChains(state.chains.rows.length);
    const r = await get(`/api/inbox?tab=${state.tab}&offset=${state.main.rows.length}&limit=${PAGE}`);
    if (!ctx.alive()) return;
    state.main = { rows: [...state.main.rows, ...r.rows], total: r.total };
    state.counts = r.counts;
    draw();
  };
  const run = async (call, done) => {
    if (state.busy) return;
    state.busy = true;
    try {
      await call();
      if (!ctx.alive()) return;
      if (done) toast(done);
      draw();
    } catch (err) {
      if (ctx.alive()) showError(err);
    } finally {
      state.busy = false;
    }
  };

  wireGoogleHints(view, form => candidate(Number(form.closest("[data-id]")?.dataset.id))?.google);

  view.addEventListener("click", e => {
    const target = e.target.closest("[data-tab], [data-verdict], [data-act], [data-more]");
    if (!target) return;
    const card = target.closest("[data-id]");
    const id = card ? Number(card.dataset.id) : null;
    const placeId = Number(target.closest("[data-place]")?.dataset.place);
    const act = target.dataset.act;
    if (target.dataset.tab) {
      location.hash = `#/inbox?tab=${encodeURIComponent(target.dataset.tab)}`;
    } else if (target.dataset.more) {
      run(() => loadMore(target.dataset.more));
    } else if (target.dataset.verdict) {
      state.form = { id, verdict: target.dataset.verdict };
      draw();
      view.querySelector(`[data-id="${id}"] form input[name=ownership_reason]`)?.focus();
    } else if (act === "cancel") {
      state.form = null;
      draw();
    } else if (act === "dismiss") {
      const r = candidate(id);
      run(async () => { await send("POST", `/api/inbox/${id}/dismiss`, {}); drop(r.status, id); }, "Dismissed");
    } else if (act === "unhide") {
      run(async () => {
        await send("POST", `/api/inbox/${id}/unhide`, {});
        drop("chain_hidden", id);
        state.counts.new += 1;
      }, "Moved to New");
    } else if (act === "flag-confirm" || act === "flag-dismiss") {
      const verb = act === "flag-confirm" ? "confirm" : "dismiss";
      run(async () => { await send("POST", `/api/places/${placeId}/flag/${verb}`, {}); dropFlag(placeId); },
        verb === "confirm" ? "Marked closed" : "Flag dismissed");
    } else if (act === "anyway") {
      accept(target.closest("form"), true);
    }
  });

  const dropFlag = id => {
    state.main.rows = state.main.rows.filter(f => f.id !== id);
    state.main.total -= 1;
    state.counts.flags = Math.max(0, state.counts.flags - 1);
  };

  const accept = async (form, addAnyway = false) => {
    const id = Number(form.closest("[data-id]").dataset.id);
    const r = candidate(id);
    const body = readPlaceForm(form, { asGoogle: true });
    if (addAnyway) body.add_anyway = true;
    if (state.busy) return;
    state.busy = true;
    try {
      const place = await send("POST", `/api/inbox/${id}/accept`, body);
      if (!ctx.alive()) return;
      drop(r.status, id);
      draw();
      toast(`Added ${place.name}`);
    } catch (err) {
      if (ctx.alive()) showFormError(form, err);
    } finally {
      state.busy = false;
    }
  };

  view.addEventListener("submit", e => {
    e.preventDefault();
    const form = e.target.closest("form.place-form");
    if (form) accept(form);
  });

  // <details> fires "toggle" without bubbling, so listen while capturing.
  view.addEventListener("toggle", e => {
    if (e.target.id !== "chains") return;
    state.chainsOpen = e.target.open;
    if (state.chainsOpen && !state.chains.loaded) loadChains().catch(err => ctx.alive() && showError(err));
  }, true);

  draw();
}

register("/inbox", { label: "Inbox", nav: 20, render });

// The header count, once the router has drawn the navigation (main.js starts it right after the screens load).
if (globalThis.document) setTimeout(refreshNavCount, 0);
