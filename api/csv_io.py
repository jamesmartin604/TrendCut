"""CSV import/export for entries (PRD FR-24 / FR-25, data portability).

Import format
-------------
A header row plus one row per day. Recognised columns (case-insensitive):

* ``date``   — required, ISO ``YYYY-MM-DD``.
* ``weight_kg`` (aliases: ``weight``) — optional; blank means "no weigh-in".
* ``kcal`` (aliases: ``calories``, ``cals``) — optional; blank means "not logged".

Rules:

* A row with a missing/unparseable ``date`` is **skipped** with a warning.
* Blank ``weight_kg``/``kcal`` cells become ``None`` (a gap), not zero.
* Duplicate dates (in the file or already in the DB) **upsert** — the last value
  wins; entries are never duplicated.

Export
------
:func:`entries_to_csv` writes the canonical ``date,weight_kg,kcal`` columns;
:func:`entries_to_json` returns a list of plain dicts. Round-tripping export ->
import reproduces the same entries.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
from dataclasses import dataclass

from api.storage import Entry

__all__ = ["ParsedRow", "entries_to_csv", "entries_to_json", "parse_entries_csv"]

_DATE_KEYS = ("date",)
_WEIGHT_KEYS = ("weight_kg", "weight")
_KCAL_KEYS = ("kcal", "calories", "cals")


@dataclass(frozen=True)
class ParsedRow:
    date: str
    weight_kg: float | None
    kcal: int | None


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def parse_entries_csv(text: str) -> tuple[list[ParsedRow], list[str]]:
    """Parse CSV text into rows to upsert, plus a list of warnings.

    Never raises on bad rows — malformed rows are skipped and reported.
    """
    warnings: list[str] = []
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], ["CSV had no header row; expected at least a 'date' column."]

    # Normalise header names to lowercase/stripped for case-insensitive matching.
    normalised = {(name or "").strip().lower(): name for name in reader.fieldnames}
    if not any(k in normalised for k in _DATE_KEYS):
        return [], ["CSV is missing a 'date' column."]

    rows: list[ParsedRow] = []
    for lineno, raw in enumerate(reader, start=2):  # line 1 is the header
        row = {normalised.get(k.strip().lower(), k): v for k, v in raw.items()}
        date_str = (_pick(row, _DATE_KEYS) or "").strip()
        try:
            date = _dt.date.fromisoformat(date_str).isoformat()
        except ValueError:
            warnings.append(f"line {lineno}: skipped — bad or missing date {date_str!r}.")
            continue

        weight = _parse_float(_pick(row, _WEIGHT_KEYS))
        kcal = _parse_int(_pick(row, _KCAL_KEYS))
        if weight is _INVALID:
            warnings.append(f"line {lineno}: weight not a number — treated as missing.")
            weight = None
        if kcal is _INVALID:
            warnings.append(f"line {lineno}: calories not a number — treated as missing.")
            kcal = None
        rows.append(ParsedRow(date=date, weight_kg=weight, kcal=kcal))  # type: ignore[arg-type]
    return rows, warnings


class _Invalid:
    pass


_INVALID = _Invalid()


def _parse_float(value: str | None):
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return _INVALID


def _parse_int(value: str | None):
    if value is None or value.strip() == "":
        return None
    try:
        return int(round(float(value)))
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
