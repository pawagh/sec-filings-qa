"""Pre-load data-quality checks. Each check returns audit records; the caller writes them to dq_audit.

Severity:
  error   -> offending rows are dropped before load
  warning -> loaded, but flagged for review (coverage gaps, short sections, unit surprises)
  info    -> context
"""

import pandas as pd

REQUIRED_FACT_COLS = ["cik", "concept", "unit", "period_end", "value", "accession", "filed_date"]
FACT_KEY = ["cik", "taxonomy", "concept", "unit", "period_start", "period_end", "accession"]
CORE_METRICS = ["revenue", "net_income", "total_assets"]
MIN_SECTION_CHARS = 2000


def audit(check: str, severity: str, entity: str, **details) -> dict:
    return {"check_name": check, "severity": severity, "entity": entity, "details": details}


def check_facts(df: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame, list[dict]]:
    records: list[dict] = []
    if df.empty:
        return df, [audit("facts_present", "error", ticker, message="no 10-K facts for mapped concepts")]

    null_mask = df[REQUIRED_FACT_COLS].isna().any(axis=1)
    if null_mask.any():
        records.append(audit("required_not_null", "error", ticker, rows=int(null_mask.sum()),
                             sample=df[null_mask].head(3).astype(str).to_dict("records")))
        df = df[~null_mask]

    # Same period reported twice in ONE filing with different values = conflicting data.
    key_cols = [c for c in FACT_KEY if c in df.columns]
    dup = df.duplicated(key_cols, keep=False)
    if dup.any():
        conflicts = df[dup].groupby(key_cols, dropna=False)["value"].nunique()
        n_conflicts = int((conflicts > 1).sum())
        records.append(audit("duplicate_period", "error" if n_conflicts else "info", ticker,
                             duplicate_rows=int(dup.sum()), conflicting_keys=n_conflicts))
        df = df.drop_duplicates(key_cols, keep="first")
    return df.reset_index(drop=True), records


def check_units(unit_issues: list[dict], ticker: str) -> list[dict]:
    return [audit("unit_mismatch", "warning", ticker, **u) for u in unit_issues]


def check_coverage(df: pd.DataFrame, concepts: pd.DataFrame, ticker: str, fiscal_years: list[int]) -> list[dict]:
    """Warn when a core metric has no annual value for an in-scope fiscal year."""
    if df.empty:
        return []
    m = df.merge(concepts[["metric", "concept", "unit", "period_type"]], on=["concept", "unit"])
    dur = (pd.to_datetime(m["period_end"]) - pd.to_datetime(m["period_start"])).dt.days
    annual = ((m["period_type"] == "duration") & dur.between(350, 380)) | (
        (m["period_type"] == "instant") & m["period_start"].isna())
    have = set(zip(m[annual]["metric"], m[annual]["fiscal_year"], strict=False))
    missing = [(metric, fy) for metric in CORE_METRICS for fy in fiscal_years if (metric, fy) not in have]
    return [audit("core_metric_coverage", "warning", f"{ticker}/FY{fy}", metric=metric) for metric, fy in missing]


def check_sections(sections: dict[str, str], ticker: str, fiscal_year: int) -> list[dict]:
    records = []
    for sec in ("item1", "item1a", "item7"):
        n = len(sections.get(sec, ""))
        if n < MIN_SECTION_CHARS:
            records.append(audit("section_extracted", "warning", f"{ticker}/FY{fiscal_year}/{sec}", chars=n,
                                 message="missing or implausibly short (incorporated by reference or unusual layout?)"))
    return records


BALANCE_CHECK_SQL = """
SELECT ticker, fiscal_year, total_assets, total_liabilities, stockholders_equity
FROM mv_company_year_metrics
WHERE total_assets IS NOT NULL AND total_liabilities IS NOT NULL AND stockholders_equity IS NOT NULL
  AND abs(total_assets - (total_liabilities + stockholders_equity)) > 0.01 * total_assets
"""
