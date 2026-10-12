from datetime import date

import pandas as pd

from secqa.etl import quality

BASE = {"cik": 1, "taxonomy": "us-gaap", "concept": "Revenues", "unit": "USD", "period_start": date(2023, 1, 1),
        "period_end": date(2023, 12, 31), "fiscal_year": 2023, "value": 100.0, "accession": "A",
        "form": "10-K", "filed_date": date(2024, 2, 1), "frame": None}


def test_nulls_are_dropped_and_logged():
    df = pd.DataFrame([BASE, {**BASE, "value": None, "accession": "B"}])
    out, recs = quality.check_facts(df, "X")
    assert len(out) == 1
    assert recs[0]["check_name"] == "required_not_null" and recs[0]["severity"] == "error"


def test_conflicting_duplicate_in_same_filing_is_error():
    df = pd.DataFrame([BASE, {**BASE, "value": 101.0}])
    out, recs = quality.check_facts(df, "X")
    assert len(out) == 1
    assert recs[0]["severity"] == "error" and recs[0]["details"]["conflicting_keys"] == 1


def test_identical_duplicate_is_info():
    out, recs = quality.check_facts(pd.DataFrame([BASE, BASE]), "X")
    assert len(out) == 1 and recs[0]["severity"] == "info"


def test_same_period_in_two_filings_is_not_a_duplicate():
    out, recs = quality.check_facts(pd.DataFrame([BASE, {**BASE, "accession": "B"}]), "X")
    assert len(out) == 2 and recs == []


def test_coverage_flags_missing_core_metrics():
    concepts = pd.DataFrame([{"metric": "revenue", "concept": "Revenues", "unit": "USD", "period_type": "duration"}])
    recs = quality.check_coverage(pd.DataFrame([BASE]), concepts, "X", [2023, 2024])
    missing = {(r["entity"], r["details"]["metric"]) for r in recs}
    assert ("X/FY2024", "revenue") in missing and ("X/FY2023", "revenue") not in missing
    assert ("X/FY2023", "net_income") in missing


def test_short_sections_flagged():
    recs = quality.check_sections({"item1": "x" * 5000, "item1a": "short"}, "X", 2024)
    assert {r["entity"] for r in recs} == {"X/FY2024/item1a", "X/FY2024/item7"}
