// The place page (#/p/<id>): the facts, the ownership verdict and standing, their dated history, and the buttons that
// change them. Ownership and standing always take a reason and always leave a line in the history; a place with
// visits can't be deleted, so a sold or closed place is changed instead and moves to Past places.
import { get, send, ApiError } from "./api.js";
import { esc, fmtDate, ago, stars, townLabel, renderError, toast, todayLA } from "./core.js";
import { register } from "./router.js";

export const OWNERSHIP = { counts: "Counts", unsure: "Not sure", no: "Doesn't count" };
export const OWNERSHIP_WAS = { counts: "Counted", unsure: "Not sure", no: "Doesn't count" };
export const STANDING = { open: "Open", temp_closed: "Temporarily closed", closed: "Closed" };

// "Counted → Doesn't count", "Open → Closed": one history entry's headline.
export function eventTitle(e) {
  const labels = e.kind === "ownership" ? OWNERSHIP_WAS : STANDING;
  return `${e.old ? labels[e.old] ?? e.old : "Set"} → ${labels[e.new] ?? e.new}`;
}

const renderOption = (value, label) => `<option value="${esc(value)}">${esc(label)}</option>`;
const renderOptions = (labels, skip) => Object.entries(labels).filter(([v]) => v !== skip).map(([v, l]) => renderOption(v, l)).join("");

const renderBadges = p => [
  p.ownership === "unsure" ? `<span class="badge unsure">Not sure it counts</span>` : "",
  p.ownership === "no" ? `<span class="badge closed">Doesn't count</span>` : "",
  p.standing !== "open" ? `<span class="badge closed">${esc(STANDING[p.standing])}</span>` : "",
  p.want_to_try ? `<span class="badge accent">Want to try</span>` : "",
].join("");

const renderClosureNote = p => p.closure_flag
  ? `<p class="note">Google says this place may be ${esc(p.closure_flag === "CLOSED_PERMANENTLY" ? "closed for good" : "closed for now")}. Confirm it below if that's right.</p>`
  : "";

const renderFact = (label, value) => (value ? `<div><span class="muted">${esc(label)}</span> ${esc(value)}</div>` : "");
const renderPhone = phone => (phone ? `<div><span class="muted">Phone</span> <a href="tel:${esc(phone)}">${esc(phone)}</a></div>` : "");
// Only http(s) addresses become links; anything else is shown as plain text.
const renderWebsite = url => {
  if (!url) return "";
  return /^https?:\/\//i.test(url)
    ? `<div><span class="muted">Website</span> <a href="${esc(url)}" rel="noopener noreferrer">${esc(url)}</a></div>`
    : renderFact("Website", url);
};

const renderFacts = p => [
  renderFact("Town", townLabel(p.town)),
  renderFact("Address", p.address),
  renderFact("Cuisine", p.cuisine),
  renderPhone(p.phone),
  renderWebsite(p.website),
  renderFact("Hours", p.hours),
  renderFact("Reason for the verdict", p.ownership_reason),
].join("");

const renderTagBadge = t => `<span class="badge">${esc(t.label)}</span>`;
const renderTagBadges = tags => tags.map(renderTagBadge).join("");
const renderTags = p => (p.tags.length ? `<p class="chips">${renderTagBadges(p.tags)}</p>` : "");
const renderLists = p => (p.lists.length ? `<p class="small muted">On: ${esc(p.lists.map(l => l.name).join(", "))}</p>` : "");
const renderNotes = p => (p.notes ? `<div class="note">${esc(p.notes)}</div>` : "");

function renderVisits(p) {
  const v = p.last_visit;
  if (!v) return `<p class="muted">No visits yet. <a href="#/p/${esc(p.id)}/visits">Visits</a></p>`;
  const who = v.people.map(x => (x.rating ? `${x.name} ${stars(x.rating)}` : x.name)).join(", ");
  const said = `Last visit ${ago(v.on)}${who ? ` · ${who}` : ""}.`;
  const count = `${p.visit_count} visit${p.visit_count === 1 ? "" : "s"}`;
  return `<p>${esc(said)} <a href="#/p/${esc(p.id)}/visits">${esc(count)}</a></p>`;
}

const renderReason = reason => (reason ? `<div class="small">${esc(reason)}</div>` : "");
const renderEvent = e => `<li><strong>${esc(eventTitle(e))}</strong> <span class="muted small">${esc(fmtDate(e.on))} · ${esc(e.by)}</span>${renderReason(e.reason)}</li>`;
const renderEvents = events => events.map(renderEvent).join("");
const renderHistory = p => (p.history.length
  ? `<ul class="rows history">${renderEvents(p.history)}</ul>`
  : `<p class="muted small">No changes to its ownership or standing yet.</p>`);

function renderChangeForm(kind, p) {
  const [id, labels, current] = kind === "ownership" ? ["own", OWNERSHIP, p.ownership] : ["stand", STANDING, p.standing];
  const title = kind === "ownership" ? "Change whether it counts" : "Change its standing";
  return `<form id="${esc(id)}-form" class="form panel" data-kind="${esc(kind)}">
    <strong>${esc(title)}</strong>
    <label class="f">New value<select id="${esc(id)}-value">${renderOptions(labels, current)}</select></label>
    <label class="f">Reason<input id="${esc(id)}-reason" maxlength="200" required autocomplete="off"></label>
    <label class="f">As of (leave empty for today)<input id="${esc(id)}-on" type="date" max="${esc(todayLA())}"></label>
    <div class="row"><button class="primary" type="submit">Save</button><button type="button" data-act="close">Cancel</button></div>
  </form>`;
}

const renderDeletePanel = () => `<div class="panel form">
  <strong>Delete this place?</strong>
  <p class="small">It has no visits, so nothing else is lost. This can't be undone.</p>
  <div class="row"><button class="danger" type="button" data-act="delete-yes">Yes, delete it</button><button type="button" data-act="close">Cancel</button></div>
</div>`;

function renderPanel(panel, p) {
  if (panel === "ownership" || panel === "standing") return renderChangeForm(panel, p);
  if (panel === "delete" && p.visit_count === 0) return renderDeletePanel();
  return "";
}

const renderDeleteButton = p => (p.visit_count === 0 ? `<button type="button" data-act="delete">Delete</button>` : "");

function renderPlace(p, panel) {
  return `<article class="place">
    <div class="spread"><h1>${esc(p.name)}</h1><a class="btn" href="#/p/${esc(p.id)}/edit">Edit</a></div>
    <p class="row">${renderBadges(p)}</p>
    ${renderClosureNote(p)}
    <div id="place-msg"></div>
    <div class="facts">${renderFacts(p)}</div>
    ${renderTags(p)}
    ${renderLists(p)}
    ${renderNotes(p)}
    ${renderVisits(p)}
    <div class="row">
      <button type="button" data-act="ownership">Change whether it counts</button>
      <button type="button" data-act="standing">Change standing</button>
      <button type="button" data-act="want">${esc(p.want_to_try ? "Take off want to try" : "Want to try")}</button>
      ${renderDeleteButton(p)}
    </div>
    ${renderPanel(panel, p)}
    <h2 class="section-h">History</h2>
    ${renderHistory(p)}
  </article>`;
}

async function render(ctx) {
  const { view, params } = ctx;
  const place = await get(`/api/places/${encodeURIComponent(params.id)}`);
  if (!ctx.alive()) return;
  let panel = null;
  const draw = () => { view.innerHTML = renderPlace(place, panel); };
  const showError = message => { view.querySelector("#place-msg").innerHTML = renderError(message); };
  const act = async (call, done) => {
    try {
      await call();
    } catch (e) {
      if (ctx.alive()) showError(e instanceof ApiError ? e.error : "Something went wrong. Try again.");
      return;
    }
    toast(done);
    if (ctx.alive()) done === "Deleted" ? (location.hash = "#/past") : ctx.rerender();
  };
  draw();
  view.addEventListener("click", e => {
    const button = e.target.closest("[data-act]");
    if (!button) return;
    const name = button.dataset.act;
    if (name === "ownership" || name === "standing" || name === "delete") {
      panel = name;
      draw();
      view.querySelector("form input, form select")?.focus();
    } else if (name === "close") {
      panel = null;
      draw();
    } else if (name === "want") {
      act(() => send("PUT", `/api/places/${place.id}/want`, { on: !place.want_to_try }), "Saved");
    } else if (name === "delete-yes") {
      act(() => send("DELETE", `/api/places/${place.id}`, {}), "Deleted");
    }
  });
  view.addEventListener("submit", e => {
    e.preventDefault();
    const form = e.target.closest("form[data-kind]");
    if (!form) return;
    const id = form.id === "own-form" ? "own" : "stand";
    const body = { value: form.querySelector(`#${id}-value`).value, reason: form.querySelector(`#${id}-reason`).value };
    const on = form.querySelector(`#${id}-on`).value;
    if (on) body.on = on;
    act(() => send("POST", `/api/places/${place.id}/${form.dataset.kind}`, body), "Saved");
  });
}

register("/p/:id", { label: "Place", render });
