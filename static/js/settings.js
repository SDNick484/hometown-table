// Settings (#/settings): the people, the tags with their occasion switch, synced visits (Task 15), what the app is set
// to, and the exports.
// One screen, drawn from `state` and redrawn after every change. A failed save shows its message in the section's
// error slot without redrawing, so what was typed stays.
import { ApiError, get, send } from "./api.js";
import { esc, fmtDate, renderError, toast, todayLA } from "./core.js";
import { register } from "./router.js";

const GROUPS = { adult: "Adult", kid: "Kid" };

const places = n => `${n} ${n === 1 ? "place" : "places"}`;
const dayOf = epoch => fmtDate(todayLA(new Date(epoch * 1000)));

// ---------------------------------------------------------------- people
const renderGroupOptions = selected =>
  Object.entries(GROUPS).map(([value, label]) => `<option value="${esc(value)}"${esc(selected === value ? " selected" : "")}>${esc(label)}</option>`).join("");

const renderPersonForm = (person, formName, submitLabel) => `
  <form class="form" data-form="${esc(formName)}" data-id="${esc(person?.id ?? "")}" autocomplete="off">
    <div class="grid2">
      <label class="f">Name<input name="name" maxlength="40" required value="${esc(person?.name ?? "")}"></label>
      <label class="f">Group<select name="grp">${renderGroupOptions(person?.grp ?? "adult")}</select></label>
    </div>
    <label class="f">Editor email (optional)<input name="email" type="email" maxlength="254" value="${esc(person?.email ?? "")}"></label>
    <p class="hint">An email that is listed as an editor links this person to that sign-in, so "me" means them.</p>
    <div class="row"><button class="primary" type="submit">${esc(submitLabel)}</button>${renderCancel(person)}</div>
  </form>`;

const renderCancel = person => (person ? `<button type="button" data-act="cancel">Cancel</button>` : "");

const renderPersonAsk = person => `
  <p>Remove ${esc(person.name)}? Someone who has been on a visit is kept as inactive instead of deleted.</p>
  <div class="row"><button class="danger" type="button" data-act="remove-person" data-id="${esc(person.id)}">Remove</button>
  <button type="button" data-act="cancel">Cancel</button></div>`;

const renderPersonView = person => `
  <div class="spread">
    <div><strong>${esc(person.name)}</strong> <span class="badge">${esc(GROUPS[person.grp] ?? person.grp)}</span>
    ${renderInactiveBadge(person)}
    <div class="small muted">${esc(person.email || "No email linked")}</div></div>
    <div class="row">${renderPersonButtons(person)}</div>
  </div>`;

const renderInactiveBadge = person => (person.active ? "" : `<span class="badge">Inactive</span>`);

const renderPersonButtons = person =>
  `<button type="button" data-act="edit-person" data-id="${esc(person.id)}">Edit</button>` +
  (person.active ? "" : `<button type="button" data-act="activate-person" data-id="${esc(person.id)}">Reactivate</button>`) +
  `<button type="button" data-act="ask-person" data-id="${esc(person.id)}">Remove</button>`;

const renderPersonRow = (state, person) => `
  <div data-person="${esc(person.id)}">${renderPersonBody(state, person)}</div>`;

function renderPersonBody(state, person) {
  if (state.editing === `person:${person.id}`) return renderPersonForm(person, "person-edit", "Save");
  if (state.asking === `person:${person.id}`) return renderPersonAsk(person);
  return renderPersonView(person);
}

const renderPeople = state => `
  <div class="section-h"><h2>People</h2></div>
  <div data-error="people"></div>
  <div class="rows">${renderPersonRows(state)}</div>
  <div class="panel"><h3>Add a person</h3>${renderPersonForm(null, "person", "Add person")}</div>`;

const renderPersonRows = state => state.people.map(p => renderPersonRow(state, p)).join("");

// ---------------------------------------------------------------- tags
const renderTagEdit = tag => `
  <form class="form" data-form="tag-edit" data-id="${esc(tag.id)}" autocomplete="off">
    <label class="f">Label<input name="label" maxlength="40" required value="${esc(tag.label)}"></label>
    <div class="row"><button class="primary" type="submit">Save</button><button type="button" data-act="cancel">Cancel</button></div>
  </form>`;

const renderTagAsk = tag => `
  <p>Delete "${esc(tag.label)}"? It is on ${esc(places(tag.places))}, and will come off ${esc(tag.places === 1 ? "it" : "them")}.</p>
  <div class="row"><button class="danger" type="button" data-act="delete" data-id="${esc(tag.id)}">Delete</button>
  <button type="button" data-act="cancel">Cancel</button></div>`;

const renderTagView = (tag, first, last) => `
  <div class="spread">
    <label class="check"><input type="checkbox" data-act="occasion" data-id="${esc(tag.id)}"${esc(tag.occasion ? " checked" : "")}>
    <span>${esc(tag.label)}</span></label>
    <div class="row">
      <button class="icon-btn" type="button" data-act="up" data-id="${esc(tag.id)}" aria-label="Move up"${esc(first ? " disabled" : "")}>↑</button>
      <button class="icon-btn" type="button" data-act="down" data-id="${esc(tag.id)}" aria-label="Move down"${esc(last ? " disabled" : "")}>↓</button>
      <button type="button" data-act="edit-tag" data-id="${esc(tag.id)}">Rename</button>
      <button type="button" data-act="ask-delete" data-id="${esc(tag.id)}">Delete</button>
    </div>
  </div>`;

function renderTagBody(state, tag, i) {
  if (state.editing === `tag:${tag.id}`) return renderTagEdit(tag);
  if (state.asking === `tag:${tag.id}`) return renderTagAsk(tag);
  return renderTagView(tag, i === 0, i === state.tags.length - 1);
}

const renderTagRow = (state, tag, i) => `<div data-tag="${esc(tag.id)}">${renderTagBody(state, tag, i)}</div>`;
const renderTagRows = state => state.tags.map((t, i) => renderTagRow(state, t, i)).join("");

const renderTags = state => `
  <div class="section-h"><h2>Tags</h2></div>
  <p class="hint">A ticked tag is an occasion tag: "Occasion" on the Tonight screen picks places that have one.</p>
  <div data-error="tags"></div>
  <div class="rows">${renderTagRows(state)}</div>
  <div class="panel"><h3>Add a tag</h3>
    <form class="form" data-form="tag" autocomplete="off">
      <label class="f">Label<input name="label" maxlength="40" required></label>
      <label class="check"><input type="checkbox" name="occasion"><span>Occasion tag</span></label>
      <div><button class="primary" type="submit">Add tag</button></div>
    </form></div>`;

// ---------------------------------------------------------------- synced visits (Task 15)
// One source's state in words: not set up, failed, waiting for people, never run, or its last run's counts.
export function syncSourceLine(src) {
  const last = src.last;
  if (!src.configured) return `not set up (set ${src.settings.join(" and ")} in /etc/conf.d/hometown)`;
  if (src.error) return `failed${last ? ` on ${dayOf(last.at)}` : ""}: ${src.error}`;
  if (!last) return "set up; it runs once a day";
  if (last.status === "no_people") return `nothing synced on ${dayOf(last.at)}: no one is chosen above`;
  return `${dayOf(last.at)}: ${last.new} new, ${last.logged} logged, ${last.duplicate} already logged, ${last.unmatched} unmatched`;
}

// "Nov 1, 2026 · Lafayette · Dinner Log"
export const unmatchedLine = u => [fmtDate(u.day) || u.day, u.location, u.label].filter(Boolean).join(" · ");

const renderSyncBox = (person, chosen) => `
  <label class="check"><input type="checkbox" name="sync-person" value="${esc(person.id)}"${esc(chosen.includes(person.id) ? " checked" : "")}>
  <span>${esc(person.name)}</span></label>`;

const renderSyncBoxes = (people, chosen) => people.map(p => renderSyncBox(p, chosen)).join("");

function renderSyncPeople(state) {
  const active = state.people.filter(p => p.active);
  if (!active.length) return `<p class="hint">Add people above first.</p>`;
  return `
    <form class="form" data-form="sync-people">
      <div class="row">${renderSyncBoxes(active, state.sync.people)}</div>
      <div><button class="primary" type="submit">Save</button></div>
    </form>`;
}

const renderSyncNotice = sync =>
  (sync.people.length ? "" : `<p class="notice" role="status">Pick who usually goes: until then nothing is synced.</p>`);

const renderSyncSources = sync => sync.sources.map(s => renderFact(`${s.label}:`, syncSourceLine(s))).join("");

const renderUnmatchedRow = u => `
  <div class="spread" data-unmatched="${esc(u.source)}:${esc(u.ref)}">
    <div><strong>${esc(u.name || "(no name)")}</strong><div class="small muted">${esc(unmatchedLine(u))}</div></div>
    <button type="button" data-act="dismiss-sync" data-source="${esc(u.source)}" data-ref="${esc(u.ref)}">Dismiss</button>
  </div>`;

function renderUnmatched(sync) {
  if (!sync.unmatched.length) return `<p class="hint">Nothing waiting for a place.</p>`;
  return `<div class="rows">${renderUnmatchedRows(sync.unmatched)}</div>`;
}

const renderUnmatchedRows = rows => rows.map(renderUnmatchedRow).join("");

const renderSync = state => `
  <div data-section="sync">
  <div class="section-h"><h2>Synced visits</h2></div>
  <p class="hint">Once a day, the restaurant visits recorded in Dinner Log and Where to Eat are logged here at the
  matching place, with the people ticked below and no ratings: rate them yourselves, and edit a visit when someone else
  went. Later changes or deletions in those apps are not copied.</p>
  <div class="stack">
  <div data-error="sync"></div>
  ${renderSyncNotice(state.sync)}
  ${renderSyncPeople(state)}
  <div class="facts">${renderSyncSources(state.sync)}</div>
  <div><h3>Waiting for a place</h3>
  <p class="hint">No single place here matched these. Add the place and the next run logs the visit, or dismiss it.</p></div>
  ${renderUnmatched(state.sync)}
  </div></div>`;

// ---------------------------------------------------------------- status and export
const renderFact = (name, value) => `<div><span class="muted">${esc(name)}</span> ${esc(value)}</div>`;

function renderScan(scan) {
  if (!scan) return renderFact("Last scan:", "none yet");
  if (scan.error) return renderFact("Last scan:", `failed on ${dayOf(scan.started_at)}: ${scan.error}`);
  return renderFact("Last scan:", `${dayOf(scan.finished_at ?? scan.started_at)}, ${scan.new_candidates} new`);
}

const renderUsage = u => renderFact("Google calls this month:", `${u.pro_calls} of ${u.max_pro_calls} Pro, ${u.id_calls} ID`);

const renderMe = me => {
  if (!me.email) return renderFact("Signed in as:", "no email (a local or key session)");
  return renderFact("Signed in as:", `${me.email} ${me.person_id ? "(linked to a person)" : "(not linked: give a person this email)"}`);
};

const renderStatus = s => `
  <div class="section-h"><h2>Status</h2></div>
  <div class="facts">
    ${renderMe(s.me)}
    ${renderFact("Towns:", s.towns.map(t => t.name).join(", "))}
    ${renderFact("Rest days:", String(s.rest_days))}
    ${renderFact("Google key:", s.google_key_set ? "set" : "not set")}
    ${renderFact("Google prefill:", s.google_prefill ? "on" : "off")}
    ${renderUsage(s.google_usage)}
    ${renderScan(s.last_scan)}
  </div>`;

const renderExport = () => `
  <div class="section-h"><h2>Export</h2></div>
  <p class="hint">Everything the app holds, to keep or to open in a spreadsheet.</p>
  <div class="row">
    <a class="btn" href="/api/export.json" download>Everything (JSON)</a>
    <a class="btn" href="/api/export.csv" download>Visits (CSV)</a>
  </div>`;

const renderSettings = state => `
  <h1>Settings</h1>
  ${renderPeople(state)}
  ${renderTags(state)}
  ${renderSync(state)}
  ${renderStatus(state.status)}
  ${renderExport()}`;

// ---------------------------------------------------------------- the screen
async function render(ctx) {
  const state = { people: [], tags: [], status: null, sync: null, editing: null, asking: null };

  async function load() {
    const [p, t, s, sync] = await Promise.all([get("/api/people?inactive=1"), get("/api/tags"), get("/api/settings/status"),
      get("/api/sync")]);
    state.people = p.people;
    state.tags = t.tags;
    state.status = s;
    state.sync = sync;
  }
  const draw = () => {
    ctx.view.innerHTML = renderSettings(state);
  };
  const fail = (slot, e) => {
    const el = ctx.view.querySelector(`[data-error="${slot}"]`);
    if (el) el.innerHTML = renderError(e instanceof ApiError ? e.error : "Something went wrong. Reload the page.");
    if (!(e instanceof ApiError)) console.error(e);
  };
  // Run a change, reload everything and redraw; a failure shows in the section's error slot. `keepOpen` leaves an
  // open edit form or confirm alone (a tick or a move happens while someone may already be doing something else).
  async function change(slot, work, done, keepOpen = false) {
    try {
      await work();
      if (!keepOpen) {
        state.editing = null;
        state.asking = null;
      }
      await load();
    } catch (e) {
      if (ctx.alive()) fail(slot, e);
      return;
    }
    if (!ctx.alive()) return;
    draw();
    if (done) toast(done);
  }
  const tagMove = (id, by) => {
    const ids = state.tags.map(t => t.id);
    const at = ids.indexOf(id);
    const to = at + by;
    if (at < 0 || to < 0 || to >= ids.length) return null;
    [ids[at], ids[to]] = [ids[to], ids[at]];
    return ids;
  };

  await load();
  if (!ctx.alive()) return;
  draw();

  ctx.view.addEventListener("submit", e => {
    e.preventDefault();
    const form = e.target.closest("form[data-form]");
    if (!form) return;
    const f = new FormData(form);
    const id = Number(form.dataset.id);
    switch (form.dataset.form) {
      case "person":
        return change("people", () => send("POST", "/api/people", { name: f.get("name"), grp: f.get("grp"), email: f.get("email") || null }), "Person added");
      case "person-edit":
        return change("people", () => send("PATCH", `/api/people/${id}`, { name: f.get("name"), grp: f.get("grp"), email: f.get("email") || null }), "Saved");
      case "tag":
        return change("tags", () => send("POST", "/api/tags", { label: f.get("label"), occasion: f.get("occasion") === "on" }), "Tag added");
      case "tag-edit":
        return change("tags", () => send("PATCH", `/api/tags/${id}`, { label: f.get("label") }), "Saved");
      case "sync-people":
        return change("sync", () => send("PUT", "/api/sync/people", { people: f.getAll("sync-person").map(Number) }), "Saved");
    }
  });

  ctx.view.addEventListener("click", e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    switch (btn.dataset.act) {
      case "edit-person": state.editing = `person:${id}`; state.asking = null; return draw();
      case "edit-tag": state.editing = `tag:${id}`; state.asking = null; return draw();
      case "ask-person": state.asking = `person:${id}`; state.editing = null; return draw();
      case "ask-delete": state.asking = `tag:${id}`; state.editing = null; return draw();
      case "cancel": state.editing = null; state.asking = null; return draw();
      case "activate-person": return change("people", () => send("PATCH", `/api/people/${id}`, { active: true }), "Reactivated");
      case "remove-person":
        return change("people", async () => {
          const r = await send("DELETE", `/api/people/${id}`);
          toast(r.result === "deleted" ? "Person deleted" : "Person made inactive");
        });
      case "delete": return change("tags", () => send("DELETE", `/api/tags/${id}`), "Tag deleted");
      case "dismiss-sync": {
        const path = `/api/sync/${encodeURIComponent(btn.dataset.source)}/${encodeURIComponent(btn.dataset.ref)}/dismiss`;
        return change("sync", () => send("POST", path), "Dismissed");
      }
      case "up":
      case "down": {
        const ids = tagMove(id, btn.dataset.act === "up" ? -1 : 1);
        return ids && change("tags", () => send("PUT", "/api/tags/order", { ids }), null, true);
      }
    }
  });

  ctx.view.addEventListener("change", e => {
    const box = e.target.closest('input[data-act="occasion"]');
    if (box) change("tags", () => send("PATCH", `/api/tags/${Number(box.dataset.id)}`, { occasion: box.checked }), null, true);
  });
}

register("/settings", { label: "Settings", nav: 50, render });
