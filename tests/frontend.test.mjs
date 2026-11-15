// Frontend unit tests: `node --test tests/*.test.mjs`. The modules under static/js/ are ES modules with no top-level
// DOM access, so they load in Node as they are; only the DOM-free helpers are tested here.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ago, esc, fmtDate, renderError, stars, todayLA, townLabel } from "../static/js/core.js";
import { match, parseHash, register, routeFor } from "../static/js/router.js";

test("esc", () => {
  assert.equal(esc(`<a href="x">'&`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
  assert.equal(esc(null), "");
  assert.equal(esc(undefined), "");
  assert.equal(esc(0), "0");
  assert.equal(esc("&amp;"), "&amp;amp;");
  assert.equal(esc("<img src=x onerror=alert(1)>"), "&lt;img src=x onerror=alert(1)&gt;");
});

test("ago counts calendar days in words", () => {
  const today = "2026-11-14";
  assert.equal(ago("2026-11-14", today), "today");
  assert.equal(ago("2026-11-13", today), "yesterday");
  assert.equal(ago("2026-11-09", today), "5 days ago");
  assert.equal(ago("2026-10-31", today), "2 weeks ago");
  assert.equal(ago("2026-10-07", today), "5 weeks ago");
  assert.equal(ago("2026-08-14", today), "3 months ago");
  assert.equal(ago("2026-08-15", today), "2 months ago");
  assert.equal(ago("2025-11-14", today), "1 year ago");
  assert.equal(ago("2023-12-01", today), "2 years ago");
  assert.equal(ago("2026-03-01", "2026-03-01"), "today");
});

test("ago refuses what isn't a past calendar day", () => {
  for (const bad of [null, undefined, "", "2026-02-30", "11/14/2026", "2026-11-15"]) assert.equal(ago(bad, "2026-11-14"), "", String(bad));
});

test("stars: whole stars from 1 to 5", () => {
  assert.equal(stars(1), "★☆☆☆☆");
  assert.equal(stars(4), "★★★★☆");
  assert.equal(stars(5), "★★★★★");
  for (const bad of [0, 6, 4.5, null, undefined, "4"]) assert.equal(stars(bad), "", String(bad));
});

test("fmtDate reads a calendar day", () => {
  assert.equal(fmtDate("2026-11-14"), "Nov 14, 2026");
  assert.equal(fmtDate("2027-01-02"), "Jan 2, 2027");
  assert.equal(fmtDate("2026-02-30"), "");
  assert.equal(fmtDate(null), "");
});

test("townLabel", () => {
  assert.equal(townLabel("Walnut Creek"), "Walnut Creek");
  assert.equal(townLabel(""), "No town");
  assert.equal(townLabel(null), "No town");
});

test("todayLA is the date in Concord, whatever the phone's zone", () => {
  assert.equal(todayLA(new Date(Date.UTC(2026, 10, 15, 7, 30))), "2026-11-14");   // 23:30 on Nov 14 in Concord
  assert.equal(todayLA(new Date(Date.UTC(2026, 10, 15, 8, 30))), "2026-11-15");
});

test("renderError escapes and renders nothing for no message", () => {
  assert.equal(renderError(""), "");
  assert.match(renderError("<b>"), /role="alert">&lt;b&gt;</);
});

test("parseHash", () => {
  assert.deepEqual(parseHash("#/p/12/visits?person=3"), { path: "/p/12/visits", query: { person: "3" } });
  assert.deepEqual(parseHash(""), { path: "/", query: {} });
  assert.deepEqual(parseHash("#/"), { path: "/", query: {} });
  assert.deepEqual(parseHash("#/past/"), { path: "/past", query: {} });
});

test("match: literal segments and :params", () => {
  assert.deepEqual(match("/", "/"), {});
  assert.deepEqual(match("/p/:id", "/p/12"), { id: "12" });
  assert.deepEqual(match("/p/:id/visits", "/p/12/visits"), { id: "12" });
  assert.deepEqual(match("/lists/:id", "/lists/Taco%20Trail"), { id: "Taco Trail" });
  assert.equal(match("/p/:id", "/p/12/visits"), null);
  assert.equal(match("/p/:id", "/past"), null);
  assert.equal(match("/", "/past"), null);
});

test("register and routeFor: screens self-register; the first matching pattern wins", () => {
  const render = () => {};
  register("/test-only/:id", { label: "Test", render });
  assert.deepEqual(routeFor("/test-only/7").params, { id: "7" });
  assert.equal(routeFor("/test-only/7").route.label, "Test");
  assert.equal(routeFor("/nowhere"), null);
  assert.throws(() => register("/test-only/:id", { label: "Again", render }), /already/);
  assert.throws(() => register("no-slash", { label: "Bad", render }), /start with/);
  assert.throws(() => register("/no-render", { label: "Bad" }), /render/);
});
