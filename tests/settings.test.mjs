import test from "node:test";
import assert from "node:assert/strict";
import { syncSourceLine, unmatchedLine } from "../static/js/settings.js";

const at = Date.UTC(2026, 10, 14, 20) / 1000;   // Nov 14, 2026, noon in Los Angeles
const source = (fields = {}) => ({ label: "Dinner Log", configured: true, settings: ["DINNER_LOG_URL", "DINNER_LOG_TOKEN"],
  last: null, error: null, ...fields });

test("syncSourceLine says what a source did last", () => {
  assert.equal(syncSourceLine(source({ configured: false })),
    "not set up (set DINNER_LOG_URL and DINNER_LOG_TOKEN in /etc/conf.d/hometown)");
  assert.equal(syncSourceLine(source()), "set up; it runs once a day");
  assert.equal(syncSourceLine(source({ last: { at, status: "no_people" } })), "nothing synced on Nov 14, 2026: no one is chosen above");
  assert.equal(syncSourceLine(source({ last: { at, status: "ok", new: 3, logged: 1, duplicate: 1, unmatched: 1 } })),
    "Nov 14, 2026: 3 new, 1 logged, 1 already logged, 1 unmatched");
  assert.equal(syncSourceLine(source({ last: { at, status: "failed" }, error: "Dinner Log refused the token (401)." })),
    "failed on Nov 14, 2026: Dinner Log refused the token (401).");
});

test("unmatchedLine leaves out what is missing", () => {
  assert.equal(unmatchedLine({ day: "2026-11-01", location: "Lafayette", label: "Dinner Log" }), "Nov 1, 2026 · Lafayette · Dinner Log");
  assert.equal(unmatchedLine({ day: "not a day", location: null, label: "Where to Eat" }), "not a day · Where to Eat");
});
