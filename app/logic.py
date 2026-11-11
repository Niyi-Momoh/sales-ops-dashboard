"""Calculations behind the dashboard. Nothing here depends on Shiny, so it is unit-tested."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("date", "region", "category", "orders", "revenue", "on_time_orders", "returns")
COUNT_COLUMNS = ("orders", "on_time_orders", "returns")
MAX_ROWS = 500_000


class DataError(ValueError):
    """A problem with uploaded data, written for a business user to read."""


def load_sales(source) -> pd.DataFrame:
    """Read and validate a sales CSV, raising DataError with a plain explanation."""
    try:
        data = pd.read_csv(source)
    except Exception as error:  # noqa: BLE001 - any parse failure becomes one clear message
        raise DataError("The file could not be read as a CSV.") from error
    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise DataError(
            f"Missing columns: {', '.join(missing)}. Download the template to see the format."
        )
    if data.empty:
        raise DataError("The file has no rows.")
    if len(data) > MAX_ROWS:
        raise DataError(f"The file has {len(data):,} rows; the limit is {MAX_ROWS:,}.")
    data = data[list(REQUIRED_COLUMNS)].copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce", format="mixed")
    if data["date"].isna().any():
        row = int(data.index[data["date"].isna()][0]) + 2
        raise DataError(f"Row {row} has a date that could not be read. Use YYYY-MM-DD.")
    for column in ("orders", "revenue", "on_time_orders", "returns"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
        bad = data[column].isna() | (data[column] < 0)
        if bad.any():
            row = int(data.index[bad][0]) + 2
            raise DataError(f"Row {row}: '{column}' must be a number of zero or more.")
    if (data["on_time_orders"] > data["orders"]).any() or (data["returns"] > data["orders"]).any():
        raise DataError("On-time orders and returns cannot exceed orders.")
    data["region"] = data["region"].astype(str).str.strip()
    data["category"] = data["category"].astype(str).str.strip()
    return data.sort_values("date").reset_index(drop=True)


def filter_sales(
    data: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    regions: list[str] | tuple[str, ...],
    categories: list[str] | tuple[str, ...],
) -> pd.DataFrame:
    mask = (
        (data["date"] >= pd.Timestamp(start))
        & (data["date"] <= pd.Timestamp(end))
        & data["region"].isin(regions)
        & data["category"].isin(categories)
    )
    return data[mask]


def comparison_window(start, end, mode: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The period to compare against: the one just before, or the same dates a year earlier."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if mode == "year":
        return start - pd.DateOffset(years=1), end - pd.DateOffset(years=1)
    length = end - start
    previous_end = start - pd.Timedelta(days=1)
    return previous_end - length, previous_end


def kpis(frame: pd.DataFrame) -> dict[str, float]:
    orders = float(frame["orders"].sum())
    revenue = float(frame["revenue"].sum())
    return {
        "revenue": revenue,
        "orders": orders,
        "average_order_value": revenue / orders if orders else float("nan"),
        "on_time_rate": float(frame["on_time_orders"].sum()) / orders if orders else float("nan"),
        "return_rate": float(frame["returns"].sum()) / orders if orders else float("nan"),
    }


def change(current: float, previous: float, rate: bool = False) -> float | None:
    """Relative change for amounts, or the difference in points for rates."""
    if any(pd.isna(value) for value in (current, previous)):
        return None
    if rate:
        return (current - previous) * 100
    return None if previous == 0 else (current - previous) / previous


def weekly(frame: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """Weekly totals, with weeks ending on Sunday.

    With ``start`` and ``end``, only weeks lying wholly inside that period are kept, because a
    part-week would look like a sudden fall.
    """
    columns = ["week", "revenue", "orders", "on_time_orders", "returns"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    totals = (
        frame.set_index("date")[["revenue", "orders", "on_time_orders", "returns"]]
        .resample("W-SUN")
        .sum()
        .reset_index()
        .rename(columns={"date": "week"})
    )
    if start is not None:
        totals = totals[totals["week"] - pd.Timedelta(days=6) >= pd.Timestamp(start)]
    if end is not None:
        totals = totals[totals["week"] <= pd.Timestamp(end)]
    return totals.reset_index(drop=True)


def complete_weeks(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop a partial final week, which would otherwise look like a sudden fall."""
    weeks = weekly(frame)
    if weeks.empty:
        return weeks
    last_day = frame["date"].max()
    if last_day.dayofweek != 6:
        weeks = weeks[weeks["week"] < last_day]
    return weeks.reset_index(drop=True)


def breakdown(current: pd.DataFrame, previous: pd.DataFrame, by: str) -> pd.DataFrame:
    def summarise(frame: pd.DataFrame) -> pd.DataFrame:
        grouped = frame.groupby(by)[["revenue", "orders", "on_time_orders", "returns"]].sum()
        return grouped

    now, before = summarise(current), summarise(previous)
    table = pd.DataFrame(index=now.index)
    table["Revenue"] = now["revenue"]
    table["Share"] = now["revenue"] / now["revenue"].sum() if now["revenue"].sum() else np.nan
    previous_revenue = before["revenue"].reindex(now.index)
    table["Revenue change"] = (now["revenue"] - previous_revenue) / previous_revenue.replace(
        0, np.nan
    )
    table["Orders"] = now["orders"]
    table["On-time rate"] = now["on_time_orders"] / now["orders"].replace(0, np.nan)
    table["Return rate"] = now["returns"] / now["orders"].replace(0, np.nan)
    table = table.sort_values("Revenue", ascending=False)
    table.index.name = by.capitalize()
    return table.reset_index()


@dataclass(frozen=True)
class Alert:
    region: str
    week: pd.Timestamp
    on_time_rate: float
    baseline: float

    @property
    def message(self) -> str:
        drop = (self.baseline - self.on_time_rate) * 100
        return (
            f"{self.region}: on-time delivery was {self.on_time_rate:.1%} in the week ending "
            f"{self.week:%d %b %Y}, {drop:.1f} points below its previous 8-week average."
        )


def delivery_alerts(frame: pd.DataFrame, threshold_points: float = 5.0, since=None) -> list[Alert]:
    """Flag weeks where a region's on-time rate fell well below its own recent level.

    ``frame`` should include about 8 weeks before ``since`` so the first weeks of the period
    have a baseline. Only weeks ending on or after ``since`` plus 6 days are reported.
    """
    alerts: list[Alert] = []
    for region, group in frame.groupby("region"):
        weeks = complete_weeks(group)
        if len(weeks) < 9:
            continue
        rate = weeks["on_time_orders"] / weeks["orders"].replace(0, np.nan)
        baseline = rate.shift(1).rolling(8, min_periods=8).mean()
        flagged = (baseline - rate) * 100 >= threshold_points
        if since is not None:
            flagged &= weeks["week"] - pd.Timedelta(days=6) >= pd.Timestamp(since)
        for index in weeks.index[flagged.fillna(False)]:
            alerts.append(Alert(region, weeks.at[index, "week"], rate[index], baseline[index]))
    return sorted(alerts, key=lambda alert: (alert.week, alert.region), reverse=True)


# Forecasting -----------------------------------------------------------------------------

HORIZON = 12


def seasonal_forecast(history: pd.Series, horizon: int = HORIZON) -> np.ndarray:
    """Last year's same weeks, scaled by recent year-on-year growth.

    ``history`` holds weekly values in order. It needs at least 52 + 13 weeks.
    """
    values = history.to_numpy(dtype=float)
    if len(values) < 65:
        raise ValueError("at least 65 weeks of history are needed")
    recent, year_before = values[-13:].sum(), values[-65:-52].sum()
    growth = recent / year_before if year_before else 1.0
    base = values[-52 : -52 + horizon] if horizon < 52 else values[-52:]
    return base * growth


def naive_forecast(history: pd.Series, horizon: int = HORIZON) -> np.ndarray:
    """The simplest benchmark: every future week equals the last week."""
    return np.full(horizon, float(history.iloc[-1]))


@dataclass(frozen=True)
class ForecastResult:
    weeks: pd.DatetimeIndex
    point: np.ndarray
    low: np.ndarray
    high: np.ndarray
    model_mape: float
    naive_mape: float
    origins: int


def forecast_with_backtest(weeks: pd.DataFrame, origins: int = 26) -> ForecastResult:
    """Forecast weekly revenue and measure accuracy on past weeks the model did not see.

    For each of the last ``origins`` weeks, both methods forecast the next 12 weeks using only
    data before that point. Their average absolute percentage errors are compared, and the
    spread of the model's past errors sets the 80% range around the new forecast.
    """
    series = weeks.set_index("week")["revenue"].astype(float)
    if len(series) < 65 + HORIZON + origins:
        raise ValueError("not enough history to forecast; select at least two years")
    model_errors, naive_errors = [], []
    ratios: list[float] = []
    for offset in range(origins + HORIZON, HORIZON, -1):
        history, actual = series.iloc[:-offset], series.iloc[-offset:][:HORIZON].to_numpy()
        predicted = seasonal_forecast(history)
        model_errors.append(np.abs(predicted - actual) / actual)
        naive_errors.append(np.abs(naive_forecast(history) - actual) / actual)
        ratios.extend(actual / predicted)
    point = seasonal_forecast(series)
    low_ratio, high_ratio = np.quantile(ratios, [0.10, 0.90])
    future = pd.date_range(series.index[-1] + pd.Timedelta(weeks=1), periods=HORIZON, freq="W-SUN")
    return ForecastResult(
        future,
        point,
        point * low_ratio,
        point * high_ratio,
        float(np.mean(model_errors)),
        float(np.mean(naive_errors)),
        origins,
    )


def template_csv() -> str:
    rows = [
        "date,region,category,orders,revenue,on_time_orders,returns",
        "2026-01-05,London,Electronics,25,4500.00,24,1",
        "2026-01-05,Midlands,Home,40,2600.00,38,2",
    ]
    return "\n".join(rows) + "\n"
