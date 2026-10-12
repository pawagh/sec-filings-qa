"""Draft gold answers from SQL, then verify them against the filings, and write eval/questions.jsonl.

Verification (the part that makes the eval non-circular):
  * a reported metric's value must appear in one of that company's cached 10-K documents, as rendered
    in the statements (in millions with thousands separators, or in thousands, or the EPS figure)
  * a derived metric (margins) is verified when its components are verified
  * key phrases must occur in at least one chunk of the gold source section(s)
Items that fail stay in the file with verified=false and are excluded from headline numeric accuracy.
Hand-review a sample of the verified items too; `verified_method` records how each one was checked.

    python eval/build_gold.py
"""

import json
import re
import sys
import warnings
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup

from secqa.agent import sql_tools
from secqa.config import get_settings
from secqa.db import connect

warnings.filterwarnings("ignore", module="bs4")
sys.path.insert(0, str(Path(__file__).parent))
from spec import Q  # noqa: E402

OUT = Path(__file__).parent / "questions.jsonl"
DERIVED = {"gross_margin": ["revenue", "cost_of_revenue"], "operating_margin": ["operating_income", "revenue"],
           "net_margin": ["net_income", "revenue"], "free_cash_flow": ["operating_cash_flow", "capex"]}


@lru_cache(maxsize=64)
def filing_text(cik: int) -> str:
    root = get_settings().cache_dir / "filings" / str(cik)
    parts = []
    for p in sorted(root.glob("*/*.htm")):
        if not re.match(r"R\d+\.htm$", p.name):
            # Deliberately independent of the ETL's text pipeline: plain get_text over the raw HTML.
            parts.append(BeautifulSoup(p.read_bytes(), "lxml").get_text(" "))
    return re.sub(r"\s+", " ", "\n".join(parts))


def renderings(value: float, kind: str) -> list[str]:
    if kind == "per_share":
        return [f"{value:.2f}"]
    a = abs(value)
    return [f"{round(a / 1e6):,}", f"{round(a / 1e3):,}", f"{a / 1e9:,.1f} billion"]


def verify_value(conn, ticker: str, metric: str, fy: int) -> tuple[float | None, bool, str]:
    rows = sql_tools.get_metric(conn, [ticker], metric, [fy])
    if not rows:
        return None, False, "missing_in_db"
    value = rows[0]["value"]
    cik = conn.execute("SELECT cik FROM companies WHERE ticker = %s", (ticker,)).fetchone()[0]
    if metric in DERIVED:
        ok = all(verify_value(conn, ticker, m, fy)[1] for m in DERIVED[metric])
        return value, ok, "auto:components_in_filing" if ok else "unverified:component_not_found"
    text = filing_text(cik)
    hit = next((r for r in renderings(value, sql_tools.METRICS[metric].kind) if re.search(rf"(?<![\d,.]){re.escape(r)}(?![\d,])", text)), None)
    return value, hit is not None, f"auto:value_in_filing({hit})" if hit else "unverified:not_found_in_filing"


def phrase_coverage(conn, sources, phrases) -> list[str]:
    found = []
    for ph in phrases:
        for ticker, fy, section in sources:
            n = conn.execute(
                """SELECT count(*) FROM document_chunks ch JOIN companies c USING (cik)
                   WHERE c.ticker = %s AND (%s::int IS NULL OR ch.fiscal_year = %s)
                     AND (%s::text IS NULL OR ch.section = %s) AND ch.content ILIKE %s""",
                (ticker, fy, fy, section, section, f"%{ph}%")).fetchone()[0]
            if n:
                found.append(ph)
                break
    return found


def main() -> None:
    items = []
    with connect() as conn:
        for spec in Q:
            item = {"id": spec["id"], "question": spec["q"], "expected_route": spec["route"],
                    "type": {"sql": "numeric", "rag": "narrative", "both": "hybrid"}[spec["route"]],
                    "gold_numbers": [], "gold_sources": [], "key_phrases": spec.get("phrases", [])}
            if "rank" in spec:
                metric, fy, sector, desc = spec["rank"]
                top = sql_tools.rank_companies_by_metric(conn, metric, fy, sector, desc, limit=1)[0]
                v, ok, how = verify_value(conn, top["ticker"], metric, fy)
                item["gold_numbers"].append({"ticker": top["ticker"], "metric": metric, "fiscal_year": fy,
                                             "kind": top["kind"], "value": v, "verified": ok, "verified_method": how})
            for ticker, metric, fy in spec.get("numbers", []):
                v, ok, how = verify_value(conn, ticker, metric, fy)
                item["gold_numbers"].append({"ticker": ticker, "metric": metric, "fiscal_year": fy,
                                             "kind": sql_tools.METRICS[metric].kind, "value": v, "verified": ok,
                                             "verified_method": how})
            for ticker, fy, section in spec.get("sources", []):
                item["gold_sources"].append({"ticker": ticker, "fiscal_year": fy, "section": section})
            if item["key_phrases"]:
                covered = phrase_coverage(conn, spec.get("sources", []), item["key_phrases"])
                item["phrases_in_corpus"] = covered
            item["verified"] = all(g["verified"] for g in item["gold_numbers"]) and (
                not item["key_phrases"] or bool(item.get("phrases_in_corpus")))
            items.append(item)
            flag = "ok " if item["verified"] else "!! "
            nums = ", ".join(f"{g['ticker']} {g['metric']} FY{g['fiscal_year']}={g['value']} [{g['verified_method']}]"
                             for g in item["gold_numbers"])
            print(f"{flag}{item['id']} {nums} {item.get('phrases_in_corpus', '')}")
    OUT.write_text("".join(json.dumps(i) + "\n" for i in items))
    print(f"wrote {len(items)} items to {OUT} ({sum(i['verified'] for i in items)} verified)")


if __name__ == "__main__":
    main()
