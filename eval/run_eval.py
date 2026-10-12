"""Run the 50-question eval end to end and write eval/results/<timestamp>.{json,md}.

Metrics
  routing accuracy        predicted route == expected route
  numeric exact match     every gold number appears in the answer (±0.5% relative, unit-normalized);
                          headline uses verified items only
  retrieval recall@5/MRR  a top-5 passage matches a gold (ticker, year, section) AND contains a key phrase
  citation precision      share of citations that point at a gold source / gold company-metric
  answer correctness      numeric: all numbers right; narrative: cites a gold source and the answer
                          contains a key phrase; hybrid: both
Ablations (narrative + hybrid items): hybrid vs vector-only vs full-text-only, with and without
router metadata filters.

    python eval/run_eval.py [--router rules|llm] [--no-ablations]
"""

import argparse
import json
import re
import statistics
import time
from datetime import datetime
from pathlib import Path

from secqa.agent import service
from secqa.agent.router import Plan
from secqa.db import connect

HERE = Path(__file__).parent
TOL = 0.005

_NUM = re.compile(
    r"(?P<neg>-)?\$?(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>%|B\b|M\b|K\b|billion|million|thousand|trillion|T\b)?",
    re.IGNORECASE)
_SCALE = {"b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3, "t": 1e12, "trillion": 1e12}


def parse_numbers(text: str) -> list[tuple[float, str]]:
    out = []
    for m in _NUM.finditer(text):
        try:
            v = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        unit = (m.group("unit") or "").lower()
        if unit == "%":
            out.append((v / 100, "ratio"))
            continue
        v *= _SCALE.get(unit, 1)
        out.append((-v if m.group("neg") else v, "abs"))
    return out


def number_in_answer(gold: dict, nums: list[tuple[float, str]]) -> bool:
    g = gold["value"]
    if g is None:
        return False
    want = "ratio" if gold["kind"] == "ratio" else "abs"
    for v, kind in nums:
        if kind != want:
            continue
        if gold["kind"] == "ratio" and abs(v - g) <= 0.0006:  # one decimal of a percentage
            return True
        if g != 0 and abs(abs(v) - abs(g)) / abs(g) <= TOL:
            return True
    return False


def source_match(src: dict, ticker: str, fy: int | None, section: str | None) -> bool:
    return (src["ticker"] == ticker and (src["fiscal_year"] is None or src["fiscal_year"] == fy)
            and (src["section"] is None or src["section"] == section))


def passage_hit(p: dict, item: dict) -> bool:
    if not any(source_match(s, p["ticker"], p["fiscal_year"], p["section"]) for s in item["gold_sources"]):
        return False
    phrases = item.get("key_phrases") or []
    return not phrases or any(ph.lower() in p["content"].lower() for ph in phrases)


def retrieval_scores(passages: list[dict], item: dict, k: int = 5) -> tuple[bool, float]:
    for rank, p in enumerate(passages[:k], 1):
        if passage_hit(p, item):
            return True, 1 / rank
    return False, 0.0


def citation_precision(cites: list[dict], item: dict) -> float | None:
    if not cites:
        return None
    gold_cos = {(g["ticker"], g["metric"]) for g in item["gold_numbers"]}
    good = 0
    for c in cites:
        if c["type"] == "filing_text":
            good += any(source_match(s, c["ticker"], c["fiscal_year"], c["section"]) for s in item["gold_sources"])
        else:
            good += (c["ticker"], c["metric"]) in gold_cos or (
                item["expected_route"] == "sql" and not gold_cos)
    return good / len(cites)


def evaluate_item(conn, item: dict, router: str) -> dict:
    t0 = time.perf_counter()
    out = service.ask(conn, item["question"], top_k=5, router=router)
    latency = (time.perf_counter() - t0) * 1000
    nums = parse_numbers(out["answer"])
    num_hits = [number_in_answer(g, nums) for g in item["gold_numbers"]]
    hit5, rr = retrieval_scores(out["passages"], item) if item["gold_sources"] else (None, None)
    phrase_in_answer = any(ph.lower() in out["answer"].lower() for ph in item.get("key_phrases") or [])
    cites_gold_source = any(c["type"] == "filing_text" and any(
        source_match(s, c["ticker"], c["fiscal_year"], c["section"]) for s in item["gold_sources"])
        for c in out["citations"])
    numeric_ok = all(num_hits) if num_hits else None
    narrative_ok = (phrase_in_answer and cites_gold_source) if item["gold_sources"] else None
    correct = all(x for x in (numeric_ok, narrative_ok) if x is not None)
    return {
        "id": item["id"], "type": item["type"], "question": item["question"], "verified": item["verified"],
        "expected_route": item["expected_route"], "route": out["route"],
        "route_correct": out["route"] == item["expected_route"],
        "numbers_expected": len(num_hits), "numbers_matched": sum(num_hits), "numeric_correct": numeric_ok,
        "recall_at_5": hit5, "reciprocal_rank": rr, "citation_precision": citation_precision(out["citations"], item),
        "answer_correct": correct, "latency_ms": round(latency), "answer": out["answer"],
        "citations": out["citations"], "plan": out["plan"],
    }


def ablations(conn, items: list[dict]) -> dict:
    catalog = service.get_catalog(conn)
    from secqa.agent.router import route

    rows = {}
    for mode in ("hybrid", "vector", "fts"):
        for filtered in (True, False):
            hits, rrs = [], []
            for item in items:
                plan = route(item["question"], catalog.companies, catalog.years, catalog.sectors)
                if not filtered:
                    plan = Plan(route="rag", tickers=[], years=[], metrics=[], sections=[])
                ps = [p.to_dict() for p in service.run_retrieval(conn, item["question"], plan, catalog, 5, mode)]
                h, rr = retrieval_scores(ps, item)
                hits.append(h)
                rrs.append(rr)
            rows[f"{mode}{'' if filtered else ' (no filters)'}"] = {
                "recall_at_5": sum(hits) / len(hits), "mrr": statistics.mean(rrs)}
    return rows


def rate(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def summarize(results: list[dict]) -> dict:
    verified = [r for r in results if r["verified"]]
    s = {
        "n_questions": len(results), "n_verified": len(verified),
        "routing_accuracy": rate(r["route_correct"] for r in results),
        "numeric_exact_match_verified": rate(r["numeric_correct"] for r in verified),
        "numbers_matched": f"{sum(r['numbers_matched'] for r in verified)}/{sum(r['numbers_expected'] for r in verified)}",
        "retrieval_recall_at_5": rate(r["recall_at_5"] for r in results),
        "retrieval_mrr": rate(r["reciprocal_rank"] for r in results),
        "citation_precision": rate(r["citation_precision"] for r in results),
        "answer_correctness": rate(r["answer_correct"] for r in results),
        "latency_ms_p50": statistics.median(r["latency_ms"] for r in results),
        "latency_ms_p95": sorted(r["latency_ms"] for r in results)[int(0.95 * (len(results) - 1))],
    }
    s["by_type"] = {t: {"n": len(rs), "answer_correctness": rate(r["answer_correct"] for r in rs),
                        "routing_accuracy": rate(r["route_correct"] for r in rs)}
                    for t in ("numeric", "narrative", "hybrid") if (rs := [r for r in results if r["type"] == t])}
    return s


def pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def to_markdown(summary: dict, abl: dict | None, results: list[dict], meta: dict) -> str:
    lines = [f"# Eval results — {meta['timestamp']}", "",
             f"Router: `{meta['router']}` · answer generation: `{meta['llm_provider']}` · "
             f"{summary['n_questions']} questions ({summary['n_verified']} with verified gold)", "",
             "| Metric | Value |", "|---|---|",
             f"| Routing accuracy | {pct(summary['routing_accuracy'])} |",
             f"| Numeric exact match (verified items) | {pct(summary['numeric_exact_match_verified'])} "
             f"({summary['numbers_matched']} numbers) |",
             f"| Retrieval recall@5 | {pct(summary['retrieval_recall_at_5'])} |",
             f"| Retrieval MRR | {summary['retrieval_mrr']:.3f} |",
             f"| Citation precision | {pct(summary['citation_precision'])} |",
             f"| Answer correctness (key-fact) | {pct(summary['answer_correctness'])} |",
             f"| Latency p50 / p95 | {summary['latency_ms_p50']:.0f} / {summary['latency_ms_p95']:.0f} ms |", "",
             "## By question type", "", "| Type | n | Answer correctness | Routing |", "|---|---|---|---|"]
    for t, v in summary["by_type"].items():
        lines.append(f"| {t} | {v['n']} | {pct(v['answer_correctness'])} | {pct(v['routing_accuracy'])} |")
    if abl:
        lines += ["", "## Retrieval ablation (narrative + hybrid items)", "", "| Retriever | recall@5 | MRR |", "|---|---|---|"]
        lines += [f"| {k} | {pct(v['recall_at_5'])} | {v['mrr']:.3f} |" for k, v in abl.items()]
    fails = [r for r in results if not r["answer_correct"] or not r["route_correct"]]
    if fails:
        lines += ["", "## Misses", "", "| id | route (exp→got) | numbers | recall@5 | question |", "|---|---|---|---|---|"]
        for r in fails:
            lines.append(f"| {r['id']} | {r['expected_route']}→{r['route']} | {r['numbers_matched']}/{r['numbers_expected']} "
                         f"| {r['recall_at_5']} | {r['question']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--router", default="rules", choices=["rules", "llm"])
    p.add_argument("--no-ablations", action="store_true")
    p.add_argument("--questions", default=str(HERE / "questions.jsonl"))
    args = p.parse_args()

    items = [json.loads(line) for line in Path(args.questions).read_text().splitlines() if line.strip()]
    with connect() as conn:
        service.ask(conn, "warm up the embedding model", router="rules")
        results = [evaluate_item(conn, it, args.router) for it in items]
        abl = None if args.no_ablations else ablations(conn, [i for i in items if i["gold_sources"]])
    from secqa.agent import llm

    meta = {"timestamp": datetime.now().strftime("%Y%m%d-%H%M%S"), "router": args.router,
            "llm_provider": llm.provider() if llm.provider() != "none" else "extractive (no LLM)"}
    summary = summarize(results)
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{meta['timestamp']}.json").write_text(json.dumps(
        {"meta": meta, "summary": summary, "ablations": abl, "results": results}, indent=2, default=str))
    md = to_markdown(summary, abl, results, meta)
    (out_dir / f"{meta['timestamp']}.md").write_text(md)
    (out_dir / "latest.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
