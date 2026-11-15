// #/p/<id>/visits: a place's visits, newest first, and the form that logs a new one. "Who went" starts with the
// signed-in editor's own person ticked. Deleting asks twice in the page (never a browser pop-up).
import { get, send } from "./api.js";
import { ago, esc, fmtDate, renderError, stars, todayLA, toast } from "./core.js";
import { register } from "./router.js";

const renderRating = p => (p.rating ? `<span class="stars" aria-label="${esc(p.rating)} out of 5">${esc(stars(p.rating))}</span>` : `<span class="muted">no rating</span>`);

const renderPersonNote = p => (p.note ? `<span class="muted">${esc(p.note)}</span>` : "");

const renderPerson = p => `<li><strong>${esc(p.name)}</strong> ${renderRating(p)} ${renderPersonNote(p)}</li>`;

const renderPeople = people => people.map(p => renderPerson(p)).join("");

const renderOccasion = v => (v.occasion ? `<div class="m"><span class="badge">${esc(v.occasion)}</span></div>` : "");

const renderVisitNote = v => (v.note ? `<p class="m">${esc(v.note)}</p>` : "");

const renderVisit = v => `<article class="card" data-visit="${esc(v.id)}"><div class="b">
  <div class="t">${esc(fmtDate(v.visited_on))} <span class="muted small">${esc(ago(v.visited_on))}</span></div>
  ${renderOccasion(v)}
  <ul class="m">${renderPeople(v.people)}</ul>
  ${renderVisitNote(v)}
  <div class="row"><button type="button" class="link" data-act="ask-delete" data-id="${esc(v.id)}">Delete</button></div>
</div></article>`;

const renderVisits = list => (list.length ? list.map(v => renderVisit(v)).join("") : `<p class="empty">No visits yet.</p>`);

const renderRatingOptions = () => ["", 1, 2, 3, 4, 5].map(n => `<option value="${esc(n)}">${esc(n ? stars(n) : "No rating")}</option>`).join("");

const renderChecked = on => (on ? "checked" : "");

const renderWentRows = (people, meId) => people.map(p => renderWent(p, meId)).join("");

const renderWent = (p, meId) => renderPersonRow(p, p.id === meId);

const renderPersonRow = (p, checked) => `<div class="panel" data-person="${esc(p.id)}">
  <label class="check"><input type="checkbox" name="went" value="${esc(p.id)}" ${renderChecked(checked)}> ${esc(p.name)}</label>
  <label class="f">Rating<select name="rating-${esc(p.id)}">${renderRatingOptions()}</select></label>
  <label class="f">Note<input type="text" name="note-${esc(p.id)}" maxlength="500"></label>
</div>`;

const renderForm = (people, meId, error) => `<form class="form" id="visit-form" novalidate>
  ${renderError(error)}
  <label class="f">Date<input type="date" name="visited_on" value="${esc(todayLA())}" max="${esc(todayLA())}" required></label>
  <fieldset class="form"><legend>Who went</legend>${renderWentRows(people, meId)}</fieldset>
  <label class="f">Occasion<input type="text" name="occasion" maxlength="80" placeholder="Optional"></label>
  <label class="f">Note<textarea name="note" maxlength="2000"></textarea></label>
  <div class="row"><button type="submit" class="primary">Log visit</button></div>
</form>`;

function formBody(form) {
  const field = n => form.elements[n]?.value ?? "";
  const people = [...form.querySelectorAll("input[name=went]:checked")].map(box => {
    const id = Number(box.value);
    const rating = field(`rating-${id}`);
    return { person: id, rating: rating ? Number(rating) : null, note: field(`note-${id}`) };
  });
  return { visited_on: field("visited_on"), people, occasion: field("occasion"), note: field("note") };
}

async function render(ctx) {
  const id = ctx.params.id;
  const [list, who, me] = await Promise.all([get(`/api/visits?place=${encodeURIComponent(id)}`), get("/api/visits/people"), get("/api/me")]);
  if (!ctx.alive()) return;
  const name = list.place?.name ?? "This place";
  const draw = (error = "") => {
    ctx.view.innerHTML = `<p><a href="#/p/${esc(id)}">Back to ${esc(name)}</a></p>
      <h1>${esc(name)}: visits</h1>
      <h2 class="section-h">Log a visit</h2>
      ${renderForm(who.people, me.person?.id ?? null, error)}
      <h2 class="section-h">History</h2>
      <div class="list">${renderVisits(list.visits)}</div>`;
  };
  draw();

  ctx.view.addEventListener("submit", async e => {
    e.preventDefault();
    const button = e.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await send("POST", `/api/places/${encodeURIComponent(id)}/visits`, formBody(e.target));
      toast("Visit logged");
      if (ctx.alive()) ctx.rerender();
    } catch (err) {
      if (!ctx.alive()) return;
      const typed = formBody(e.target);          // keep what was typed: redraw, then put it back
      draw(err.error ?? "Something went wrong. Try again.");
      restore(ctx.view.querySelector("#visit-form"), typed);
    }
  });

  ctx.view.addEventListener("click", async e => {
    const btn = e.target.closest("[data-act]");
    if (!btn || !["ask-delete", "delete"].includes(btn.dataset.act)) return;
    if (btn.dataset.act === "ask-delete") {
      btn.dataset.act = "delete";
      btn.textContent = "Really delete this visit?";
      btn.classList.add("danger");
      return;
    }
    btn.disabled = true;
    try {
      await send("DELETE", `/api/visits/${encodeURIComponent(btn.dataset.id)}`, {});
      toast("Visit deleted");
      if (ctx.alive()) ctx.rerender();
    } catch (err) {
      btn.disabled = false;
      toast(err.error ?? "Couldn't delete it. Try again.");
    }
  });
}

function restore(form, typed) {
  if (!form) return;
  form.elements.visited_on.value = typed.visited_on;
  form.elements.occasion.value = typed.occasion;
  form.elements.note.value = typed.note;
  for (const box of form.querySelectorAll("input[name=went]")) {
    const was = typed.people.find(p => p.person === Number(box.value));
    box.checked = Boolean(was);
    if (was) {
      form.elements[`rating-${box.value}`].value = was.rating ?? "";
      form.elements[`note-${box.value}`].value = was.note ?? "";
    }
  }
}

register("/p/:id/visits", { label: "Visits", render });
