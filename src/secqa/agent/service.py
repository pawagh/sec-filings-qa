"""Question -> plan -> SQL tools and/or hybrid retrieval -> grounded answer with citations."""

import logging
import time
from dataclasses import dataclass

import psycopg

from secqa.agent import answer, llm, sql_tools
from secqa.agent.router import Company, Plan, route
from secqa.rag import retrieval

log = logging.getLogger(__name__)


@dataclass
class Catalog:
    companies: list[Company]
    years: list[int]
    sectors: list[str]
    loaded_at: float


_catalog: Catalog | None = None


def get_catalog(conn: psycopg.Connection, ttl: float = 300) -> Catalog:
    global _catalog
    if _catalog is None or time.time() - _catalog.loaded_at > ttl:
        rows = conn.execute("SELECT ticker, name, sector, aliases FROM companies ORDER BY ticker").fetchall()
        _catalog = Catalog(companies=[Company(*r) for r in rows], years=sql_tools.available_years(conn),
                           sectors=sql_tools.sectors(conn), loaded_at=time.time())
    return _catalog


def run_sql(conn: psycopg.Connection, plan: Plan, catalog: Catalog) -> list[dict]:
    results = []
    if plan.rank:
        args = dict(plan.rank)
        results.append({"tool": "rank_companies_by_metric", "args": args,
                        "result": sql_tools.rank_companies_by_metric(conn, **args)})
        return results
    years = plan.years or catalog.years
    for ticker in plan.tickers:
        for metric in plan.metrics:
            if len(years) == 1:
                args = {"tickers": [ticker], "metric": metric, "years": years}
                results.append({"tool": "get_metric", "args": args, "result": sql_tools.get_metric(conn, **args)})
            else:
                # Ask for one extra prior year so the first requested year gets a YoY figure.
                args = {"ticker": ticker, "metric": metric, "start_year": min(years), "end_year": max(years)}
                results.append({"tool": "compare_metric_over_years", "args": args,
                                "result": sql_tools.compare_metric_over_years(conn, **args)})
    return results


def run_retrieval(conn: psycopg.Connection, question: str, plan: Plan, catalog: Catalog, top_k: int,
                  mode: str = "hybrid") -> list[retrieval.Passage]:
    years = [y for y in plan.years if y in catalog.years] or None
    passages = retrieval.search(conn, question, tickers=plan.tickers or None, years=years,
                                sections=plan.sections or None, top_k=top_k, mode=mode)
    if len(passages) < top_k and plan.sections:  # section guess too narrow: relax it
        passages = retrieval.search(conn, question, tickers=plan.tickers or None, years=years,
                                    top_k=top_k, mode=mode)
    return passages


def ask(conn: psycopg.Connection, question: str, top_k: int = 5, retrieval_mode: str = "hybrid",
        router: str = "rules") -> dict:
    t0 = time.perf_counter()
    if router == "llm" and llm.provider() == "gemini":
        try:
            from secqa.agent.lc_agent import ask_with_tools

            out = ask_with_tools(conn, question, top_k=top_k)
            out["latency_ms"] = round((time.perf_counter() - t0) * 1000)
            return out
        except Exception:
            log.exception("tool-calling agent failed; falling back to rule router")

    catalog = get_catalog(conn)
    plan = route(question, catalog.companies, catalog.years, catalog.sectors)
    notes = []
    sql_results: list[dict] = []
    if plan.route in ("sql", "both"):
        if plan.tickers or plan.rank:
            sql_results = run_sql(conn, plan, catalog)
        else:
            notes.append("No company recognized for the numeric part; searched filing text instead.")
            plan.route = "rag"
    t_sql = time.perf_counter()

    passages: list[retrieval.Passage] = []
    if plan.route in ("rag", "both"):
        passages = run_retrieval(conn, question, plan, catalog, top_k, retrieval_mode)
    t_ret = time.perf_counter()

    facts = [line for res in sql_results for line in answer.describe_sql_result(res)]
    generated = llm.generate(question, facts, passages) if (facts or passages) else None
    citations = answer.citations_from_sql(sql_results)
    if generated:
        used = llm.cited_markers(generated, len(passages))
        text = generated
        provider = llm.provider()
    else:
        summary, used = answer.extractive_summary(question, passages) if passages else ("", [])
        text = "\n\n".join(x for x in [" ".join(facts), summary] if x) or \
            "I couldn't find data for that question in the loaded filings."
        provider = "extractive"
    citations += [answer.passage_citation(passages[i]) for i in used]
    if notes:
        text = " ".join(notes) + "\n\n" + text

    return {
        "question": question,
        "answer": text,
        "route": plan.route,
        "plan": plan.to_dict(),
        "sql_results": sql_results,
        "passages": [p.to_dict() for p in passages],
        "citations": citations,
        "llm_provider": provider,
        "timings_ms": {"sql": round((t_sql - t0) * 1000), "retrieval": round((t_ret - t_sql) * 1000),
                       "total": round((time.perf_counter() - t0) * 1000)},
        "latency_ms": round((time.perf_counter() - t0) * 1000),
    }
