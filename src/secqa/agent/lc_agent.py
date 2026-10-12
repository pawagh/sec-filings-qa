"""LangChain tool-calling agent (Gemini). The model chooses between the fixed SQL tools and filing search.

It cannot write SQL: every tool is a parameterized function from `sql_tools` / `retrieval`.
Used when LLM_PROVIDER=gemini and router="llm"; otherwise the rule router in `router.py` runs.
"""

import json
import re

import psycopg
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from secqa.agent import answer, llm, sql_tools
from secqa.rag import retrieval

AGENT_PROMPT = llm.SYSTEM_PROMPT + """
You have tools. Use the SQL tools for any number (never estimate figures), and search_filings for
explanations, risks, strategy, or anything narrative. Use both for mixed questions. Tickers are
uppercase symbols (Apple = AAPL). Fiscal years are integers like 2024. Cite passages as [chunk_id]."""

MAX_STEPS = 5


def build_tools(conn: psycopg.Connection, top_k: int, sink: dict):
    metric_names = ", ".join(sorted(sql_tools.METRICS))

    @tool
    def compare_metric_over_years(ticker: str, metric: str, start_year: int, end_year: int) -> str:
        """Year-by-year values of a financial metric for one company, with YoY change, total change and CAGR."""
        args = {"ticker": ticker.upper(), "metric": metric, "start_year": start_year, "end_year": end_year}
        res = {"tool": "compare_metric_over_years", "args": args,
               "result": sql_tools.compare_metric_over_years(conn, **args)}
        sink["sql"].append(res)
        return "\n".join(answer.describe_sql_result(res))

    @tool
    def get_metric(tickers: list[str], metric: str, years: list[int] | None = None) -> str:
        """Values of one financial metric for one or more companies in given fiscal years."""
        args = {"tickers": [t.upper() for t in tickers], "metric": metric, "years": years}
        res = {"tool": "get_metric", "args": args, "result": sql_tools.get_metric(conn, **args)}
        sink["sql"].append(res)
        return "\n".join(answer.describe_sql_result(res))

    @tool
    def rank_companies_by_metric(metric: str, fiscal_year: int, sector: str | None = None,
                                 descending: bool = True, limit: int = 5) -> str:
        """Rank all loaded companies (optionally within a GICS sector) by a metric in one fiscal year."""
        args = {"metric": metric, "fiscal_year": fiscal_year, "sector": sector, "descending": descending,
                "limit": limit}
        res = {"tool": "rank_companies_by_metric", "args": args,
               "result": sql_tools.rank_companies_by_metric(conn, **args)}
        sink["sql"].append(res)
        return "\n".join(answer.describe_sql_result(res))

    @tool
    def search_filings(query: str, tickers: list[str] | None = None, fiscal_years: list[int] | None = None,
                       sections: list[str] | None = None) -> str:
        """Hybrid search over 10-K text. sections: item1 (Business), item1a (Risk Factors), item7 (MD&A)."""
        ps = retrieval.search(conn, query, tickers=[t.upper() for t in tickers] if tickers else None,
                              years=fiscal_years, sections=sections, top_k=top_k)
        sink["passages"].extend(ps)
        return json.dumps([{"chunk_id": p.chunk_id, "ticker": p.ticker, "fiscal_year": p.fiscal_year,
                            "section": p.section, "text": p.content} for p in ps])

    for t in (compare_metric_over_years, get_metric, rank_companies_by_metric):
        t.description += f" Valid metrics: {metric_names}."
    return [compare_metric_over_years, get_metric, rank_companies_by_metric, search_filings]


def ask_with_tools(conn: psycopg.Connection, question: str, top_k: int = 5) -> dict:
    sink: dict = {"sql": [], "passages": []}
    tools = build_tools(conn, top_k, sink)
    by_name = {t.name: t for t in tools}
    model = llm.gemini_chat().bind_tools(tools)
    messages = [SystemMessage(AGENT_PROMPT), HumanMessage(question)]
    final = None
    for _ in range(MAX_STEPS):
        ai = model.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:
            final = ai.content if isinstance(ai.content, str) else "".join(
                p.get("text", "") for p in ai.content if isinstance(p, dict))
            break
        for call in ai.tool_calls:
            try:
                out = by_name[call["name"]].invoke(call["args"])
            except Exception as e:  # surface tool errors to the model so it can correct its arguments
                out = f"ERROR: {e}"
            messages.append(ToolMessage(content=str(out), tool_call_id=call["id"]))

    passages = list({p.chunk_id: p for p in sink["passages"]}.values())
    cited_ids = {int(x) for x in re.findall(r"\[(\d+)\]", final or "")}
    route = "both" if sink["sql"] and passages else "sql" if sink["sql"] else "rag"
    return {
        "question": question,
        "answer": final or "The agent did not produce an answer.",
        "route": route,
        "plan": {"router": "llm", "tool_calls": [m.tool_calls for m in messages if getattr(m, "tool_calls", None)]},
        "sql_results": sink["sql"],
        "passages": [p.to_dict() for p in passages],
        "citations": answer.citations_from_sql(sink["sql"])
        + [answer.passage_citation(p) for p in passages if p.chunk_id in cited_ids],
        "llm_provider": "gemini-tools",
    }
