// Shared helpers. No top-level DOM access, so Node can import this file (tests/frontend.test.mjs).
//
// The escaping rule: everything a person typed (and everything from the server) reaches innerHTML only through esc().
// Markup is built in template literals whose every ${...} is one call to esc or to a render* function (which returns
// markup it escaped itself). tests/test_frontend.py scans every module for this. Plain-text helpers such as ago(),
// stars() and fmtDate() go through esc() like anything else.

// ---------------------------------------------------------------- escaping
// The regex spells the two quote characters as hex escapes so the scanner in tests/test_frontend.py, which does not
// parse regex literals, never mistakes one for the start of a string.
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>\x22\x27]/g, c => ESC[c]);
}

// ---------------------------------------------------------------- calendar days
// Dates are "YYYY-MM-DD" calendar days in America/Los_Angeles (the server's common.today()). They are read as calendar
// days, never through the phone's time zone.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-11-14" -> { y: 2026, m: 11, d: 14 }, or null for anything that isn't a real calendar day.
function parseDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(typeof iso === "string" ? iso : "");
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = new Date(Date.UTC(y, mo - 1, d));
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null;
  return { y, m: mo, d, days: t.getTime() / 86400000 };
}

// Today in America/Los_Angeles as "YYYY-MM-DD", whatever zone the phone is in.
export function todayLA(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

// "2026-11-14" -> "Nov 14, 2026". "" for anything else.
export function fmtDate(iso) {
  const p = parseDay(iso);
  return p ? `${MONTHS[p.m - 1]} ${p.d}, ${p.y}` : "";
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"} ago`;

// How long ago a calendar day was, from `today` (both "YYYY-MM-DD"): "today", "yesterday", "5 days ago",
// "2 weeks ago" (from 14 days), "3 months ago" (from 8 weeks, in calendar months), "1 year ago". "" for a bad or
// future date.
export function ago(dateStr, today = todayLA()) {
  const a = parseDay(dateStr);
  const b = parseDay(today);
  if (!a || !b || a.days > b.days) return "";
  const days = b.days - a.days;
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return plural(days, "day");
  if (days < 56) return plural(Math.floor(days / 7), "week");
  const months = (b.y - a.y) * 12 + (b.m - a.m) - (b.d < a.d ? 1 : 0);
  if (months < 12) return plural(Math.max(months, 1), "month");
  return plural(Math.floor(months / 12), "year");
}

// ---------------------------------------------------------------- ratings and places
// Whole stars, 1 to 5: stars(4) -> "★★★★☆". Anything else -> "".
export function stars(n) {
  if (!Number.isInteger(n) || n < 1 || n > 5) return "";
  return "★".repeat(n) + "☆".repeat(5 - n);
}

// A place's town for display: its name, or "No town" when it has none.
export function townLabel(town) {
  return typeof town === "string" && town.trim() ? town.trim() : "No town";
}

// ---------------------------------------------------------------- small markup helpers (each returns escaped markup)
// A message box above a form: role=alert so a screen reader says it. Empty text renders nothing.
export const renderError = message => (message ? `<p class="msg error" role="alert">${esc(message)}</p>` : "");

// A short-lived note at the bottom of the screen ("Saved"), in the page's #toast live region.
export function toast(message) {
  const el = globalThis.document?.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
}
