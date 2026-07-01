# Run .venv\Scripts\python.exe my_tdee.py from root

import csv, datetime as dt
import numpy as np
from estimator.tdee import estimate_tdee

rows = {}
with open("my_log.csv", newline="") as f:
    for r in csv.DictReader(f):
        d = dt.date.fromisoformat(r["date"].strip())
        w = float(r["weight_kg"]) if r["weight_kg"].strip() else np.nan
        c = float(r["calories"]) if r["calories"].strip() else np.nan
        rows[d] = (w, c)

# Build a continuous daily calendar from first to last date, NaN-filling gaps.
start, end = min(rows), max(rows)
days = (end - start).days + 1
# Build a continuous daily calendar from first to last date, NaN-filling gaps.
start, end = min(rows), max(rows)
days = (end - start).days + 1
weight = np.full(days, np.nan)
intake = np.full(days, np.nan)
for d, (w, c) in rows.items():
    i = (d - start).days
    weight[i], intake[i] = w, c

est = estimate_tdee(weight, intake, window_days=28)
if est.tdee is None:
    print(f"Not enough data: {est.status} "
          f"({est.n_valid_weight_days} weigh-ins, {est.n_valid_intake_days} intake days in window)")
else:
    print(f"Estimated TDEE: {est.tdee:.0f} kcal/day  "
          f"95% range [{est.ci_low:.0f}, {est.ci_high:.0f}]  status: {est.status}")
    print(f"(trend {est.slope_kg_per_day*7:+.2f} kg/week, ate {est.mean_intake:.0f} kcal/day avg)")