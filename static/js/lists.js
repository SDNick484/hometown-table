// Curated lists. #/lists: the named source lists and a box to start one. #/lists/:id: one list's places and
// candidates, a paste box (one place per line; bullets, numbers and trailing commentary are stripped by the server),
// and the lines nothing matched, each with Search again (retype it) and Drop. Everything found goes to the inbox.
import { ApiError, get, send } from "./api.js";
import { esc, renderError, toast } from "./core.js";
import { register } from "./router.js";

const CANDIDATE_STATUS = {
  new: "In the inbox", pending: "Waiting for details", backlog: "In the inbox backlog",
  chain_hidden: "Hidden as a chain", dismissed: "Set aside", accepted: "Added",
};

const renderEach = (items, renderItem) => items.map(renderItem).join("");
const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const errorText = e => (e instanceof ApiError ? e.error : "Something went wrong. Try again.");
const showError = (view, message) => {
  const slot = view.querySelector("[data-error]");
  if (slot) slot.innerHTML = renderError(message);
};

// "2 matched existing places, 1 new in the inbox" from an import's counts.
export function importSummary(r) {
  const parts = [];
  if (r.matched_places) parts.push(r.matched_places === 1 ? "1 matched an existing place" : `${r.matched_places} matched existing places`);
  if (r.new_candidates) parts.push(`${r.new_candidates} new in the inbox`);
  if (r.linked_candidates) parts.push(`${r.linked_candidates} already known to the inbox`);
  if (r.unmatched) parts.push(`${r.unmatched} not found`);
  return parts.length ? `${parts.join(", ")}.` : "Nothing to import.";
}

// ---------------------------------------------------------------- the lists
const listStats = l => `${count(l.places, "place", "places")} · ${l.candidates} in the inbox · ${count(l.unmatched, "line not found", "lines not found")}`;

const renderListCard = l => `<a class="card" href="#/lists/${esc(l.id)}"><div class="b">
  <span class="t">${esc(l.name)}</span>
  <span class="m">${esc(listStats(l))}</span>
</div></a>`;

const renderListCards = lists => (lists.length
  ? `<div class="list">${renderEach(lists, renderListCard)}</div>`
  : `<div class="empty"><p>No lists yet. Start one below, then paste its places in.</p></div>`);

const renderNewList = () => `<form id="list-form" class="form panel" autocomplete="off">
  <h2>Start a list</h2>
  <div data-error></div>
  <label class="f">Name<input name="name" maxlength="100" required></label>
  <label class="f">Link (optional)<input name="url" type="url" maxlength="500"></label>
  <div class="row"><button class="primary" type="submit">Start list</button></div>
</form>`;

async function renderIndex(ctx) {
  const { lists } = await get("/api/lists");
  if (!ctx.alive()) return;
  ctx.view.innerHTML = `<h1>Lists</h1>
    <p class="muted small">Places from a list you found. Paste one in; each place goes to the inbox to get a verdict.</p>
    ${renderListCards(lists)}
    ${renderNewList()}`;
  ctx.view.querySelector("#list-form").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const body = { name: form.elements.name.value, url: form.elements.url.value };
    try {
      const made = await send("POST", "/api/lists", body);
      location.hash = `#/lists/${made.id}`;
    } catch (err) {
      showError(ctx.view, errorText(err));
    }
  });
}

// ---------------------------------------------------------------- one list
const renderPlaceCard = p => `<a class="card" href="#/p/${esc(p.id)}"><div class="b">
  <span class="t">${esc(p.name)}</span>
  <span class="m">${esc([p.town, p.cuisine].filter(Boolean).join(" · "))}</span>
</div></a>`;

const renderCandidateCard = c => `<div class="card"><div class="b">
  <span class="t">${esc(c.name)}</span>
  <span class="m">${esc([CANDIDATE_STATUS[c.status] ?? c.status, c.address].filter(Boolean).join(" · "))}</span>
</div></div>`;

const renderUnmatched = u => `<form class="form panel" data-form="retry" data-id="${esc(u.id)}" autocomplete="off">
  <label class="f">Not found<input name="retry" maxlength="200" required value="${esc(u.line)}"></label>
  <div class="row"><button class="primary" type="submit">Search again</button>
  <button type="button" data-act="drop" data-id="${esc(u.id)}">Drop</button></div>
</form>`;

const renderSection = (title, items, renderItem) => (items.length
  ? `<div class="section-h"><h2>${esc(title)}</h2></div><div class="list">${renderEach(items, renderItem)}</div>`
  : "");

const renderUnmatchedSection = items => (items.length
  ? `<div class="section-h"><h2>Not found</h2></div>
     <p class="muted small">Google found nothing for these. Fix the spelling and search again, or drop the line.</p>
     ${renderEach(items, renderUnmatched)}`
  : `<p class="muted small">Nothing is waiting for a second look.</p>`);

const renderNotice = text => (text ? `<p class="notice" role="status">${esc(text)}</p>` : "");

const renderLink = url => (url ? `<p class="small"><a href="${esc(url)}" rel="noopener noreferrer">${esc(url)}</a></p>` : "");

const renderPaste = () => `<form id="paste-form" class="form panel" autocomplete="off">
  <h2>Paste places</h2>
  <div data-error></div>
  <label class="f">One place per line<textarea name="lines" rows="8" required></textarea></label>
  <p class="hint">Numbers, bullets and anything after a dash or colon are dropped. Up to 200 lines.</p>
  <div class="row"><button class="primary" type="submit">Add to list</button></div>
</form>`;

const renderDelete = asking => (asking
  ? `<div class="panel"><p>Delete this list? Its places and inbox candidates stay.</p>
     <div class="row"><button class="danger" type="button" data-act="delete-yes">Yes, delete it</button>
     <button type="button" data-act="cancel">Cancel</button></div></div>`
  : `<div class="row"><button type="button" data-act="delete">Delete list</button></div>`);

async function renderOne(ctx) {
  const id = Number(ctx.params.id);
  const state = { data: await get(`/api/lists/${id}`), notice: "", asking: false };
  if (!ctx.alive()) return;

  const draw = () => {
    const { list, places, candidates, unmatched } = state.data;
    ctx.view.innerHTML = `<p class="small"><a href="#/lists">Lists</a></p>
      <h1>${esc(list.name)}</h1>
      ${renderLink(list.url)}
      ${renderNotice(state.notice)}
      ${renderSection("Places", places, renderPlaceCard)}
      ${renderSection("In the inbox", candidates, renderCandidateCard)}
      ${renderUnmatchedSection(unmatched)}
      ${renderPaste()}
      ${renderDelete(state.asking)}`;
  };
  const reload = async notice => {
    state.data = await get(`/api/lists/${id}`);
    if (!ctx.alive()) return;
    state.notice = notice;
    state.asking = false;
    draw();
  };
  draw();

  ctx.view.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    try {
      if (form.id === "paste-form") {
        const r = await send("POST", `/api/lists/${id}/import`, { lines: form.elements.lines.value.split(/\r?\n/) });
        await reload(importSummary(r));
      } else if (form.dataset.form === "retry") {
        const r = await send("POST", `/api/lists/entries/${Number(form.dataset.id)}/retry`, { text: form.elements.retry.value });
        await reload(r.unmatched ? "Still not found. Try another spelling, or drop the line." : importSummary(r));
      }
    } catch (err) {
      if (ctx.alive()) showError(ctx.view, errorText(err));
    }
  });

  ctx.view.addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    try {
      switch (btn.dataset.act) {
        case "drop":
          await send("DELETE", `/api/lists/entries/${Number(btn.dataset.id)}`);
          return await reload("");
        case "delete": state.asking = true; return draw();
        case "cancel": state.asking = false; return draw();
        case "delete-yes":
          await send("DELETE", `/api/lists/${id}`);
          toast("List deleted");
          location.hash = "#/lists";
          return;
      }
    } catch (err) {
      if (ctx.alive()) showError(ctx.view, errorText(err));
    }
  });
}

register("/lists", { label: "Lists", nav: 30, render: renderIndex });
register("/lists/:id", { label: "List", render: renderOne });
