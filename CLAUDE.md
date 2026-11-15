# Hometown Table

A private, phone-first Flask + SQLite app that answers "where tonight?" from the family's list of small,
independently owned restaurants in eight East Bay towns. Spec: `docs/superpowers/specs/2026-11-14-hometown-table-design.md`.
Plan: `docs/superpowers/plans/2026-11-14-hometown-table.md` (each task is one PR; the plan's task block is the contract
for names, signatures, routes and messages; where the plan and the spec disagree, the plan wins).

## Commands (the gate: run all of it before every commit)

```
python -m pytest -q
HOMETOWN_NO_SKIP=1 python -m pytest -q tests/ui tests/mcp   # needs Playwright + Chromium and requirements-mcp.txt
node --test tests/*.test.mjs
for f in static/js/*.js; do node --check "$f"; done
ruff check
shellcheck -s sh installer/*.sh ops/*                    # nothing to check until Tasks 7 and 14 add files
sh installer/build.sh && sh -n dist/setup-hometown.sh   # from Task 14 on
python3 app.py                                           # dev server on 127.0.0.1:8782 (LAN_EDIT=1 to sign in)
python3 manage.py --help                                 # host chores, one per commands/<name>.py
```

Tools: `pip install -r requirements-dev.txt`; Node 20 or newer; `shellcheck`. For `tests/ui/`:
`pip install playwright && python -m playwright install chromium`. For `tests/mcp/`: `pip install -r requirements-mcp.txt`.
Without them those tests skip locally; CI's `ui` and `mcp` jobs set `HOMETOWN_NO_SKIP=1`, which turns a skip into a
failure. `tests/test_hygiene.py` walks `git ls-files`, so `git add` new files before running it.

CI (`.github/workflows/ci.yml`) runs `pytest` (3.12 and 3.x), `alpine` (Alpine's `py3-*` packages), `lint`,
`shellcheck` (and the installer build), `ui` and `mcp`. Every step uses globs; a glob with no files yet is a no-op that
says so. No task needs to edit the workflow.

## Rules that must keep holding

- Python 3.12. Runtime dependencies are only Alpine packages `py3-flask py3-gunicorn py3-jwt py3-cryptography`, plus
  `tzdata` (ZoneInfo needs it; a bare Alpine has none) and `sqlite` (the backup's `sqlite3`). Everything else is the
  standard library. App modules never import `mcp`, `uvicorn` or anything from the MCP venv.
- No frontend framework, bundler or npm step: native ES modules in `static/js/`, `static/js/package.json` =
  `{"type": "module"}`.
- No network in any test. Google and OSM go through `common.http_json`, which tests replace with `fake_http`; the
  test setup fails any other socket connect outside loopback.
- No personal data in the repo: invented people (Alex, Sam, Jordan, Riley, Casey), invented restaurants
  ("Casa Ejemplo", "Pho Example", "Chain Burger Example"), `example.com` hosts, RFC 5737 addresses, coordinates inside
  the town boxes. Never commit real visits, ratings, lists, names, emails, addresses, the Google key, `CF_AUD`, the team
  domain, the bearer keys, `deploy/deploy.env`, `.env`, `data/` or `tests/hygiene_denylist.txt`.
- Every route except `/healthz` needs a verified editor (`access.py`); routes still carry `@editor_required`. Every
  mutation (non-GET under `/api/`) needs `X-Hometown: 1`, a JSON object body, and a same-host `Origin` (bearer-key
  requests are exempt from Origin): otherwise 400, or 403 for a foreign Origin. A read key on a non-GET is 403
  `{"error": "The read key can only read."}`.
- Bodies via `common.json_body()`, strings via `common.text(data, key, max_len)`; over-length text is 400, never cut.
  Errors are `raise ApiError(status, message, **extra)` -> `{"error": message, **extra}`.
- Everything typed goes through `esc()` before `innerHTML`; `tests/test_frontend.py` scans every module. Markup
  interpolations are one `esc(...)` or `render*(...)` call. No `confirm(`/`alert(`, no inline handlers, styles or
  scripts, no `document.title`.
- CSP `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`,
  `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer` on everything; `Cache-Control: no-store` on `/api/`.
  `<title>` and the manifest use `APP_NAME` (default `Hometown Table`).
- Schema changes only through idempotent `db.migrate()` with `SCHEMA_VERSION`. Task 1 created the whole v1 schema and
  every setting; no other task changes `db.py`, `settings.py` or `.env.example`. A task that finds it needs a schema
  or settings change stops and asks the owner.
- Timestamps are integer epoch seconds (`common.now()`); dates are `YYYY-MM-DD` in America/Los_Angeles
  (`common.today()`); months are `common.month_key()`. Call them through the module (`common.now()`), never
  `from common import now`, so the `clock` fixture reaches them. The same goes for `common.http_json`.
- Commits: Conventional Commits, one logical change each, author `SDNick484 <SDNick484@users.noreply.github.com>`.
  Docs use OpenRC commands and say `vim`.
- Ports: app 8782 (`HOMETOWN_PORT`), MCP 8783 (`MCP_HTTP_PORT`).

## Layout

```
app.py              Flask object, hooks (sign-in, mutation guard, security headers), error handlers, the shell
                    routes (/, /manifest.json, /sw.js, /robots.txt, /healthz), db(), INIT_HOOKS, init_db(),
                    register_routes() (every routes/ module's bp, sorted by name)
settings.py         every setting, read once at import (ENV_KEYS lists the environment keys); Town, parse_towns
db.py               V1 schema, SCHEMA_VERSION (PRAGMA user_version), connect(), transaction(), migrate()
common.py           ApiError, UpstreamError, json_body, text, int_arg, fold, names_match, km_between, now, today,
                    month_key, town_for, http_json (the only outbound HTTP)
access.py           Identity, identity(), require(), editor_required, who(), me_email(), me_person_id()
health.py           problems(con) for /healthz
manage.py           `python3 manage.py <command>`: runs commands/<name>.py
routes/             one module per feature with `bp`; ping.py (GET/POST /api/ping)
commands/           one module per host chore: NAME, HELP, add_arguments(p), run(args) -> int
static/             index.html (GET / fills __APP_NAME__), style.css (Graphite Red), fonts/ (Archivo + OFL),
                    manifest.json, icon.svg, sw.js (served at /sw.js), robots.txt
static/js/          core.js (esc, fmtDate, ago, stars, townLabel, todayLA, renderError, toast), api.js (get, send),
                    router.js (register, start), main.js (entry), screens.js (one import block per screen task)
tests/              conftest.py (fixtures and factories), test_<module>.py, test_frontend.py, test_hygiene.py,
                    test_slots.py (shared-file shape), frontend.test.mjs, plugins/ (per-task pytest plugins),
                    fixtures/<service>/ (hand-written JSON), ui/ (Playwright), mcp/ (Task 13)
.github/workflows/  ci.yml
deploy/             deploy.env.example (deploy.env is git-ignored)
```

## How to add to it (Task 1 conventions)

- **A route module**: `routes/<name>.py` with `bp = Blueprint("<name>", __name__)`; every view `@editor_required`;
  use `from app import db` for the request's connection (defined before routes are imported). Something to run at
  start: `import app; app.INIT_HOOKS.append(fn)` where `fn(con)` is idempotent and writes inside `db.transaction`
  (two gunicorn workers run it at once).
- **A command**: `commands/<name>.py` with `NAME`, `HELP`, `add_arguments(p)`, `run(args) -> int`. It opens the
  database itself: `con = db.connect(settings.DB_PATH); db.migrate(con)`.
- **A screen**: `static/js/<screen>.js` calls `register(pattern, {label, nav, render})` from `router.js` at import;
  add `import "./<screen>.js";` in your own block of `screens.js`. `nav` orders the header links: Tonight 10,
  Inbox 20, Lists 30, Past places 40, Settings 50; omit it for screens reached by links. `render(ctx)` gets
  `{view, params, query, path, alive, rerender}`; check `ctx.alive()` after every `await`.
- **Access in code**: `access.who()` for `added_by`/`by` (the email, `lan`, or `mcp`); `access.me_person_id(con)` for
  "me" (the Access email, or `X-Hometown-As` sent with a valid key from loopback and listed in `ADMIN_EMAILS`).
- **Tests**: fixtures `con`, `client` (anonymous), `editor` (alex@example.com, mutation headers set), `editor2`
  (sam@example.com), `keyed(kind, as_email=None)` (bearer key from 127.0.0.1, no `X-Hometown` header), `clock`
  (`set(local_iso)`, `set_utc(utc_iso)`, `advance(s)`), `jwt_for(email, **claims)`, `fake_http`
  (`on(substring_or_predicate, dict | "service/file.json" | exception | callable | [one per call])`, `.calls`; the
  newest matching rule answers; an unmatched call fails the test). Import factories from conftest:
  `from conftest import mk_place, mk_person, mk_tag, mk_visit, mk_candidate, mk_list, fixture_json`.
  `tests/conftest.py` is never edited after Task 1: a task's fixtures and helpers go in `tests/plugins/<task>.py`
  (loaded as a plugin; import helpers with `from plugins.<task> import ...`). Recorded responses go in
  `tests/fixtures/<service>/`, hand-written in the real response shapes.
- **tests/mcp/** is deliberately not a package: a `tests/mcp/__init__.py` would make `import mcp` find the test
  directory instead of the MCP library. Test file names there must not repeat a name used elsewhere under `tests/`.
  `pytest tests/mcp` with no test files in it exits 0 and says so (see `pytest_sessionfinish` in conftest).
- **Schema notes**: the plan's columns plus `place.want_since` (epoch seconds when want_to_try was last turned on:
  "want" mode is oldest starred first; set it with want_to_try, clear it when the star goes) and
  `place.closure_dismissed` (the Google status an editor dismissed; the scan doesn't flag that status again, and
  `OPERATIONAL` clears it). `meta.created_at` is when the database was made (health's grace period); `meta.baseline_at`
  is Task 9's. `place_event.by` and `visit.added_by` hold `access.who()`. Visits keep places: `visit.place_id` has no
  cascade, so a place with visits can't be deleted; a place's events, tags and list entries go with it.
- **Shared files**: `CLAUDE.md` (Status, Modules), `README.md` and `static/js/screens.js` hold one slot per task. Edit
  only the inside of your own slot (your Status item's `planned` -> `done`; your Modules heading's `Not built yet.`
  line; your README heading's `_Lands with Task N._` line; your screens.js block's `// (not built yet)` line). Never
  edit a heading or marker line, another slot, or the end of a file. `tests/test_slots.py` checks the shape.

## Status

- Task 1 (Scaffold, schema, access, gate and shared files): done

- Task 2 (Places, ownership, standing and Past places): done

- Task 3 (People, tags, export and Settings): done

- Task 4 (Visits and ratings): done

- Task 5 (Opening-hours parser): done

- Task 6 (The decider, "Tonight"): planned

- Task 7 (Google Places client and the monthly cap): done

- Task 8 (OpenStreetMap client): done

- Task 9 (The weekly scan): planned

- Task 10 (Inbox, flags and Add place): planned

- Task 11 (Curated lists): done

- Task 12 (Import from Where to Eat): done

- Task 13 (MCP server, stdio and remote): planned

- Task 14 (Installer, services, backups and health checks): done

## Modules

### Task 2: Places, ownership, standing and Past places
- `places.py` (route-free, takes a `Connection`): `create`, `get`, `update`, `set_ownership`, `set_standing`, `delete`,
  `search`, `past`, `set_tags`, `set_want_to_try`; the plan's signatures. `routes/places.py` is the thin layer.
- Ownership and standing change only through `set_ownership` / `set_standing` (a reason is required: 400 "Give a
  reason."; `on` is an optional past date, default today). Each real change writes a `place_event` and bumps `version`;
  `set_standing` also stamps `standing_since` and clears `closure_flag`. Setting the value a place already has writes no
  event (ownership still updates its reason). `update` refuses `ownership`, `standing` and `ownership_reason` (400).
- A place creation writes no `place_event`: history is changes. `past()` therefore dates a place that arrived closed or
  not counting by its own `standing_since` / `created_at` and `ownership_reason`.
- `get()` returns every column plus `tags`, `lists`, `history` (newest first: `{kind, old, new, reason, on, by}`),
  `last_visit` and `visit_count`. Lists are `{"places": [...]}`: `GET /api/places?q=&past=1&limit=` (folded over name,
  cuisine, town, address, notes, every word must match) and `GET /api/past` (adds `kind` "closed"|"ownership", `since`,
  `reason`, `visit_count`).
- Bodies: `PATCH /api/places/<id>` needs `version`; `POST .../ownership|standing` `{value, reason, on?}`;
  `PUT .../tags` `{tag_ids}`; `PUT .../want` `{on}` (`want_to_try` also accepted); `DELETE` answers 204 with no body, which
  `api.js` reports as an `ApiError` with status 204 (place.js treats that as success).
- Duplicate guard: 150 m and `common.names_match`, 409 `{duplicate_of}`, `add_anyway` overrides. A place's `google_place_id`
  collision is 409.
- Screens: `place.js` (`#/p/:id`, exports label maps and `eventTitle`), `place-edit.js` (`#/p/:id/edit`; tag boxes appear
  only if `GET /api/tags` answers), `past.js` (`#/past`, nav 40, exports `pastLine`).

### Task 3: People, tags, export and Settings
- `people.py` (`list`, `get`, `create`, `update`, `deactivate`): names are unique folded (inactive people included), at most
  40 characters, not `everyone`/`adults`/`kids` ("That name is reserved."), no comma, not all digits. `email` is lowercased,
  must be in `ADMIN_EMAILS` ("That email isn't an editor.") and unique (409). `deactivate` deletes a person with no visits
  and otherwise sets `active = 0`; it returns `"deleted"` or `"deactivated"`.
- `tags.py` (`SEED`, `seed`, `list`, `get`, `create`, `rename`, `update`, `reorder`, `delete`): labels unique folded, at most
  40 characters; `list` rows carry `places` (how many places have the tag); `delete` returns that count. `reorder(ids)`
  needs every tag id exactly once. The module defines `list`, so it never calls the builtin. `seed` runs from
  `routes/settings.py` via `app.INIT_HOOKS` and only when the table is empty.
- `export.py`: `to_json` (every table but `meta`, keyed by table name, under `tables`) and `to_csv` (one row per visit
  person; the `note` column is the person's own note; text cells starting `= + - @` get a leading quote).
- `routes/settings.py`: `/api/people`, `/api/tags` (`PUT /api/tags/order` takes `{"ids": [...]}`), `/api/export.json`,
  `/api/export.csv`, `GET /api/settings/status` (never any key text: `google_key_set` is a boolean).
- `static/js/settings.js`: `#/settings`, one screen redrawn from state; delete-tag and remove-person confirm inline.

### Task 4: Visits and ratings
`visits.py` (no routes): `log(con, place_id, data, by, me_person_id) -> id`, `update(con, id, data, version, by,
me_person_id=None)`, `delete`, `get`, `history(...)`, and the scores Task 6 uses: `latest_ratings`, `score`,
`liked_by_all`. Rules: `visited_on` defaults to `common.today()` and is never in the future; at least one active
person, matched by id or folded name, or `"me"` (the `me_person_id` the route passes; none is 400 "Link your email to
a person in Settings."); a rating is `None` or a whole 1 to 5; everything is checked before anything is written, and
logging clears `want_to_try` and `want_since` in the same transaction (it doesn't bump the place's `version`). A
person's rating is the one on their latest rated visit (same day: the later-logged visit wins). `routes/visits.py`:
visit CRUD, `GET /api/visits` (also returns `place`), `GET /api/me`, and `GET /api/visits/people` (the active people
the form offers). `static/js/visits.js` is `#/p/<id>/visits`.

### Task 5: Opening-hours parser
`hours.py`, standard library only. `open_at(spec, when) -> bool|None`, `closes_at(spec, when) -> str|None` ("9 pm",
"9:30 pm"; None unless open now and it closes within a week) and `parse(spec) -> [(weekday 0=Mo, start_min, end_min)]|None`.
`when` is a naive datetime in LA local time. Subset: day ranges/lists, several time ranges, past-midnight (split onto
the next day), `off`, `24/7`, `;`-separated rules where a later rule replaces earlier ones for its days. Anything else
(`PH`, months, week numbers, `sunrise`) makes the whole spec unknown (None), never a guess. `parse` returns `[]` for a
spec that is valid but never open.

### Task 6: The decider ("Tonight")
Not built yet.

### Task 7: Google Places client and the monthly cap
`google.py` (the only Google caller; everything goes through `common.http_json`, key in the `X-Goog-Api-Key` header,
never the URL). `enabled()` is "key set"; without a key `search_ids` returns `([], {})` and `details` returns None,
with no call, so callers check `enabled()` to tell "off" from "not found".
- `search_ids(con, *, text, rect=None, included_type=None) -> (ids, {old: new})`: Text Search under the IDs-only mask
  `places.id,places.movedPlaceId,nextPageToken`, at most 3 pages (Google returns 60 results at most). A moved id stays in
  `ids`; the move is in the dict. Counts `id_calls`; never capped.
- `details(con, place_id) -> {name, address, lat, lng, status, type} | None`: Pro tier. One `google_usage.pro_calls` counter
  per month (`common.month_key()`) covers every Pro call. The check and the increment are one `UPDATE ... WHERE pro_calls < cap`
  inside `db.transaction`, before the request, so two workers can't pass the cap together. Raises `CapReached` (no request
  made). A call that errors or answers 404 stays counted (Google may have billed it). None for 404.
- Callers must not hold a `db.transaction` open when they call `search_ids` or `details` (the counter opens its own).
- `usage(con) -> {month, pro_calls, id_calls, cap}`; `tidy(con, now=None) -> int` clears the `g_*` columns of candidates
  whose `g_fetched_at` is 30 days old or more (OSM columns stay). `python3 manage.py tidy` runs it; `ops/hometown-tidy` is
  the daily cron entry (`su` to `hometown`), which Task 14 installs only if the file exists.
- Test helpers: `tests/plugins/google.py` (`g_ids_page`, `g_details`); fixtures in `tests/fixtures/google/`.

### Task 8: OpenStreetMap client
`osm.py` (no key; credit "© OpenStreetMap contributors" wherever OSM data shows). A box is `(south, west, north, east)`.
A place dict is `{ref "node/123", name, address, lat, lng, cuisine, phone, website, opening_hours, is_chain}`;
`is_chain` is `brand` or `brand:wikidata` set.
- `food_places(bbox)`: one Overpass POST (`out center tags`); elements with no name or pin are dropped.
- `match(name, lat, lng, elements)`: nearest within 100 m whose name `common.names_match`es, else None.
- `search(name, bbox)`: Nominatim, `bounded=1` to the box, same shape (add-by-hand).
- `geocode(address, town)`: Nominatim, then Photon (also when Nominatim errors); None if neither knows it.
- A failed call raises `common.UpstreamError`, never an empty list; callers decide. All calls send
  `User-Agent: HometownTable/1 (+https://github.com/SDNick484/hometown-table)`.
- Tests: `overpass_el(ref, name, lat, lng, **tags)` in `tests/plugins/osm.py`; fixtures in `tests/fixtures/osm/`.

### Task 9: The weekly scan
Not built yet.

### Task 10: Inbox, flags and Add place
Not built yet.

### Task 11: Curated lists
`lists.py` (route-free; the module defines `all`, so it never calls the builtin) and `routes/lists.py`; the plan's
signatures. `clean_line` strips bullets and numbering and cuts at the first ` - `, ` – `, ` — ` or `: ` after a name.
- `import_lines` (up to 200 cleaned lines, 400 over; a line over 200 characters is 400): a line repeated in another
  case counts once. Google is called first for every line with no folded-name hit, with no transaction open
  (google.py's rule), then everything is written in one transaction. A line resolves to: a place or candidate whose
  name has the same `common.name_words` set (equal, not a subset: "Taco" must not match every taqueria) -> else
  `google.search_ids(text=line, rect=<bounding box of every town box>)`, first id (a moved id is followed) -> a place
  with that `google_place_id`, else a candidate with it, else a new candidate (`origin list`, status `new`, only the id:
  no Pro call is spent, so the inbox shows the pasted line until details arrive).
- A found candidate: `backlog` or `dismissed` goes back to `new` (`decided_at` cleared); `chain_hidden` stays hidden;
  `accepted` links its place; `new` and `pending` are left alone. A Google failure keeps that line and the rest
  unmatched (no more calls that import); no key, no results and a failure are all "unmatched".
- Counts: `matched_places` (a place, directly or through an accepted candidate), `new_candidates`, `linked_candidates`,
  `unmatched`, `unmatched_lines` (strings). A line already on the list is counted but not added twice.
- `get()` returns `{list, places, candidates, unmatched}`: a candidate entry that was later accepted shows under places
  (the entry reaches the place through `candidate.place_id`); `unmatched` is `[{id, line}]` (`id` is the entry id).
  `all()` rows carry `places`, `candidates`, `unmatched` counts. `retry(con, entry_id, text)` answers like an import (409
  if the entry is already matched); `drop` deletes the entry; `delete` removes the list and its entries only.
- Routes: `POST /api/lists` `{name, url, get_or_create}` (201, or 200 for an existing list with `get_or_create`);
  `DELETE` answers 204. Screens: `lists.js` (`#/lists` nav 30, `#/lists/:id`), exports `importSummary`.

### Task 12: Import from Where to Eat
`wte_import.run(con, export) -> {"created", "skipped_closed", "skipped_outside", "skipped_existing"}` (one transaction);
`commands/import_where_to_eat.py` (`import-where-to-eat <path>`) prints the counts. Per place, in order: `closed` ->
skipped_closed; pin not within a town's radius and `area` not a town name -> skipped_outside; same `wte_id` in
`place` or `candidate` (any status, so a dismissed one stays dismissed), or within 150 m of a place or candidate with
`common.names_match` -> skipped_existing; else a `where_to_eat` candidate, status `new`, `wte_id`, and `wte_json`
holding only name, address, cuisine, phone, website, hours, lat, lng. Dishes, photos, notes and private notes are never
read into the database. Fixture: `tests/fixtures/where_to_eat/export.json` (invented).

### Task 13: MCP server (stdio and remote)
Not built yet.

### Task 14: Installer, services, backups and health checks
`installer/build.sh` writes `dist/setup-hometown.sh` (git-ignored): `installer/setup-head.sh` plus a base64 tarball of
every top-level `*.py`, `routes/`, `commands/`, `static/`, `ops/`, `requirements-mcp.txt` and the two init scripts, so
a new module or ops job needs no edit. The head is plain `sh` (BusyBox): no bashisms, no GNU-only flags.
- `setup-head.sh`: safe to re-run; `--uninstall` removes `/opt/hometown`, `/opt/hometown-mcp`, the services and the
  cron files but keeps `/var/lib/hometown`, `/etc/conf.d/hometown` and the backups. `/opt/hometown` is replaced each
  run. `add_setting` only adds a missing key and writes `KEY="value"` (quoted: the init script sources the file). A new
  setting means a new `add_setting` line (`tests/test_installer_static.py` checks every `settings.ENV_KEYS` and
  `.env.example` key has one). The two bearer keys are generated once with `genkey` and never printed. The config is
  `0600 root`. `LAN_EDIT` is added as `0` (the app default), not the `.env.example` development `1`.
- Packages: `python3 py3-flask py3-gunicorn py3-jwt py3-cryptography sqlite tzdata py3-virtualenv`. The MCP venv
  (`/opt/hometown-mcp/venv`, from `requirements-mcp.txt`) failing only skips `hometown-mcp`.
- Cron files are installed only if present in the build (`[ -f ]`): `ops/hometown-backup` daily, `ops/hometown-tidy`
  daily (Task 7), `ops/hometown-scan` weekly (Task 9). They run as root from `/etc/periodic/`.
- `installer/init-hometown` (gunicorn, 2 workers, no `--preload`, `${HOMETOWN_BIND:-0.0.0.0}:${HOMETOWN_PORT:-8782}`)
  and `installer/init-hometown-mcp` are OpenRC runscripts under `supervise-daemon`, and `export` every key their
  service reads. The MCP script unsets `HOMETOWN_API_KEY` unless `HOMETOWN_MCP_WRITES` is `log` or `all`, because at
  `none` the service refuses to start when it can see the write key. A new setting the app reads goes in the `export`
  line of `init-hometown`.
- `ops/hometown-backup`: `sqlite3 .backup` + `integrity_check`, gzip, to `$LOCAL_DIR/<date>/hometown.db.gz` (mode 0600),
  `KEEP_DAYS` pruning, optional copy to `$BACKUP_TARGET/db/<date>/`, log and `ct210-notify` as the other CT 210 jobs.
  It puts the WAL files back to the database's owner if running as root created them.
- `HEALTH_CHECKS` in `/etc/conf.d/ct210-ops`: `add_health_check` (between the `add_health_check begin/end` markers;
  `tests/test_installer_static.py` runs it on sample files) appends inside the quoted value and keeps `.bak`. The app's
  line is added always; the MCP's only once the service answers, so an unconfigured MCP is not reported down.
