"""Check that the Shinylive export, which is what gets deployed, works in a browser.

Skipped unless SITE_DIR points at the output of `shinylive export app site`.
"""

from __future__ import annotations

import functools
import http.server
import os
import threading

import pytest
from playwright.sync_api import Page, expect

SITE = os.environ.get("SITE_DIR")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - signature set by the base class
        pass


@pytest.fixture(scope="module")
def site_url():
    if not SITE:
        pytest.skip("set SITE_DIR to a Shinylive export to run this test")
    handler = functools.partial(QuietHandler, directory=SITE)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_exported_app_runs_python_in_the_browser(page: Page, site_url: str):
    page.goto(site_url)
    app = page.frame_locator("iframe")
    # The first load downloads Python and pandas into the browser, so allow time.
    expect(app.locator("#kpi_revenue")).to_have_text("£6.63m", timeout=180_000)
    app.get_by_label("London").uncheck()
    expect(app.locator("#kpi_revenue")).not_to_have_text("£6.63m")
    app.get_by_role("tab", name="Forecast").click()
    expect(app.locator("#forecast_text")).to_contain_text("It beat that baseline", timeout=60_000)
