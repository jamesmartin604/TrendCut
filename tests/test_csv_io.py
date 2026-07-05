"""Tolerant CSV parser tests (import format tolerance).

This is the layer that can silently corrupt data (kg/lb, month/day), so the tests
are deliberately thorough: header aliases and BOM, every accepted date style,
explicit-unit conversion, the ambiguous-unit assumption + its warning, per-row
skip-and-continue, and the native export → import round-trip.
"""

from __future__ import annotations

import pytest

from api.csv_io import entries_to_csv, parse_entries_csv
from api.storage import Entry


def _entry(date: str, weight: float | None, kcal: int | None) -> Entry:
    return Entry(date=date, weight_kg=weight, kcal=kcal, created_at="t", updated_at="t")


# --- header mapping --------------------------------------------------------- #
@pytest.mark.parametrize(
    "header",
    [
        "date,weight,calories",
        "Date,Weight,Calories",
        "  Date , Weight , Calories ",  # padded headers
        "day,mass,energy",
        "timestamp,weight,kcal",
        "date,weight,cals",
    ],
)
def test_header_aliases_map(header: str) -> None:
    rows, _ = parse_entries_csv(f"{header}\n2026-07-04,80.5,1900\n")
    assert len(rows) == 1
    assert rows[0].date == "2026-07-04"
    assert rows[0].weight_kg == 80.5
    assert rows[0].kcal == 1900


def test_bom_prefixed_header_is_stripped() -> None:
    rows, warnings = parse_entries_csv("﻿date,weight_kg,kcal\n2026-07-04,80.5,1900\n")
    assert len(rows) == 1
    # weight_kg is explicit kg, so the BOM must not break unit detection either.
    assert not any("no explicit unit" in w for w in warnings)


def test_extra_unknown_columns_are_ignored_silently() -> None:
    text = (
        "Date,Weight (kg),Body Fat %,Neck,Waist,Notes,Calories\n"
        '2026-07-04,80.5,22.1,38,90,"felt bloated",1900\n'
    )
    rows, warnings = parse_entries_csv(text)
    assert rows == [rows[0]]
    assert rows[0].weight_kg == 80.5
    assert rows[0].kcal == 1900
    assert warnings == []


def test_missing_weight_column_fails_whole_import() -> None:
    rows, warnings = parse_entries_csv("date,kcal\n2026-07-04,1900\n")
    assert rows == []
    assert any("'weight'" in w for w in warnings)


def test_missing_date_column_fails_whole_import() -> None:
    rows, warnings = parse_entries_csv("weight,kcal\n80.5,1900\n")
    assert rows == []
    assert any("'date'" in w for w in warnings)


def test_headerless_or_empty_text_fails_cleanly() -> None:
    rows, warnings = parse_entries_csv("")
    assert rows == []
    assert warnings


# --- date formats ------------------------------------------------------------ #
@pytest.mark.parametrize(
    "raw",
    [
        "2026-07-04",  # ISO
        "2026-07-04 08:30:00",  # ISO timestamp
        "07/04/2026",  # US month-first (ambiguous → US)
        "4/7/2026",  # ambiguous M/D vs D/M — read month-first (US) → 2026-04-07
        "Jul 4 2026",
        "Jul 4, 2026",
        "4 Jul 2026",
        "July 4 2026",
        "4 July 2026",
    ],
)
def test_accepted_date_styles_normalise_to_iso(raw: str) -> None:
    cell = f'"{raw}"' if "," in raw else raw  # a comma-containing date must be quoted
    rows, _ = parse_entries_csv(f"date,weight\n{cell},80.5\n")
    assert len(rows) == 1
    # "4/7/2026" is ambiguous and reads month-first → 2026-04-07; everything else
    # in the list is the 4th of July.
    expected = "2026-04-07" if raw == "4/7/2026" else "2026-07-04"
    assert rows[0].date == expected


def test_day_first_when_month_slot_is_impossible() -> None:
    rows, _ = parse_entries_csv("date,weight\n25/12/2026,80.5\n")
    assert rows[0].date == "2026-12-25"


def test_ambiguous_numeric_date_warns_once() -> None:
    text = "date,weight\n04/07/2026,80.5\n05/06/2026,80.4\n"
    rows, warnings = parse_entries_csv(text)
    assert [r.date for r in rows] == ["2026-04-07", "2026-05-06"]
    ambiguous = [w for w in warnings if "ambiguous" in w]
    assert len(ambiguous) == 1
    assert "MM/DD/YYYY" in ambiguous[0]


def test_unparseable_date_skips_row_not_file() -> None:
    text = "date,weight\nnot-a-date,80.5\n2026-07-04,80.4\n"
    rows, warnings = parse_entries_csv(text)
    assert [r.date for r in rows] == ["2026-07-04"]
    assert any("bad or missing date" in w for w in warnings)


def test_impossible_calendar_date_skips_row() -> None:
    rows, warnings = parse_entries_csv("date,weight\n02/30/2026,80.5\n")
    assert rows == []
    assert any("bad or missing date" in w for w in warnings)


# --- units (the silent-corruption risk) -------------------------------------- #
def test_lb_header_converts_to_kg() -> None:
    rows, warnings = parse_entries_csv('Date,"Weight (lbs)"\n2026-07-04,176.4\n')
    assert rows[0].weight_kg == pytest.approx(80.01, abs=0.01)
    # Explicit unit → no assumption warning.
    assert not any("no explicit unit" in w for w in warnings)


@pytest.mark.parametrize("header", ['"Weight (lb)"', '"Weight (lbs)"', '"weight (LBS)"'])
def test_lb_header_spellings(header: str) -> None:
    rows, _ = parse_entries_csv(f"date,{header}\n2026-07-04,220.46\n")
    assert rows[0].weight_kg == pytest.approx(100.0, abs=0.01)


def test_explicit_kg_header_used_as_is_even_if_user_prefers_lb() -> None:
    rows, warnings = parse_entries_csv('date,"Weight (kg)"\n2026-07-04,80.5\n', assumed_units="lb")
    assert rows[0].weight_kg == 80.5
    assert not any("no explicit unit" in w for w in warnings)


def test_bare_weight_header_assumes_user_units_kg_and_warns() -> None:
    rows, warnings = parse_entries_csv("date,weight\n2026-07-04,80.5\n", assumed_units="kg")
    assert rows[0].weight_kg == 80.5  # no conversion
    assert any("no explicit unit" in w and "read as kg" in w for w in warnings)


def test_bare_weight_header_assumes_user_units_lb_and_warns() -> None:
    rows, warnings = parse_entries_csv("date,weight\n2026-07-04,176.4\n", assumed_units="lb")
    assert rows[0].weight_kg == pytest.approx(80.01, abs=0.01)  # converted
    assert any("no explicit unit" in w and "read as lb" in w for w in warnings)


def test_no_unit_warning_when_file_has_no_weight_values() -> None:
    rows, warnings = parse_entries_csv("date,weight,kcal\n2026-07-04,,1900\n")
    assert rows[0].weight_kg is None
    assert not any("no explicit unit" in w for w in warnings)


# --- robustness --------------------------------------------------------------- #
def test_blank_rows_skipped_silently() -> None:
    text = "date,weight_kg\n2026-07-04,80.5\n\n,,\n2026-07-05,80.3\n"
    rows, warnings = parse_entries_csv(text)
    assert [r.date for r in rows] == ["2026-07-04", "2026-07-05"]
    assert warnings == []


def test_row_with_nothing_usable_is_skipped_with_warning() -> None:
    rows, warnings = parse_entries_csv("date,weight,kcal\n2026-07-04,,\n")
    assert rows == []
    assert any("no usable weight or calories" in w for w in warnings)


def test_one_bad_row_does_not_abort_good_rows() -> None:
    text = "date,weight,kcal\n2026-07-01,80.5,1900\ngarbage,heavy,many\n2026-07-03,80.2,1850\n"
    rows, warnings = parse_entries_csv(text)
    assert [r.date for r in rows] == ["2026-07-01", "2026-07-03"]
    assert any("bad or missing date" in w for w in warnings)


def test_thousands_separator_kcal_coerced() -> None:
    rows, _ = parse_entries_csv('date,weight,kcal\n2026-07-04,80.5,"1,900"\n')
    assert rows[0].kcal == 1900


def test_european_decimal_comma_is_not_misread_as_thousands() -> None:
    # "78,5" must NOT become 785 — it stays unparseable (missing + warning).
    rows, warnings = parse_entries_csv('date,weight,kcal\n2026-07-04,"78,5",1900\n')
    assert rows[0].weight_kg is None
    assert any("weight not a number" in w for w in warnings)


# --- native round-trip --------------------------------------------------------- #
def test_native_export_import_roundtrip() -> None:
    entries = [
        _entry("2026-07-01", 80.5, 1900),
        _entry("2026-07-02", None, 1850),  # calorie-only day
        _entry("2026-07-03", 80.2, None),  # weight-only day
    ]
    rows, warnings = parse_entries_csv(entries_to_csv(entries))
    assert [(r.date, r.weight_kg, r.kcal) for r in rows] == [
        ("2026-07-01", 80.5, 1900),
        ("2026-07-02", None, 1850),
        ("2026-07-03", 80.2, None),
    ]
    # The canonical weight_kg header is explicit kg — no unit-assumption warning,
    # even for a user whose display units are lb.
    assert warnings == []
    rows_lb_user, warnings_lb_user = parse_entries_csv(entries_to_csv(entries), assumed_units="lb")
    assert [r.weight_kg for r in rows_lb_user] == [80.5, None, 80.2]
    assert warnings_lb_user == []
