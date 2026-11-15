"""The lists screens at phone width: make a list, paste into it, see unmatched lines, drop one."""
import settings
from conftest import mk_list, mk_place


def test_make_a_list_paste_and_drop_an_unmatched_line(page, con, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_KEY", "")
    mk_place(con, "Casa Ejemplo")
    page.goto("/#/lists")
    page.wait_for_selector("#list-form")
    page.fill("input[name=name]", "Example Taco Trail <b>")
    page.click("#list-form button[type=submit]")
    page.wait_for_selector("#paste-form")
    assert "Example Taco Trail <b>" in page.locator("h1").inner_text()
    page.fill("textarea[name=lines]", "1. Casa Ejemplo - al pastor\n\n- Nowhere Example\n")
    page.click("#paste-form button[type=submit]")
    page.wait_for_selector("input[name=retry]")
    assert page.input_value("input[name=retry]") == "Nowhere Example"
    assert "1 matched an existing place" in page.locator(".notice").inner_text()
    assert page.locator("text=Casa Ejemplo").count() >= 1
    page.fill("input[name=retry]", "Nowhere Example Cafe")
    page.get_by_role("button", name="Search again").click()
    page.wait_for_selector("[role=alert], .notice")
    page.get_by_role("button", name="Drop").click()
    page.wait_for_selector("text=Nothing is waiting")
    assert page.evaluate("document.documentElement.scrollWidth") <= 412


def test_index_shows_lists_and_opens_one(page, con):
    lid = mk_list(con, "Example Taco Trail")
    page.goto("/#/lists")
    page.get_by_role("link", name="Example Taco Trail").click()
    page.wait_for_selector("#paste-form")
    assert page.url.endswith(f"#/lists/{lid}")
