"""
streamlit_app.py
----------------
A clickable interface for the workforce forecasting tool. It wraps the same
forecast_department() function the notebooks use.

Two views (tabs):
  - Department: forecast one department, with per-month adjustments for known
    future events (a hiring class, a freeze, a restructure).
  - Company: a consolidated rollup of ALL departments, where the forecast and
    its source-breakdown reflect every department's adjustments added together.

Adjustments are saved per-department in session state, so the ones you set for
Engineering stay put when you switch to Sales — and the Company tab can sum
them all.

Run it from the project root with:
    streamlit run app/streamlit_app.py
"""

import sys
import os

# Make the src folder importable so we can use our shared forecasting logic.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st

from forecast import (
    forecast_department,
    PAYROLL_TAX_RATE,
    BENEFITS_RATE,
    OVERHEAD_RATE,
)


# ----------------------------------------------------------------------
# Page setup
# ----------------------------------------------------------------------
st.set_page_config(page_title="Workforce Forecasting", page_icon="📊", layout="wide")

st.title("Workforce Headcount & Cost Forecasting")
st.caption(
    "Project future headcount and fully loaded cost. Plan at the department "
    "level, then see it all roll up at the company level."
)


# ----------------------------------------------------------------------
# Load the data once and cache it (so it doesn't reload on every click)
# ----------------------------------------------------------------------
@st.cache_data
def load_data():
    path = os.path.join(PROJECT_ROOT, "data", "headcount_data.csv")
    return pd.read_csv(path, parse_dates=["date"])


df = load_data()
departments = sorted(df["department"].unique())

# Map the user-facing mode labels to the values the forecast function expects.
MODE_LABELS = {"Add to forecast": "add", "Replace value": "replace"}


# ----------------------------------------------------------------------
# Session state: store each department's adjustments so they persist
# ----------------------------------------------------------------------
# "dept_overrides" is a dict like {"Engineering": [ {..override..}, ... ], ...}.
# Keeping it here means adjustments survive switching departments and can be
# summed for the company view.
if "dept_overrides" not in st.session_state:
    st.session_state.dept_overrides = {d: [] for d in departments}


# ----------------------------------------------------------------------
# Sidebar: shared settings (apply to both tabs)
# ----------------------------------------------------------------------
st.sidebar.header("Forecast settings")

horizon = st.sidebar.slider("Months to forecast", min_value=3, max_value=24, value=12)

method_label = st.sidebar.radio(
    "Forecasting method",
    ["Holt-Winters (trend + seasonality)", "Baseline (seasonal average)"],
)
method = "holt_winters" if method_label.startswith("Holt") else "baseline"

st.sidebar.markdown("---")
st.sidebar.subheader("Cost loading rates")
st.sidebar.caption(
    "An employee costs more than base salary. Adjust the add-on rates below "
    "to test different assumptions. These apply to every department."
)
payroll_tax = st.sidebar.slider(
    "Payroll taxes (%)", 0.0, 20.0, PAYROLL_TAX_RATE * 100, step=0.05
) / 100
benefits = st.sidebar.slider(
    "Health & benefits (%)", 0.0, 40.0, BENEFITS_RATE * 100, step=0.5
) / 100
overhead = st.sidebar.slider(
    "Other overhead (%)", 0.0, 40.0, OVERHEAD_RATE * 100, step=0.5
) / 100
loading_factor = 1 + payroll_tax + benefits + overhead
st.sidebar.metric("Loading factor", f"{loading_factor:.2f}x salary")


# ----------------------------------------------------------------------
# Helper: build the month labels for a given horizon
# ----------------------------------------------------------------------
def month_labels_for(dept):
    last_date = df[df["department"] == dept]["date"].max()
    future = pd.date_range(
        start=last_date + pd.offsets.MonthBegin(1), periods=horizon, freq="MS"
    )
    return [f"Month {i+1} — {d.strftime('%b %Y')}" for i, d in enumerate(future)]


# ----------------------------------------------------------------------
# Helper: run the three-layer decomposition for one department
# ----------------------------------------------------------------------
# Returns (pure, add_only, final) forecast DataFrames so we can show how much
# of the forecast comes from the method vs. the "add" vs. the "replace" events.
def decompose(dept, overrides):
    add_overrides = [o for o in overrides if o["mode"] == "add"]
    common = dict(
        periods=horizon, method=method,
        payroll_tax_rate=payroll_tax, benefits_rate=benefits, overhead_rate=overhead,
    )
    pure = forecast_department(df, dept, overrides=None, **common)
    add_only = forecast_department(df, dept, overrides=add_overrides, **common)
    final = forecast_department(df, dept, overrides=overrides, **common)
    return pure, add_only, final


# ----------------------------------------------------------------------
# Helper: draw the "forecast sources" breakdown chart + table
# ----------------------------------------------------------------------
# Works for both a single department and the summed company view, because it
# just takes three already-computed forecast frames (pure, add_only, final).
def render_breakdown(pure, add_only, final, title):
    st.markdown("#### Where the forecast comes from")
    st.caption(
        "How the adjustments build on top of the statistical forecast. The "
        "shaded bands are the headcount added (or removed) by known events."
    )
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(pure["date"], pure["headcount"],
            label="1. Forecast method only", marker="o", ms=4, color="#1f77b4")
    ax.plot(add_only["date"], add_only["headcount"],
            label="2. + known events (added)", marker="s", ms=4, color="#2ca02c")
    ax.plot(final["date"], final["headcount"],
            label="3. Final (+ replace-value events)", marker="^", ms=5, color="#d62728")
    ax.fill_between(pure["date"], pure["headcount"], add_only["headcount"],
                    alpha=0.15, color="#2ca02c")
    ax.fill_between(add_only["date"], add_only["headcount"], final["headcount"],
                    alpha=0.15, color="#d62728")
    ax.set_title(title)
    ax.set_xlabel("Date"); ax.set_ylabel("Headcount")
    ax.legend(); ax.grid(alpha=0.3)
    st.pyplot(fig)

    method_hc = int(pure["headcount"].iloc[-1])
    add_effect = int(add_only["headcount"].iloc[-1]) - method_hc
    replace_effect = int(final["headcount"].iloc[-1]) - int(add_only["headcount"].iloc[-1])
    final_hc = int(final["headcount"].iloc[-1])
    summary = pd.DataFrame({
        "Source": [
            "Forecast method only", "Known events (added)",
            "Known events (replace value)", "Final forecast",
        ],
        "Headcount effect (final month)": [
            str(method_hc), f"{add_effect:+d}", f"{replace_effect:+d}", str(final_hc),
        ],
    })
    st.table(summary)


# ----------------------------------------------------------------------
# Helper: the two standard charts (headcount, cost) for any forecast
# ----------------------------------------------------------------------
def render_headcount_chart(history, final, title):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(history["date"], history["headcount"], label="History", color="black")
    ax.plot(final["date"], final["headcount"],
            label="Forecast", ls="--", marker="o", ms=4, color="#1f77b4")
    ax.axvline(history["date"].iloc[-1], color="gray", ls=":", alpha=0.6)
    ax.set_title(title)
    ax.set_xlabel("Date"); ax.set_ylabel("Headcount")
    ax.legend(); ax.grid(alpha=0.3)
    st.pyplot(fig)


def render_cost_chart(history, final, title):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(history["date"], history["monthly_total_cost"] / 1e6,
            label="History", color="black")
    ax.plot(final["date"], final["monthly_total_cost"] / 1e6,
            label="Forecast", ls="--", marker="o", ms=4, color="green")
    ax.axvline(history["date"].iloc[-1], color="gray", ls=":", alpha=0.6)
    ax.set_title(title)
    ax.set_xlabel("Date"); ax.set_ylabel("Cost ($ millions)")
    ax.legend(); ax.grid(alpha=0.3)
    st.pyplot(fig)


# ----------------------------------------------------------------------
# Helper: sum a list of forecast frames into one consolidated frame
# ----------------------------------------------------------------------
# Each department's forecast has the same dates and columns, so we group by
# date and sum the numeric columns to get a company-wide total.
def sum_forecasts(frames):
    combined = pd.concat(frames)
    numeric = ["headcount", "hires", "leavers", "monthly_salary_cost",
               "monthly_payroll_tax", "monthly_benefits", "monthly_overhead",
               "monthly_total_cost"]
    return combined.groupby("date", as_index=False)[numeric].sum()


# ======================================================================
# TABS
# ======================================================================
tab_dept, tab_company = st.tabs(["📋 Department", "🏢 Company (consolidated)"])


# ----------------------------------------------------------------------
# DEPARTMENT TAB
# ----------------------------------------------------------------------
with tab_dept:
    department = st.selectbox("Department", departments, key="dept_picker")

    st.subheader("Adjustments for known events (optional)")
    st.write(
        "Layer planned changes on top of the statistical forecast — a hiring "
        "class, a freeze, a restructure. Each row targets one forecast month. "
        "Adjustments are saved per department and roll up into the Company tab."
    )

    labels = month_labels_for(department)

    # The durable store of THIS department's adjustments. Streamlit can garbage-
    # collect a widget's state when the widget isn't rendered (e.g. while you're
    # viewing another department), so we don't rely on the widgets to remember
    # values. Instead we keep the real data here and use it to seed the widgets
    # each time they're drawn — that's what makes adjustments truly persist.
    stored = st.session_state.dept_overrides.get(department, [])

    # How many adjustment rows for THIS department, defaulting to however many
    # we have stored.
    count_key = f"num_overrides_{department}"
    if count_key not in st.session_state:
        st.session_state[count_key] = len(stored)

    num_overrides = st.number_input(
        "Number of adjustments", min_value=0, max_value=10, step=1, key=count_key,
    )

    # Reverse lookup so we can show the stored mode value as the right label.
    MODE_LABELS_REV = {v: k for k, v in MODE_LABELS.items()}
    field_options = ["hires", "leavers"]
    mode_options = list(MODE_LABELS.keys())

    overrides = []
    for n in range(int(num_overrides)):
        # If we have a stored adjustment for this row, use it to seed the
        # widgets' starting values; otherwise fall back to sensible defaults.
        prev = stored[n] if n < len(stored) else None
        def_month = prev["month_index"] if prev else 0
        def_month = min(def_month, len(labels) - 1)  # guard if horizon shrank
        def_field = prev["field"] if prev else "hires"
        def_mode_label = MODE_LABELS_REV[prev["mode"]] if prev else mode_options[0]
        def_value = prev["value"] if prev else 0

        c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
        with c1:
            month_choice = st.selectbox(
                "When", labels, index=def_month, key=f"month_{department}_{n}"
            )
            month_index = labels.index(month_choice)
        with c2:
            field = st.selectbox(
                "What", field_options, index=field_options.index(def_field),
                key=f"field_{department}_{n}",
                help="'hires' adds people; 'leavers' removes them (a positive "
                     "number of leavers reduces headcount).",
            )
        with c3:
            mode_label = st.selectbox(
                "How", mode_options, index=mode_options.index(def_mode_label),
                key=f"mode_{department}_{n}",
                help="'Add to forecast' adjusts the model's number up or down. "
                     "'Replace value' forces this month's number to exactly the "
                     "value you enter.",
            )
            mode = MODE_LABELS[mode_label]
        with c4:
            value = st.number_input(
                "Value", value=def_value, step=1, key=f"value_{department}_{n}",
                help="For 'leavers', a value of 4 removes 4 people — no need "
                     "for a negative number.",
            )
        overrides.append(
            {"month_index": month_index, "field": field, "mode": mode, "value": value}
        )

    # Save this department's adjustments so the Company tab can use them.
    st.session_state.dept_overrides[department] = overrides

    # Forecast this department, three ways for the breakdown.
    pure_fc, add_only_fc, final_fc = decompose(department, overrides)
    history = df[df["department"] == department].sort_values("date")

    # Headline numbers.
    st.subheader(f"{department}: {horizon}-month forecast")
    c1, c2, c3 = st.columns(3)
    c1.metric("Current headcount", int(history["headcount"].iloc[-1]))
    c2.metric(
        "Forecast headcount", int(final_fc["headcount"].iloc[-1]),
        delta=int(final_fc["headcount"].iloc[-1] - history["headcount"].iloc[-1]),
    )
    c3.metric(
        "Total loaded cost (forecast period)",
        f"${final_fc['monthly_total_cost'].sum()/1e6:.2f}M",
    )

    render_headcount_chart(history, final_fc, f"{department}: Headcount")

    if len(overrides) > 0:
        render_breakdown(pure_fc, add_only_fc, final_fc, f"{department}: Forecast Sources")

    render_cost_chart(history, final_fc, f"{department}: Fully Loaded Monthly Cost")

    st.subheader("Forecast detail")
    st.dataframe(final_fc, width="stretch")
    csv = final_fc.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download this forecast as CSV", data=csv,
        file_name=f"{department}_forecast.csv", mime="text/csv",
    )


# ----------------------------------------------------------------------
# COMPANY TAB (consolidated rollup of all departments)
# ----------------------------------------------------------------------
with tab_company:
    st.subheader("Company-wide forecast (all departments combined)")
    st.write(
        "This consolidates every department. The forecast and its breakdown "
        "below reflect all the adjustments you've set on the Department tab, "
        "summed across the whole company."
    )

    # Forecast every department three ways, then sum each layer across all
    # departments. This is what makes the company breakdown reflect every
    # department's adjustments together.
    pure_frames, add_frames, final_frames = [], [], []
    for d in departments:
        d_overrides = st.session_state.dept_overrides.get(d, [])
        p, a, f = decompose(d, d_overrides)
        pure_frames.append(p)
        add_frames.append(a)
        final_frames.append(f)

    company_pure = sum_forecasts(pure_frames)
    company_add = sum_forecasts(add_frames)
    company_final = sum_forecasts(final_frames)

    # Company history = sum of all departments' actuals by date.
    company_history = (
        df.groupby("date", as_index=False)[["headcount", "monthly_total_cost"]].sum()
        .sort_values("date")
    )

    # How many departments currently have adjustments? (for a helpful note)
    depts_with_adj = [
        d for d in departments if len(st.session_state.dept_overrides.get(d, [])) > 0
    ]

    # Headline numbers.
    c1, c2, c3 = st.columns(3)
    c1.metric("Current total headcount", int(company_history["headcount"].iloc[-1]))
    c2.metric(
        "Forecast total headcount", int(company_final["headcount"].iloc[-1]),
        delta=int(company_final["headcount"].iloc[-1] - company_history["headcount"].iloc[-1]),
    )
    c3.metric(
        "Total loaded cost (forecast period)",
        f"${company_final['monthly_total_cost'].sum()/1e6:.2f}M",
    )

    if depts_with_adj:
        st.info(
            "Adjustments currently applied from: " + ", ".join(depts_with_adj)
        )
    else:
        st.info(
            "No department adjustments set yet. Add some on the Department tab "
            "to see them roll up here."
        )

    render_headcount_chart(company_history, company_final, "Company: Total Headcount")

    # The breakdown always shows for the company view (it's the whole point),
    # summed across departments.
    render_breakdown(company_pure, company_add, company_final, "Company: Forecast Sources (all departments)")

    render_cost_chart(company_history, company_final, "Company: Fully Loaded Monthly Cost")

    st.subheader("Consolidated forecast detail")
    st.dataframe(company_final, width="stretch")
    csv_co = company_final.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download company forecast as CSV", data=csv_co,
        file_name="company_forecast.csv", mime="text/csv",
    )
