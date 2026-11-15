// Every screen module, imported for its side effect: each calls router.register() for its own routes. One block per
// screen task; a task replaces only the "(not built yet)" line of its own block with its import lines, and never
// edits another block or adds one (plan: Shared files).

// --- Task 2: Places ---
import "./place.js";
import "./place-edit.js";
import "./past.js";

// --- Task 3: Settings ---
import "./settings.js";

// --- Task 4: Visits ---
import "./visits.js";

// --- Task 6: Tonight ---
import "./tonight.js";

// --- Task 10: Inbox and Add place ---
// (not built yet)

// --- Task 11: Lists ---
import "./lists.js";
