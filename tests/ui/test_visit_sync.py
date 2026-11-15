"""Settings' "Synced visits" section at 412 px: the default group, each source's last run, the unmatched list."""
import json

import settings
import visit_sync
from conftest import mk_person
from plugins.visit_sync import dl_visit


def open_sync(page):
    page.goto("/#/settings")
    page.wait_for_selector("[data-section=sync]")
    return page.locator("[data-section=sync]")


def test_pick_the_group_see_the_runs_and_dismiss_an_unmatched_visit(page, con, feeds, monkeypatch):
    alex, sam = mk_person(con, "Alex", email="alex@example.com"), mk_person(con, "Sam")
    gone = mk_person(con, "Casey")
    con.execute("UPDATE person SET active = 0 WHERE id = ?", (gone,))
    visit_sync.set_people(con, [alex])
    monkeypatch.setattr(settings, "WTE_URL", "")
    feeds.dl.entries = [dl_visit(1, "2026-11-01", "Noodle <i>Example</i>", "Lafayette")]
    visit_sync.run(con)

    section = open_sync(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= 412
    boxes = section.locator("input[name=sync-person]")
    assert boxes.evaluate_all("els => els.map(e => [e.closest('label').innerText.trim(), e.checked])") == \
        [["Alex", True], ["Sam", False]]                                  # active people only
    text = section.inner_text()
    assert "Dinner Log" in text and "1 new" in text and "1 unmatched" in text
    assert "Where to Eat" in text and "not set up" in text and "WTE_URL" in text

    row = section.locator("[data-unmatched]")
    assert row.count() == 1
    assert "Noodle <i>Example</i>" in row.inner_text() and "Lafayette" in row.inner_text()
    assert "Nov 1, 2026" in row.inner_text() and row.locator("i").count() == 0

    section.locator("input[name=sync-person]").nth(1).check()
    section.locator("form[data-form=sync-people] button[type=submit]").click()
    page.wait_for_selector("#toast:has-text('Saved')")
    assert json.loads(con.execute("SELECT value FROM meta WHERE key = 'sync_people'").fetchone()[0]) == [alex, sam]

    row.get_by_role("button", name="Dismiss").click()
    page.wait_for_selector("[data-unmatched]", state="detached")
    assert con.execute("SELECT status FROM sync_seen").fetchone()[0] == "dismissed"
    assert "Nothing waiting" in open_sync(page).inner_text()


def test_no_group_says_what_to_do(page, con):
    mk_person(con, "Alex")
    section = open_sync(page)
    assert "Pick who usually goes" in section.inner_text()
    assert "not set up" in section.inner_text()
    assert page.evaluate("document.documentElement.scrollWidth") <= 412
