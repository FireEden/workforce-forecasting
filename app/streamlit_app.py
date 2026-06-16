"""
streamlit_app.py
----------------
A clickable interface for the workforce forecasting tool. It wraps the same
forecast_department() function the notebooks use, so a planner can:

  - pick a department and forecast horizon
  - choose the forecasting method
  - adjust the cost loading rates (payroll tax, benefits, overhead)
  - add per-month overrides to hires/leavers for known future events
  - see the headcount and fully loaded cost forecast update live

Run it from the project root with:
    streamlit run app/streamlit_app.py
"""

import sys
import os

# Make the src folder importable so we can use our shared forecasting logic.
# (We go up one level from app/ to the project root, then into src/.)
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
    "Project future headcount and fully loaded cost by department. "
    "Adjust assumptions and layer in known future events to build a plan."
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


# ----------------------------------------------------------------------
# Sidebar: all the controls live here
# ----------------------------------------------------------------------
st.sidebar.header("Forecast settings")

# When the department changes, reset the adjustment counter to 0 so one
# department's known-events don't carry over to another. Using an on_change
# callback is the robust way to do this in Streamlit: it runs the reset
# cleanly before the rest of the script re-executes.
def _reset_adjustments():
    st.session_state["num_overrides"] = 0

department = st.sidebar.selectbox(
    "Department", departments, key="department", on_change=_reset_adjustments
)

horizon = st.sidebar.slider(
    "Months to forecast", min_value=3, max_value=24, value=12
)

method_label = st.sidebar.radio(
    "Forecasting method",
    ["Holt-Winters (trend + seasonality)", "Baseline (seasonal average)"],
)
# Map the friendly label back to the value our function expects.
method = "holt_winters" if method_label.startswith("Holt") else "baseline"

st.sidebar.markdown("---")
st.sidebar.subheader("Cost loading rates")
st.sidebar.caption(
    "An employee costs more than base salary. Adjust the add-on rates below "
    "to test different assumptions."
)

# Sliders default to the standard rates from the module, shown as percentages.
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
# Main area: overrides for known future events
# ----------------------------------------------------------------------
st.subheader("Adjustments for known events (optional)")
st.write(
    "Layer planned changes on top of the statistical forecast — a hiring class, "
    "a freeze, a restructure. Each row targets one forecast month."
)

# We let the user pick how many adjustment rows they want, then render that many.
# (When the department changes, an on_change callback resets this to 0 so each
# department's adjustments stay separate.)
# We initialize the value in session state once, then let the widget manage it
# via its key — this avoids mixing a default value with session-state updates.
if "num_overrides" not in st.session_state:
    st.session_state["num_overrides"] = 0

num_overrides = st.number_input(
    "Number of adjustments", min_value=0, max_value=10, step=1,
    key="num_overrides",
)

# Build the month labels (e.g. "Month 1 — Jan 2025") so the user picks real dates.
last_date = df[df["department"] == department]["date"].max()
future_dates = pd.date_range(
    start=last_date + pd.offsets.MonthBegin(1), periods=horizon, freq="MS"
)
month_labels = [f"Month {i+1} — {d.strftime('%b %Y')}" for i, d in enumerate(future_dates)]

# Map the user-facing mode labels to the values the forecast function expects.
# "Add to forecast" -> "add";  "Replace value" -> "replace"
MODE_LABELS = {"Add to forecast": "add", "Replace value": "replace"}

overrides = []
for n in range(int(num_overrides)):
    # Lay each override out across four columns for a compact, form-like row.
    # Keys include the department name so switching departments can't reuse
    # another department's widget values.
    c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
    with c1:
        month_choice = st.selectbox(
            "When", month_labels, key=f"month_{department}_{n}"
        )
        month_index = month_labels.index(month_choice)
    with c2:
        field = st.selectbox(
            "What", ["hires", "leavers"], key=f"field_{department}_{n}",
            help="'hires' adds people; 'leavers' removes them (a positive "
                 "number of leavers reduces headcount).",
        )
    with c3:
        mode_label = st.selectbox(
            "How", list(MODE_LABELS.keys()), key=f"mode_{department}_{n}",
            help="'Add to forecast' adjusts the model's number up or down. "
                 "'Replace value' forces this month's number to exactly the "
                 "value you enter, ignoring the model's prediction.",
        )
        mode = MODE_LABELS[mode_label]
    with c4:
        value = st.number_input(
            "Value", value=0, step=1, key=f"value_{department}_{n}",
            help="For 'leavers', a value of 4 removes 4 people — no need for "
                 "a negative number.",
        )

    overrides.append(
        {"month_index": month_index, "field": field, "mode": mode, "value": value}
    )


# ----------------------------------------------------------------------
# Run the forecast with all the chosen settings
# ----------------------------------------------------------------------
fc = forecast_department(
    df,
    department,
    periods=horizon,
    method=method,
    overrides=overrides,
    payroll_tax_rate=payroll_tax,
    benefits_rate=benefits,
    overhead_rate=overhead,
)

history = df[df["department"] == department].sort_values("date")

# ----------------------------------------------------------------------
# Decompose the forecast into its sources (for the breakdown chart below)
# ----------------------------------------------------------------------
# To show how much of the final forecast comes from the model vs. the known
# events, we run the forecast a few times with different sets of adjustments:
#
#   1. PURE METHOD  - no adjustments at all (just the statistical forecast).
#   2. + ADD events - only the "Add to forecast" adjustments applied.
#   3. FINAL        - everything, including "Replace value" adjustments.
#
# The gaps between these three tell us each source's contribution.
add_overrides = [o for o in overrides if o["mode"] == "add"]

pure_fc = forecast_department(
    df, department, periods=horizon, method=method,
    overrides=None,
    payroll_tax_rate=payroll_tax, benefits_rate=benefits, overhead_rate=overhead,
)
add_only_fc = forecast_department(
    df, department, periods=horizon, method=method,
    overrides=add_overrides,
    payroll_tax_rate=payroll_tax, benefits_rate=benefits, overhead_rate=overhead,
)
# "fc" (computed above) is the FINAL forecast with all adjustments.


# ----------------------------------------------------------------------
# Headline numbers
# ----------------------------------------------------------------------
st.subheader(f"{department}: {horizon}-month forecast")

col1, col2, col3 = st.columns(3)
col1.metric("Current headcount", int(history["headcount"].iloc[-1]))
col2.metric(
    "Forecast headcount",
    int(fc["headcount"].iloc[-1]),
    delta=int(fc["headcount"].iloc[-1] - history["headcount"].iloc[-1]),
)
col3.metric(
    "Total loaded cost (forecast period)",
    f"${fc['monthly_total_cost'].sum()/1e6:.2f}M",
)


# ----------------------------------------------------------------------
# Chart 1: headcount, history + forecast
# ----------------------------------------------------------------------
fig1, ax1 = plt.subplots(figsize=(11, 4.5))
ax1.plot(history["date"], history["headcount"], label="History", color="black")
ax1.plot(
    fc["date"], fc["headcount"],
    label="Forecast", ls="--", marker="o", ms=4, color="#1f77b4",
)
ax1.axvline(history["date"].iloc[-1], color="gray", ls=":", alpha=0.6)
ax1.set_title(f"{department}: Headcount")
ax1.set_xlabel("Date")
ax1.set_ylabel("Headcount")
ax1.legend()
ax1.grid(alpha=0.3)
st.pyplot(fig1)


# ----------------------------------------------------------------------
# Chart 1b: where the forecast comes from (only shown if there are events)
# ----------------------------------------------------------------------
# This breaks the final forecast into its sources so it's clear how much
# is the statistical method vs. the known events you entered.
if len(overrides) > 0:
    st.markdown("#### Where the forecast comes from")
    st.caption(
        "This shows how your adjustments build on top of the statistical "
        "forecast. The shaded bands are the headcount added (or removed) by "
        "your known events."
    )

    fig_bd, ax_bd = plt.subplots(figsize=(11, 4.5))

    # Line 1: pure statistical method.
    ax_bd.plot(pure_fc["date"], pure_fc["headcount"],
               label="1. Forecast method only", marker="o", ms=4, color="#1f77b4")
    # Line 2: after adding the "Add to forecast" events.
    ax_bd.plot(add_only_fc["date"], add_only_fc["headcount"],
               label="2. + known events (added)", marker="s", ms=4, color="#2ca02c")
    # Line 3: final, after "Replace value" events too.
    ax_bd.plot(fc["date"], fc["headcount"],
               label="3. Final (+ replace-value events)", marker="^", ms=5, color="#d62728")

    # Shade the contribution of the "add" events (between line 1 and line 2).
    ax_bd.fill_between(pure_fc["date"], pure_fc["headcount"], add_only_fc["headcount"],
                       alpha=0.15, color="#2ca02c")
    # Shade the contribution of the "replace" events (between line 2 and line 3).
    ax_bd.fill_between(add_only_fc["date"], add_only_fc["headcount"], fc["headcount"],
                       alpha=0.15, color="#d62728")

    ax_bd.set_title(f"{department}: Forecast Sources")
    ax_bd.set_xlabel("Date")
    ax_bd.set_ylabel("Headcount")
    ax_bd.legend()
    ax_bd.grid(alpha=0.3)
    st.pyplot(fig_bd)

    # --- Small summary table of the final-month contributions ---
    method_hc = int(pure_fc["headcount"].iloc[-1])
    add_effect = int(add_only_fc["headcount"].iloc[-1]) - method_hc
    replace_effect = int(fc["headcount"].iloc[-1]) - int(add_only_fc["headcount"].iloc[-1])
    final_hc = int(fc["headcount"].iloc[-1])

    summary = pd.DataFrame({
        "Source": [
            "Forecast method only",
            "Known events (added)",
            "Known events (replace value)",
            "Final forecast",
        ],
        "Headcount effect (final month)": [
            str(method_hc),
            f"{add_effect:+d}",
            f"{replace_effect:+d}",
            str(final_hc),
        ],
    })
    st.table(summary)


# ----------------------------------------------------------------------
# Chart 2: fully loaded monthly cost
# ----------------------------------------------------------------------
fig2, ax2 = plt.subplots(figsize=(11, 4.5))
ax2.plot(history["date"], history["monthly_total_cost"] / 1e6,
         label="History", color="black")
ax2.plot(fc["date"], fc["monthly_total_cost"] / 1e6,
         label="Forecast", ls="--", marker="o", ms=4, color="green")
ax2.axvline(history["date"].iloc[-1], color="gray", ls=":", alpha=0.6)
ax2.set_title(f"{department}: Fully Loaded Monthly Cost")
ax2.set_xlabel("Date")
ax2.set_ylabel("Cost ($ millions)")
ax2.legend()
ax2.grid(alpha=0.3)
st.pyplot(fig2)


# ----------------------------------------------------------------------
# The detailed forecast table + a download button
# ----------------------------------------------------------------------
st.subheader("Forecast detail")
st.dataframe(fc, width="stretch")

# Let the user download their scenario as a CSV.
csv = fc.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download this forecast as CSV",
    data=csv,
    file_name=f"{department}_forecast.csv",
    mime="text/csv",
)
