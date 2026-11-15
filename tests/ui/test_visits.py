"""The visits screen at phone width: the log form defaults to me, a visit saves and shows, deleting asks twice."""
from conftest import mk_person, mk_place


def test_log_a_visit_and_delete_it(page, con):
    pid = mk_place(con)
    mk_person(con, "Alex", email="alex@example.com")
    mk_person(con, "Sam")
    page.goto(f"/#/p/{pid}/visits")
    page.wait_for_selector("#visit-form")
    assert page.locator("input[name=went]").evaluate_all("els => els.map(e => e.checked)") == [True, False]
    page.select_option("select[name^=rating-]:near(:text('Alex'))", "5")
    page.fill("input[name=occasion]", "Riley's graduation <b>")
    page.click("button[type=submit]")
    card = page.locator("article.card")
    card.wait_for()
    text = card.inner_text()
    assert "Alex" in text and "★★★★★" in text and "Riley's graduation <b>" in text
    assert page.evaluate("document.documentElement.scrollWidth") <= 412
    card.get_by_role("button", name="Delete").click()
    card.get_by_role("button", name="Really delete this visit?").click()
    page.wait_for_selector("text=No visits yet.")


def test_no_one_ticked_shows_the_servers_message_and_keeps_typing(page, con):
    pid = mk_place(con)
    mk_person(con, "Alex")
    page.goto(f"/#/p/{pid}/visits")
    page.wait_for_selector("#visit-form")
    page.uncheck("input[name=went]")
    page.fill("textarea[name=note]", "Lovely patio")
    page.click("button[type=submit]")
    page.wait_for_selector("[role=alert]")
    assert page.locator("[role=alert]").inner_text() == "Who went?"
    assert page.input_value("textarea[name=note]") == "Lovely patio"
