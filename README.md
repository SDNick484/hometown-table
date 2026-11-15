# Hometown Table

A private, phone-first app that answers "where tonight?" from a family's own list of small, independently owned
restaurants in a handful of towns: places they like but haven't been to in a while, places on the want-to-try list,
and places that are new. It keeps every visit and what each person thought, finds new local places with a weekly
scan, and answers questions from claude.ai through an MCP. Flask and SQLite, no build step, behind Cloudflare Access.

Work in progress, built task by task: the design is in `docs/superpowers/specs/` and the build plan in
`docs/superpowers/plans/`.

## Development

```
python -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/*.test.mjs
ruff check
LAN_EDIT=1 python3 app.py     # http://127.0.0.1:8782/ (LAN_EDIT=1 lets your own machine in without Access)
```

Every setting is listed, with what it does, in `.env.example`. The UI smoke tests need
`pip install playwright && python -m playwright install chromium`; without them they skip.

## Places
Every place has an **ownership verdict** and a **standing**. The verdict is yours, never the app's: *Counts* (small and
independently owned, even a local family with three locations), *Not sure*, or *Doesn't count* (a chain or a
restaurant group). Standing is *Open*, *Temporarily closed* or *Closed*. Open a place to change either: you give a reason
and, if it happened earlier, the date, and the change is written to the place's history with who made it. A place sold
to a group keeps its visits; its history shows "Counted → Doesn't count" and the date.

**Past places** (the header link) lists every place that is closed or no longer counts, newest change first, each with a
line like "Closed Mar 2027 · 14 visits" or "Stopped counting Jun 2027 (sold to a group) · 6 visits". Open one to change it
back.

Places are never deleted once they have visits ("This place has visits. Mark it closed or doesn't count instead.").
A place made by mistake, with no visits, has a Delete button that asks first. Saving the edit form while someone else
has saved first stops with "Someone else just changed this place. Reload to see it." and changes nothing. Adding a place
close to one with the same name shows the existing one and needs an explicit "Add anyway".

## Settings
Open **Settings** from the header.

- **People.** Add each person who eats out with you, as an adult or a kid. Names must be different, can't be
  `everyone`, `adults` or `kids`, can't hold a comma and can't be only digits. An editor's email on a person links
  them to that sign-in, so the visit form starts with them ticked and "me" means them. The email has to be one of
  `ADMIN_EMAILS`. Someone who has been on a visit can't be deleted; Remove makes them inactive, and Reactivate
  brings them back.
- **Tags.** Six start out: good for groups, private room, special occasion, takes reservations, kid-friendly and show off
  to guests. Rename, reorder, add or delete them. A ticked tag is an occasion tag: the "Occasion" mode on Tonight picks
  places that have one. Deleting a tag says how many places it is on first.
- **Status.** The towns, rest days, whether the Google key is set (never the key itself), this month's Google calls
  and the last weekly scan.
- **Export.** `/api/export.json` holds everything; `/api/export.csv` has one row per person on each visit
  (place, town, ownership, date, occasion, person, rating, their note).

## Visits
Open a place and choose its visits. **Log a visit** with the date (today by default, never a future day), who went
(you are ticked already), a 1 to 5 star rating and a note for each person if you like, an occasion such as "Riley's
graduation", and a note for the whole visit. Someone can be on a visit without a rating. Logging a visit takes the
place off your want-to-try list. Each person's rating of a place is the one from their most recent rated visit, and
those are what "Tonight" scores with. Deleting a visit asks twice on the page.

"You" means the person whose email you linked in Settings. Without a link, ticking "me" is refused with a message
that says so. Asking Claude to "log that I went" works the same way.

## Where tonight
_Lands with Task 6._

## Discovery
Once a week the host searches Google Maps for restaurants in each town and sorts what it finds into the Inbox. It
needs `GOOGLE_PLACES_KEY` in `/etc/conf.d/hometown`; without one the scan is off and everything else works.

- Each town's box (its centre, plus or minus `TOWN_RADIUS_KM`) is searched in squares of `SCAN_TILE_KM` for every
  type in `SCAN_TYPES`. These searches are free. A square with more results than Google will list is searched again in
  quarters, down to `SCAN_MIN_TILE_KM`.
- A place you already have, or one you dismissed, is never offered again. A place found near one you added by hand or
  imported from Where to Eat, with a matching name, is joined to it instead.
- Each new find costs one Google lookup (its name, address and pin), then is matched to OpenStreetMap, whose details
  are what get kept. Brand-name chains go to "Hidden as chains". © OpenStreetMap contributors.
- The first scan fills the Backlog, not New: everything that was already in town. From then on, what a scan finds is
  New.
- Lookups stop at `GOOGLE_MAX_PRO_PER_MONTH` (3,000) a month, shared with the closure checks and the Inbox. The first
  scan may find more than that: the rest wait, out of sight, and the next month's scans fill them in.
- Every `CLOSURE_CHECK_DAYS` (28) each place is checked with Google. Closed or temporarily closed puts a flag on the
  place for you to confirm or dismiss; a dismissed flag stays away until Google says something different.

Settings shows the last scan. To run one now, as root (the cron job does the same):

```
/etc/periodic/weekly/hometown-scan; tail -n 1 /var/log/ct210-ops.log
```

A failed scan is reported by `/healthz` (and the CT 210 health check); the next run carries on where it stopped.

## Inbox
_Lands with Task 10._

## Lists
_Lands with Task 11._

## Import from Where to Eat
One time, to seed the inbox from the list you already keep in Where to Eat. In Where to Eat choose **More, Download,
JSON**, copy the file to the host, and run as the `hometown` user:

```
python3 /opt/hometown/manage.py import-where-to-eat export.json
```

Open places in the eight towns (by map pin, or by the town named in their area) land in the Inbox as new candidates.
Closed places, places elsewhere, and places already here (same Where to Eat id, or the same name within 150 m) are
skipped, and the command prints how many of each. Running it again creates nothing. Dishes, photos and notes stay in
Where to Eat.

## MCP
_Lands with Task 13._

## Install
Runs on an Alpine container with OpenRC, next to the other family apps. Build the one-file installer on your
machine, copy it over, and run it as root; it is safe to re-run and upgrades in place.

```
sh installer/build.sh                      # writes dist/setup-hometown.sh
pct push 210 dist/setup-hometown.sh /root/setup-hometown.sh
pct exec 210 -- sh /root/setup-hometown.sh
```

It installs the packages, creates the `hometown` user, puts the app in `/opt/hometown`, writes
`/etc/conf.d/hometown` (adding only settings that are missing; the two bearer keys are generated once and never
shown), builds the MCP venv in `/opt/hometown-mcp/venv`, starts the `hometown` service (port 8782) and, when the MCP
is configured, `hometown-mcp` (port 8783), installs the cron jobs, adds both `/healthz` URLs to `HEALTH_CHECKS` in
`/etc/conf.d/ct210-ops`, and takes a first backup. `sh setup-hometown.sh --uninstall` removes the app, services and
cron jobs and keeps `/var/lib/hometown`, the config and the backups.

After the first run, by hand:

1. Check that ports 8782 and 8783 are free on the container before you run it.
2. In Cloudflare Zero Trust, add a Tunnel public hostname for the app (to `http://<container>:8782`) and a route for the
   MCP path (`/hometown/mcp` to port 8783).
3. Create two Access applications: the app hostname, with a policy for the two editors, and the MCP path, with managed
   OAuth.
4. `vim /etc/conf.d/hometown` and set `CF_TEAM_DOMAIN`, `CF_AUD`, `ADMIN_EMAILS`, the MCP's `CF_ACCESS_TEAM_DOMAIN`,
   `CF_ACCESS_AUD` and `MCP_PUBLIC_HOSTS`, and `HOMETOWN_MCP_WRITES` (`none`, `log` or `all`). Then
   `rc-service hometown restart`, and re-run the installer once so it starts `hometown-mcp`.
5. Create a Google Cloud project with billing, enable Places API (New), make a key restricted to that API and set
   `GOOGLE_PLACES_KEY`. A budget alert at $1 is a good idea.
6. Add the connector in claude.ai, and in Settings link each editor's email to a person.

Logs: `/var/log/hometown.log`, `/var/log/hometown-mcp.log`, `/var/log/ct210-ops.log`. Backups: a gzipped copy of the
database each night in `/var/backups/ct210/<date>/hometown.db.gz`, kept `KEEP_DAYS` days. To restore, stop the service,
remove `hometown.db-wal` and `hometown.db-shm`, `gzip -dc hometown.db.gz > /var/lib/hometown/hometown.db`,
`chown hometown:hometown` it, and start the service.
