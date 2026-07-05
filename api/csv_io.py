"""CSV import/export for entries (PRD FR-24 / FR-25, data portability).

Import format
-------------
A **flat** CSV — a header row plus one row per day — as exported by this app or by
popular weight/nutrition apps. There is no universal format, so the parser is
tolerant rather than matched to any one app:

* Header matching is case-insensitive, whitespace-trimmed, and strips a UTF-8 BOM.
* ``date``   — required column; aliases: ``date``, ``day``, ``timestamp``.
* ``weight`` — required column; aliases: ``weight``, ``weight_kg``, ``mass``,
  ``body weight``, and ``Weight (kg)`` / ``Weight (lb)`` / ``Weight (lbs)``.
* ``calories`` — optional; aliases: ``kcal``, ``calories``, ``cals``, ``energy``,
  and ``Energy (kcal)`` / ``Calories (kcal)``.
* Any other columns (body fat, waist, notes, …) are ignored silently.
* Date values may be ISO (``2026-07-04``, with or without a time part),
  numeric ``MM/DD/YYYY`` / ``D/M/YYYY`` (an ambiguous value like ``04/07/2026`` is
  read as US month-first, with a warning), or ``Jul 4 2026`` / ``4 Jul 2026`` style.

Units (data-integrity critical): entries are stored in **kg**. If the weight header
names a unit (``weight_kg``, ``Weight (lbs)`` …) that unit is used — lb values are
converted to kg. If the header is a bare ``weight``/``mass``, the caller's
``assumed_units`` (the user's display-units setting) is applied **and a warning is
emitted** naming the assumption, so a silent kg/lb corruption cannot happen. Value
ranges are never used to guess units.

Rules:

* A row with a missing/unparseable ``date`` is **skipped** with a warning.
* Fully blank rows are skipped silently; a row with no usable weight *and* no
  usable calories is skipped with a warning (nothing to import).
* Blank ``weight``/``calories`` cells become ``None`` (a gap), not zero.
* A single bad row never aborts the file — it becomes a warning.
* Duplicate dates (in the file or already in the DB) **upsert** — the last value
  wins; entries are never duplicated.

**Not supported** (by design): MyFitnessPal's multi-file export zip and meal-split
nutrition files — flat date/weight(/calories) CSV only.

Export
------
:func:`entries_to_csv` writes the canonical ``date,weight_kg,kcal`` columns;
:func:`entries_to_json` returns a list of plain dicts. Round-tripping export ->
import reproduces the same entries (``weight_kg`` is an explicit-kg header).
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import re
from dataclasses import dataclass

from api.storage import Entry

__all__ = ["ParsedRow", "entries_to_csv", "entries_to_json", "parse_entries_csv"]

_DATE_ALIASES = {"date", "day", "timestamp"}
_KCAL_ALIASES = {"kcal", "calories", "cals", "energy", "energy (kcal)", "calories (kcal)"}
# Bare weight headers carry no unit; suffixed ones pin it explicitly.
_WEIGHT_RE = re.compile(r"(?:body ?weight|weight|mass)(?:_kg)?(?: ?\((kg|lbs?)\))?")

_KG_PER_LB = 0.45359237


@dataclass(frozen=True)
class ParsedRow:
    date: str
    weight_kg: float | None
    kcal: int | None


def _normalise(name: str | None) -> str:
    """Header/cell key normalisation: BOM off, trimmed, lowercased, spaces collapsed."""
    return re.sub(r"\s+", " ", (name or "").lstrip("﻿").strip().lower())


def _resolve_columns(
    fieldnames: list[str],
) -> tuple[str | None, str | None, str | None, str | None]:
    """Map raw header names → (date_col, weight_col, kcal_col, weight_unit).

    ``weight_unit`` is ``"kg"``/``"lb"`` when the header states it, else ``None``
    (ambiguous — the caller's assumed units apply). First matching column wins;
    unknown columns are ignored.
    """
    date_col = weight_col = kcal_col = None
    weight_unit: str | None = None
    for name in fieldnames:
        norm = _normalise(name)
        if date_col is None and norm in _DATE_ALIASES:
            date_col = name
        elif weight_col is None and (m := _WEIGHT_RE.fullmatch(norm)):
            weight_col = name
            if norm.endswith("_kg") or m.group(1) == "kg":
                weight_unit = "kg"
            elif m.group(1) in ("lb", "lbs"):
                weight_unit = "lb"
        elif kcal_col is None and norm in _KCAL_ALIASES:
            kcal_col = name
    return date_col, weight_col, kcal_col, weight_unit


_NUMERIC_DATE_RE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})")
_TEXT_DATE_FORMATS = ("%b %d %Y", "%d %b %Y", "%B %d %Y", "%d %B %Y")


def _parse_date(value: str) -> tuple[str | None, bool]:
    """Parse one date cell → (ISO date or None, was-ambiguous-month-first).

    Accepts ISO (with or without a time part), numeric M/D/YYYY and D/M/YYYY (a
    value where both readings are valid is taken as US month-first and flagged so
    the caller can warn once), and ``Jul 4 2026`` / ``4 Jul 2026`` styles.
    """
    s = value.strip()
    if not s:
        return None, False
    # ISO date, or an ISO timestamp whose first 10 chars are the date.
    try:
        return _dt.date.fromisoformat(s[:10]).isoformat(), False
    except ValueError:
        pass
    if m := _NUMERIC_DATE_RE.fullmatch(s):
        a, b, year = (int(g) for g in m.groups())
        # Month-first (US) unless that's impossible; ambiguous values are flagged.
        month, day = (b, a) if a > 12 else (a, b)
        try:
            return _dt.date(year, month, day).isoformat(), a <= 12 and b <= 12
        except ValueError:
            return None, False
    cleaned = re.sub(r"\s+", " ", s.replace(",", " ")).strip()
    for fmt in _TEXT_DATE_FORMATS:
        try:
            return _dt.datetime.strptime(cleaned, fmt).date().isoformat(), False
        except ValueError:
            continue
    return None, False


def parse_entries_csv(text: str, assumed_units: str = "kg") -> tuple[list[ParsedRow], list[str]]:
    """Parse CSV text into rows to upsert, plus a list of warnings.

    ``assumed_units`` ("kg"/"lb") applies only when the weight header itself names
    no unit; using it always adds a warning. Never raises on bad rows — malformed
    rows are skipped and reported.
    """
    warnings: list[str] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None:
        return [], ["CSV had no header row; expected at least 'date' and 'weight' columns."]

    date_col, weight_col, kcal_col, weight_unit = _resolve_columns(reader.fieldnames)
    missing = [label for label, col in (("date", date_col), ("weight", weight_col)) if col is None]
    if missing:
        return [], [
            f"CSV is missing a {' and a '.join(f'{m!r}' for m in missing)} column — "
            f"nothing was imported. Header found: {', '.join(reader.fieldnames)}."
        ]

    unit = weight_unit or ("lb" if assumed_units == "lb" else "kg")
    unit_assumed = weight_unit is None
    any_weight = False
    ambiguous_dates = 0

    rows: list[ParsedRow] = []
    for lineno, raw in enumerate(reader, start=2):  # line 1 is the header
        # Fully blank row → skip silently. (Non-str values guard against overlong
        # rows, where DictReader parks the extras in a list under the None key.)
        if not any(v.strip() for v in raw.values() if isinstance(v, str)):
            continue
        date, ambiguous = _parse_date(raw.get(date_col) or "")
        if date is None:
            bad = (raw.get(date_col) or "").strip()
            warnings.append(f"line {lineno}: skipped — bad or missing date {bad!r}.")
            continue
        ambiguous_dates += ambiguous

        weight = _parse_float(raw.get(weight_col))
        kcal = _parse_int(raw.get(kcal_col) if kcal_col else None)
        if weight is _INVALID:
            warnings.append(f"line {lineno}: weight not a number — treated as missing.")
            weight = None
        if kcal is _INVALID:
            warnings.append(f"line {lineno}: calories not a number — treated as missing.")
            kcal = None
        if weight is None and kcal is None:
            warnings.append(f"line {lineno}: skipped — no usable weight or calories.")
            continue
        if weight is not None:
            any_weight = True
            if unit == "lb":
                weight = round(weight * _KG_PER_LB, 2)
        rows.append(ParsedRow(date=date, weight_kg=weight, kcal=kcal))  # type: ignore[arg-type]

    # File-level notices go first so they aren't buried under per-row warnings.
    notices: list[str] = []
    if unit_assumed and any_weight:
        notices.append(
            f"weight column has no explicit unit — values were read as {unit} "
            f"(your current display units). If the file is in "
            f"{'kg' if unit == 'lb' else 'lb'}, switch units and re-import."
        )
    if ambiguous_dates:
        notices.append(
            f"{ambiguous_dates} numeric date(s) like '04/07/2026' are ambiguous — "
            f"read as US month-first (MM/DD/YYYY)."
        )
    return rows, notices + warnings


class _Invalid:
    pass


_INVALID = _Invalid()

_THOUSANDS_RE = re.compile(r"\d{1,3}(,\d{3})+(\.\d+)?")


def _clean_number(value: str) -> str:
    """Trim and drop thousands separators ('1,900' → '1900'); nothing cleverer —
    a European decimal comma ('78,5') stays unparseable rather than misread ×10."""
    s = value.strip()
    return s.replace(",", "") if _THOUSANDS_RE.fullmatch(s) else s


def _parse_float(value: str | None):
    if value is None or value.strip() == "":
        return None
    try:
        return float(_clean_number(value))
    except ValueError:
        return _INVALID


def _parse_int(value: str | None):
    if value is None or value.strip() == "":
        return None
    try:
        return int(round(float(_clean_number(value))))
    except ValueError:
        return _INVALID


def entries_to_csv(entries: list[Entry]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date", "weight_kg", "kcal"])
    for e in entries:
        writer.writerow(
            [
                e.date,
                "" if e.weight_kg is None else e.weight_kg,
                "" if e.kcal is None else e.kcal,
            ]
        )
    return out.getvalue()


def entries_to_json(entries: list[Entry]) -> list[dict[str, object]]:
    return [
        {
            "date": e.date,
            "weight_kg": e.weight_kg,
            "kcal": e.kcal,
            "created_at": e.created_at,
            "updated_at": e.updated_at,
        }
        for e in entries
    ]
