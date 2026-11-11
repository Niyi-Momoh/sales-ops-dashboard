"""Drive the running app in a real browser, as a business user would."""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import logic
import pytest
from playwright.sync_api import Page, expect
from shiny.playwright import controller

ROOT = Path(__file__).parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def app_url():
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "shiny", "run", str(ROOT / "app" / "app.py"), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.5)
    yield url
    server.terminate()
    server.wait(10)


@pytest.fixture
def dashboard(page: Page, app_url: str) -> Page:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(app_url)
    expect(page.locator("#kpi_revenue")).to_have_text(re.compile(r"£"), timeout=30_000)
    yield page
    assert errors == []


def test_kpis_show_sample_figures_with_comparisons(dashboard: Page):
    expect(dashboard.locator("#kpi_revenue")).to_have_text("£6.63m")
    expect(dashboard.locator("#delta_revenue")).to_contain_text("vs previous period")
    expect(dashboard.get_by_text("Sample data: every figure is invented")).to_be_visible()


def test_filtering_a_region_changes_the_figures(dashboard: Page):
    dashboard.get_by_label("London").uncheck()
    expect(dashboard.locator("#kpi_revenue")).not_to_have_text("£6.63m")
    sample = logic.load_sales(ROOT / "app" / "data" / "sales.csv")
    period = logic.filter_sales(
        sample,
        "2026-08-01",
        "2026-10-31",
        ["Midlands", "North West", "Scotland", "South East"],
        sample["category"].unique(),
    )
    expected = f"£{period['revenue'].sum() / 1e6:.2f}m"
    expect(dashboard.locator("#kpi_revenue")).to_have_text(expected)


def test_switching_comparison_updates_labels(dashboard: Page):
    dashboard.get_by_label("Same period last year").check()
    expect(dashboard.locator("#delta_revenue")).to_contain_text("vs last year")


def test_alert_tab_finds_the_delivery_problem(dashboard: Page):
    controller.InputDateRange(dashboard, "dates").set(("2026-03-01", "2026-04-30"))
    dashboard.get_by_role("tab", name="Delivery alerts").click()
    expect(dashboard.locator("#alerts")).to_contain_text("6 week(s) flagged")
    expect(dashboard.locator("#alerts")).to_contain_text("North West")


def test_breakdown_and_download(dashboard: Page, tmp_path: Path):
    dashboard.get_by_role("tab", name="Breakdown").click()
    expect(dashboard.locator("#breakdown_table")).to_contain_text("London")
    with dashboard.expect_download() as download:
        dashboard.get_by_role("link", name="Download filtered data").click()
    saved = tmp_path / "out.csv"
    download.value.save_as(saved)
    lines = saved.read_text().splitlines()
    assert lines[0] == "date,region,category,orders,revenue,on_time_orders,returns"
    assert lines[1].startswith("2026-08-01")
    assert len(lines) == 1 + 92 * 20


def test_forecast_reports_tested_accuracy(dashboard: Page):
    dashboard.get_by_role("tab", name="Forecast").click()
    expect(dashboard.locator("#forecast_text")).to_contain_text("It beat that baseline")


def test_bad_upload_explains_the_problem_and_keeps_working(dashboard: Page, tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text("date,region\n2026-01-01,London\n")
    dashboard.locator("#upload").set_input_files(bad)
    expect(dashboard.get_by_role("alert")).to_contain_text("Missing columns")
    expect(dashboard.locator("#kpi_revenue")).to_have_text("£6.63m")


def test_own_data_upload_replaces_the_sample(dashboard: Page, tmp_path: Path):
    rows = ["date,region,category,orders,revenue,on_time_orders,returns"]
    rows += [f"2026-09-{day:02d},Wales,Tools,10,500,9,0" for day in range(1, 29)]
    upload = tmp_path / "mine.csv"
    upload.write_text("\n".join(rows) + "\n")
    dashboard.locator("#upload").set_input_files(upload)
    expect(dashboard.get_by_text("Sample data")).to_have_count(0)
    expect(dashboard.get_by_label("Wales")).to_be_checked()
    expect(dashboard.locator("#kpi_revenue")).to_have_text("£14.0k")
