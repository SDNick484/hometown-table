"""Curated lists (spec §8.5): the lists, paste import, and the unmatched lines' Search again and Drop."""
from flask import Blueprint, jsonify

import lists
from access import editor_required
from app import db
from common import json_body

bp = Blueprint("lists", __name__)


@bp.route("/api/lists", methods=["GET"])
@editor_required
def index():
    return jsonify(lists=lists.all(db()))


@bp.route("/api/lists", methods=["POST"])
@editor_required
def create():
    data = json_body()
    name, url = data.get("name"), data.get("url")
    if data.get("get_or_create") is True:
        existing = lists._find(db(), name.strip() if isinstance(name, str) else "")
        list_id, status = (existing["id"], 200) if existing else (lists.create(db(), name, url), 201)
    else:
        list_id, status = lists.create(db(), name, url), 201
    return jsonify(lists.get(db(), list_id)["list"]), status


@bp.route("/api/lists/<int:list_id>", methods=["GET"])
@editor_required
def read(list_id):
    return jsonify(lists.get(db(), list_id))


@bp.route("/api/lists/<int:list_id>", methods=["DELETE"])
@editor_required
def remove(list_id):
    json_body()
    lists.delete(db(), list_id)
    return "", 204


@bp.route("/api/lists/<int:list_id>/import", methods=["POST"])
@editor_required
def import_(list_id):
    data = json_body()
    return jsonify(lists.import_lines(db(), list_id, data.get("lines")))


@bp.route("/api/lists/entries/<int:entry_id>/retry", methods=["POST"])
@editor_required
def retry(entry_id):
    data = json_body()
    return jsonify(lists.retry(db(), entry_id, data.get("text")))


@bp.route("/api/lists/entries/<int:entry_id>", methods=["DELETE"])
@editor_required
def drop(entry_id):
    json_body()
    lists.drop(db(), entry_id)
    return "", 204
