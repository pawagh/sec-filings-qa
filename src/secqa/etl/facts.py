"""Turn an EDGAR companyfacts payload into as-reported annual fact rows.

Pitfalls handled here:
  * companyfacts' `fy` is the fiscal year of the *filing*; a FY2024 10-K also reports FY2023 and
    FY2022 values tagged fy=2024. We derive fiscal_year from the period end date instead.
  * Each prior-period value appears once per filing that reported it. We keep all of them (they are
    the as-reported history) and pick "latest filed wins" in SQL.
"""

from datetime import date

import pandas as pd

FACT_COLUMNS = ["cik", "taxonomy", "concept", "unit", "period_start", "period_end", "fiscal_year",
                "value", "accession", "form", "filed_date", "frame"]
ANNUAL_FORMS = {"10-K", "10-K/A"}


def fiscal_year_for(period_end: date) -> int:
    """Fiscal year = calendar year in which the period ends (EDGAR/company convention).

    52/53-week years that end in the first week of January (e.g. 2022-01-01) belong to the prior year.
    """
    if period_end.month == 1 and period_end.day <= 7:
        return period_end.year - 1
    return period_end.year


def extract_facts(payload: dict, concepts: pd.DataFrame, min_fy: int, max_fy: int) -> tuple[pd.DataFrame, list[dict]]:
    """Return (facts, unit_issues).

    `concepts` has columns taxonomy, concept, unit. Only 10-K facts for mapped concepts in the
    expected unit are kept; a mapped concept that exists only in other units is reported as an issue.
    """
    cik = int(payload["cik"])
    facts = payload.get("facts", {})
    rows: list[dict] = []
    unit_issues: list[dict] = []
    for c in concepts.drop_duplicates(["taxonomy", "concept", "unit"]).itertuples(index=False):
        node = facts.get(c.taxonomy, {}).get(c.concept)
        if not node:
            continue
        units = node.get("units", {})
        if c.unit not in units:
            unit_issues.append({"concept": c.concept, "expected_unit": c.unit, "found_units": sorted(units)})
            continue
        for r in units[c.unit]:
            if r.get("form") not in ANNUAL_FORMS:
                continue
            rows.append({
                "cik": cik,
                "taxonomy": c.taxonomy,
                "concept": c.concept,
                "unit": c.unit,
                "period_start": r.get("start"),
                "period_end": r.get("end"),
                "value": r.get("val"),
                "accession": r.get("accn"),
                "form": r.get("form"),
                "filed_date": r.get("filed"),
                "frame": r.get("frame"),
            })
    df = pd.DataFrame(rows, columns=[c for c in FACT_COLUMNS if c != "fiscal_year"])
    if df.empty:
        return pd.DataFrame(columns=FACT_COLUMNS), unit_issues
    for col in ("period_start", "period_end", "filed_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    df["fiscal_year"] = df["period_end"].map(lambda d: fiscal_year_for(d) if pd.notna(d) else None)
    df = df[df["fiscal_year"].between(min_fy, max_fy)]
    return df[FACT_COLUMNS].reset_index(drop=True), unit_issues
