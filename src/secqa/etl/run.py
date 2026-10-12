"""ETL entry point: EDGAR -> data-quality checks -> PostgreSQL (facts, filings, sections, chunks) -> embeddings.

    secqa-etl                       # all companies in config/companies.csv
    secqa-etl --tickers AAPL,MSFT   # subset
    secqa-etl --skip-embed          # load text but don't embed yet
"""

import argparse
import logging
import re
import time

import pandas as pd

from secqa.config import get_settings
from secqa.db import connect, migrate
from secqa.edgar.client import EdgarClient
from secqa.etl import load, quality
from secqa.etl.facts import extract_facts
from secqa.etl.filings import find_10k_filings
from secqa.etl.sections import extract_from_exhibit, extract_sections

log = logging.getLogger("secqa.etl")


def read_companies(path, tickers: list[str] | None, limit: int | None) -> pd.DataFrame:
    """Optional `cik` column overrides SEC's ticker map (e.g. XOM's ticker moved to a new holding
    company CIK with no 10-K history; the filings still live under Exxon Mobil Corp, CIK 34088)."""
    df = pd.read_csv(path, dtype={"cik": "Int64"})
    df[["sector", "aliases"]] = df[["sector", "aliases"]].fillna("")
    if tickers:
        df = df[df["ticker"].isin(tickers)]
    return df.head(limit) if limit else df


def resolve_ciks(client: EdgarClient) -> dict[str, int]:
    return {v["ticker"].upper(): int(v["cik_str"]) for v in client.company_tickers().values()}


_SKIP_DOC = re.compile(r"^R\d+\.htm$|ex-?(?:3|4|10|19|21|22|23|24|31|32|97)\d*", re.IGNORECASE)


def sections_with_exhibits(client: EdgarClient, f: dict, min_chars: int = quality.MIN_SECTION_CHARS):
    """Primary 10-K document first; if Item 1A/7 is missing or a stub ("see the Annual Report"),
    look for it in the filing's other large HTML documents (Exhibit 13 and similar)."""
    sections = extract_sections(client.filing_document(f["cik"], f["accession"], f["primary_doc"]), min_chars)
    have = {s.section: len(s.content) for s in sections}
    missing = [sec for sec in ("item1a", "item7") if have.get(sec, 0) < min_chars]
    if not missing:
        return sections
    docs = [d for d in client.filing_index(f["cik"], f["accession"])
            if d["name"].lower().endswith(".htm") and d["name"] != f["primary_doc"]
            and not _SKIP_DOC.search(d["name"]) and int(d.get("size") or 0) > 100_000]
    for d in docs:
        for s in extract_from_exhibit(client.filing_document(f["cik"], f["accession"], d["name"]), missing, min_chars):
            if len(s.content) > have.get(s.section, 0):
                sections = [x for x in sections if x.section != s.section] + [s]
                have[s.section] = len(s.content)
    return sections


def process_company(conn, client: EdgarClient, row, cik: int, concepts: pd.DataFrame, years: list[int],
                    run_id: int, skip_text: bool, settings) -> dict:
    ticker = row.ticker
    audits: list[dict] = []
    sub = client.submissions(cik)
    load.upsert_company(conn, {
        "cik": cik, "ticker": ticker, "name": sub.get("name", ticker), "sector": row.sector or None,
        "sic": sub.get("sic"), "sic_description": sub.get("sicDescription"),
        "fiscal_year_end": sub.get("fiscalYearEnd"),
        "aliases": [a.strip() for a in str(row.aliases).split("|") if a.strip()],
    })

    # Facts: include the year before the first in-scope year so YoY works for the first year.
    facts, unit_issues = extract_facts(client.company_facts(cik), concepts, min(years) - 1, max(years))
    facts, fact_audits = quality.check_facts(facts, ticker)
    audits += fact_audits + quality.check_units(unit_issues, ticker)
    audits += quality.check_coverage(facts, concepts, ticker, years)
    inserted = load.load_facts(conn, facts, run_id)
    stats = {"facts_seen": len(facts), "facts_inserted": inserted, "filings": 0,
             "sections_changed": 0, "chunks_inserted": 0}

    filings = find_10k_filings(client, cik, years, submissions=sub)
    missing_years = sorted(set(years) - {f["fiscal_year"] for f in filings})
    if missing_years:
        audits.append(quality.audit("filing_coverage", "warning", ticker, missing_fiscal_years=missing_years))
    for f in filings:
        load.upsert_filing(conn, f)
        stats["filings"] += 1
        if skip_text:
            continue
        sections = sections_with_exhibits(client, f)
        audits += quality.check_sections({s.section: s.content for s in sections}, ticker, f["fiscal_year"])
        s = load.upsert_sections(conn, f, sections, settings.chunk_size, settings.chunk_overlap)
        stats["sections_changed"] += s["sections_changed"]
        stats["chunks_inserted"] += s["chunks_inserted"]

    load.write_audit(conn, run_id, audits)
    stats["dq_errors"] = sum(a["severity"] == "error" for a in audits)
    stats["dq_warnings"] = sum(a["severity"] == "warning" for a in audits)
    return stats


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="SEC filings ETL")
    p.add_argument("--tickers", help="comma-separated subset, e.g. AAPL,MSFT")
    p.add_argument("--limit", type=int, help="first N companies from the CSV")
    p.add_argument("--years", help="override FISCAL_YEARS, e.g. 2023,2024,2025")
    p.add_argument("--skip-text", action="store_true", help="facts + filings only, no 10-K text")
    p.add_argument("--skip-embed", action="store_true")
    p.add_argument("--refresh", action="store_true", help="ignore the on-disk API cache")
    args = p.parse_args(argv)

    settings = get_settings()
    years = [int(y) for y in args.years.split(",")] if args.years else settings.fiscal_years
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    companies = read_companies(settings.companies_csv, tickers, args.limit)
    concepts = pd.read_csv(settings.concepts_csv)
    client = EdgarClient(refresh=args.refresh)

    migrate()
    t0 = time.time()
    totals: dict = {"companies_ok": 0, "companies_failed": 0}
    with connect() as conn:
        run_id = load.start_run(conn, {"tickers": list(companies["ticker"]), "years": years})
        load.upsert_concepts(conn, concepts)
        conn.commit()
        ciks = resolve_ciks(client)
        for row in companies.itertuples(index=False):
            cik = int(row.cik) if pd.notna(row.cik) else ciks.get(row.ticker.replace(".", "-"))
            try:
                if cik is None:
                    raise LookupError(f"ticker {row.ticker} not in SEC company_tickers.json")
                stats = process_company(conn, client, row, cik, concepts, years, run_id, args.skip_text, settings)
                conn.commit()
                totals["companies_ok"] += 1
                for k, v in stats.items():
                    totals[k] = totals.get(k, 0) + v
                log.info("%-5s %s", row.ticker, stats)
            except Exception as e:  # isolate per-company failures; the run continues
                conn.rollback()
                totals["companies_failed"] += 1
                log.exception("%s failed", row.ticker)
                load.write_audit(conn, run_id, [quality.audit("company_etl", "error", row.ticker, error=repr(e))])
                conn.commit()

        load.refresh_materialized_views(conn)
        imbalanced = conn.execute(quality.BALANCE_CHECK_SQL).fetchall()
        load.write_audit(conn, run_id, [
            quality.audit("balance_sheet_identity", "warning", f"{r[0]}/FY{r[1]}", assets=r[2], liabilities=r[3], equity=r[4])
            for r in imbalanced])
        conn.commit()

        if not args.skip_embed and not args.skip_text:
            from secqa.rag.embeddings import embed_pending
            totals["chunks_embedded"] = embed_pending(conn)

        totals["seconds"] = round(time.time() - t0, 1)
        totals["http_requests"] = client.http_requests
        status = "failed" if totals["companies_ok"] == 0 and len(companies) else "succeeded"
        load.finish_run(conn, run_id, status, totals)
        conn.commit()
    log.info("run %s %s: %s", run_id, status, totals)


if __name__ == "__main__":
    main()
