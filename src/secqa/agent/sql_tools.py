"""Fixed, parameterized SQL tools. The LLM picks a tool and arguments; it never writes SQL.

Metric names are validated against a whitelist before being used as column identifiers.
"""

from dataclasses import dataclass

import psycopg
from psycopg import sql


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    kind: str  # money | ratio | per_share | shares


METRICS = {m.name: m for m in [
    Metric("revenue", "Revenue", "money"),
    Metric("cost_of_revenue", "Cost of revenue", "money"),
    Metric("gross_profit", "Gross profit", "money"),
    Metric("rd_expense", "R&D expense", "money"),
    Metric("sga_expense", "SG&A expense", "money"),
    Metric("operating_income", "Operating income", "money"),
    Metric("net_income", "Net income", "money"),
    Metric("eps_diluted", "Diluted EPS", "per_share"),
    Metric("diluted_shares", "Diluted shares", "shares"),
    Metric("operating_cash_flow", "Operating cash flow", "money"),
    Metric("capex", "Capital expenditures", "money"),
    Metric("free_cash_flow", "Free cash flow", "money"),
    Metric("dividends_paid", "Dividends paid", "money"),
    Metric("share_repurchases", "Share repurchases", "money"),
    Metric("total_assets", "Total assets", "money"),
    Metric("total_liabilities", "Total liabilities", "money"),
    Metric("stockholders_equity", "Stockholders' equity", "money"),
    Metric("cash", "Cash and equivalents", "money"),
    Metric("long_term_debt", "Long-term debt", "money"),
    Metric("gross_margin", "Gross margin", "ratio"),
    Metric("operating_margin", "Operating margin", "ratio"),
    Metric("net_margin", "Net margin", "ratio"),
    Metric("rd_intensity", "R&D as % of revenue", "ratio"),
    Metric("revenue_yoy", "Revenue growth (YoY)", "ratio"),
    Metric("liabilities_to_equity", "Liabilities-to-equity", "ratio"),
]}


def _metric(name: str) -> Metric:
    if name not in METRICS:
        raise ValueError(f"unknown metric {name!r}; choose from {sorted(METRICS)}")
    return METRICS[name]


def get_metric(conn: psycopg.Connection, tickers: list[str], metric: str, years: list[int] | None = None) -> list[dict]:
    """Values of one metric for companies/years, with the source filing for citation."""
    m = _metric(metric)
    q = sql.SQL("""
        SELECT mv.ticker, mv.name, mv.fiscal_year, mv.{col}::float8 AS value,
               COALESCE(v.accession, fl.accession) AS accession, COALESCE(v.concept, 'derived') AS concept, fl.url
        FROM mv_company_year_metrics mv
        LEFT JOIN v_metric_values v ON v.cik = mv.cik AND v.fiscal_year = mv.fiscal_year AND v.metric = %(metric)s
        LEFT JOIN filings fl ON fl.cik = mv.cik AND fl.fiscal_year = mv.fiscal_year AND fl.form = '10-K'
        WHERE mv.ticker = ANY(%(tickers)s)
          AND (%(years)s::int[] IS NULL OR mv.fiscal_year = ANY(%(years)s::int[]))
          AND mv.{col} IS NOT NULL
        ORDER BY mv.ticker, mv.fiscal_year
    """).format(col=sql.Identifier(m.name))
    rows = conn.execute(q, {"metric": m.name, "tickers": tickers, "years": years}).fetchall()
    return [dict(ticker=r[0], company=r[1], fiscal_year=r[2], metric=m.name, label=m.label, kind=m.kind,
                 value=r[3], accession=r[4], concept=r[5], url=r[6]) for r in rows]


def compare_metric_over_years(conn: psycopg.Connection, ticker: str, metric: str, start_year: int, end_year: int) -> dict:
    """Year-by-year values with YoY change, plus total change and CAGR between the endpoints."""
    m = _metric(metric)
    q = sql.SQL("""
        WITH series AS (
            SELECT fiscal_year, {col}::float8 AS value
            FROM mv_company_year_metrics
            WHERE ticker = %(ticker)s AND fiscal_year BETWEEN %(y0)s AND %(y1)s AND {col} IS NOT NULL
        )
        SELECT fiscal_year, value,
               value / NULLIF(LAG(value) OVER w, 0) - 1                    AS yoy,
               FIRST_VALUE(value) OVER (ORDER BY fiscal_year ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS first_value,
               LAST_VALUE(value)  OVER (ORDER BY fiscal_year ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_value,
               MIN(fiscal_year) OVER () AS first_year, MAX(fiscal_year) OVER () AS last_year
        FROM series
        WINDOW w AS (ORDER BY fiscal_year)
        ORDER BY fiscal_year
    """).format(col=sql.Identifier(m.name))
    rows = conn.execute(q, {"ticker": ticker, "y0": start_year, "y1": end_year}).fetchall()
    sources = {r["fiscal_year"]: r for r in get_metric(conn, [ticker], metric, list(range(start_year, end_year + 1)))}
    out = {"ticker": ticker, "metric": m.name, "label": m.label, "kind": m.kind, "series": [], "change": None}
    for fy, value, yoy, *_ in rows:
        src = sources.get(fy, {})
        out["series"].append({"fiscal_year": fy, "value": value, "yoy": yoy,
                              "accession": src.get("accession"), "url": src.get("url")})
    if rows:
        _, _, _, v0, v1, y0, y1 = rows[0]
        n = y1 - y0
        out["change"] = {
            "from_year": y0, "to_year": y1, "absolute": v1 - v0,
            "percent": (v1 / v0 - 1) if v0 else None,
            "cagr": ((v1 / v0) ** (1 / n) - 1) if n > 0 and v0 and v0 > 0 and v1 > 0 else None,
        }
    return out


def rank_companies_by_metric(conn: psycopg.Connection, metric: str, fiscal_year: int, sector: str | None = None,
                             descending: bool = True, limit: int = 10) -> list[dict]:
    m = _metric(metric)
    order = sql.SQL("DESC") if descending else sql.SQL("ASC")
    q = sql.SQL("""
        SELECT mv.ticker, mv.name, mv.sector, mv.{col}::float8 AS value,
               RANK() OVER (ORDER BY mv.{col} {order}) AS rank, fl.accession, fl.url
        FROM mv_company_year_metrics mv
        LEFT JOIN filings fl ON fl.cik = mv.cik AND fl.fiscal_year = mv.fiscal_year AND fl.form = '10-K'
        WHERE mv.fiscal_year = %(fy)s AND mv.{col} IS NOT NULL
          AND (%(sector)s::text IS NULL OR mv.sector = %(sector)s)
        ORDER BY rank
        LIMIT %(limit)s
    """).format(col=sql.Identifier(m.name), order=order)
    rows = conn.execute(q, {"fy": fiscal_year, "sector": sector, "limit": limit}).fetchall()
    return [dict(ticker=r[0], company=r[1], sector=r[2], value=r[3], rank=r[4], accession=r[5], url=r[6],
                 metric=m.name, label=m.label, kind=m.kind, fiscal_year=fiscal_year) for r in rows]


def get_company_profile(conn: psycopg.Connection, ticker: str) -> dict | None:
    r = conn.execute("""
        SELECT c.ticker, c.name, c.sector, c.sic_description, c.fiscal_year_end,
               COALESCE(json_agg(json_build_object('fiscal_year', f.fiscal_year, 'accession', f.accession,
                                                   'filed', f.filed_date, 'url', f.url)
                                 ORDER BY f.fiscal_year) FILTER (WHERE f.accession IS NOT NULL), '[]')
        FROM companies c LEFT JOIN filings f USING (cik)
        WHERE c.ticker = %s
        GROUP BY c.cik
    """, (ticker,)).fetchone()
    if not r:
        return None
    return dict(ticker=r[0], company=r[1], sector=r[2], industry=r[3], fiscal_year_end=r[4], filings=r[5])


def available_years(conn: psycopg.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT DISTINCT fiscal_year FROM filings ORDER BY 1")]


def sectors(conn: psycopg.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT sector FROM companies WHERE sector IS NOT NULL ORDER BY 1")]
