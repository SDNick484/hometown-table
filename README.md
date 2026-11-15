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
_Lands with Task 2._

## Settings
_Lands with Task 3._

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
_Lands with Task 9._

## Inbox
_Lands with Task 10._

## Lists
_Lands with Task 11._

## Import from Where to Eat
_Lands with Task 12._

## MCP
_Lands with Task 13._

## Install
_Lands with Task 14._
