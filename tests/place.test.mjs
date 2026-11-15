import test from "node:test";
import assert from "node:assert/strict";
import { pastLine } from "../static/js/past.js";
import { eventTitle } from "../static/js/place.js";

test("pastLine reads like the spec", () => {
  assert.equal(pastLine({ kind: "closed", since: "2027-03-09", reason: null, visit_count: 14 }), "Closed Mar 2027 · 14 visits");
  assert.equal(pastLine({ kind: "ownership", since: "2027-06-01", reason: "sold to a group", visit_count: 6 }),
    "Stopped counting Jun 2027 (sold to a group) · 6 visits");
  assert.equal(pastLine({ kind: "closed", since: "2027-03-09", reason: "", visit_count: 1 }), "Closed Mar 2027 · 1 visit");
});

test("eventTitle names the old and new values", () => {
  assert.equal(eventTitle({ kind: "ownership", old: "counts", new: "no" }), "Counted → Doesn't count");
  assert.equal(eventTitle({ kind: "standing", old: "open", new: "temp_closed" }), "Open → Temporarily closed");
  assert.equal(eventTitle({ kind: "ownership", old: null, new: "unsure" }), "Set → Not sure");
});
