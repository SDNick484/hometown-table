# Hometown Table — design

Status: approved by the owner, 2026-11-14.
Repo (to be created by the owner): `SDNick484/hometown-table`, public. Service `hometown`.

## 1. Why

The family is hungry and can't decide where to eat. Hometown Table answers "where tonight?" quickly, from a list of
small, independently owned restaurants in Concord and the towns around it, by surfacing:

- places the family likes but hasn't been to in a while,
- places on the want-to-try list,
- places that are new: new to the family (never visited) or new in town (opened since the app started watching).

It also answers "where for this occasion?" (a graduation, a birthday, a baptism, out-of-town guests), and keeps a
record of every visit and what each person thought.

It is a narrower cousin of Where to Eat (`SDNick484/where-to-eat`): one area, only independent places, and built
around visits and a judgement about ownership instead of dishes and prices.

### Success

- On a hungry evening, opening the app on a phone and getting three good, eligible suggestions takes under ten
  seconds, with no typing.
- A new local place that opens in one of the eight towns reaches the inbox within about a week.
- From claude.ai on the phone, "where should we go tonight, somewhere the kids haven't been in a while" gets a
  real answer from the family's own data, and "log that we went to X, Sam gave it 4, I gave it 5" records it.

### Anti-goals (a failure even if it works)

- The app decides on its own that a place is or isn't family owned. That judgement is always the owners'.
- A place silently disappears (closed, sold, stopped counting) and its visit history goes with it.
- Visit history, ratings, notes about people, or the ownership verdicts become visible to anyone outside the
  family's sign-in, or get committed to the public repo.
- A Google bill. Scanning must stay inside Google's free monthly allowance by design, not by luck.

## 2. Separate app, not a filtered view of Where to Eat

Decided: separate app. Reasons:

- Where to Eat is built around dishes and prices (`items`, `price_checked`, the "cheap under $15" cap, Deals). This
  app is built around an ownership judgement and visits by people, neither of which Where to Eat has.
- Where to Eat is public by default (`PUBLIC_VIEW=1`); everything here is private.
- Cost accepted: a place can be in both apps. A one-time import of Where to Eat's `export.json` (section 9) covers
  existing entries; a place can keep a link to its Where to Eat id.

Reused from Where to Eat (copied and adapted, not shared at runtime): the OpenStreetMap lookups (Nominatim, Photon,
Overpass), `fold()` for accent-insensitive search, its Cloudflare Access JWT check (`access_identity`), the
Graphite Red stylesheet and bundled Archivo font, the small service worker for installing on Android, and the shape
of the CT 210 installer, backup and health-check hookup.

## 3. Goals and non-goals

### In v1

- Places with an ownership verdict and a standing, each with dated history (section 5).
- People, visits, per-person ratings and notes, occasions (section 6).
- The decider: modes, filters, shuffle (section 7).
- Discovery: a weekly Google scan of the eight towns into an inbox, OSM details and chain hints, a first-scan
  backlog, closure flags (section 8).
- Curated lists imported by paste or through the MCP, into the inbox (section 8.5).
- One-time import from Where to Eat's export (section 9).
- MCP: stdio and remote (claude.ai) with tiered writes (section 10).
- Installable PWA, CSV and JSON export, nightly backup, health check (section 12).

### Out of scope for v1

- Dishes, prices and menu reading (Where to Eat's job; a place can link to its Where to Eat entry).
- A public view of any kind.
- Kids signing in. Only the two editors sign in; others are people on a visit.
- An in-app Ask box (the MCP from claude.ai covers questions in plain language).
- Reservations, booking, or contacting restaurants.
- Photos.
- A map view (v2; "near me" sorting covers proximity in v1).

## 4. Area

A list of towns, not a radius. Setting `TOWNS`, default:

```
Concord|37.9780|-122.0311;Clayton|37.9410|-121.9358;Pleasant Hill|37.9480|-122.0608;Martinez|38.0194|-122.1341;
Walnut Creek|37.9101|-122.0652;Pacheco|37.9835|-122.0753;Pittsburg|38.0280|-121.8847;Lafayette|37.8858|-122.1180
```

(one line in the real setting; `name|lat|lng` per town). Each town also has a scan box: centre ± `TOWN_RADIUS_KM`
(default 4 km) in each direction, overridable per town as `name|lat|lng|radius_km`. Boxes may overlap; a place is
found once by its Google place id.

A place's town comes from its address's city when that names one of `TOWNS` (case- and accent-insensitive), else
the nearest town centre within 10 km of its pin, else the editor picks. A place outside every town can still be
added by hand with the town set by the editor; the scan never adds one.

## 5. Places, ownership and standing

### 5.1 Ownership verdict

The rule, in the owner's words: small, independently owned businesses count; chains and restaurant groups don't,
however many locations. A local family with three locations counts.

- `ownership`: `counts`, `unsure`, `no`. Set by an editor (or by the MCP at the `all` write level, on the user's
  instruction), never inferred. A new place's verdict is required on accepting it from the inbox (section 8.3);
  a place added by hand defaults to `unsure`.
- `ownership_reason`: required text, up to 200 characters, in the editor's words ("owner works the counter, one
  location", "on the taco trail list").
- `unsure` places stay in the decider and list, marked; `no` places leave them (section 5.3).

### 5.2 Standing

- `standing`: `open`, `temp_closed`, `closed`, with `standing_since` (a date).
- `temp_closed` and `closed` places leave the decider. `temp_closed` stays on the list, marked; `closed` goes to
  Past places.

### 5.3 History, never deletion

Every change of `ownership` or `standing` writes a row to `place_event` (kind, old value, new value, reason, date,
who). A place sold to a group: ownership goes to `no` with the reason, the old verdict and its date stay in the
history, the place and every visit stay.

**Past places** lists every place that is `closed` or `ownership = no`, newest change first, each with a last line
like "Closed Mar 2027 · 14 visits" or "Stopped counting Jun 2027 (sold to a group) · 6 visits". A past place can be
changed back.

Places are never hard-deleted from the UI or the MCP. An editor can delete a place only if it has no visits (for
mistakes); otherwise the delete is 409 "This place has visits. Mark it closed or doesn't count instead."

### 5.4 Place fields

name, town, address, lat, lng, cuisine (free text, folded for search; the filter offers the distinct values),
phone, website, hours (an OSM `opening_hours` string), want_to_try (bool), tags (section 6.4), notes (editor
notes, up to 2,000 chars), `google_place_id` (unique, nullable), `osm_ref` (e.g. `node/123`, nullable), `wte_id`
(Where to Eat place id, nullable), added_by, created_at, updated_at, `version` (optimistic concurrency, as in
gift-exchange: a stale save is 409 and saves nothing).

Duplicate guard: adding or accepting a place within 150 m of an existing place whose folded name matches
(token-subset either way) shows the existing one and needs an explicit "Add anyway".

## 6. People, visits, ratings, occasions

### 6.1 People

A short list: name, group (`adult` or `kid`), sort order, active, and an optional `email` that links a person to an
editor's sign-in (unique when set; must be in `ADMIN_EMAILS`). Set up in Settings. Names are unique (folded),
can't be `everyone`, `adults` or `kids`, contain a comma, or be all digits (Matinee's rule). A person with visits
can be deactivated, not deleted.

**Me.** The signed-in editor's person is "me": the visit form defaults "who went" to them, and the audience `me`
means that person. Through the MCP, the remote service forwards the Access-verified email of the claude.ai user to
the app in `X-Hometown-As` (accepted only with a valid bearer key from loopback, and only if the email is in
`ADMIN_EMAILS`), so `log_visit` can take `"me"` as a person and `suggest_places` can take `audience="me"`. With no
linked person, `me` is a 400 naming the fix ("Link your email to a person in Settings.").

### 6.2 Visits

A visit: place, date (defaults to today in America/Los_Angeles; can be any past date, never future), who went (one
or more active people), an optional occasion (up to 80 chars, e.g. "Riley's graduation"), an optional visit
note (up to 2,000 chars), added_by, `version`.

Per person on the visit: an optional rating, whole stars 1 to 5, and an optional note (up to 500 chars). A person
can be on a visit without a rating.

Logging a visit clears `want_to_try` on that place in the same save (Matinee's "watching clears Watch Soon").

### 6.3 Scores

- A person's rating of a place is their rating on their most recent rated visit there.
- A place's score for an audience (section 7.2) is the mean of those latest ratings over the people in the
  audience who have one; none rated means no score.
- "Everyone liked it" means every person in the audience who has a rating has a latest rating of 4 or 5, and at
  least one does.

### 6.4 Tags and occasions

Tags are a fixed, editable short list, seeded with: *good for groups*, *private room*, *takes reservations*,
*special occasion*, *kid-friendly*, *show off to guests*. Editors can add, rename, reorder and delete tags in
Settings (deleting removes it from places after a confirm that names the count). Labels are unique folded, up to
40 chars.

Each tag has an `occasion` flag. The three seeded tags *good for groups*, *private room* and *special occasion* have
it set; editors can set it on any tag. Occasion mode (section 7.1) uses the flag, never labels, so renaming or
deleting a tag can't break it silently.

## 7. The decider ("where tonight?")

The home screen is the list of eligible places with a decider bar on top.

**Eligible:** `ownership` in (`counts`, `unsure`) and `standing = open`.

### 7.1 Modes

| Mode | Picks | Order |
|---|---|---|
| `not_lately` (default) | eligible places with an audience score ≥ 4 whose most recent visit by anyone in the audience is more than `REST_DAYS` ago (default 45) | longest since last visit first, ties by score |
| `want` | `want_to_try` and never visited by anyone in the audience | oldest starred first |
| `never_been` | never visited by anyone in the audience, starred or not | newest added first |
| `new_in_town` | places created from a scan candidate first seen after the baseline (section 8.2), never visited by anyone | newest first seen first |
| `occasion` | places with any tag whose `occasion` flag is set, ignoring `REST_DAYS` | audience score, highest first; unrated last |
| `any` | every eligible place | name |

The response says which mode ran and how many matched. With `seed` set, the list is shuffled reproducibly
(Fisher–Yates with `random.Random(seed)`) after filtering; **Shuffle** in the UI sends a new seed. `limit` 1 to 50
(default 10) and `offset` page the result.

### 7.2 Filters

- `audience`: `everyone` (default), `adults`, `kids`, `me` (section 6.1), or one person's name. It changes who counts for scores,
  "visited", and "last visit" (Matinee's audiences).
- `cuisine` (folded substring), `town` (one of `TOWNS`), `tag` (repeatable; all must match), `list` (a source list
  id, section 8.5), `liked_by_all` (bool, section 6.3).
- `near=lat,lng`: adds `distance_km` to each result and, for `any` and `never_been`, sorts by distance. Places
  without a pin come last.
- `open_now` (bool): keeps places whose hours say they're open now (America/Los_Angeles). Places with unknown or
  unparseable hours are kept and marked "hours unknown" unless `open_now=strict`. Parsing covers the common
  `opening_hours` subset: day ranges and lists (`Mo-Fr`, `Sa,Su`), multiple time ranges, times past midnight
  (`17:00-01:00`), `off`, and `24/7`. Anything else (public holidays, months, week numbers) counts as unknown.

### 7.3 Card

Each result shows: name, town, cuisine, an `unsure` mark if so, the last visit ("3 months ago · Sam 5, Riley 4"),
the audience score, want-to-try star, tags, distance if `near`, and "open now" / "closes 9 pm" / "hours unknown".
Tapping opens the place.

One ranking function (`decide.py`) serves the page, `/api/decide`, and the MCP `suggest_places` tool.

## 8. Discovery: scan, inbox, lists

### 8.1 Sources and costs

- **Google Places API (New)** finds places and checks closures. Needs a Google Cloud project with billing and an
  API key, supplied by the owner (`GOOGLE_PLACES_KEY`). Without a key, scanning and list matching are off and
  everything else works.
- **OpenStreetMap** (Overpass, Nominatim, Photon) gives the details that are stored, and chain hints. No key.
  Credit "© OpenStreetMap contributors" wherever OSM data is shown.

Google terms (checked 2026-11-14): place ids may be stored indefinitely; latitude and longitude may be cached for up
to 30 days; other Places content must not be stored beyond that, and details should be fetched fresh when a user
interacts with a place. Showing Google data without a Google map needs the Google logo or, where space is tight,
the text "Google Maps". So:

- Stored place fields never come from Google. `google_place_id` is the only stored Google value.
- The candidate table caches Google name, address, lat, lng and business status with `google_fetched_at`; a daily
  job clears those columns once they are 30 days old. The inbox re-fetches them for the rows on screen.
- Inbox rows and the accept form label Google data "Google Maps".

Cost model (prices checked 2026-11-14; Google may change them):

- Scan searches use Text Search with the field mask `places.id,nextPageToken` only, which bills as "Text Search
  Essentials (IDs Only)", currently free without limit. Text Search returns at most 60 results across pages, and
  `locationRestriction` takes a rectangle.
- Details for an id use Place Details with `displayName,formattedAddress,location,businessStatus,primaryType`,
  which is the Pro tier (5,000 free calls a month). Each id gets details **once, at the scan that finds it**, so it
  can be matched to OSM. Expected use: the baseline (likely 1,500 to 2,500 ids across eight towns, spread over one
  or two months by the cap), then tens a month.
- One counter, `google_pro_calls` per calendar month (America/Los_Angeles) in the database, covers **every** Pro
  call: scan details, closure checks, inbox re-fetches and add-by-hand lookups. `GOOGLE_MAX_PRO_PER_MONTH` (default
  3,000) is a hard cap; past it, Pro calls wait for next month and the inbox and Settings say so. Text Search IDs
  Only calls are counted too, for the record, but not capped.
- What a candidate keeps after its Google cache is cleared: its id, `osm_ref`, the chain hint, and the OSM element's
  name, address and pin (OSM data may be stored). So the inbox shows OSM values for matched rows without any Google
  call. Only rows with no OSM match re-fetch Google details when shown on screen (20 per page, counted).

### 8.2 The weekly scan

`ops/hometown-scan` (installed in `/etc/periodic/weekly`) runs `python3 manage.py scan`:

1. For each town box, tile it into squares of `SCAN_TILE_KM` (default 1.5 km). For each tile and each type in
   `SCAN_TYPES` (default `restaurant`), run Text Search (`textQuery` "restaurant", `includedType` the type,
   `strictTypeFiltering` true, `locationRestriction` the tile), following `nextPageToken`. A tile that returns the
   60-result cap is split in four and searched again, down to a minimum of 0.25 km.
2. Each returned id not already a place or a candidate becomes a candidate with `origin = scan`. After details
   (step 4), a new id within 150 m of an existing place (or `where_to_eat` candidate) that has no
   `google_place_id` and whose folded name matches is attached to it instead of creating a candidate. This covers
   places added by hand or imported from Where to Eat, in either order. Moved ids
   (`movedPlaceId`) update the stored id on the place or candidate.
3. One Overpass query per town box per run fetches food places (`amenity` in restaurant, cafe, fast_food, food_court,
   ice_cream; `shop=bakery`). Candidates get the details fetched (step 4) and are matched to an OSM element within
   100 m whose folded name matches (token-subset either way). The match is stored as `osm_ref`. If the OSM element
   has `brand` or `brand:wikidata`, the candidate's status becomes `chain_hidden`.
4. Details for new ids are fetched within the monthly cap, then step 3's OSM match runs on them. Ids still
   waiting for details (cap reached) stay as bare candidates, hidden from the inbox until a later scan fills them.
   Steps 3 and 4 run in this order: details, then OSM match, then attach (step 2).
5. Closure check: for places with a `google_place_id` not checked in `CLOSURE_CHECK_DAYS` (default 28), fetch
   `businessStatus`. `CLOSED_PERMANENTLY` or `CLOSED_TEMPORARILY` sets a closure flag on the place (not its
   standing) shown on the place and in the inbox's Flags tab, with Confirm (sets standing, writes history) and
   Dismiss (clears the flag until the status changes again).
6. A `scan_run` row records start, finish, tiles, search calls, detail calls, new candidates and any error. The
   last run shows in Settings. A failed run alerts through the CT 210 health check (section 12).

**Baseline:** the first completed scan marks every candidate it creates `backlog`, not `new`, and records
`baseline_at`. Later scans create `new`. "New in town" (section 7.1) means first seen after `baseline_at`.

### 8.3 The inbox

Tabs: **New** (status `new`), **Backlog**, **Lists** (unmatched list lines), **Flags** (closure flags), and a
collapsed **Hidden as chains** section. Each row: name, address, town, primary type ("Google Maps" labelled),
the list(s) it came from, OSM match yes/no, chain hint.

Actions per row:

- **Counts / Not sure / Doesn't count** with a reason (prefilled with "on <list>" when it came from one): creates
  the place with that verdict (section 5) from the OSM match's name, address, pin, cuisine, phone, website and
  hours. Where OSM has nothing for a field, the form shows the live Google value beside it with **Use this**
  (the owner's decision, section 14; setting `GOOGLE_PREFILL`). A pin with no OSM match comes from geocoding the confirmed address (Nominatim, then
  Photon, as Where to Eat does). The candidate becomes `accepted` and links to the place.
- **Not a restaurant / not ours** (dismiss): `dismissed`, never shown again unless searched for.
- **Want to try** alongside a verdict sets the star.
- Rows in Hidden as chains can be un-hidden into New.

`doesn't count` from the inbox still creates a place (so the verdict and reason are kept, and the next scan doesn't
offer it again), straight into Past places.

### 8.4 Adding by hand

**Add place** takes a name (and optional town), searches OSM like Where to Eat's "look it up", and, with a key, a
Google Text Search (IDs only) plus details for the top three. Picking one fills the form as in 8.3. Saving needs a
verdict and reason.

### 8.5 Curated lists

A source list: name (up to 100 chars, unique folded), optional URL, created_at. Lists live only in the database,
never in the repo.

**Import** (`POST /api/lists/<id>/import` `{lines: [...]}`, up to 200 lines): for each non-empty line (trimmed,
bullets and numbering stripped):

1. If a place or candidate already has that folded name in the area, link the list to it.
2. Else, with a Google key, Text Search IDs only for the line within the bounding box of all towns; the top id
   becomes (or links to) a candidate with `origin = list`, status `new` (a `backlog` or `dismissed` candidate is
   moved back to `new`; a `chain_hidden` one stays hidden but shows the list).
3. Else the line is kept as an unmatched `list_entry`, shown under the inbox's Lists tab with **Search again**
   (edit the text) and **Drop**.

The response: `{matched_places, new_candidates, linked_candidates, unmatched}` counts plus the unmatched lines.
Everything from a list goes through the inbox; nothing skips the verdict.

A list's page shows its places and candidates. A place keeps its lists after accepting, and the decider's `list`
filter uses them ("Taco Trail places we haven't tried").

## 9. Import from Where to Eat

`python3 manage.py import-where-to-eat <export.json>`, run once on the host by the owner. For each place in the
export that is not `closed` and whose pin is within a town box (or whose `area` folds to a town name): create a
candidate with `origin = where_to_eat`, status `new`, carrying its name, address, cuisine, phone, website, hours,
lat and lng as the stored values (these are the owner's own data, not Google's), and `wte_id`. Places already
matched (same `wte_id`, or within 150 m with a matching folded name) are skipped. Prints counts. Idempotent.
Dishes, photos and notes are not imported.

## 10. MCP

Approach A (chosen): two services, as Matinee does.

- `hometown` is the Flask app (port `HOMETOWN_PORT`, default 8782).
- Claude Code and Claude Desktop use the same remote connector URL as claude.ai (OAuth through Access). Stdio
  mode is kept for development against a local dev server and for running on the host itself, where loopback keys
  work. There is no LAN or workstation key path.
- `hometown-mcp` is a separate service on `MCP_HTTP_PORT` (default 8783), path `/hometown/mcp`, in its own venv
  (`/opt/hometown-mcp/venv`, `requirements-mcp.txt`: `mcp`, `uvicorn`, `pyjwt[crypto]`). It serves Streamable HTTP
  behind Cloudflare Access managed OAuth, using `mcp_remote.py`, a verbatim hand copy of
  `SDNick484/mcp-server-shared` `src/mcp_shared/remote.py` with Matinee's two-line header pinning the upstream
  commit and sha256. `mcp_server.py` also runs over stdio (above).
- The MCP holds no database access. It calls the app's `/api/` over loopback with `Authorization: Bearer` and
  either `HOMETOWN_READ_KEY` (GET only; other methods 403) or `HOMETOWN_API_KEY` (read and write). Every write goes
  through the same validation as the phone.

Write level `HOMETOWN_MCP_WRITES` = `none` (default), `log`, `all` (Matinee's pattern). At `none` the service
refuses to start if `HOMETOWN_API_KEY` is in its environment.

| Tool | Level | What it does |
|---|---|---|
| `suggest_places` | read | the decider, same parameters as section 7 (no `near` from claude.ai unless given) |
| `search_places` | read | folded search over name, cuisine, town, notes; `include_past` |
| `get_place` | read | a place with history, tags, lists, and recent visits with ratings |
| `visit_history` | read | visits filtered by place, person, occasion text, date range |
| `list_inbox` | read | inbox rows by tab, with counts |
| `list_people`, `list_tags`, `list_lists` | read | reference data |
| `log_visit` | log | place, date, people with optional rating and note, occasion, note |
| `set_want_to_try` | log | star or unstar a place |
| `import_places` | all | `names`, `list_name`, optional `list_url`: creates the list if needed, then 8.5 |
| `sort_candidate` | all | accept with verdict and reason, or dismiss |
| `set_ownership`, `set_standing` | all | change with reason; writes history |

Tool results name places by name and id. People are named as the family entered them; the MCP never returns
editor emails.

## 11. Access and privacy

- The whole hostname sits behind one Cloudflare Access application (no public paths; `/healthz` is answered
  directly on loopback and from the LAN for the health check).
- The app verifies the `Cf-Access-Jwt-Assertion` JWT itself on every request except `/healthz` (signature from the
  team's certs, audience `CF_AUD`, issuer `https://<CF_TEAM_DOMAIN>`, expiry), as Where to Eat's
  `access_identity` does, then checks the email against `ADMIN_EMAILS` (required; empty refuses everyone).
- Bearer keys (`HOMETOWN_READ_KEY`, `HOMETOWN_API_KEY`) are accepted only from loopback (the MCP service), compared
  in constant time, and may hold several comma-separated values for rotation (Cookbook's pattern).
- `LAN_EDIT` (default `0`): when `1`, requests from RFC 1918 addresses without Cloudflare headers are treated as
  editor `lan`. For development and recovery only.
- Every mutation (non-GET under `/api/`) needs header `X-Hometown: 1`, a JSON object body, and an `Origin` equal
  to the request host's origin (bearer-key requests are exempt from Origin). Otherwise 400, or 403 for a foreign
  Origin.
- Response headers on everything: CSP `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
  data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`,
  `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on `/api/`.
- `<title>` and the manifest use `APP_NAME` (default `Hometown Table`).

## 12. Operations

- Alpine LXC with OpenRC, next to the other apps on CT 210 (owner's choice of container; the plan assumes CT 210).
  Runtime packages: `py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite`, plus `python3` and `py3-virtualenv`
  for the MCP venv. Gunicorn binds `127.0.0.1` and the LAN interface only as configured (`HOMETOWN_BIND`, default
  `0.0.0.0` so the tunnel on another host can reach it; same as Where to Eat).
- One-file installer `dist/setup-hometown.sh` built by `installer/build.sh` (head + base64 tarball), safe to re-run,
  `--uninstall` keeps data. `/opt/hometown` replaced each run; `/var/lib/hometown` and `/etc/conf.d/hometown` kept;
  `add_setting` only adds missing keys. The installer generates `HOMETOWN_READ_KEY` and `HOMETOWN_API_KEY` on
  first install and never prints them.
- Cron: `ops/hometown-backup` daily (consistent `sqlite3 .backup`, gzip, to `/var/backups/ct210/<date>/`, using
  `/etc/conf.d/ct210-ops`'s `KEEP_DAYS` and `BACKUP_TARGET`, as Where to Eat does); `ops/hometown-scan` weekly;
  `ops/hometown-tidy` daily (clears Google cache columns older than 30 days).
- The installer adds Hometown and Hometown MCP to `HEALTH_CHECKS` in `/etc/conf.d/ct210-ops` (as Where to Eat
  does). `/healthz` returns 503 when the last scan failed or no scan has completed in 9 days (with a key set).
- Exports: `GET /api/export.json` (everything) and `GET /api/export.csv` (one row per visit person).

## 13. Engineering constraints (from gift-exchange, with departures)

Kept: Python 3.12, Flask + gunicorn, SQLite (WAL), standard library otherwise; native ES modules in `static/js/`
with `static/js/package.json` = `{"type": "module"}`, no framework, bundler or npm step; `esc()` for everything
typed before `innerHTML` (gift-exchange's scan in `tests/test_frontend.py`); idempotent `migrate()` with
`SCHEMA_VERSION`; route-free logic in sibling modules taking a `sqlite3.Connection`; `json_body()` and
`text(data, key, max_len)`; Conventional Commits, author `SDNick484 <SDNick484@users.noreply.github.com>`; docs in
OpenRC and `vim`; no personal data in the repo, enforced by `tests/test_hygiene.py` with a git-ignored denylist.

Departures (approved by the owner 2026-11-14):

1. **Cloudflare Access instead of a shared password**, and the app verifies the Access JWT, so the runtime adds
   Alpine `py3-jwt` and `py3-cryptography`. Reason: claude.ai connectors need OAuth, which Access provides.
2. **A second service with a venv** (`hometown-mcp`: `mcp`, `uvicorn`, `pyjwt[crypto]`), as Matinee does.
3. **Outbound network calls** to Google Places and OSM, from the scan, list import, add-by-hand and inbox. Tests
   use recorded fixtures and never touch the network (Matinee's rule).
4. **Bearer keys for loopback only**, for the MCP service.

## 14. Calls I made (for the owner to check)

- `REST_DAYS` 45 for "not lately"; ratings 1–5 whole stars; "liked" means 4 or 5.
- A place's score uses each person's latest rating (one bad night doesn't sink a favourite).
- (Owner's decision, 2026-11-14.) In the accept form, a field OSM lacks can be filled from the live Google value
  with an explicit **Use this**. Google's terms say not to store Places content other than ids; the owner judged
  that a value an editor confirms with one tap is acceptable. Setting `GOOGLE_PREFILL` (default `1`); `0` hides
  the button and the editor types the value.
- Graphite Red look, reused from Where to Eat.
- Claude Code and Desktop reach the MCP through the remote connector, not stdio over the LAN (keeps keys loopback
  only).
- The scan fetches Google details once per new id (for OSM matching and chain hints) and keeps OSM values on
  candidates; one monthly counter caps every Pro call.
- A person can be linked to an editor's email, which makes "me" work in the app and from claude.ai.
- Occasion mode uses an `occasion` flag on tags, not tag names.
- Ports 8782 and 8783; the owner checks they are free on the host.
- Scan defaults: weekly, 1.5 km tiles, type `restaurant` only (cafés and bakeries can be added in `SCAN_TYPES`).

## 15. Left to the builder

Exact page layouts within the Graphite Red system; index choices; the OSM name-matching details beyond
"folded token subset within 100 m"; copy wording not given here; how tests record fixtures.
