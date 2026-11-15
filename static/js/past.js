// Past places (#/past): every place that is closed or no longer counts, newest change first. Nothing is deleted: the
// visits and the history stay, and a past place can be changed back from its page.
import { get } from "./api.js";
import { esc, fmtDate, townLabel } from "./core.js";
import { register } from "./router.js";

// "Nov 14, 2026" -> "Nov 2026".
const monthYear = iso => fmtDate(iso).replace(/ \d+,/, "");

// "Closed Mar 2027 · 14 visits" or "Stopped counting Jun 2027 (sold to a group) · 6 visits".
export function pastLine(p) {
  const what = p.kind === "closed" ? "Closed" : "Stopped counting";
  const visits = `${p.visit_count} visit${p.visit_count === 1 ? "" : "s"}`;
  return `${what} ${monthYear(p.since)}${p.reason ? ` (${p.reason})` : ""} · ${visits}`;
}

const renderPlaceCard = p => `<a class="card" href="#/p/${esc(p.id)}"><div class="b">
  <span class="t">${esc(p.name)}</span>
  <span class="m">${esc([townLabel(p.town), p.cuisine].filter(Boolean).join(" · "))}</span>
  <span class="m">${esc(pastLine(p))}</span>
</div></a>`;

const renderCards = places => places.map(renderPlaceCard).join("");
const renderPast = places => (places.length
  ? `<div class="list">${renderCards(places)}</div>`
  : `<div class="empty"><p>No past places yet.</p></div>`);

async function render(ctx) {
  const { places } = await get("/api/past");
  if (!ctx.alive()) return;
  ctx.view.innerHTML = `<h1>Past places</h1>
    <p class="muted small">Closed, or no longer counting. Their visits and history stay; open one to change it back.</p>
    ${renderPast(places)}`;
}

register("/past", { label: "Past places", nav: 40, render });
