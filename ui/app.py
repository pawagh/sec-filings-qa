"""Streamlit front end for the SEC filings Q&A API."""

import os

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
EXAMPLES = [
    "How did Apple's R&D spending change from 2022 to 2024, and what risks did it cite?",
    "Which Information Technology company had the highest operating margin in 2024?",
    "What does Microsoft say about competition in AI?",
    "Compare Nvidia's revenue from 2023 to 2025",
    "What supply chain risks does Tesla describe?",
]

st.set_page_config(page_title="SEC Filings Q&A", layout="wide")
st.title("SEC Filings Q&A")
st.caption("Numbers from XBRL via SQL, explanations from 10-K text via hybrid retrieval. Every claim cited.")

with st.sidebar:
    st.subheader("Examples")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            st.session_state["q"] = ex
    router = st.radio("Router", ["rules", "llm"], help="llm = LangChain tool-calling with Gemini (needs GEMINI_API_KEY)")
    top_k = st.slider("Passages", 1, 10, 5)

q = st.text_input("Ask about a company's 10-K", key="q")
if q:
    with st.spinner("Thinking..."):
        try:
            r = httpx.post(f"{API_URL}/ask", json={"question": q, "top_k": top_k, "router": router}, timeout=120)
            r.raise_for_status()
            out = r.json()
        except httpx.HTTPError as e:
            st.error(f"API error: {e}")
            st.stop()

    st.markdown(out["answer"])
    st.caption(f"route: **{out['route']}** · answer: {out['llm_provider']} · {out['latency_ms']} ms")

    for res in out["sql_results"]:
        if res["tool"] == "compare_metric_over_years" and res["result"]["series"]:
            d = res["result"]
            df = pd.DataFrame(d["series"]).set_index("fiscal_year")[["value"]].rename(columns={"value": d["label"]})
            st.subheader(f"{d['ticker']} · {d['label']}")
            st.bar_chart(df)
        elif res["tool"] == "rank_companies_by_metric" and res["result"]:
            st.dataframe(pd.DataFrame(res["result"])[["rank", "ticker", "company", "value"]], hide_index=True)

    if out["citations"]:
        st.subheader("Sources")
        for i, c in enumerate(out["citations"], 1):
            what = c.get("section_label") or c.get("metric")
            st.markdown(f"{i}. [{c['ticker']} FY{c['fiscal_year']} 10-K — {what}]({c['url']}) · `{c['accession']}`")
    with st.expander("Retrieved passages"):
        for i, p in enumerate(out["passages"], 1):
            st.markdown(f"**[{i}] {p['ticker']} FY{p['fiscal_year']} {p['section']}** · score {p['score']:.4f}")
            st.text(p["content"][:1500])
    with st.expander("Plan / debug"):
        st.json(out["plan"])
