from datetime import date

import pandas as pd

from secqa.etl.facts import extract_facts, fiscal_year_for

CONCEPTS = pd.DataFrame([
    {"taxonomy": "us-gaap", "concept": "Revenues", "unit": "USD"},
    {"taxonomy": "us-gaap", "concept": "EarningsPerShareDiluted", "unit": "USD/shares"},
])


def fact(start, end, val, accn, form="10-K", fy=2024, filed="2024-11-01"):
    return {"start": start, "end": end, "val": val, "accn": accn, "fy": fy, "fp": "FY", "form": form, "filed": filed}


def payload(rows, eps_units=None):
    return {"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": rows}},
        "EarningsPerShareDiluted": {"units": eps_units or {"USD/shares": []}},
    }}}


def test_fiscal_year_uses_period_end():
    assert fiscal_year_for(date(2024, 9, 28)) == 2024   # Apple
    assert fiscal_year_for(date(2025, 1, 26)) == 2025   # NVIDIA names FY by the January end
    assert fiscal_year_for(date(2022, 1, 1)) == 2021    # 52/53-week year ending first days of January


def test_fy_comes_from_period_not_filing():
    # A FY2024 10-K re-reports FY2023 and FY2022; companyfacts tags all three fy=2024.
    rows = [fact("2023-10-01", "2024-09-28", 391, "A24"), fact("2022-09-25", "2023-09-30", 383, "A24"),
            fact("2021-09-26", "2022-09-24", 394, "A24")]
    df, _ = extract_facts(payload(rows), CONCEPTS, 2022, 2024)
    assert sorted(df["fiscal_year"]) == [2022, 2023, 2024]


def test_keeps_only_annual_forms_and_year_window():
    rows = [fact("2024-06-30", "2024-09-28", 95, "Q", form="10-Q"),
            fact("2023-10-01", "2024-09-28", 391, "A24"),
            fact("2018-10-01", "2019-09-28", 260, "A19")]
    df, _ = extract_facts(payload(rows), CONCEPTS, 2023, 2024)
    assert list(df["value"]) == [391]


def test_unit_mismatch_is_reported():
    _, issues = extract_facts(payload([], eps_units={"USD/share": []}), CONCEPTS, 2023, 2024)
    assert issues == [{"concept": "EarningsPerShareDiluted", "expected_unit": "USD/shares", "found_units": ["USD/share"]}]
