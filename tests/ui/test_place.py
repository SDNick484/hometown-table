"""The place page at phone width: change ownership with a reason, see the history line and the Past places entry."""
from conftest import mk_person, mk_place, mk_visit


def test_sold_place_shows_history_and_lands_in_past_places(page, con):
    pid = mk_place(con, "Casa Ejemplo", town="Concord", cuisine="Mexican")
    a = mk_person(con, "Alex")
    mk_visit(con, pid, "2026-10-01", {a: 5})
    page.goto(f"/#/p/{pid}")
    page.wait_for_selector("h1")
    assert page.locator("h1").inner_text() == "Casa Ejemplo"
    page.locator("[data-act=ownership]").click()
    page.locator("#own-value").select_option("no")
    page.locator("#own-reason").fill("sold to a group")
    page.locator("#own-form button.primary").click()
    page.wait_for_selector("text=Counted → doesn't count")
    assert "sold to a group" in page.locator(".history").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth") <= 412
    page.goto("/#/past")
    page.wait_for_selector("a.card")
    text = page.locator("#view").inner_text()
    assert "Casa Ejemplo" in text and "Stopped counting" in text and "sold to a group" in text and "1 visit" in text
    assert page.locator("#nav a", has_text="Past places").count() == 1


def test_edit_form_saves_and_shows_the_conflict_message(page, con):
    pid = mk_place(con, "Casa Ejemplo")
    page.goto(f"/#/p/{pid}/edit")
    page.wait_for_selector("#place-form")
    page.locator("#f-cuisine").fill("Mexican")
    page.locator("#place-form button.primary").click()
    page.wait_for_selector("article.place")
    assert "Mexican" in page.locator("#view").inner_text()
    page.goto(f"/#/p/{pid}/edit")
    page.wait_for_selector("#place-form")
    con.execute("UPDATE place SET version = version + 1 WHERE id = ?", (pid,))
    page.locator("#f-cuisine").fill("Thai")
    page.locator("#place-form button.primary").click()
    page.wait_for_selector("text=Someone else just changed this place")


def test_delete_is_offered_only_without_visits_and_asks_first(page, con):
    empty = mk_place(con, "Pho Example")
    used = mk_place(con, "Casa Ejemplo")
    mk_visit(con, used, "2026-10-01", {mk_person(con, "Alex"): 4})
    page.goto(f"/#/p/{used}")
    page.wait_for_selector("article.place")
    assert page.locator("[data-act=delete]").count() == 0
    page.goto(f"/#/p/{empty}")
    page.wait_for_selector("article.place")
    page.locator("[data-act=delete]").click()
    page.locator("[data-act=delete-yes]").click()
    page.wait_for_selector("text=No past places yet")
    assert con.execute("SELECT COUNT(*) FROM place WHERE id = ?", (empty,)).fetchone()[0] == 0
