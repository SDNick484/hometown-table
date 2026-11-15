// The edit form (#/p/<id>/edit): the place's facts, want to try and, when the tag list is there, its tags. Saving sends
// the version the form was loaded with; if someone else saved first the server says so and nothing is overwritten.
// Ownership and standing are not here: they change on the place page, with a reason, so they leave a history.
import { get, send, ApiError } from "./api.js";
import { esc, renderError, toast } from "./core.js";
import { register } from "./router.js";

// attrs: extra attributes as an object, e.g. { maxlength: 120, required: "" }.
const renderAttrs = attrs => Object.entries(attrs).map(([k, v]) => `${k}="${esc(v)}"`).join(" ");
const renderField = (id, label, value, attrs = {}) => `<label class="f">${esc(label)}<input id="f-${esc(id)}" value="${esc(value)}" ${renderAttrs(attrs)}></label>`;

const renderChecked = on => (on ? "checked" : "");
const renderTagBox = (t, chosen) => `<label class="check"><input type="checkbox" name="tag" value="${esc(t.id)}" ${renderChecked(chosen.has(t.id))}> ${esc(t.label)}</label>`;
const renderTagBoxes = (tags, chosen) => tags.map(t => renderTagBox(t, chosen)).join("");
const renderTagField = (tags, chosen) => (tags.length ? `<fieldset class="f"><legend>Tags</legend><div class="chips">${renderTagBoxes(tags, chosen)}</div></fieldset>` : "");

function renderForm(p, tags) {
  const chosen = new Set(p.tags.map(t => t.id));
  return `<form id="place-form" class="form" novalidate>
    <h1>Edit ${esc(p.name)}</h1>
    <div id="form-msg"></div>
    ${renderField("name", "Name", p.name, { maxlength: 120, required: "" })}
    ${renderField("town", "Town", p.town, { maxlength: 60 })}
    ${renderField("address", "Address", p.address, { maxlength: 200 })}
    <div class="grid2">${renderField("lat", "Latitude", p.lat, { inputmode: "decimal" })}${renderField("lng", "Longitude", p.lng, { inputmode: "decimal" })}</div>
    ${renderField("cuisine", "Cuisine", p.cuisine, { maxlength: 80 })}
    ${renderField("phone", "Phone", p.phone, { maxlength: 40, inputmode: "tel" })}
    ${renderField("website", "Website", p.website, { maxlength: 300, inputmode: "url" })}
    ${renderField("hours", "Hours (OpenStreetMap opening_hours)", p.hours, { maxlength: 300 })}
    <label class="f">Notes<textarea id="f-notes" maxlength="2000">${esc(p.notes)}</textarea></label>
    <label class="check"><input id="f-want" type="checkbox" ${renderChecked(p.want_to_try)}> Want to try</label>
    ${renderTagField(tags, chosen)}
    <div class="row"><button class="primary" type="submit">Save</button><a class="btn" href="#/p/${esc(p.id)}">Cancel</a></div>
  </form>`;
}

// The tag list belongs to Settings; if it can't be read the form simply has no tag boxes.
async function loadTags() {
  try {
    const r = await get("/api/tags");
    const list = Array.isArray(r) ? r : r.tags;
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

const num = text => (text.trim() === "" ? null : Number(text));

async function render(ctx) {
  const { view, params } = ctx;
  const [place, tags] = await Promise.all([get(`/api/places/${encodeURIComponent(params.id)}`), loadTags()]);
  if (!ctx.alive()) return;
  view.innerHTML = renderForm(place, tags);
  const value = id => view.querySelector(`#f-${id}`).value;
  view.querySelector("#place-form").addEventListener("submit", async e => {
    e.preventDefault();
    const body = { version: place.version, want_to_try: view.querySelector("#f-want").checked };
    for (const key of ["name", "town", "address", "cuisine", "phone", "website", "hours", "notes"]) body[key] = value(key);
    body.lat = num(value("lat"));
    body.lng = num(value("lng"));
    if (tags.length) body.tag_ids = [...view.querySelectorAll("input[name=tag]:checked")].map(b => Number(b.value));
    try {
      await send("PATCH", `/api/places/${place.id}`, body);
    } catch (err) {
      if (ctx.alive()) view.querySelector("#form-msg").innerHTML = renderError(err instanceof ApiError ? err.error : "Something went wrong. Try again.");
      return;
    }
    toast("Saved");
    location.hash = `#/p/${place.id}`;
  });
}

register("/p/:id/edit", { label: "Edit place", render });
