// Add place (#/add): look a name up in OpenStreetMap (and Google, with a key), pick a result or start from nothing, then
// the same form the inbox uses to accept, saved with POST /api/places. A verdict and a reason are required.
//
// Google's terms: stored fields never come from Google on their own. Where the picked place has no OSM value for its
// name or address, the form shows Google's beside the box, labelled "Google Maps", with "Use this" (only while the
// owner's GOOGLE_PREFILL setting is on). The pin is OSM's, or the typed address geocoded on save, never Google's.
//
// This module also exports the place form for inbox.js.
import { get, send, ApiError } from "./api.js";
import { esc, townLabel, renderError, toast } from "./core.js";
import { register } from "./router.js";
import { OWNERSHIP } from "./place.js";

export const FIELDS = [
  ["name", "Name", 120], ["address", "Address", 200], ["town", "Town", 60], ["cuisine", "Cuisine", 80],
  ["phone", "Phone", 40], ["website", "Website", 300], ["hours", "Hours (OpenStreetMap opening_hours)", 300],
];
const GOOGLE_FIELDS = ["name", "address"];

export const renderOsmCredit = () => `<p class="foot">Place details from OpenStreetMap: © <a href="https://www.openstreetmap.org/copyright" rel="noopener noreferrer">OpenStreetMap contributors</a>. Values marked Google Maps come from Google and are saved only when you choose them.</p>`;

// The tag list belongs to Settings; if it can't be read the form simply has no tag boxes.
export async function loadTags() {
  try {
    const r = await get("/api/tags");
    const list = Array.isArray(r) ? r : r.tags;
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

const renderHint = (key, values, google, prefill) => (prefill && google && GOOGLE_FIELDS.includes(key) && !values[key] && google[key]
  ? `<div class="row small" data-hint="${esc(key)}"><span class="badge">Google Maps</span> <span>${esc(google[key])}</span> <button type="button" class="link" data-use="${esc(key)}">Use this</button></div>`
  : "");
const renderInput = ([key, label, max], values, google, prefill) => `<label class="f">${esc(label)}<input name="${esc(key)}" value="${esc(values[key])}" maxlength="${esc(max)}" autocomplete="off"></label>${renderHint(key, values, google, prefill)}`;
const renderInputs = (values, google, prefill) => FIELDS.map(f => renderInput(f, values, google, prefill)).join("");
const renderSelected = on => (on ? "selected" : "");
const renderOption = (value, label, chosen) => `<option value="${esc(value)}" ${renderSelected(value === chosen)}>${esc(label)}</option>`;
const renderVerdicts = chosen => Object.entries(OWNERSHIP).map(([v, l]) => renderOption(v, l, chosen)).join("");
const renderTagBox = t => `<label class="check"><input type="checkbox" name="tag" value="${esc(t.id)}"> ${esc(t.label)}</label>`;
const renderTagBoxes = tags => tags.map(renderTagBox).join("");
const renderTagField = tags => (tags.length ? `<fieldset class="f"><legend>Tags</legend><div class="chips">${renderTagBoxes(tags)}</div></fieldset>` : "");

// The place form. values: the starting field values (OSM's or Where to Eat's, never Google's); google: {name, address}
// from Google Maps, shown with "Use this" where a value is missing and prefill is on; verdict: the preselected
// ownership; reason: the prefilled reason; submit: the button's words.
export function renderPlaceForm({ values = {}, google = null, prefill = false, verdict = "unsure", reason = "", tags = [], submit = "Save" }) {
  return `<form class="form panel place-form" novalidate>
    <div data-msg></div>
    ${renderInputs(values, google, prefill)}
    <label class="f">Does it count?<select name="ownership">${renderVerdicts(verdict)}</select></label>
    <label class="f">Reason<input name="ownership_reason" value="${esc(reason)}" maxlength="200" required autocomplete="off"></label>
    <label class="check"><input type="checkbox" name="want_to_try"> Want to try</label>
    ${renderTagField(tags)}
    <div class="row"><button class="primary" type="submit">${esc(submit)}</button><button type="button" data-act="cancel">Cancel</button></div>
  </form>`;
}

// "Use this" copies the Google value into its box and marks it; typing over it drops the mark. Call once per view.
export function wireGoogleHints(view, googleFor) {
  view.addEventListener("click", e => {
    const button = e.target.closest("[data-use]");
    if (!button) return;
    const form = button.closest("form");
    const key = button.dataset.use;
    const input = form.querySelector(`input[name="${key}"]`);
    const value = googleFor(form)?.[key];
    if (!input || !value) return;
    input.value = value;
    input.dataset.fromGoogle = "1";
    form.querySelector(`[data-hint="${key}"]`)?.remove();
  });
  view.addEventListener("input", e => {
    if (e.target.dataset?.fromGoogle) delete e.target.dataset.fromGoogle;
  });
}

// The form's values. With asGoogle, a field filled by "Use this" is sent as {from_google: true} (the inbox's accept
// checks it against GOOGLE_PREFILL); without, as the value the editor confirmed.
export function readPlaceForm(form, { asGoogle = false } = {}) {
  const body = {};
  for (const [key] of FIELDS) {
    const input = form.querySelector(`input[name="${key}"]`);
    body[key] = asGoogle && input.dataset.fromGoogle ? { from_google: true } : input.value.trim();
  }
  body.ownership = form.querySelector("select[name=ownership]").value;
  body.ownership_reason = form.querySelector("input[name=ownership_reason]").value.trim();
  body.want_to_try = form.querySelector("input[name=want_to_try]").checked;
  body.tags = [...form.querySelectorAll("input[name=tag]:checked")].map(b => Number(b.value));
  return body;
}

// A 409 duplicate: the existing place and an "Add anyway" button (data-act="anyway").
export const renderDuplicate = (message, id) => `<div class="msg error" role="alert"><p>${esc(message)}</p><div class="row"><a class="btn" href="#/p/${esc(id)}">See it</a><button type="button" data-act="anyway">Add anyway</button></div></div>`;

export function showFormError(form, err) {
  const box = form.querySelector("[data-msg]");
  if (err instanceof ApiError && err.status === 409 && err.data?.duplicate_of) box.innerHTML = renderDuplicate(err.error, err.data.duplicate_of);
  else box.innerHTML = renderError(err instanceof ApiError ? err.error : "Something went wrong. Try again.");
  box.scrollIntoView?.({ block: "nearest" });
}

// ---------------------------------------------------------------- the Add place screen
const renderTownOption = t => `<option value="${esc(t.name)}">${esc(t.name)}</option>`;
const renderTownOptions = towns => towns.map(renderTownOption).join("");
const renderTownPick = towns => (towns.length
  ? `<label class="f">Town<select name="town"><option value="">Any of the towns</option>${renderTownOptions(towns)}</select></label>`
  : "");

const renderUseButton = r => `<button type="button" data-pick="${esc(r.i)}">Use this one</button>`;
const renderInInbox = r => (r.in_inbox
  ? `<span class="small muted">${esc(r.inbox_status === "dismissed" ? "Dismissed from the inbox once; adding it here brings it back" : "In the inbox; adding it here sorts it")}</span>`
  : "");
const renderMark = r => (r.in_list
  ? `<a class="btn" href="#/p/${esc(r.in_list)}">Already on the list</a>`
  : `${renderUseButton(r)}${renderInInbox(r)}`);
const renderSource = r => (r.source === "google" ? `<span class="badge">Google Maps</span>` : `<span class="badge">OpenStreetMap</span>`);
const renderChain = r => (r.is_chain ? `<span class="badge unsure">Chain hint</span>` : "");
const renderResult = r => `<div class="card"><div class="b">
    <div class="t">${esc(r.name)}</div>
    <div class="m">${esc([r.address, townLabel(r.town)].filter(Boolean).join(" · "))}</div>
    <div class="row">${renderSource(r)}${renderChain(r)}</div>
    <div class="row">${renderMark(r)}</div>
  </div></div>`;
const renderResultList = results => results.map(renderResult).join("");
const renderNote = n => `<p class="note">${esc(n)}</p>`;
const renderNotes = notes => notes.map(renderNote).join("");
const renderNothing = found => (found.results.length ? "" : `<p class="muted">Nothing found. Fill it in yourself.</p>`);
const renderResults = found => (found
  ? `${renderNotes(found.notes)}${renderNothing(found)}<div class="list">${renderResultList(found.results)}</div>
    <p><button type="button" data-act="blank">Not here? Fill it in yourself</button></p>`
  : "");

function renderAdd(state) {
  return `<h1>Add place</h1>
    <form id="lookup" class="form" novalidate>
      <div data-msg></div>
      <label class="f">Name<input name="name" maxlength="120" required autocomplete="off" value="${esc(state.name)}"></label>
      ${renderTownPick(state.towns)}
      <div class="row"><button class="primary" type="submit">Look it up</button></div>
    </form>
    <div id="results">${renderResults(state.found)}</div>
    <div id="add-form"></div>
    ${renderOsmCredit()}`;
}

// The typed address's pin (Nominatim, then Photon). A miss or a failure saves the place without one; its edit form
// can add it later.
async function geocodeInto(body) {
  try {
    const pin = await get(`/api/geocode?address=${encodeURIComponent(body.address)}&town=${encodeURIComponent(body.town)}`);
    if (pin.lat != null) Object.assign(body, { lat: pin.lat, lng: pin.lng });
  } catch {
    // no pin
  }
}

async function loadTowns() {
  try {
    const s = await get("/api/settings/status");
    return Array.isArray(s.towns) ? s.towns : [];
  } catch {
    return [];
  }
}

async function render(ctx) {
  const { view } = ctx;
  const [towns, tags] = await Promise.all([loadTowns(), loadTags()]);
  if (!ctx.alive()) return;
  const state = { name: ctx.query.name ?? "", towns, found: null, prefill: false, picked: null, saving: false };
  view.innerHTML = renderAdd(state);
  const lookupForm = view.querySelector("#lookup");
  const formBox = view.querySelector("#add-form");

  const openForm = picked => {
    state.picked = picked;
    const values = {};
    // An OSM result fills the form; a Google one fills nothing by itself (its values wait behind "Use this").
    for (const [key] of FIELDS) values[key] = picked?.source === "osm" ? picked[key] ?? "" : "";
    if (!picked) values.name = lookupForm.querySelector("input[name=name]").value.trim();
    if (!values.town) values.town = lookupForm.querySelector("select[name=town]")?.value ?? "";
    formBox.innerHTML = renderPlaceForm({ values, google: picked?.google, prefill: state.prefill, verdict: "unsure", tags, submit: "Add place" });
    formBox.querySelector("input[name=name]").focus();
    formBox.scrollIntoView?.({ block: "start" });
  };

  wireGoogleHints(view, () => state.picked?.google);

  lookupForm.addEventListener("submit", async e => {
    e.preventDefault();
    const name = lookupForm.querySelector("input[name=name]").value.trim();
    const town = lookupForm.querySelector("select[name=town]")?.value ?? "";
    const msg = lookupForm.querySelector("[data-msg]");
    msg.innerHTML = "";
    if (!name) {
      msg.innerHTML = renderError("Type a name to look up.");
      return;
    }
    const button = lookupForm.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const found = await get(`/api/lookup?name=${encodeURIComponent(name)}&town=${encodeURIComponent(town)}`);
      if (!ctx.alive()) return;
      found.results.forEach((r, i) => { r.i = i; });
      state.found = found;
      state.prefill = Boolean(found.prefill);
      view.querySelector("#results").innerHTML = renderResults(found);
      formBox.innerHTML = "";
    } catch (err) {
      if (ctx.alive()) msg.innerHTML = renderError(err instanceof ApiError ? err.error : "Something went wrong. Try again.");
    } finally {
      button.disabled = false;
    }
  });

  view.addEventListener("click", e => {
    const pick = e.target.closest("[data-pick]");
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (pick) openForm(state.found.results[Number(pick.dataset.pick)]);
    else if (act === "blank") openForm(null);
    else if (act === "cancel") formBox.innerHTML = "";
    else if (act === "anyway") save(true);
  });

  // A pick that is already an inbox candidate is accepted through the inbox, so the candidate links to the new place
  // (and its lists move with it) instead of staying behind as a duplicate.
  const save = async (addAnyway = false) => {
    const form = formBox.querySelector("form");
    if (!form || state.saving) return;
    const body = readPlaceForm(form);
    const picked = state.picked;
    if (addAnyway) body.add_anyway = true;
    if (!body.name || !body.ownership_reason) {
      form.querySelector("[data-msg]").innerHTML = renderError(body.name ? "Give a reason for the verdict." : "The place needs a name.");
      return;
    }
    // OSM's pin when it gave one and the address is still its address.
    if (picked?.source === "osm" && picked.lat != null && body.address === (picked.address ?? "")) {
      body.lat = picked.lat;
      body.lng = picked.lng;
    }
    state.saving = true;
    try {
      let place;
      if (picked?.in_inbox) {
        place = await send("POST", `/api/inbox/${picked.in_inbox}/accept`, body);   // the inbox geocodes a missing pin
      } else {
        body.tag_ids = body.tags;
        delete body.tags;
        if (picked?.osm_ref) body.osm_ref = picked.osm_ref;
        if (picked?.google_place_id) body.google_place_id = picked.google_place_id;
        if (body.lat === undefined && body.address) await geocodeInto(body);
        if (!ctx.alive()) return;
        place = await send("POST", "/api/places", body);
      }
      toast("Added");
      location.hash = `#/p/${place.id}`;
    } catch (err) {
      if (ctx.alive()) showFormError(form, err);
    } finally {
      state.saving = false;
    }
  };

  formBox.addEventListener("submit", e => {
    e.preventDefault();
    save(false);
  });
  if (state.name) lookupForm.requestSubmit();
}

register("/add", { label: "Add place", render });
