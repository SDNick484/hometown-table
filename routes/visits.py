"""Visits and "me" (spec §6.2, §6.1): log, change, delete and list visits, and who the signed-in editor is."""
from flask import Blueprint, jsonify, request

import access
import common
import visits
from access import editor_required
from app import db
from common import ApiError

bp = Blueprint("visits", __name__)


@bp.post("/api/places/<int:place_id>/visits")
@editor_required
def log_visit(place_id):
    con = db()
    vid = visits.log(con, place_id, common.json_body(), access.who(), access.me_person_id(con))
    return jsonify(visit=visits.get(con, vid)), 201


@bp.patch("/api/visits/<int:visit_id>")
@editor_required
def change_visit(visit_id):
    data = common.json_body()
    if "version" not in data:
        raise ApiError(400, "version is required.")
    version = common.int_arg(data["version"], 1, 2 ** 31, 1)
    con = db()
    body = {k: v for k, v in data.items() if k != "version"}
    return jsonify(visit=visits.update(con, visit_id, body, version, access.who(), access.me_person_id(con)))


@bp.delete("/api/visits/<int:visit_id>")
@editor_required
def delete_visit(visit_id):
    visits.delete(db(), visit_id)
    return jsonify(ok=True)


@bp.get("/api/visits")
@editor_required
def list_visits():
    q, con = request.args, db()
    place_id = common.int_arg(q.get("place"), 1, 2 ** 62, 0) or None
    place = None
    if place_id:
        row = con.execute("SELECT id, name FROM place WHERE id = ?", (place_id,)).fetchone()
        if not row:
            raise ApiError(404, "No such place.")
        place = dict(row)
    found = visits.history(con, place_id=place_id, person=q.get("person"), occasion=q.get("occasion"),
                           since=q.get("since"), until=q.get("until"), limit=common.int_arg(q.get("limit"), 1, 500, 50))
    return jsonify(visits=found, place=place)


@bp.get("/api/visits/people")
@editor_required
def visit_people():
    """The active people the log-visit form offers, in Settings order."""
    rows = db().execute("SELECT id, name, grp FROM person WHERE active = 1 ORDER BY sort_order, id").fetchall()
    return jsonify(people=[dict(r) for r in rows])


@bp.get("/api/me")
@editor_required
def me():
    con = db()
    pid = access.me_person_id(con)
    person = None
    if pid is not None:
        row = con.execute("SELECT id, name FROM person WHERE id = ?", (pid,)).fetchone()
        person = {"id": row["id"], "name": row["name"]}
    return jsonify(email=access.me_email(), person=person)
