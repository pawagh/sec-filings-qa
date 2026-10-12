"""Answer composition: formatting numbers, building citations, and the extractive (no-LLM) answer."""

import re

import numpy as np

from secqa.rag.embeddings import SECTION_LABELS, embed
from secqa.rag.retrieval import Passage


def fmt_value(value: float | None, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "ratio":
        return f"{value * 100:.1f}%"
    if kind == "per_share":
        return f"${value:,.2f}"
    a = abs(value)
    sign = "-" if value < 0 else ""
    unit = "" if kind == "money" else " shares"
    prefix = "$" if kind == "money" else ""
    if a >= 1e9:
        return f"{sign}{prefix}{a / 1e9:,.2f}B{unit}"
    if a >= 1e6:
        return f"{sign}{prefix}{a / 1e6:,.1f}M{unit}"
    return f"{sign}{prefix}{a:,.0f}{unit}"


def fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def fact_citation(ticker: str, fiscal_year: int, metric: str, accession: str | None, url: str | None) -> dict:
    return {"type": "xbrl", "ticker": ticker, "fiscal_year": fiscal_year, "metric": metric,
            "accession": accession, "url": url}


def passage_citation(p: Passage) -> dict:
    return {"type": "filing_text", "ticker": p.ticker, "fiscal_year": p.fiscal_year, "section": p.section,
            "section_label": SECTION_LABELS.get(p.section, p.section), "accession": p.accession,
            "chunk_id": p.chunk_id, "url": p.url}


def describe_sql_result(res: dict) -> list[str]:
    """Human-readable fact lines from a SQL tool result (also fed to the LLM as grounded facts)."""
    lines = []
    if res["tool"] == "compare_metric_over_years":
        d = res["result"]
        if not d["series"]:
            return [f"No {d['label']} data found for {d['ticker']}."]
        parts = []
        for s in d["series"]:
            yoy = f" ({fmt_pct(s['yoy'])} YoY)" if s["yoy"] is not None and d["kind"] != "ratio" else ""
            parts.append(f"FY{s['fiscal_year']} {fmt_value(s['value'], d['kind'])}{yoy}")
        line = f"{d['ticker']} {d['label']}: " + "; ".join(parts) + "."
        ch = d["change"]
        if ch and ch["from_year"] != ch["to_year"]:
            if d["kind"] == "ratio":
                line += (f" Change FY{ch['from_year']}→FY{ch['to_year']}: "
                         f"{ch['absolute'] * 100:+.1f} percentage points.")
            else:
                line += (f" Change FY{ch['from_year']}→FY{ch['to_year']}: {fmt_value(ch['absolute'], d['kind'])} "
                         f"({fmt_pct(ch['percent'])})")
                line += f", CAGR {fmt_pct(ch['cagr'])}." if ch.get("cagr") is not None else "."
        lines.append(line)
    elif res["tool"] == "get_metric":
        for r in res["result"]:
            lines.append(f"{r['ticker']} {r['label']} FY{r['fiscal_year']}: {fmt_value(r['value'], r['kind'])}.")
        if not res["result"]:
            lines.append(f"No data found for {res['args'].get('metric')} {res['args'].get('tickers')}.")
    elif res["tool"] == "rank_companies_by_metric":
        rows = res["result"]
        if rows:
            scope = f" in {res['args']['sector']}" if res["args"].get("sector") else ""
            lines.append(f"Ranking by {rows[0]['label']}, FY{rows[0]['fiscal_year']}{scope}: " + "; ".join(
                f"{r['rank']}. {r['ticker']} {fmt_value(r['value'], r['kind'])}" for r in rows) + ".")
        else:
            lines.append("No companies had data for that ranking.")
    return lines


def citations_from_sql(results: list[dict]) -> list[dict]:
    cites: list[dict] = []
    for res in results:
        if res["tool"] == "compare_metric_over_years":
            d = res["result"]
            cites += [fact_citation(d["ticker"], s["fiscal_year"], d["metric"], s["accession"], s["url"])
                      for s in d["series"]]
        elif res["tool"] in ("get_metric", "rank_companies_by_metric"):
            cites += [fact_citation(r["ticker"], r["fiscal_year"], r["metric"], r["accession"], r["url"])
                      for r in res["result"]]
    return cites


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_CLAUSE = re.compile(r",?\s+and\s+(?=(?:what|why|how|which|did|does)\b)", re.IGNORECASE)
_STOP = set("""a an and the of to in on for by with from at as is are was were be been it its it's this that these
those what which who why how did does do company company's fiscal year years their our we us about say said
describe cite cited change changed from""".split())


def narrative_focus(question: str) -> str:
    """For hybrid questions, the part after ", and what/why/how ..." is what the passages must answer."""
    parts = _CLAUSE.split(question)
    return parts[-1] if len(parts) > 1 else question


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z&'-]+", text.lower()) if w not in _STOP and len(w) > 2}


def extractive_summary(question: str, passages: list[Passage], max_sentences: int = 4) -> tuple[str, list[int]]:
    """Pick the passage sentences that best answer the question; returns (text, used passage indexes).

    score = cosine(sentence, narrative focus of the question)
          + 0.15 * keyword overlap with the full question
          + 0.03 / (1 + passage rank)            (prefer higher-ranked passages)
    """
    cands: list[tuple[int, str]] = []
    for i, p in enumerate(passages[:4]):
        for s in _SENT.split(p.content.replace("\n", " ")):
            s = s.strip()
            if 60 <= len(s) <= 500:
                cands.append((i, s))
    if not cands:
        return "", []
    focus = narrative_focus(question)
    vecs = embed([focus] + [s for _, s in cands])
    sims = vecs[1:] @ vecs[0]
    qk = _keywords(question)
    scores = [sims[j] + 0.15 * (len(qk & _keywords(s)) / max(len(qk), 1)) + 0.03 / (1 + i)
              for j, (i, s) in enumerate(cands)]
    best = sorted(np.argsort(scores)[::-1][:max_sentences])  # keep document order for readability
    used = sorted({cands[j][0] for j in best})
    text = " ".join(f"{cands[j][1]} [{cands[j][0] + 1}]" for j in best)
    return text, used
