"""The inbox and Add place at 412 px: accept with a verdict, "Use this" for a Google value, closure flags, un-hiding a
chain, and adding a place found in OpenStreetMap."""
import json

from conftest import mk_candidate, mk_list, mk_place

import common

OSM_EXTRA = json.dumps({"cuisine": "mexican", "phone": "+1 555 0100", "opening_hours": "Mo-Su 11:00-21:00"})


def osm_candidate(con, **cols):
    row = dict(google_place_id="g-casa", osm_ref="node/1001", osm_name="Casa Ejemplo",
               osm_address="100 Example Street, Concord, CA 94520", osm_lat=37.9781, osm_lng=-122.0312, osm_extra=OSM_EXTRA)
    return mk_candidate(con, **{**row, **cols})


def open_inbox(page, tab=None):
    page.goto("/#/inbox" + (f"?tab={tab}" if tab else ""))
    page.wait_for_selector("#view h1:has-text('Inbox')")


def test_accept_with_a_verdict_and_the_list_reason(page, con):
    cid = osm_candidate(con)
    lid = mk_list(con, "Example Taco Trail")
    con.execute("INSERT INTO list_entry (list_id, line, candidate_id, status, created_at) VALUES (?, ?, ?, 'matched', ?)",
                (lid, "Casa Ejemplo", cid, common.now()))
    open_inbox(page)
    page.wait_for_selector("#nav a:has-text('Inbox (1)')")
    card = page.locator(f"[data-id='{cid}']")
    text = card.inner_text()
    assert "Casa Ejemplo" in text and "OSM match" in text and "Example Taco Trail" in text
    assert "OpenStreetMap contributors" in page.locator("#view").inner_text()
    card.locator("[data-verdict=counts]").click()
    form = card.locator("form")
    assert form.locator("input[name=ownership_reason]").input_value() == "on Example Taco Trail"
    assert form.locator("input[name=name]").input_value() == "Casa Ejemplo"
    assert form.locator("select[name=ownership]").input_value() == "counts"
    form.locator("input[name=want_to_try]").check()
    form.locator("button[type=submit]").click()
    page.wait_for_selector("text=Nothing new")
    assert page.evaluate("document.documentElement.scrollWidth") <= 412
    place = con.execute("SELECT * FROM place").fetchone()
    assert (place["name"], place["cuisine"], place["ownership"], place["ownership_reason"], place["want_to_try"]) == \
        ("Casa Ejemplo", "mexican", "counts", "on Example Taco Trail", 1)
    assert con.execute("SELECT status FROM candidate WHERE id = ?", (cid,)).fetchone()[0] == "accepted"
    page.wait_for_selector("#nav a[data-path='/inbox']:text-is('Inbox')")


def test_use_this_takes_the_google_name_only_when_tapped(page, con, fake_http):
    cid = mk_candidate(con, google_place_id="g-pho", g_name="Pho Example", g_address="200 Example Street, Concord, CA",
                       g_lat=37.9795, g_lng=-122.0305, g_type="vietnamese_restaurant", g_fetched_at=common.now())
    fake_http.on("nominatim", "osm/nominatim_address.json")
    open_inbox(page)
    card = page.locator(f"[data-id='{cid}']")
    assert "Google Maps" in card.inner_text() and "No OSM match" in card.inner_text()
    card.locator("[data-verdict=unsure]").click()
    form = card.locator("form")
    assert form.locator("input[name=name]").input_value() == ""
    form.locator("[data-use=name]").click()
    form.locator("[data-use=address]").click()
    assert form.locator("input[name=name]").input_value() == "Pho Example"
    form.locator("input[name=ownership_reason]").fill("family run")
    form.locator("button[type=submit]").click()
    page.wait_for_selector("text=Nothing new")
    place = con.execute("SELECT * FROM place").fetchone()
    assert (place["name"], place["address"], place["lat"]) == ("Pho Example", "200 Example Street, Concord, CA", 37.979)


def test_flags_confirm_and_dismiss(page, con):
    shut = mk_place(con, "Casa Ejemplo", closure_flag="CLOSED_PERMANENTLY")
    fine = mk_place(con, "Pho Example", closure_flag="CLOSED_TEMPORARILY")
    open_inbox(page, "flags")
    page.locator(f"[data-place='{shut}'] [data-act=flag-confirm]").click()
    page.wait_for_selector(f"[data-place='{shut}']", state="detached")
    page.locator(f"[data-place='{fine}'] [data-act=flag-dismiss]").click()
    page.wait_for_selector("text=No closure flags")
    assert con.execute("SELECT standing FROM place WHERE id = ?", (shut,)).fetchone()[0] == "closed"
    assert con.execute("SELECT standing, closure_dismissed FROM place WHERE id = ?", (fine,)).fetchone()[:] == \
        ("open", "CLOSED_TEMPORARILY")


def test_hidden_as_chains_is_collapsed_and_can_unhide(page, con):
    cid = mk_candidate(con, status="chain_hidden", osm_ref="way/2002", osm_name="Chain Burger Example", chain_hint=1)
    open_inbox(page)
    assert not page.locator(f"[data-id='{cid}']").is_visible()
    page.locator("#chains summary").click()
    page.locator(f"[data-id='{cid}'] [data-act=unhide]").click()
    page.wait_for_selector(f"[data-id='{cid}']", state="detached")
    assert con.execute("SELECT status FROM candidate WHERE id = ?", (cid,)).fetchone()[0] == "new"


def test_add_place_from_a_lookup(page, con, fake_http):
    mk_place(con, "Pho Example", osm_ref="way/2002")
    fake_http.on("nominatim", "osm/nominatim_search.json")
    page.goto("/#/add")
    page.wait_for_selector("#lookup")
    page.fill("#lookup input[name=name]", "example")
    page.click("#lookup button[type=submit]")
    page.wait_for_selector("[data-pick]")
    assert "Already on the list" in page.locator("#results").inner_text()
    page.locator("[data-pick]").first.click()
    form = page.locator("#add-form form")
    assert form.locator("input[name=name]").input_value() == "Casa Ejemplo"
    assert form.locator("select[name=ownership]").input_value() == "unsure"
    form.locator("button[type=submit]").click()
    assert "reason" in form.locator("[role=alert]").inner_text()
    form.locator("input[name=ownership_reason]").fill("owner at the counter")
    form.locator("button[type=submit]").click()
    page.wait_for_selector("article.place h1:has-text('Casa Ejemplo')")
    place = con.execute("SELECT * FROM place WHERE name = 'Casa Ejemplo'").fetchone()
    assert (place["osm_ref"], place["lat"], place["hours"], place["origin"]) == ("node/1001", 37.9781, "Mo-Su 11:00-21:00", "hand")
    assert page.evaluate("document.documentElement.scrollWidth") <= 412


def test_add_place_picked_from_the_inbox_sorts_the_candidate(page, con, fake_http):
    cid = osm_candidate(con, status="dismissed")
    fake_http.on("nominatim", "osm/nominatim_search.json")
    page.goto("/#/add?name=casa")
    page.wait_for_selector("[data-pick]")
    assert "Dismissed from the inbox once" in page.locator("#results").inner_text()
    page.locator("[data-pick]").first.click()
    form = page.locator("#add-form form")
    form.locator("select[name=ownership]").select_option("counts")
    form.locator("input[name=ownership_reason]").fill("one location")
    form.locator("button[type=submit]").click()
    page.wait_for_selector("article.place h1:has-text('Casa Ejemplo')")
    pid = con.execute("SELECT place_id FROM candidate WHERE id = ? AND status = 'accepted'", (cid,)).fetchone()[0]
    place = con.execute("SELECT * FROM place WHERE id = ?", (pid,)).fetchone()
    assert (place["google_place_id"], place["origin"], place["ownership"]) == ("g-casa", "scan", "counts")
