"""Find a company's 10-K filings for the requested fiscal years from the submissions API."""

from datetime import date

from secqa.edgar.client import EdgarClient
from secqa.etl.facts import fiscal_year_for


def _rows(block: dict):
    keys = ["accessionNumber", "form", "filingDate", "reportDate", "primaryDocument"]
    for vals in zip(*(block.get(k, []) for k in keys), strict=False):
        yield dict(zip(keys, vals, strict=False))


def find_10k_filings(client: EdgarClient, cik: int, fiscal_years: list[int], submissions: dict | None = None) -> list[dict]:
    sub = submissions or client.submissions(cik)
    wanted = set(fiscal_years)
    found: dict[int, dict] = {}

    blocks = [sub["filings"]["recent"]]
    pages = sub["filings"].get("files", [])
    oldest_needed = date(min(wanted) - 1, 1, 1)
    i = 0
    while i < len(blocks):
        for r in _rows(blocks[i]):
            if r["form"] != "10-K" or not r["reportDate"]:
                continue
            fy = fiscal_year_for(date.fromisoformat(r["reportDate"]))
            # Keep the first (= most recent) original 10-K per fiscal year.
            if fy in wanted and fy not in found:
                found[fy] = {
                    "accession": r["accessionNumber"],
                    "cik": cik,
                    "form": r["form"],
                    "fiscal_year": fy,
                    "period_end": r["reportDate"],
                    "filed_date": r["filingDate"],
                    "primary_doc": r["primaryDocument"],
                    "url": client.filing_url(cik, r["accessionNumber"], r["primaryDocument"]),
                }
        # Heavy filers (banks issuing notes) push older 10-Ks out of "recent" into paged files.
        if len(found) < len(wanted) and i < len(pages) and date.fromisoformat(pages[i]["filingTo"]) >= oldest_needed:
            blocks.append(client.submissions_page(pages[i]["name"]))
        i += 1
    return [found[fy] for fy in sorted(found)]
