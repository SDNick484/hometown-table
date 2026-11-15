"""The MCP tools over the app's API (plan Task 13; spec §10). Every call goes through the real Flask app with the
service's bearer key (plugins/mcp_api.py), so the app's sign-in, mutation guard and validation are the ones answering."""
import json
import socket

import pytest

pytest.importorskip("mcp.server.mcpserver")

import mcp_server
import settings
from conftest import EDITOR, EDITOR2, READ_KEY, WRITE_KEY, mk_candidate, mk_list, mk_person, mk_place, mk_tag, mk_visit
from mcp_server import Api, build_server
from plugins.google import g_ids_page
from plugins.mcp_api import call, call_error, flask_api, run_async, tool_names

READ_TOOLS = {"suggest_places", "search_places", "get_place", "visit_history", "list_inbox", "list_people", "list_tags",
              "list_lists"}
LOG_TOOLS = {"log_visit", "set_want_to_try"}
ALL_TOOLS = {"import_places", "sort_candidate", "set_ownership", "set_standing"}


def _visits(con):
    return con.execute("SELECT COUNT(*) FROM visit").fetchone()[0]


# ---------------------------------------------------------------- write levels
def test_none_level_has_no_write_tools(api):
    assert "log_visit" not in tool_names(build_server(api, "none"))
    assert tool_names(build_server(api, "none")) == READ_TOOLS
    assert tool_names(build_server(api, "log")) == READ_TOOLS | LOG_TOOLS
    assert tool_names(build_server(api, "all")) == READ_TOOLS | LOG_TOOLS | ALL_TOOLS


def test_an_unknown_level_is_refused(api):
    with pytest.raises(ValueError):
        build_server(api, "everything")


def test_read_tools_are_marked_read_only(api):
    tools = {t.name: t for t in run_async(build_server(api, "all").list_tools)}
    assert all(tools[n].annotations.read_only_hint for n in READ_TOOLS)
    assert not any(tools[n].annotations.read_only_hint for n in LOG_TOOLS | ALL_TOOLS)


def test_the_read_key_cannot_write_even_when_a_write_tool_is_registered(api, con):
    pid = mk_place(con)
    assert call_error(build_server(api, "all"), "set_want_to_try", place_id=pid, on=True) == "The read key can only read."
    assert con.execute("SELECT want_to_try FROM place WHERE id = ?", (pid,)).fetchone()[0] == 0


# ---------------------------------------------------------------- "me" (Review Focus 4)
def test_log_level_logs_a_visit_for_me(api_as_alex, con, clock):
    clock.set("2026-11-14T23:30")
    alex = mk_person(con, "Alex", email=EDITOR)
    mk_person(con, "Sam", email=EDITOR2)
    pid = mk_place(con, want_to_try=1, want_since=1)
    out = call(build_server(api_as_alex, "log"), "log_visit", place="casa ejemplo",
               people=[{"person": "me", "rating": 5, "note": "The mole"}], occasion="Birthday")
    visit = out["visit"]
    assert (visit["place_id"], visit["place_name"], visit["visited_on"]) == (pid, "Casa Ejemplo", "2026-11-14")
    assert [(p["name"], p["rating"], p["note"]) for p in visit["people"]] == [("Alex", 5, "The mole")]
    assert "added_by" not in visit
    row = con.execute("SELECT person_id, rating FROM visit_person").fetchone()
    assert tuple(row) == (alex, 5)
    assert con.execute("SELECT want_to_try FROM place WHERE id = ?", (pid,)).fetchone()[0] == 0
    sent = api_as_alex.sent[-1]
    assert (sent["method"], sent["path"]) == ("POST", f"/api/places/{pid}/visits")
    assert sent["headers"]["X-Hometown-As"] == EDITOR
    assert sent["headers"]["X-Hometown"] == "1"
    assert sent["headers"]["Authorization"] == f"Bearer {WRITE_KEY}"


def test_me_without_a_linked_person_is_refused_and_saves_nothing(api_as_alex, con):
    pid = mk_place(con)
    mk_person(con, "Alex")                                   # not linked to alex@example.com yet
    server = build_server(api_as_alex, "log")
    assert call_error(server, "log_visit", place_id=pid, people=["me"]) == "Link your email to a person in Settings."
    assert _visits(con) == 0


def test_an_unlisted_acting_as_email_does_not_make_me(con):
    pid = mk_place(con)
    mk_person(con, "Alex", email=EDITOR)
    forged = flask_api(WRITE_KEY, as_email="mallory@example.com")
    server = build_server(forged, "log")
    assert call_error(server, "log_visit", place_id=pid, people=["me"]) == "Link your email to a person in Settings."
    assert _visits(con) == 0


def test_without_anyone_to_act_for_no_x_hometown_as_is_sent(api_all, con):
    pid = mk_place(con)
    call(build_server(api_all, "log"), "set_want_to_try", place_id=pid, on=True)
    assert all("X-Hometown-As" not in s["headers"] for s in api_all.sent)


# ---------------------------------------------------------------- places by id or name
def test_ambiguous_place_name_is_refused_with_choices(api, con):
    a = mk_place(con, "Casa Ejemplo", town="Concord")
    b = mk_place(con, "Casa Ejemplo", town="Walnut Creek")
    pho = mk_place(con, "Pho Example", town="Clayton")
    pho2 = mk_place(con, "Pho Example Express", town="Martinez")
    server = build_server(api, "none")
    msg = call_error(server, "get_place", place="Casa Ejemplo")
    assert msg.startswith('More than one place matches "Casa Ejemplo"')
    assert f"Casa Ejemplo (Concord, id {a})" in msg and f"Casa Ejemplo (Walnut Creek, id {b})" in msg
    assert "place_id" in msg
    msg = call_error(server, "get_place", place="pho")
    assert f"id {pho})" in msg and f"id {pho2})" in msg
    assert call(server, "get_place", place="pho example")["id"] == pho         # the exact name beats a longer one
    assert call(server, "get_place", place="EXPRESS")["id"] == pho2            # one match is enough
    assert call(server, "get_place", place_id=b)["town"] == "Walnut Creek"
    assert call_error(server, "get_place", place="Taqueria Ejemplo") == \
        'No place matches "Taqueria Ejemplo". Try search_places.'
    assert call_error(server, "get_place") == "Say which place: place_id, or place with its name."


def test_an_ambiguous_name_writes_nothing(api_all, con):
    mk_place(con, "Casa Ejemplo", town="Concord")
    mk_place(con, "Casa Ejemplo", town="Walnut Creek")
    msg = call_error(build_server(api_all, "all"), "set_standing", place="Casa Ejemplo", value="closed", reason="Gone")
    assert msg.startswith("More than one place matches")
    assert {s["method"] for s in api_all.sent} == {"GET"}
    assert con.execute("SELECT COUNT(*) FROM place_event").fetchone()[0] == 0


def test_a_closed_place_is_still_found_by_name(api_all, con):
    pid = mk_place(con, standing="closed")
    out = call(build_server(api_all, "all"), "set_standing", place="Casa Ejemplo", value="open", reason="Reopened",
               on="2026-10-01")
    assert (out["id"], out["standing"], out["standing_since"]) == (pid, "open", "2026-10-01")


# ---------------------------------------------------------------- the tools
def test_import_places_creates_list_then_imports(api_all, con, fake_http, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "test-key-not-real")
    fake_http.on("searchText", [g_ids_page(["g1"]), g_ids_page(["g2"])])
    pho = mk_place(con, "Pho Example")
    server = build_server(api_all, "all")
    out = call(server, "import_places", names=["1. Casa Ejemplo – great mole", "Pho Example", "", "Taqueria Ejemplo"],
               list_name="Example Taco Trail", list_url="https://example.com/trail")
    lid = out["list"]["id"]
    assert (out["list"]["name"], out["list"]["url"]) == ("Example Taco Trail", "https://example.com/trail")
    assert (out["matched_places"], out["new_candidates"], out["unmatched"]) == (1, 2, 0)
    writes = [(s["method"], s["path"], json.loads(s["data"])) for s in api_all.sent if s["method"] != "GET"]
    assert writes[0] == ("POST", "/api/lists", {"name": "Example Taco Trail", "url": "https://example.com/trail",
                                                "get_or_create": True})
    assert writes[1][:2] == ("POST", f"/api/lists/{lid}/import")
    entries = con.execute("SELECT line, place_id FROM list_entry WHERE list_id = ? ORDER BY id", (lid,)).fetchall()
    assert [tuple(e) for e in entries] == [("Casa Ejemplo", None), ("Pho Example", pho), ("Taqueria Ejemplo", None)]
    again = call(server, "import_places", names=["Pho Example"], list_name="example taco trail")
    assert again["list"]["id"] == lid
    assert con.execute("SELECT COUNT(*) FROM source_list").fetchone()[0] == 1


def test_tool_results_never_contain_editor_emails(api, editor, con, clock):
    clock.set("2026-11-14T18:00")
    alex = mk_person(con, "Alex", email=EDITOR)
    sam = mk_person(con, "Sam", email=EDITOR2)
    pid = mk_place(con, cuisine="Mexican", closure_flag="CLOSED_TEMPORARILY")
    mk_visit(con, pid, "2026-09-01", {alex: 5, sam: 4})
    tag = mk_tag(con, "Patio")
    assert editor.put(f"/api/places/{pid}/tags", json={"tag_ids": [tag]}).status_code == 200
    assert editor.post(f"/api/places/{pid}/ownership", json={"value": "unsure", "reason": "New owners"}).status_code == 200
    mk_list(con, "Example Taco Trail")
    mk_candidate(con, osm_name="Pho Example", osm_ref="node/1")
    server = build_server(api, "none")
    results = [call(server, "get_place", place_id=pid), call(server, "search_places", query="casa"),
               call(server, "visit_history", person="Alex"), call(server, "list_people"),
               call(server, "suggest_places", mode="any"), call(server, "list_inbox", tab="flags"),
               call(server, "list_inbox"), call(server, "list_tags"), call(server, "list_lists")]
    writer = build_server(flask_api(WRITE_KEY, as_email=EDITOR), "all")
    results += [call(writer, "log_visit", place_id=pid, people=["me"]),
                call(writer, "set_ownership", place_id=pid, value="counts", reason="Checked again")]
    blob = json.dumps(results)
    assert EDITOR not in blob and EDITOR2 not in blob and "@" not in blob
    place = results[0]
    assert [h["new"] for h in place["history"]] == ["unsure"] and "by" not in place["history"][0]
    assert results[2]["visits"][0]["people"][0]["name"] == "Alex"
    assert [(p["name"], p["linked"]) for p in results[3]["people"]] == [("Alex", True), ("Sam", True)]


def test_requests_carry_the_key_and_mark_only_writes(api_all, con):
    pid = mk_place(con)
    server = build_server(api_all, "log")
    call(server, "get_place", place_id=pid)
    call(server, "set_want_to_try", place_id=pid, on=True)
    read, *_, write = api_all.sent
    assert read["method"] == "GET" and "X-Hometown" not in read["headers"]
    assert read["headers"]["Authorization"] == f"Bearer {WRITE_KEY}"
    assert (write["method"], write["path"], json.loads(write["data"])) == ("PUT", f"/api/places/{pid}/want", {"on": True})
    assert write["headers"]["X-Hometown"] == "1" and write["headers"]["Content-Type"] == "application/json"
    assert con.execute("SELECT want_to_try FROM place WHERE id = ?", (pid,)).fetchone()[0] == 1


def test_app_errors_become_tool_errors_with_the_app_message(api_as_alex, con, clock):
    clock.set("2026-11-14T12:00")
    pid = mk_place(con)
    mk_person(con, "Alex", email=EDITOR)
    server = build_server(api_as_alex, "all")
    assert call_error(server, "log_visit", place_id=pid, people=["Alex"], visited_on="2026-12-01") == \
        "That date is in the future."
    assert call_error(server, "log_visit", place_id=pid, people=["Jordan"]) == "No one called Jordan."
    assert call_error(server, "set_ownership", place_id=pid, value="no", reason="") == "Give a reason."
    assert call_error(server, "get_place", place_id=pid + 1) == "Place not found."
    assert _visits(con) == 0


def test_a_duplicate_names_the_place_it_duplicates(api_all, con):
    pid = mk_place(con, "Casa Ejemplo", lat=37.978, lng=-122.031)
    cid = mk_candidate(con, osm_name="Casa Ejemplo", osm_ref="node/7", osm_lat=37.978, osm_lng=-122.031)
    msg = call_error(build_server(api_all, "all"), "sort_candidate", candidate_id=cid, decision="accept",
                     ownership="counts", reason="Family owned")
    assert f"duplicate_of: {pid}" in msg


def test_suggest_places_passes_the_decider_parameters(api_as_alex, con):
    mk_person(con, "Alex", email=EDITOR)
    mk_place(con, "Casa Ejemplo", town="Concord", cuisine="Mexican")
    mk_place(con, "Pho Example", town="Walnut Creek", cuisine="Vietnamese")
    server = build_server(api_as_alex, "none")
    out = call(server, "suggest_places", mode="never_been", audience="me", town="walnut creek", seed=7)
    assert [p["name"] for p in out["places"]] == ["Pho Example"]
    path = api_as_alex.sent[-1]["path"]
    assert path.startswith("/api/decide?") and "audience=me" in path and "seed=7" in path
    assert call(server, "suggest_places", tag=["good for groups", "private room"])["places"] == []
    assert "tag=good+for+groups&tag=private+room" in api_as_alex.sent[-1]["path"]
    assert call_error(build_server(flask_api(READ_KEY), "none"), "suggest_places", audience="me") == \
        "Link your email to a person in Settings."


def test_get_place_has_recent_visits_and_visit_history_filters(api, con):
    alex, sam = mk_person(con, "Alex"), mk_person(con, "Sam")
    pid = mk_place(con)
    other = mk_place(con, "Pho Example")
    for day in ("2026-01-05", "2026-02-05", "2026-03-05"):
        mk_visit(con, pid, day, {alex: 4})
    mk_visit(con, other, "2026-04-01", {sam: 3}, occasion="Birthday dinner")
    server = build_server(api, "none")
    place = call(server, "get_place", place="Casa Ejemplo", visits=2)
    assert [v["visited_on"] for v in place["recent_visits"]] == ["2026-03-05", "2026-02-05"]
    assert place["visit_count"] == 3
    found = call(server, "visit_history", occasion="birthday")["visits"]
    assert [(v["place_name"], v["visited_on"]) for v in found] == [("Pho Example", "2026-04-01")]
    found = call(server, "visit_history", place="casa ejemplo", since="2026-02-01", until="2026-02-28")["visits"]
    assert [v["visited_on"] for v in found] == ["2026-02-05"]
    assert call_error(server, "visit_history", person="Jordan") == "No one called Jordan."


def test_sort_candidate_accepts_or_dismisses(api_all, con):
    keep = mk_candidate(con, osm_name="Casa Ejemplo", osm_ref="node/1", osm_lat=37.97, osm_lng=-122.03,
                        osm_address="1 Example St, Concord, CA")
    drop = mk_candidate(con, osm_name="Chain Burger Example", osm_ref="node/2")
    tag = mk_tag(con, "Patio")
    server = build_server(api_all, "all")
    place = call(server, "sort_candidate", candidate_id=keep, decision="accept", ownership="counts",
                 reason="Family owned since 1990", tags=[tag], want_to_try=True)["place"]
    assert (place["name"], place["ownership"], place["ownership_reason"]) == ("Casa Ejemplo", "counts",
                                                                            "Family owned since 1990")
    assert [t["label"] for t in place["tags"]] == ["Patio"] and place["want_to_try"] == 1
    assert call(server, "sort_candidate", candidate_id=drop, decision="dismiss") == {"id": drop, "status": "dismissed"}
    statuses = dict(con.execute("SELECT id, status FROM candidate").fetchall())
    assert statuses == {keep: "accepted", drop: "dismissed"}
    assert call_error(server, "sort_candidate", candidate_id=keep, decision="accept", ownership="counts",
                      reason="Again") == "That one is already on the list. (place_id: %d)" % place["id"]
    assert "decision" in call_error(server, "sort_candidate", candidate_id=drop, decision="maybe")


def test_set_ownership_writes_history_without_an_email(api_all, con):
    pid = mk_place(con)
    out = call(build_server(api_all, "all"), "set_ownership", place="Casa Ejemplo", value="no",
               reason="Sold to a restaurant group", on="2026-10-01")
    assert (out["id"], out["ownership"]) == (pid, "no")
    assert out["history"] == [{"kind": "ownership", "old": "counts", "new": "no", "reason": "Sold to a restaurant group",
                               "on": "2026-10-01"}]
    assert con.execute("SELECT by FROM place_event").fetchone()[0] == "mcp"


def test_reference_lists(api, con):
    mk_person(con, "Alex", email=EDITOR)
    gone = mk_person(con, "Casey")
    con.execute("UPDATE person SET active = 0 WHERE id = ?", (gone,))
    con.commit()
    mk_list(con, "Example Taco Trail")
    server = build_server(api, "none")
    assert [p["name"] for p in call(server, "list_people")["people"]] == ["Alex"]
    assert [p["name"] for p in call(server, "list_people", include_inactive=True)["people"]] == ["Alex", "Casey"]
    assert "good for groups" in [t["label"] for t in call(server, "list_tags")["tags"]]
    assert [x["name"] for x in call(server, "list_lists")["lists"]] == ["Example Taco Trail"]
    page = call(server, "list_inbox", tab="new", limit=5)
    assert page["tab"] == "new" and set(page["counts"]) == {"new", "backlog", "flags", "chain_hidden"}


# ---------------------------------------------------------------- talking to the app
def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_an_unreachable_app_is_a_plain_message():
    server = build_server(Api(f"http://127.0.0.1:{_closed_port()}", "secret-key-123"), "none")
    msg = call_error(server, "get_place", place_id=1)
    assert msg == "Hometown Table is unreachable right now. Try again in a minute."


def test_a_refused_key_says_which_setting_without_the_key(con):
    pid = mk_place(con)
    msg = call_error(build_server(flask_api("not-the-key"), "none"), "get_place", place_id=pid)
    assert msg == "Hometown Table refused this service's key (HTTP 401). Check HOMETOWN_READ_KEY and HOMETOWN_API_KEY."
    assert "not-the-key" not in msg


def test_acting_as_makes_a_copy(api):
    other = api.acting_as(EDITOR)
    assert (api.as_email, other.as_email, other.key) == (None, EDITOR, READ_KEY)


# ---------------------------------------------------------------- stdio
def test_stdio_main_uses_the_level_key_and_acting_as(monkeypatch):
    runs = []
    monkeypatch.setattr(mcp_server.MCPServer, "run", lambda self, *a, **k: runs.append(self))
    env = {"HOMETOWN_URL": "http://127.0.0.1:8782", "HOMETOWN_READ_KEY": READ_KEY}
    assert mcp_server.main(["--as", EDITOR], env=env) == 0
    assert tool_names(runs[-1]) == READ_TOOLS
    env = {**env, "HOMETOWN_MCP_WRITES": "log", "HOMETOWN_API_KEY": f"{WRITE_KEY},old-key"}
    built = []
    real = mcp_server.build_server
    monkeypatch.setattr(mcp_server, "build_server", lambda api, level: built.append(api) or real(api, level))
    assert mcp_server.main([], env=env) == 0
    assert (built[-1].key, built[-1].as_email, built[-1].base_url) == (WRITE_KEY, None, "http://127.0.0.1:8782")
    assert tool_names(runs[-1]) == READ_TOOLS | LOG_TOOLS


def test_stdio_main_refuses_the_write_key_at_none(monkeypatch, capsys):
    monkeypatch.setattr(mcp_server.MCPServer, "run", lambda self, *a, **k: pytest.fail("must not start"))
    assert mcp_server.main([], env={"HOMETOWN_READ_KEY": READ_KEY, "HOMETOWN_API_KEY": WRITE_KEY}) == 2
    err = capsys.readouterr().err
    assert "HOMETOWN_API_KEY" in err and WRITE_KEY not in err
