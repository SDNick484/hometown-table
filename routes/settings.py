"""People, tags, exports, the Settings screen's status (spec §6.1, §6.4, §12) and its synced-visits section (Task 15).
The logic is in people.py, tags.py, export.py and visit_sync.py; this module only reads requests and writes answers.
Registering the tag seed here runs it at every start."""
import json

from flask import Blueprint, Response, jsonify, request

import access
import app
import common
import export
import people
import settings
import tags
import visit_sync
from access import editor_required
from common import json_body, text

bp = Blueprint("settings", __name__)
app.INIT_HOOKS.append(tags.seed)

PERSON_KEYS = ("name", "grp", "email", "sort_order", "active")


# ---------------------------------------------------------------- people
@bp.route("/api/people")
@editor_required
def people_list():
    inactive = common.int_arg(request.args.get("inactive"), 0, 1, 0)
    return jsonify(people=people.list(app.db(), include_inactive=bool(inactive)))


@bp.route("/api/people", methods=["POST"])
@editor_required
def people_create():
    data = json_body()
    con = app.db()
    new_id = people.create(con, text(data, "name", people.NAME_MAX, required=True), data.get("grp"), text(data, "email", 254))
    return jsonify(people.get(con, new_id)), 201


@bp.route("/api/people/<int:person_id>", methods=["PATCH"])
@editor_required
def people_update(person_id):
    data = json_body()
    fields = {k: data[k] for k in PERSON_KEYS if k in data}
    return jsonify(people.update(app.db(), person_id, **fields))


@bp.route("/api/people/<int:person_id>", methods=["DELETE"])
@editor_required
def people_delete(person_id):
    json_body()
    return jsonify(result=people.deactivate(app.db(), person_id))


# ---------------------------------------------------------------- tags
@bp.route("/api/tags")
@editor_required
def tags_list():
    return jsonify(tags=tags.list(app.db()))


@bp.route("/api/tags", methods=["POST"])
@editor_required
def tags_create():
    data = json_body()
    con = app.db()
    new_id = tags.create(con, text(data, "label", tags.LABEL_MAX, required=True), data.get("occasion", False))
    return jsonify(tags.get(con, new_id)), 201


@bp.route("/api/tags/<int:tag_id>", methods=["PATCH"])
@editor_required
def tags_update(tag_id):
    data = json_body()
    return jsonify(tags.update(app.db(), tag_id, label=text(data, "label", tags.LABEL_MAX), occasion=data.get("occasion")))


@bp.route("/api/tags/<int:tag_id>", methods=["DELETE"])
@editor_required
def tags_delete(tag_id):
    json_body()
    return jsonify(removed_from=tags.delete(app.db(), tag_id))


@bp.route("/api/tags/order", methods=["PUT"])
@editor_required
def tags_order():
    data = json_body()
    con = app.db()
    tags.reorder(con, data.get("ids"))
    return jsonify(tags=tags.list(con))


# ---------------------------------------------------------------- export and status
def _download(body, mimetype, extension):
    return Response(body, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="hometown-{common.today()}.{extension}"'})


@bp.route("/api/export.json")
@editor_required
def export_json():
    return _download(json.dumps(export.to_json(app.db()), indent=1, ensure_ascii=False), "application/json", "json")


@bp.route("/api/export.csv")
@editor_required
def export_csv():
    return _download(export.to_csv(app.db()), "text/csv", "csv")


@bp.route("/api/settings/status")
@editor_required
def status():
    con = app.db()
    scan = con.execute("SELECT * FROM scan_run ORDER BY id DESC LIMIT 1").fetchone()
    usage = con.execute("SELECT pro_calls, id_calls FROM google_usage WHERE month = ?", (common.month_key(),)).fetchone()
    return jsonify(
        towns=[t._asdict() for t in settings.TOWNS], rest_days=settings.REST_DAYS,
        google_key_set=bool(settings.GOOGLE_PLACES_KEY), google_prefill=settings.GOOGLE_PREFILL,
        last_scan=dict(scan) if scan else None,
        google_usage={"month": common.month_key(), "pro_calls": usage["pro_calls"] if usage else 0,
                      "id_calls": usage["id_calls"] if usage else 0, "max_pro_calls": settings.GOOGLE_MAX_PRO_PER_MONTH},
        me={"email": access.me_email(), "person_id": access.me_person_id(con)})


# ---------------------------------------------------------------- synced visits (Task 15)
@bp.route("/api/sync")
@editor_required
def sync_status():
    return jsonify(visit_sync.status(app.db()))


@bp.route("/api/sync/people")
@editor_required
def sync_people():
    return jsonify(people=visit_sync.people(app.db()))


@bp.route("/api/sync/people", methods=["PUT"])
@editor_required
def sync_people_set():
    data = json_body()
    return jsonify(people=visit_sync.set_people(app.db(), data.get("people")))


@bp.route("/api/sync/<source>/<ref>/dismiss", methods=["POST"])
@editor_required
def sync_dismiss(source, ref):
    json_body()
    if source not in visit_sync.SOURCES:
        raise common.ApiError(404, "No such synced visit.")
    visit_sync.dismiss(app.db(), source, ref)
    return jsonify(status="dismissed")
