"""Places: create, read, edit, delete, ownership, standing, tags, want-to-try, search and Past places (spec §5)."""
from flask import Blueprint, jsonify, request

import access
import places
from access import editor_required
from app import db
from common import ApiError, int_arg, json_body

bp = Blueprint("places", __name__)


@bp.route("/api/places", methods=["POST"])
@editor_required
def create():
    data = json_body()
    place_id = places.create(db(), data, access.who())
    return jsonify(places.get(db(), place_id)), 201


@bp.route("/api/places", methods=["GET"])
@editor_required
def search():
    past = request.args.get("past", "").lower() in ("1", "true", "yes")
    limit = int_arg(request.args.get("limit"), 1, places.SEARCH_LIMIT_MAX, 20)
    return jsonify(places=places.search(db(), request.args.get("q", ""), include_past=past, limit=limit))


@bp.route("/api/past", methods=["GET"])
@editor_required
def past():
    return jsonify(places=places.past(db()))


@bp.route("/api/places/<int:place_id>", methods=["GET"])
@editor_required
def read(place_id):
    return jsonify(places.get(db(), place_id))


@bp.route("/api/places/<int:place_id>", methods=["PATCH"])
@editor_required
def edit(place_id):
    data = json_body()
    version = data.pop("version", None)
    return jsonify(places.update(db(), place_id, data, version, access.who()))


@bp.route("/api/places/<int:place_id>", methods=["DELETE"])
@editor_required
def remove(place_id):
    json_body()
    places.delete(db(), place_id)
    return "", 204


@bp.route("/api/places/<int:place_id>/ownership", methods=["POST"])
@editor_required
def ownership(place_id):
    data = json_body()
    return jsonify(places.set_ownership(db(), place_id, data.get("value"), data.get("reason"), access.who(), data.get("on")))


@bp.route("/api/places/<int:place_id>/standing", methods=["POST"])
@editor_required
def standing(place_id):
    data = json_body()
    return jsonify(places.set_standing(db(), place_id, data.get("value"), data.get("reason"), access.who(), data.get("on")))


@bp.route("/api/places/<int:place_id>/tags", methods=["PUT"])
@editor_required
def tags(place_id):
    data = json_body()
    if "tag_ids" not in data:
        raise ApiError(400, "tag_ids is required.")
    return jsonify(places.set_tags(db(), place_id, data["tag_ids"]))


@bp.route("/api/places/<int:place_id>/want", methods=["PUT"])
@editor_required
def want(place_id):
    data = json_body()
    on = data.get("on", data.get("want_to_try"))
    if not isinstance(on, bool):
        raise ApiError(400, "Say whether to star it: on true or false.")
    return jsonify(places.set_want_to_try(db(), place_id, on))
