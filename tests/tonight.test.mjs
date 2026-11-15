import test from "node:test";
import assert from "node:assert/strict";
import { distanceText, initialState, lastVisitLine, openLine, queryFor, renderCard, scoreText } from "../static/js/tonight.js";

test("lastVisitLine reads like the spec's card", () => {
  const lv = { on: "2026-08-14", people: [{ name: "Sam", rating: 5 }, { name: "Riley", rating: 4 }, { name: "Alex", rating: null }] };
  assert.equal(lastVisitLine(lv, "2026-11-14"), "3 months ago · Sam 5, Riley 4, Alex");
  assert.equal(lastVisitLine(null, "2026-11-14"), "Not been yet");
});

test("openLine, scoreText and distanceText", () => {
  assert.equal(openLine({ open: true, closes_at: "9 pm" }), "Open now · closes 9 pm");
  assert.equal(openLine({ open: true, closes_at: null }), "Open now");
  assert.equal(openLine({ open: false, closes_at: null }), "Closed now");
  assert.equal(openLine({ open: null, closes_at: null }), "Hours unknown");
  assert.equal(scoreText(4.5), "4.5");
  assert.equal(scoreText(4), "4");
  assert.equal(scoreText(13 / 3), "4.3");
  assert.equal(scoreText(null), "");
  assert.equal(distanceText(0.42), "420 m");
  assert.equal(distanceText(3.14159), "3.1 km");
  assert.equal(distanceText(null), "");
});

test("the address wins over what the device remembers, which wins over the defaults", () => {
  assert.deepEqual([initialState({}, {}).mode, initialState({}, {}).audience], ["not_lately", "everyone"]);
  assert.equal(initialState({}, { mode: "want", audience: "kids" }).audience, "kids");
  assert.equal(initialState({ mode: "any" }, { mode: "want" }).mode, "any");
  assert.equal(initialState({ mode: "bogus" }, { mode: "nonsense" }).mode, "not_lately");
  const s = initialState({ mode: "any", town: "Concord", seed: "7" });
  assert.equal(queryFor(s), "mode=any&audience=everyone&town=Concord&seed=7");
  assert.equal(queryFor(s, { limit: 10, offset: 0 }), "mode=any&audience=everyone&town=Concord&seed=7&limit=10&offset=0");
});

test("a card escapes what people typed", () => {
  const html = renderCard({ id: 3, name: "<b>Casa</b>", town: null, cuisine: "Mexican", unsure: true, want_to_try: false,
    tags: [{ id: 1, label: "a&b" }], last_visit: null, score: null, open: null, closes_at: null }, "2026-11-14");
  assert.match(html, /&lt;b&gt;Casa&lt;\/b&gt;/);
  assert.match(html, /a&amp;b/);
  assert.match(html, /class="card marked"/);
  assert.match(html, /No town · Mexican/);
  assert.doesNotMatch(html, /Score/);
});
