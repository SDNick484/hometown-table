"""The Settings screen at 412 px: people, tags, status and export links."""
import re


def open_settings(page):
    page.goto("/#/settings")
    page.wait_for_selector("#view .screen h1")


def test_settings_is_in_the_nav_and_fits_the_phone(page):
    open_settings(page)
    assert page.locator("#nav a", has_text="Settings").count() == 1
    assert page.evaluate("document.documentElement.scrollWidth") <= 412
    text = page.locator("#view .screen").inner_text()
    for word in ("People", "Tags", "Status", "Export"):
        assert word in text
    assert "private room" in text and "good for groups" in text       # the seeded tags


def test_add_a_person_and_link_my_email(page):
    open_settings(page)
    page.fill("form[data-form=person] [name=name]", "Jordan")
    page.select_option("form[data-form=person] [name=grp]", "kid")
    page.click("form[data-form=person] button[type=submit]")
    page.wait_for_selector("[data-person]:has-text('Jordan')")
    page.fill("form[data-form=person] [name=name]", "Alex")
    page.fill("form[data-form=person] [name=email]", "stranger@example.com")
    page.click("form[data-form=person] button[type=submit]")
    assert "isn't an editor" in page.locator("[role=alert]").inner_text()
    page.fill("form[data-form=person] [name=email]", "alex@example.com")
    page.click("form[data-form=person] button[type=submit]")
    page.wait_for_selector("[data-person]:has-text('Alex')")
    assert "alex@example.com" in page.locator("[data-person]:has-text('Alex')").inner_text()


def test_a_person_script_name_is_shown_as_text(page):
    open_settings(page)
    page.fill("form[data-form=person] [name=name]", "<b>Sam</b>")
    page.click("form[data-form=person] button[type=submit]")
    page.wait_for_selector("[data-person]:has-text('<b>Sam</b>')")
    assert page.locator("[data-person] b").count() == 0


def test_tags_add_toggle_occasion_and_delete_with_a_named_count(page):
    open_settings(page)
    page.fill("form[data-form=tag] [name=label]", "patio")
    page.click("form[data-form=tag] button[type=submit]")
    row = page.locator("[data-tag]:has-text('patio')")
    row.wait_for()
    box = row.locator("input[type=checkbox]")
    assert not box.is_checked()
    box.click()
    page.wait_for_selector("[data-tag]:has-text('patio') input:checked")
    row.locator("[data-act=ask-delete]").click()                        # inline confirm, never window.confirm
    assert re.search(r"patio", row.inner_text()) and "0 places" in row.inner_text()
    row.locator("[data-act=delete]").click()
    page.wait_for_selector("[data-tag]:has-text('patio')", state="detached")


def test_export_links_and_status(page):
    open_settings(page)
    hrefs = page.eval_on_selector_all("a[download]", "els => els.map(e => e.getAttribute('href'))")
    assert hrefs == ["/api/export.json", "/api/export.csv"]
    assert "Google key" in page.locator("#view .screen").inner_text()


def test_move_a_tag_down_and_remove_a_person(page):
    open_settings(page)
    first = page.locator("[data-tag]").first.inner_text()
    assert "good for groups" in first
    page.locator("[data-tag]").first.locator("[data-act=down]").click()
    page.wait_for_selector("[data-tag]:first-child:has-text('private room')")
    page.fill("form[data-form=person] [name=name]", "Casey")
    page.click("form[data-form=person] button[type=submit]")
    row = page.locator("[data-person]:has-text('Casey')")
    row.wait_for()
    row.locator("[data-act=ask-person]").click()
    row.locator("[data-act=remove-person]").click()
    page.wait_for_selector("[data-person]:has-text('Casey')", state="detached")
