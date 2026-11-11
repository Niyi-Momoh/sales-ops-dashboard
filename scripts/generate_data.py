"""Generate the bundled synthetic dataset. Every figure in it is invented.

Usage: python scripts/generate_data.py app/data/sales.csv
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

REGIONS = {"London": 1.6, "South East": 1.2, "Midlands": 1.0, "North West": 0.9, "Scotland": 0.6}
CATEGORIES = {
    "Electronics": (2.0, 180.0),
    "Home": (3.0, 65.0),
    "Grocery": (9.0, 28.0),
    "Apparel": (4.0, 45.0),
}
START, END = "2024-01-01", "2026-10-31"
SEED = 11


def main(destination: str) -> None:
    rng = np.random.default_rng(SEED)
    dates = pd.date_range(START, END, freq="D")
    days = np.arange(len(dates))
    weekday = np.array([1.05, 0.95, 0.95, 1.0, 1.1, 1.25, 0.8])[dates.dayofweek]
    yearly = 1 + 0.12 * np.sin(2 * np.pi * (dates.dayofyear - 80) / 365.25)
    christmas = 1 + 0.6 * np.exp(-(((dates.dayofyear - 345) / 12.0) ** 2))
    growth = 1.0 + 0.09 * days / 365.25
    rows = []
    for region, scale in REGIONS.items():
        on_time_base = np.full(len(dates), 0.95)
        if region == "North West":
            # A carrier problem in spring 2026, so the dashboard has something real to flag.
            problem = (dates >= "2026-03-09") & (dates <= "2026-04-19")
            on_time_base[problem] = 0.82
        for category, (volume, price) in CATEGORIES.items():
            boost = christmas ** (1.5 if category == "Electronics" else 0.6)
            expected = 40 * scale * volume / 4 * weekday * yearly * boost * growth
            orders = rng.poisson(expected)
            value = price * rng.normal(1.0, 0.04, len(dates)) * (1 + 0.03 * days / 365.25)
            revenue = np.round(orders * value, 2)
            on_time = rng.binomial(
                orders, np.clip(on_time_base + rng.normal(0, 0.01, len(dates)), 0, 1)
            )
            return_rate = 0.09 if category == "Apparel" else 0.03
            returns = rng.binomial(orders, return_rate)
            rows.append(
                pd.DataFrame(
                    {
                        "date": dates.strftime("%Y-%m-%d"),
                        "region": region,
                        "category": category,
                        "orders": orders,
                        "revenue": revenue,
                        "on_time_orders": on_time,
                        "returns": returns,
                    }
                )
            )
    data = pd.concat(rows).sort_values(["date", "region", "category"])
    data.to_csv(destination, index=False)
    print(len(data), "rows", round(data["revenue"].sum() / 1e6, 1), "m revenue")


if __name__ == "__main__":
    main(sys.argv[1])
