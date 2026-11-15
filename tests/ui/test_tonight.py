"""The Tonight screen at phone width: the default mode lists the eligible place, Shuffle reorders, a card opens the
place, and Back (or a fresh visit) keeps the mode."""
from datetime import date, timedelta

import common
from conftest import mk_person, mk_place, mk_visit

LIST = "#tonight-list"


def _days_ago(n):
    return (date.fromisoformat(common.today()) - timedelta(days=n)).isoformat()


def _names(page):
    return page.locator(f"{LIST} a.card .t").all_inner_texts()


def _after(page, action):
    """Run action, then wait until the list has been drawn again."""
    before = page.locator(LIST).get_attribute("data-query")
    action()
    page.wait_for_function("prev => document.querySelector('#tonight-list').dataset.query !== prev", arg=before)


def test_tonight_lists_shuffles_and_opens_a_place(page, con):
    a = mk_person(con, "Alex")
    fav = mk_place(con, "Old Favorite", cuisine="Mexican <b>")
    mk_visit(con, fav, _days_ago(90), {a: 5})
    recent = mk_place(con, "Recent Example")
    mk_visit(con, recent, _days_ago(3), {a: 5})
    sold = mk_place(con, "Sold Example", ownership="no")
    mk_visit(con, sold, _days_ago(90), {a: 5})

    page.goto("/")
    page.wait_for_selector(f"{LIST} a.card")
    assert _names(page) == ["Old Favorite"]                      # the default mode: not lately
    card = page.locator(f"{LIST} a.card").first.inner_text()
    assert "Mexican <b>" in card and "Alex 5" in card and "Score 5 of 5" in card and "Hours unknown" in card
    assert page.locator("#nav a.on").inner_text() == "Tonight"
    assert page.evaluate("document.documentElement.scrollWidth") <= 412

    for i in range(4):
        mk_place(con, f"Pho Example {i}")
    _after(page, lambda: page.click("button[data-mode=any]"))
    plain = _names(page)
    assert len(plain) == 6 and "Sold Example" not in plain and plain == sorted(plain)
    for _ in range(20):
        _after(page, lambda: page.click("button[data-act=shuffle]"))
        if _names(page) != plain:
            break
    shuffled = _names(page)
    assert shuffled != plain and sorted(shuffled) == plain
    assert "shuffled" in page.locator("#tonight-count").inner_text()

    page.locator(f"{LIST} a.card", has_text="Old Favorite").click()
    page.wait_for_url(f"**/#/p/{fav}")
    page.wait_for_selector("h1:text-is('Old Favorite')")

    page.go_back()                                               # the address kept the mode and the shuffle
    page.wait_for_selector(f"{LIST} a.card")
    assert page.locator("button[data-mode=any]").get_attribute("aria-pressed") == "true"
    assert _names(page) == shuffled

    page.goto("/#/past")
    page.wait_for_selector("h1:text-is('Past places')")
    page.locator("#nav a", has_text="Tonight").click()           # no address state: this device remembers "any"
    page.wait_for_selector(f"{LIST} a.card")
    assert page.locator("button[data-mode=any]").get_attribute("aria-pressed") == "true"
    assert _names(page) == plain


def test_an_audience_the_menu_lacks_falls_back_to_everyone(page, con):
    mk_place(con, "Casa Ejemplo")
    page.goto("/#/?mode=any&audience=me")       # not offered in the menu without a link, so it falls back to everyone
    page.wait_for_selector(f"{LIST} a.card")
    assert page.locator("select[name=audience]").input_value() == "everyone"
    mk_person(con, "Riley", grp="kid")
    page.goto("/#/?mode=any&audience=Riley")
    page.reload()
    page.wait_for_selector(f"{LIST} a.card")
    assert page.locator("select[name=audience]").input_value() == "Riley"
    page.goto("/#/?mode=any&audience=Nobody")
    page.reload()
    page.wait_for_selector(f"{LIST} a.card")
    assert page.locator("select[name=audience]").input_value() == "everyone"
