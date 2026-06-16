"""
forecast.py
-----------
The core forecasting logic for the workforce project. Both the notebook and
the Streamlit app import from here, so the logic lives in ONE place.

What this module does:
  1. Forecasts a department's future headcount using TWO methods:
       - "baseline": a simple, explainable trend + seasonal-average method
       - "holt_winters": exponential smoothing that models trend + seasonality
  2. Lets a planner apply per-month manual overrides to hires and leavers,
     in either "add" mode (stack on top of the model) or "replace" mode
     (force a specific value). This is how known future events — a hiring
     class, a freeze, a restructure — get layered onto the statistical forecast.
  3. Converts the headcount forecast into a salary-cost forecast.

Plain-language note on the approach:
We don't forecast headcount directly. Instead we forecast the *drivers* —
hires and leavers each month — and then build headcount up month by month:
    next_headcount = this_headcount + hires - leavers
This is what makes the override feature natural: a planner adjusts the hires
and leavers (the things they actually control), and the headcount follows.
"""

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing


# ----------------------------------------------------------------------
# Cost loading rates: turning base salary into "fully loaded" cost
# ----------------------------------------------------------------------
# These MUST match the rates in generate_data.py so the forecast is on the
# same basis as the history. An employee costs more than their base salary:
#   - Payroll taxes (~7.65%): employer payroll taxes (US FICA / Canadian CPP+EI)
#   - Health & benefits (~12%): medical, dental, retirement match, insurance
#   - Other overhead (~10%): equipment, software, workspace, training
# Together ~30% on top of salary => a loading factor of ~1.30.
PAYROLL_TAX_RATE = 0.0765
BENEFITS_RATE = 0.12
OVERHEAD_RATE = 0.10
LOADING_FACTOR = 1 + PAYROLL_TAX_RATE + BENEFITS_RATE + OVERHEAD_RATE


# ----------------------------------------------------------------------
# Overrides: a small, clear data structure
# ----------------------------------------------------------------------
# An override says: "in this forecast month, change hires (or leavers) to /
# by this number." We represent the full set of overrides as a list of dicts.
#
# Example:
#   [
#     {"month_index": 2, "field": "hires",   "mode": "add",     "value": 5},
#     {"month_index": 5, "field": "leavers", "mode": "replace", "value": 3},
#   ]
#
#   month_index = 0 means the first forecast month, 1 the second, and so on.
#   field       = "hires" or "leavers"
#   mode        = "add" (stack on top of model) or "replace" (force the value)
#   value       = the number to add or the number to force
#
# We keep it as plain dicts so the Streamlit app can build them from simple
# input boxes without needing to know anything about the internals.


def _apply_override(model_value, override_value, mode):
    """Apply ONE override to a single month's model value.

    - "add"     -> model value + override (can be negative to subtract)
    - "replace" -> just the override value, ignoring the model
    Headcount counts can't be negative, so we floor the result at 0.
    """
    if mode == "replace":
        result = override_value
    elif mode == "add":
        result = model_value + override_value
    else:
        raise ValueError(f"Unknown override mode: {mode!r}. Use 'add' or 'replace'.")
    return max(result, 0)


# ----------------------------------------------------------------------
# The two forecasting methods for a single series (hires OR leavers)
# ----------------------------------------------------------------------

def _forecast_series_baseline(series, periods):
    """Simple, explainable forecast: average by calendar month.

    Idea: to predict next March's hires, look at the average of all past
    Marches. This captures seasonality (Q1 high, summer low) without any
    fancy model, and it's trivial for a finance person to sanity-check.
    """
    # series is indexed by date. Group by calendar month (1-12) and average.
    monthly_avg = series.groupby(series.index.month).mean()

    # Build the future dates that come right after the last known date.
    last_date = series.index[-1]
    future_dates = pd.date_range(
        start=last_date + pd.offsets.MonthBegin(1), periods=periods, freq="MS"
    )

    # For each future month, look up that calendar month's historical average.
    preds = [monthly_avg.get(d.month, series.mean()) for d in future_dates]
    return pd.Series(preds, index=future_dates)


def _forecast_series_holt_winters(series, periods):
    """Holt-Winters exponential smoothing: models trend + seasonality together.

    More capable than the baseline (it learns a trend AND a repeating yearly
    pattern and weights recent data more heavily), but still interpretable —
    it's a classic, widely trusted forecasting method, not a black box.
    """
    # We need at least two full seasonal cycles (24 months) for a yearly
    # seasonal model to be meaningful. If we don't have that, fall back to
    # the baseline so the function never crashes on short data.
    if len(series) < 24:
        return _forecast_series_baseline(series, periods)

    # Tell the series its dates are monthly ("MS" = month start). This isn't
    # required, but it silences a harmless warning and is good practice.
    series = series.asfreq("MS")

    # seasonal_periods=12 -> a yearly cycle in monthly data.
    # trend/seasonal="add" -> additive trend and seasonality (simplest, stable).
    model = ExponentialSmoothing(
        series, trend="add", seasonal="add", seasonal_periods=12
    ).fit()

    forecast = model.forecast(periods)
    # Hires/leavers can't be negative, so floor at 0.
    return forecast.clip(lower=0)


# ----------------------------------------------------------------------
# The main entry point
# ----------------------------------------------------------------------

def forecast_department(
    df,
    department,
    periods=12,
    method="holt_winters",
    overrides=None,
    payroll_tax_rate=PAYROLL_TAX_RATE,
    benefits_rate=BENEFITS_RATE,
    overhead_rate=OVERHEAD_RATE,
):
    """Forecast one department's headcount and salary cost.

    Parameters
    ----------
    df : the full headcount DataFrame (from headcount_data.csv)
    department : which department to forecast, e.g. "Engineering"
    periods : how many future months to project (default 12)
    method : "baseline" or "holt_winters"
    overrides : optional list of override dicts (see top of file)
    payroll_tax_rate, benefits_rate, overhead_rate : the cost loading rates,
        as fractions of base salary. They default to the standard module
        constants, but callers (like the Streamlit app) can pass custom
        values so a planner can test different benefit/overhead assumptions.

    Returns
    -------
    A DataFrame with one row per forecast month, containing the projected
    hires, leavers, headcount, and monthly salary cost.
    """
    if overrides is None:
        overrides = []

    # --- Pull out just this department's history, sorted by date ---
    dept_df = (
        df[df["department"] == department]
        .sort_values("date")
        .set_index("date")
    )

    # The two driver series we forecast separately.
    hires_series = dept_df["hires"].astype(float)
    leavers_series = dept_df["leavers"].astype(float)

    # Pick the forecasting method.
    if method == "baseline":
        forecast_fn = _forecast_series_baseline
    elif method == "holt_winters":
        forecast_fn = _forecast_series_holt_winters
    else:
        raise ValueError(f"Unknown method: {method!r}. Use 'baseline' or 'holt_winters'.")

    # Forecast hires and leavers into the future.
    hires_fc = forecast_fn(hires_series, periods).round()
    leavers_fc = forecast_fn(leavers_series, periods).round()

    # --- Apply per-month overrides on top of the model's predictions ---
    # We turn the forecasts into plain lists so we can edit month by month.
    hires_list = hires_fc.tolist()
    leavers_list = leavers_fc.tolist()

    for ov in overrides:
        i = ov["month_index"]
        # Skip overrides that point past the forecast window.
        if i < 0 or i >= periods:
            continue
        if ov["field"] == "hires":
            hires_list[i] = _apply_override(hires_list[i], ov["value"], ov["mode"])
        elif ov["field"] == "leavers":
            leavers_list[i] = _apply_override(leavers_list[i], ov["value"], ov["mode"])
        else:
            raise ValueError(f"Unknown field: {ov['field']!r}. Use 'hires' or 'leavers'.")

    # --- Build headcount forward, month by month ---
    # Start from the last known actual headcount.
    current_headcount = float(dept_df["headcount"].iloc[-1])

    # Use the most recent salary as the basis, and keep the ~3%/yr drift going.
    last_salary = float(dept_df["avg_annual_salary"].iloc[-1])

    headcounts = []
    salary_costs, payroll_taxes, benefits, overheads, total_costs = [], [], [], [], []
    for m in range(periods):
        # Headcount recursion: add hires, subtract leavers, floor at 0.
        current_headcount = max(current_headcount + hires_list[m] - leavers_list[m], 0)
        headcounts.append(int(round(current_headcount)))

        # Salary drifts up ~3% per year => about 0.247% per month.
        drifted_salary = last_salary * (1.03 ** (m / 12))

        # Base monthly salary cost for the department.
        salary_cost = current_headcount * drifted_salary / 12

        # The loaded costs, each a percentage of base salary. These use the
        # rates passed into the function (defaulting to the standard constants),
        # so the app can let a planner adjust benefit/overhead assumptions.
        payroll_tax = salary_cost * payroll_tax_rate
        benefit = salary_cost * benefits_rate
        overhead = salary_cost * overhead_rate
        total = salary_cost + payroll_tax + benefit + overhead

        salary_costs.append(round(salary_cost, 2))
        payroll_taxes.append(round(payroll_tax, 2))
        benefits.append(round(benefit, 2))
        overheads.append(round(overhead, 2))
        total_costs.append(round(total, 2))

    # --- Assemble the result table ---
    result = pd.DataFrame({
        "date": hires_fc.index,
        "department": department,
        "hires": [int(x) for x in hires_list],
        "leavers": [int(x) for x in leavers_list],
        "headcount": headcounts,
        "monthly_salary_cost": salary_costs,
        "monthly_payroll_tax": payroll_taxes,
        "monthly_benefits": benefits,
        "monthly_overhead": overheads,
        "monthly_total_cost": total_costs,
    })
    return result
