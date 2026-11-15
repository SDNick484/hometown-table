"""The shell at phone width: it loads under the CSP, shows APP_NAME, and the router draws a screen."""
import settings


def test_shell_loads_at_412px_and_shows_app_name(page):
    page.goto("/")
    assert page.title() == settings.APP_NAME
    assert page.locator(".brand").inner_text().strip() == settings.APP_NAME
    page.wait_for_selector("#view .screen")                       # router.js ran and drew something
    assert page.evaluate("document.documentElement.scrollWidth") <= 412   # no sideways scrolling on a phone
    font = page.evaluate("getComputedStyle(document.body).fontFamily")
    assert font.startswith('"Archivo"') or font.startswith("Archivo")


def test_unknown_address_says_so(page):
    page.goto("/#/no/such/screen")
    assert "nothing at this address" in page.locator("#view .screen").inner_text()
