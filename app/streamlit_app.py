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

department = st.sidebar.selectbox("Department", departments)

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

# We let the user pick how many override rows they want, then render that many.
num_overrides = st.number_input(
    "Number of adjustments", min_value=0, max_value=10, value=0, step=1
)

# Build the month labels (e.g. "Month 1 — Jan 2025") so the user picks real dates.
last_date = df[df["department"] == department]["date"].max()
future_dates = pd.date_range(
    start=last_date + pd.offsets.MonthBegin(1), periods=horizon, freq="MS"
)
month_labels = [f"Month {i+1} — {d.strftime('%b %Y')}" for i, d in enumerate(future_dates)]

overrides = []
for n in range(int(num_overrides)):
    # Lay each override out across four columns for a compact, form-like row.
    c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
    with c1:
        month_choice = st.selectbox(
            "When", month_labels, key=f"month_{n}"
        )
        month_index = month_labels.index(month_choice)
    with c2:
        field = st.selectbox("What", ["hires", "leavers"], key=f"field_{n}")
    with c3:
        mode = st.selectbox(
            "How", ["add", "replace"], key=f"mode_{n}",
            help="'add' stacks on top of the forecast; 'replace' forces the value.",
        )
    with c4:
        value = st.number_input("Value", value=0, step=1, key=f"value_{n}")

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
