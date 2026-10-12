"""Integration tests against a real Postgres+pgvector (TEST_DATABASE_URL). CI runs these in a service container."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from secqa.agent import service, sql_tools
from secqa.config import ROOT
from secqa.etl import load
from secqa.etl.sections import Section

pytestmark = pytest.mark.integration

CIK = 320193


def facts_df():
    def row(concept, start, end, value, accn, filed, unit="USD"):
        return {"cik": CIK, "taxonomy": "us-gaap", "concept": concept, "unit": unit, "period_start": start,
                "period_end": end, "fiscal_year": end.year, "value": value, "accession": accn, "form": "10-K",
                "filed_date": filed, "frame": None}
    d = date
    return pd.DataFrame([
        # FY2023 revenue reported in two filings; the later one restated it (latest filed must win)
        row("RevenueFromContractWithCustomerExcludingAssessedTax", d(2022, 9, 25), d(2023, 9, 30), 383.0e9, "A23", d(2023, 11, 3)),
        row("RevenueFromContractWithCustomerExcludingAssessedTax", d(2022, 9, 25), d(2023, 9, 30), 383.3e9, "A24", d(2024, 11, 1)),
        row("RevenueFromContractWithCustomerExcludingAssessedTax", d(2023, 10, 1), d(2024, 9, 28), 391.0e9, "A24", d(2024, 11, 1)),
        # Lower-priority concept for the same metric/year must be ignored
        row("SalesRevenueNet", d(2023, 10, 1), d(2024, 9, 28), 1.0, "A24", d(2024, 11, 1)),
        # A quarterly duration inside a 10-K must not be treated as annual
        row("ResearchAndDevelopmentExpense", d(2024, 6, 30), d(2024, 9, 28), 8.0e9, "A24", d(2024, 11, 1)),
        row("ResearchAndDevelopmentExpense", d(2022, 9, 25), d(2023, 9, 30), 29.9e9, "A24", d(2024, 11, 1)),
        row("ResearchAndDevelopmentExpense", d(2023, 10, 1), d(2024, 9, 28), 31.4e9, "A24", d(2024, 11, 1)),
        row("NetIncomeLoss", d(2023, 10, 1), d(2024, 9, 28), 93.7e9, "A24", d(2024, 11, 1)),
        row("Assets", None, d(2024, 9, 28), 365.0e9, "A24", d(2024, 11, 1)),
        row("Liabilities", None, d(2024, 9, 28), 308.0e9, "A24", d(2024, 11, 1)),
        row("StockholdersEquity", None, d(2024, 9, 28), 57.0e9, "A24", d(2024, 11, 1)),
    ])


@pytest.fixture(scope="module")
def loaded(db_url):
    from secqa.db import connect

    with connect(db_url) as conn:
        run_id = load.start_run(conn, {"test": True})
        load.upsert_company(conn, {"cik": CIK, "ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology",
                                   "sic": "3571", "sic_description": "Electronic Computers", "fiscal_year_end": "0928",
                                   "aliases": ["Apple"]})
        load.upsert_concepts(conn, pd.read_csv(ROOT / "config" / "metric_concepts.csv"))
        first = load.load_facts(conn, facts_df(), run_id)
        second = load.load_facts(conn, facts_df(), run_id)
        filing = {"accession": "A24", "cik": CIK, "form": "10-K", "fiscal_year": 2024, "period_end": "2024-09-28",
                  "filed_date": "2024-11-01", "primary_doc": "a.htm", "url": "https://example.test/a.htm"}
        load.upsert_filing(conn, filing)
        secs = [Section("item1a", "Item 1A. Risk Factors",
                        "Supply chain disruption in Asia could harm results.\nForeign exchange rates are volatile."),
                Section("item7", "Item 7. MD&A", "Research and development expense increased due to headcount.")]
        s1 = load.upsert_sections(conn, filing, secs, 1200, 200)
        s2 = load.upsert_sections(conn, filing, secs, 1200, 200)
        rng = np.random.default_rng(0)
        for (cid,) in conn.execute("SELECT id FROM document_chunks").fetchall():
            v = rng.normal(size=384)
            conn.execute("UPDATE document_chunks SET embedding = %s, embed_model = 'test' WHERE id = %s",
                         (v / np.linalg.norm(v), cid))
        load.refresh_materialized_views(conn)
        load.refresh_materialized_views(conn)  # second refresh runs CONCURRENTLY
        conn.commit()
    return {"first": first, "second": second, "s1": s1, "s2": s2}


def test_loads_are_idempotent(loaded):
    assert loaded["first"] == len(facts_df())
    assert loaded["second"] == 0
    assert loaded["s1"]["sections_changed"] == 2 and loaded["s2"] == {"sections_changed": 0, "chunks_inserted": 0}


def test_view_picks_latest_filed_priority_concept_and_annual_only(conn, loaded):
    rows = dict(conn.execute("SELECT fiscal_year, value FROM v_metric_values WHERE metric = 'revenue'").fetchall())
    assert float(rows[2023]) == 383.3e9  # restated value wins
    assert float(rows[2024]) == 391.0e9  # higher-priority concept beats SalesRevenueNet
    rd = conn.execute("SELECT value FROM v_metric_values WHERE metric='rd_expense' AND fiscal_year=2024").fetchone()
    assert float(rd[0]) == 31.4e9        # the quarterly 8.0e9 is excluded


def test_sql_tools(conn, loaded):
    cmp = sql_tools.compare_metric_over_years(conn, "AAPL", "rd_expense", 2023, 2024)
    assert [s["fiscal_year"] for s in cmp["series"]] == [2023, 2024]
    assert cmp["change"]["percent"] == pytest.approx(31.4 / 29.9 - 1)
    assert cmp["series"][1]["accession"] == "A24"
    margin = sql_tools.get_metric(conn, ["AAPL"], "net_margin", [2024])
    assert margin[0]["value"] == pytest.approx(93.7 / 391.0)
    rank = sql_tools.rank_companies_by_metric(conn, "revenue", 2024)
    assert rank[0]["ticker"] == "AAPL" and rank[0]["rank"] == 1
    with pytest.raises(ValueError):
        sql_tools.get_metric(conn, ["AAPL"], "revenue; DROP TABLE companies", None)


def test_all_analytics_queries_run(conn, loaded):
    for path in sorted(Path(ROOT / "sql" / "analytics").glob("*.sql")):
        conn.execute(path.read_text()).fetchall()


def test_hybrid_retrieval_with_filters(conn, loaded, monkeypatch):
    from secqa.rag import retrieval

    monkeypatch.setattr(retrieval, "embed_query", lambda q: np.ones(384) / np.sqrt(384))
    hits = retrieval.search(conn, "supply chain risk in Asia", tickers=["AAPL"], sections=["item1a"], top_k=3)
    assert hits and hits[0].section == "item1a" and hits[0].fts_rank == 1
    assert retrieval.search(conn, "supply chain", tickers=["MSFT"], top_k=3) == []


def test_service_sql_route_end_to_end(conn, loaded, monkeypatch):
    monkeypatch.setattr(service, "_catalog", None)
    out = service.ask(conn, "How did Apple's R&D expense change from 2023 to 2024?")
    assert out["route"] == "sql"
    assert "$31.40B" in out["answer"] and "+5.0%" in out["answer"]
    assert {c["fiscal_year"] for c in out["citations"]} == {2023, 2024}
