"""Sales and operations dashboard for business teams."""

from __future__ import annotations

import io
from pathlib import Path

import logic
import matplotlib
import matplotlib.dates
import pandas as pd
from shiny import App, reactive, render, req, ui

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams.update({"font.size": 9, "axes.titlesize": 9})

SAMPLE = Path(__file__).parent / "data" / "sales.csv"
BLUE, GREY, RED = "#2563eb", "#9ca3af", "#dc2626"

app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.input_date_range("dates", "Period", start="2026-08-01", end="2026-10-31"),
        ui.input_radio_buttons(
            "compare",
            "Compare with",
            {"previous": "Previous period", "year": "Same period last year"},
        ),
        ui.input_checkbox_group("regions", "Regions", choices=[]),
        ui.input_checkbox_group("categories", "Categories", choices=[]),
        ui.hr(),
        ui.input_file("upload", "Use your own data (CSV)", accept=[".csv"]),
        ui.download_link("template", "Download a template"),
        width=290,
    ),
    ui.output_ui("notice"),
    ui.layout_column_wrap(
        ui.value_box("Revenue", ui.output_text("kpi_revenue"), ui.output_ui("delta_revenue")),
        ui.value_box("Orders", ui.output_text("kpi_orders"), ui.output_ui("delta_orders")),
        ui.value_box("Average order value", ui.output_text("kpi_aov"), ui.output_ui("delta_aov")),
        ui.value_box(
            "On-time delivery", ui.output_text("kpi_ontime"), ui.output_ui("delta_ontime")
        ),
        ui.value_box("Return rate", ui.output_text("kpi_returns"), ui.output_ui("delta_returns")),
        width=1 / 5,
        fill=False,
    ),
    ui.navset_card_tab(
        ui.nav_panel("Trend", ui.output_plot("trend_plot", height="380px")),
        ui.nav_panel(
            "Breakdown",
            ui.layout_columns(
                ui.input_radio_buttons(
                    "by", None, {"region": "By region", "category": "By category"}, inline=True
                ),
                ui.download_button("download_data", "Download filtered data"),
                col_widths=(8, 4),
            ),
            ui.output_data_frame("breakdown_table"),
        ),
        ui.nav_panel("Delivery alerts", ui.output_ui("alerts")),
        ui.nav_panel(
            "Forecast",
            ui.output_plot("forecast_plot", height="360px"),
            ui.output_ui("forecast_text"),
        ),
    ),
    title="Sales & Operations Dashboard",
    fillable=False,
)


def money(value: float) -> str:
    if pd.isna(value):
        return "–"
    if abs(value) >= 1e6:
        return f"£{value / 1e6:.2f}m"
    if abs(value) >= 1e3:
        return f"£{value / 1e3:.1f}k"
    return f"£{value:,.2f}"


def delta_tag(value: float | None, rate: bool, higher_is_better: bool, label: str) -> ui.Tag:
    if value is None:
        return ui.span("No comparison data", class_="text-muted small")
    shown = round(value, 1) if rate else round(value * 100, 1)
    text = f"{abs(shown):.1f} pts" if rate else f"{abs(shown):.1f}%"
    if shown == 0:
        return ui.span(f"No change vs {label}", style="color:#6b7280;font-size:0.85rem")
    good = shown > 0 if higher_is_better else shown < 0
    arrow = "▲" if shown > 0 else "▼"
    text = f"{arrow} {text}"
    colour = "#15803d" if good else RED
    return ui.span(f"{text} vs {label}", style=f"color:{colour};font-size:0.85rem")


def server(input, output, session):
    upload_error = reactive.value("")

    @reactive.calc
    def data() -> pd.DataFrame:
        file = input.upload()
        if file:
            try:
                frame = logic.load_sales(file[0]["datapath"])
                upload_error.set("")
                return frame
            except logic.DataError as error:
                upload_error.set(str(error))
        return logic.load_sales(SAMPLE)

    @reactive.calc
    def using_sample() -> bool:
        return not input.upload() or bool(upload_error())

    @reactive.effect
    def _refresh_choices():
        frame = data()
        regions = sorted(frame["region"].unique())
        categories = sorted(frame["category"].unique())
        ui.update_checkbox_group("regions", choices=regions, selected=regions)
        ui.update_checkbox_group("categories", choices=categories, selected=categories)
        start, end = frame["date"].min(), frame["date"].max()
        if not using_sample():
            ui.update_date_range(
                "dates", start=max(start, end - pd.Timedelta(days=90)), end=end, min=start, max=end
            )

    @reactive.calc
    def current() -> pd.DataFrame:
        req(input.regions(), input.categories(), input.dates())
        start, end = input.dates()
        return logic.filter_sales(data(), start, end, input.regions(), input.categories())

    @reactive.calc
    def previous() -> pd.DataFrame:
        req(input.regions(), input.categories(), input.dates())
        start, end = logic.comparison_window(*input.dates(), input.compare())
        return logic.filter_sales(data(), start, end, input.regions(), input.categories())

    @reactive.calc
    def figures() -> tuple[dict, dict]:
        return logic.kpis(current()), logic.kpis(previous())

    def label() -> str:
        return "previous period" if input.compare() == "previous" else "last year"

    @render.ui
    def notice():
        if upload_error():
            return ui.div(
                ui.strong("Your file was not used. "),
                upload_error(),
                " Showing the sample data instead.",
                class_="alert alert-danger",
                role="alert",
            )
        if using_sample():
            return ui.div(
                "Sample data: every figure is invented. Upload a CSV to see your own.",
                class_="alert alert-info py-2",
            )
        return None

    @render.text
    def kpi_revenue():
        return money(figures()[0]["revenue"])

    @render.text
    def kpi_orders():
        return f"{figures()[0]['orders']:,.0f}"

    @render.text
    def kpi_aov():
        return money(figures()[0]["average_order_value"])

    @render.text
    def kpi_ontime():
        value = figures()[0]["on_time_rate"]
        return "–" if pd.isna(value) else f"{value:.1%}"

    @render.text
    def kpi_returns():
        value = figures()[0]["return_rate"]
        return "–" if pd.isna(value) else f"{value:.1%}"

    def delta(key: str, rate: bool, higher_is_better: bool = True) -> ui.Tag:
        now, before = figures()
        return delta_tag(logic.change(now[key], before[key], rate), rate, higher_is_better, label())

    @render.ui
    def delta_revenue():
        return delta("revenue", False)

    @render.ui
    def delta_orders():
        return delta("orders", False)

    @render.ui
    def delta_aov():
        return delta("average_order_value", False)

    @render.ui
    def delta_ontime():
        return delta("on_time_rate", True)

    @render.ui
    def delta_returns():
        return delta("return_rate", True, higher_is_better=False)

    @render.plot(alt="Weekly revenue for the selected and comparison periods")
    def trend_plot():
        start, end = input.dates()
        now = logic.weekly(current(), start, end)
        before = logic.weekly(previous(), *logic.comparison_window(start, end, input.compare()))
        if now.empty:
            fig, ax = plt.subplots(figsize=(11, 3.8))
            ax.text(
                0.5,
                0.5,
                "Select at least one full week (Monday to Sunday).",
                ha="center",
                va="center",
                transform=ax.transAxes,
            )
            ax.set_axis_off()
            return fig
        fig, ax = plt.subplots(figsize=(11, 3.8))
        ax.plot(
            range(len(now)),
            now["revenue"] / 1e3,
            color=BLUE,
            lw=2.2,
            marker="o",
            ms=3,
            label="Selected period",
        )
        if not before.empty:
            ax.plot(
                range(len(before)),
                before["revenue"] / 1e3,
                color=GREY,
                lw=1.6,
                ls="--",
                label=label().capitalize(),
            )
        ticks = range(0, len(now), max(1, -(-len(now) // 10)))
        ax.set_xticks(list(ticks), [now["week"].iloc[i].strftime("%d %b") for i in ticks])
        ax.set_ylabel("Weekly revenue (£k)")
        ax.set_xlabel("Week ending (comparison weeks are aligned by position)")
        ax.set_ylim(bottom=0)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(frameon=False)
        fig.tight_layout()
        return fig

    @render.data_frame
    def breakdown_table():
        table = logic.breakdown(current(), previous(), input.by())
        shown = table.copy()
        shown["Revenue"] = shown["Revenue"].map(money)
        for column in ("Share", "On-time rate", "Return rate"):
            shown[column] = shown[column].map(lambda v: "–" if pd.isna(v) else f"{v:.1%}")
        shown["Revenue change"] = shown["Revenue change"].map(
            lambda v: "–" if pd.isna(v) else f"{v:+.1%}"
        )
        shown["Orders"] = shown["Orders"].map(lambda v: f"{v:,.0f}")
        return render.DataGrid(shown, width="100%")

    @render.download(filename=lambda: f"sales_{input.dates()[0]}_{input.dates()[1]}.csv")
    def download_data():
        buffer = io.StringIO()
        frame = current().copy()
        frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
        frame.to_csv(buffer, index=False)
        yield buffer.getvalue()

    @render.download(filename="sales_template.csv")
    def template():
        yield logic.template_csv()

    @render.ui
    def alerts():
        start, end = input.dates()
        lookback = logic.filter_sales(
            data(),
            pd.Timestamp(start) - pd.Timedelta(weeks=9),
            end,
            input.regions(),
            input.categories(),
        )
        found = logic.delivery_alerts(lookback, since=start)
        if not found:
            return ui.p(
                "No region's weekly on-time delivery fell 5 or more points below its "
                "previous 8-week average in this period."
            )
        return ui.div(
            ui.p(f"{len(found)} week(s) flagged, newest first."),
            ui.tags.ul([ui.tags.li(alert.message) for alert in found]),
        )

    @reactive.calc
    def forecast():
        frame = data()
        req(input.regions(), input.categories())
        selected = logic.filter_sales(
            frame, frame["date"].min(), frame["date"].max(), input.regions(), input.categories()
        )
        try:
            return logic.complete_weeks(selected), logic.forecast_with_backtest(
                logic.complete_weeks(selected)
            )
        except ValueError as error:
            return None, str(error)

    @render.plot(alt="Weekly revenue history and a 12-week forecast with an 80% range")
    def forecast_plot():
        history, result = forecast()
        req(history is not None)
        recent = history.tail(52)
        fig, ax = plt.subplots(figsize=(11, 3.6))
        ax.plot(recent["week"], recent["revenue"] / 1e3, color=GREY, lw=1.6, label="Actual")
        ax.plot(result.weeks, result.point / 1e3, color=BLUE, lw=2.2, label="Forecast")
        ax.fill_between(
            result.weeks,
            result.low / 1e3,
            result.high / 1e3,
            color=BLUE,
            alpha=0.15,
            label="80% range",
        )
        ax.set_ylabel("Weekly revenue (£k)")
        ax.set_ylim(bottom=0)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(frameon=False, loc="lower left")
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %Y"))
        fig.tight_layout()
        return fig

    @render.ui
    def forecast_text():
        history, result = forecast()
        if history is None:
            return ui.div(f"A forecast needs more history: {result}.", class_="alert alert-warning")
        better = result.model_mape < result.naive_mape
        return ui.div(
            ui.p(
                f"Next 12 weeks, for the selected regions and categories: "
                f"{money(result.point.sum())} expected, with an 80% range of "
                f"{money(result.low.sum())} to {money(result.high.sum())}."
            ),
            ui.p(
                f"Tested on {result.origins} past starting points, the forecast was off by "
                f"{result.model_mape:.1%} a week on average, against "
                f"{result.naive_mape:.1%} for simply repeating the last week. "
                + ("It beat that baseline." if better else "It did not beat that baseline."),
                class_="text-muted",
            ),
            ui.p(
                "Method: last year's same weeks, scaled by the latest 13 weeks' growth. "
                "The period selection above does not apply to this tab.",
                class_="text-muted small",
            ),
        )


app = App(app_ui, server)
