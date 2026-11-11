from __future__ import annotations

import io

import logic
import numpy as np
import pandas as pd
import pytest
from logic import DataError

SAMPLE = logic.load_sales(logic.__file__.replace("logic.py", "data/sales.csv"))
ALL_REGIONS = tuple(SAMPLE["region"].unique())
ALL_CATEGORIES = tuple(SAMPLE["category"].unique())


def csv(text: str) -> io.StringIO:
    return io.StringIO(text)


def test_sample_data_loads_and_covers_the_expected_range():
    assert SAMPLE["date"].min() == pd.Timestamp("2024-01-01")
    assert SAMPLE["date"].max() == pd.Timestamp("2026-10-31")
    assert len(ALL_REGIONS) == 5 and len(ALL_CATEGORIES) == 4


def test_template_is_valid_input():
    frame = logic.load_sales(csv(logic.template_csv()))
    assert len(frame) == 2


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("date,region\n2026-01-01,London\n", "Missing columns"),
        (logic.template_csv().splitlines()[0] + "\n", "no rows"),
        (logic.template_csv().replace("2026-01-05,London", "05/13/2026x,London"), "Row 2"),
        (logic.template_csv().replace(",25,", ",-3,"), "orders"),
        (logic.template_csv().replace(",24,1", ",99,1"), "cannot exceed"),
    ],
)
def test_bad_uploads_get_plain_messages(text, message):
    with pytest.raises(DataError, match=message):
        logic.load_sales(csv(text))


def test_unreadable_file_is_reported():
    with pytest.raises(DataError, match="could not be read"):
        logic.load_sales(io.BytesIO(b"\x00\xff\x00"))


def test_filter_and_kpis_match_a_hand_calculation():
    frame = logic.filter_sales(SAMPLE, "2026-01-05", "2026-01-11", ["London"], ["Home"])
    assert len(frame) == 7
    figures = logic.kpis(frame)
    assert figures["revenue"] == pytest.approx(frame["revenue"].sum())
    assert figures["average_order_value"] == pytest.approx(
        frame["revenue"].sum() / frame["orders"].sum()
    )
    assert 0 < figures["on_time_rate"] <= 1


def test_kpis_of_nothing_are_not_numbers_rather_than_errors():
    empty = SAMPLE.iloc[0:0]
    figures = logic.kpis(empty)
    assert figures["revenue"] == 0 and np.isnan(figures["on_time_rate"])


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("previous", ("2026-07-22", "2026-07-31")),
        ("year", ("2025-08-01", "2025-08-10")),
    ],
)
def test_comparison_windows(mode, expected):
    start, end = logic.comparison_window("2026-08-01", "2026-08-10", mode)
    assert (start, end) == tuple(pd.Timestamp(day) for day in expected)


def test_change_handles_amounts_rates_and_missing_values():
    assert logic.change(110, 100) == pytest.approx(0.10)
    assert logic.change(0.95, 0.90, rate=True) == pytest.approx(5.0)
    assert logic.change(10, 0) is None
    assert logic.change(float("nan"), 1.0) is None


def test_weekly_totals_keep_only_full_weeks_when_bounded():
    frame = logic.filter_sales(SAMPLE, "2026-08-01", "2026-10-31", ALL_REGIONS, ALL_CATEGORIES)
    weeks = logic.weekly(frame, "2026-08-01", "2026-10-31")
    # 1 Aug 2026 is a Saturday, so the first full week ends on Sunday 9 Aug.
    assert weeks["week"].iloc[0] == pd.Timestamp("2026-08-09")
    assert weeks["week"].iloc[-1] == pd.Timestamp("2026-10-25")
    inside = frame[(frame["date"] >= "2026-08-03") & (frame["date"] <= "2026-10-25")]
    assert weeks["revenue"].sum() == pytest.approx(inside["revenue"].sum())


def test_breakdown_shares_sum_to_one_and_changes_are_correct():
    now = logic.filter_sales(SAMPLE, "2026-08-01", "2026-10-31", ALL_REGIONS, ALL_CATEGORIES)
    before_window = logic.comparison_window("2026-08-01", "2026-10-31", "previous")
    before = logic.filter_sales(SAMPLE, *before_window, ALL_REGIONS, ALL_CATEGORIES)
    table = logic.breakdown(now, before, "region").set_index("Region")
    assert table["Share"].sum() == pytest.approx(1.0)
    london_now = now.loc[now["region"] == "London", "revenue"].sum()
    london_before = before.loc[before["region"] == "London", "revenue"].sum()
    assert table.at["London", "Revenue change"] == pytest.approx(london_now / london_before - 1)


def test_new_region_without_history_has_no_change_instead_of_an_error():
    now = SAMPLE.head(3).assign(region="Wales")
    table = logic.breakdown(now, SAMPLE.head(3), "region")
    assert table["Revenue change"].isna().all()


def test_alerts_find_the_planted_delivery_problem_and_nothing_else():
    lookback = logic.filter_sales(SAMPLE, "2025-12-29", "2026-06-30", ALL_REGIONS, ALL_CATEGORIES)
    alerts = logic.delivery_alerts(lookback, since="2026-03-01")
    assert {alert.region for alert in alerts} == {"North West"}
    weeks = sorted(alert.week for alert in alerts)
    assert weeks[0] == pd.Timestamp("2026-03-15")
    assert weeks[-1] <= pd.Timestamp("2026-04-19")
    assert "points below" in alerts[0].message


def test_alerts_need_a_baseline():
    short = logic.filter_sales(SAMPLE, "2026-03-09", "2026-04-19", ["North West"], ALL_CATEGORIES)
    assert logic.delivery_alerts(short) == []


def test_seasonal_forecast_repeats_last_year_scaled_by_growth():
    history = pd.Series(np.r_[np.full(52, 100.0), np.full(13, 110.0)])
    forecast = logic.seasonal_forecast(history, 4)
    assert forecast == pytest.approx(np.full(4, 110.0))


def test_forecast_needs_enough_history():
    with pytest.raises(ValueError):
        logic.seasonal_forecast(pd.Series(np.ones(30)))
    with pytest.raises(ValueError, match="not enough history"):
        logic.forecast_with_backtest(logic.weekly(SAMPLE.tail(5000)))


def test_backtest_beats_naive_on_sample_data_and_range_contains_point():
    result = logic.forecast_with_backtest(logic.complete_weeks(SAMPLE))
    assert len(result.weeks) == logic.HORIZON
    assert result.weeks[0] == pd.Timestamp("2026-11-01")
    assert result.model_mape < result.naive_mape
    assert np.all(result.low <= result.point) and np.all(result.point <= result.high)
