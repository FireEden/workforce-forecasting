"""
generate_data.py
-----------------
Creates a realistic (but fake) monthly headcount dataset for a company,
broken down by department. Real HR data is private, so we simulate data
that behaves the way real workforce data does: it grows over time, people
get hired and leave (attrition), hiring speeds up and slows down with the
seasons, and each department has its own average salary.

Running this file writes a CSV to: data/headcount_data.csv

Why this matters for the project:
The forecasting later only makes sense if the data has realistic patterns
(a trend + seasonality + some randomness). This script bakes those in on
purpose so the forecasting models have something meaningful to learn.
"""

import numpy as np
import pandas as pd
import os

# We set a "random seed" so that the random numbers come out the same way
# every time we run this. That makes the project reproducible: anyone who
# runs it gets the exact same dataset we did.
np.random.seed(42)


# ----------------------------------------------------------------------
# Cost loading rates: turning base salary into "fully loaded" cost
# ----------------------------------------------------------------------
# An employee costs the company far more than their base salary. The extra
# costs are expressed here as a percentage of base salary, using realistic
# industry averages:
#
#   - Payroll taxes (~7.65%): the employer's share of payroll taxes. In the
#     US this is FICA (Social Security + Medicare); in Canada it maps to the
#     employer portion of CPP and EI, which is similar in size.
#   - Health & benefits (~12%): medical, dental, retirement matching,
#     life/disability insurance — the employer-paid portion.
#   - Other overhead (~10%): equipment, software licenses, workspace,
#     onboarding, and training attributable to each person.
#
# Together these add ~30% on top of salary, giving a loading factor of ~1.30.
# That lines up with the common rule of thumb that the fully loaded cost of
# an employee is roughly 1.25x-1.4x their base salary.
PAYROLL_TAX_RATE = 0.0765   # employer payroll taxes as a share of salary
BENEFITS_RATE = 0.12        # health & other benefits as a share of salary
OVERHEAD_RATE = 0.10        # equipment, software, workspace, etc.

# The total loading factor multiplies base salary to get fully loaded cost.
LOADING_FACTOR = 1 + PAYROLL_TAX_RATE + BENEFITS_RATE + OVERHEAD_RATE


# ----------------------------------------------------------------------
# 1. Basic setup: what departments exist, and the time range we cover
# ----------------------------------------------------------------------

# Each department has a starting headcount, a rough monthly growth rate
# (how fast it tends to hire), a monthly attrition rate (the % of people
# who leave each month), and an average annual salary.
# Note on the numbers below:
# We've deliberately set Engineering to grow fast (high growth, low attrition)
# while the other departments stay roughly flat (growth and attrition close to
# each other). This is realistic for a tech-driven company AND it makes the
# forecast charts more interesting: one clear upward trend, the rest stable.
departments = {
    # name        start  growth  attrition  avg_salary
    "Engineering":  {"start": 40, "growth": 0.035, "attrition": 0.008, "salary": 130000},
    "Sales":        {"start": 30, "growth": 0.016, "attrition": 0.013, "salary": 95000},
    "Marketing":    {"start": 15, "growth": 0.011, "attrition": 0.012, "salary": 85000},
    "Finance":      {"start": 12, "growth": 0.008, "attrition": 0.008, "salary": 100000},
    "Operations":   {"start": 25, "growth": 0.010, "attrition": 0.011, "salary": 75000},
}

# We'll generate 4 years of monthly data (48 months).
# pd.date_range builds a list of month-start dates for us.
start_date = "2021-01-01"
n_months = 48
dates = pd.date_range(start=start_date, periods=n_months, freq="MS")  # "MS" = Month Start


# ----------------------------------------------------------------------
# 2. A helper that creates a seasonal "hiring multiplier" for each month
# ----------------------------------------------------------------------

def seasonal_factor(month):
    """
    Returns a number that nudges hiring up or down depending on the month.

    Real companies don't hire evenly all year. A common pattern:
      - Strong hiring early in the year (fresh budgets in Q1)
      - A summer slowdown (vacations, fewer candidates available)
      - A small bump in the autumn

    We model that with a smooth wave (a cosine curve) so the effect
    rises and falls gradually instead of jumping around.
    """
    # month is 1-12. We turn it into an angle around a circle (0 to 2*pi)
    # so December connects smoothly back to January.
    angle = 2 * np.pi * (month - 1) / 12
    # The cosine peaks in January and dips mid-year. We scale it down to
    # a +/- 30% effect so it nudges hiring rather than dominating it.
    return 1 + 0.30 * np.cos(angle)


# ----------------------------------------------------------------------
# 3. Generate the data, department by department, month by month
# ----------------------------------------------------------------------

# We'll collect one row per department per month, then turn it into a table.
rows = []

for dept_name, info in departments.items():
    # Start each department at its given headcount.
    headcount = float(info["start"])

    for date in dates:
        month = date.month

        # --- Hiring for this month ---
        # Base hiring = current headcount * growth rate, then adjusted by
        # the season. So a 40-person team growing 1.8%/month hires ~0.7
        # people in an average month, more in Q1, fewer in summer.
        expected_hires = headcount * info["growth"] * seasonal_factor(month)

        # Real hiring isn't a clean number, so we draw from a Poisson
        # distribution (good for "count of events" like number of hires).
        hires = np.random.poisson(max(expected_hires, 0))

        # --- Attrition (people leaving) for this month ---
        # Expected leavers = current headcount * attrition rate.
        expected_leavers = headcount * info["attrition"]
        leavers = np.random.poisson(max(expected_leavers, 0))

        # --- Update the running headcount ---
        # New headcount = old + hires - leavers. We never let it go below 1.
        headcount = max(headcount + hires - leavers, 1)

        # --- Cost for the month: base salary PLUS loaded costs ---
        # We add a tiny bit of yearly raise drift so salaries creep up ~3%/yr.
        years_elapsed = (date.year - pd.Timestamp(start_date).year)
        adjusted_salary = info["salary"] * (1.03 ** years_elapsed)

        # Monthly base salary cost for the whole department.
        monthly_salary = headcount * adjusted_salary / 12

        # Each loaded cost is a percentage of that base salary cost.
        monthly_payroll_tax = monthly_salary * PAYROLL_TAX_RATE
        monthly_benefits = monthly_salary * BENEFITS_RATE
        monthly_overhead = monthly_salary * OVERHEAD_RATE

        # Total fully loaded monthly cost = base + all the add-ons.
        monthly_total = (
            monthly_salary + monthly_payroll_tax + monthly_benefits + monthly_overhead
        )

        # Save this month's row.
        rows.append({
            "date": date,
            "department": dept_name,
            "headcount": int(round(headcount)),
            "hires": int(hires),
            "leavers": int(leavers),
            "avg_annual_salary": round(adjusted_salary, 2),
            "monthly_salary_cost": round(monthly_salary, 2),
            "monthly_payroll_tax": round(monthly_payroll_tax, 2),
            "monthly_benefits": round(monthly_benefits, 2),
            "monthly_overhead": round(monthly_overhead, 2),
            "monthly_total_cost": round(monthly_total, 2),
        })

# Turn the list of rows into a pandas DataFrame (a table).
df = pd.DataFrame(rows)


# ----------------------------------------------------------------------
# 4. Save the result to a CSV file
# ----------------------------------------------------------------------

# Make sure the "data" folder exists before we try to write into it.
os.makedirs("data", exist_ok=True)

output_path = os.path.join("data", "headcount_data.csv")
df.to_csv(output_path, index=False)

# Print a short summary so we can confirm it worked when we run the script.
print(f"Created dataset with {len(df)} rows.")
print(f"Saved to: {output_path}\n")
print("Preview of the first few rows:")
print(df.head(8).to_string(index=False))
print(f"\nLoading factor applied: {LOADING_FACTOR:.2f}x base salary")
print("(payroll tax + benefits + overhead added on top of salary)\n")
print("Salary vs. fully loaded cost in the final month, by department:")
final_month = df["date"].max()
print(
    df[df["date"] == final_month][
        ["department", "headcount", "monthly_salary_cost", "monthly_total_cost"]
    ].to_string(index=False)
)
