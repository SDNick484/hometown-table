"""The inbox, closure flags and the add-by-hand lookup (spec §8.3, §8.4). The logic is in inbox.py."""
from flask import Blueprint, jsonify, request

import access
import common
import google
import inbox
import osm
import places
import settings
from access import editor_required
from app import db
from common import ApiError, int_arg, json_body

bp = Blueprint("inbox", __name__)


@bp.route("/api/inbox", methods=["GET"])
@editor_required
def tab():
    offset = int_arg(request.args.get("offset"), 0, inbox.OFFSET_MAX, 0)
    limit = int_arg(request.args.get("limit"), 0, inbox.PAGE_MAX, inbox.PAGE)
    page = inbox.rows(db(), request.args.get("tab") or "new", offset, limit)
    return jsonify(**page, google=google.enabled(), prefill=settings.GOOGLE_PREFILL)


@bp.route("/api/inbox/<int:candidate_id>/accept", methods=["POST"])
@editor_required
def accept(candidate_id):
    place_id = inbox.accept(db(), candidate_id, json_body(), access.who())
    return jsonify(places.get(db(), place_id)), 201


@bp.route("/api/inbox/<int:candidate_id>/dismiss", methods=["POST"])
@editor_required
def dismiss(candidate_id):
    json_body()
    return jsonify(inbox.dismiss(db(), candidate_id))


@bp.route("/api/inbox/<int:candidate_id>/unhide", methods=["POST"])
@editor_required
def unhide(candidate_id):
    json_body()
    return jsonify(inbox.unhide(db(), candidate_id))


@bp.route("/api/places/<int:place_id>/flag/confirm", methods=["POST"])
@editor_required
def flag_confirm(place_id):
    json_body()
    return jsonify(inbox.confirm_flag(db(), place_id, access.who()))


@bp.route("/api/places/<int:place_id>/flag/dismiss", methods=["POST"])
@editor_required
def flag_dismiss(place_id):
    json_body()
    return jsonify(inbox.dismiss_flag(db(), place_id))


@bp.route("/api/lookup", methods=["GET"])
@editor_required
def lookup():
    found = inbox.lookup(db(), request.args.get("name"), request.args.get("town"))
    return jsonify(**found, google=google.enabled(), prefill=settings.GOOGLE_PREFILL)


@bp.route("/api/geocode", methods=["GET"])
@editor_required
def geocode():
    """The Add place form's pin for a typed address (one Nominatim call, then Photon), when nothing it picked had one."""
    address = common.text(request.args, "address", places.TEXT_FIELDS["address"], required=True)
    town = common.text(request.args, "town", places.TEXT_FIELDS["town"]) or common.town_for(address, None, None)
    try:
        found = osm.geocode(address, town)
    except common.UpstreamError:
        raise ApiError(502, "The map services didn't answer. Try again, or save it without a pin.") from None
    lat, lng = found if found else (None, None)
    return jsonify(lat=lat, lng=lng)
