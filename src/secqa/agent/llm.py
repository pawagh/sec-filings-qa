"""LLM answer generation over grounded context. Providers: Gemini (LangChain), Ollama (local Granite), none."""

import logging
import re

import httpx

from secqa.agent.answer import SECTION_LABELS
from secqa.config import get_settings
from secqa.rag.retrieval import Passage

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial filings analyst. Answer ONLY from the provided FACTS and PASSAGES.
- FACTS come from XBRL data in SEC 10-K filings and are authoritative for numbers. Do not recompute or
  invent numbers; quote them as given.
- PASSAGES are excerpts from 10-K text. Cite every claim drawn from a passage with its marker, e.g. [2].
- If the context does not contain the answer, say so plainly.
- Be concise: 2-6 sentences, or a short list for multi-part questions."""


def build_user_prompt(question: str, facts: list[str], passages: list[Passage]) -> str:
    fact_block = "\n".join(f"- {f}" for f in facts) or "(none)"
    pass_block = "\n\n".join(
        f"[{i + 1}] {p.company} ({p.ticker}) FY{p.fiscal_year} 10-K, {SECTION_LABELS.get(p.section, p.section)}:\n"
        f"{p.content}" for i, p in enumerate(passages)) or "(none)"
    return f"QUESTION: {question}\n\nFACTS:\n{fact_block}\n\nPASSAGES:\n{pass_block}"


def cited_markers(text: str, n_passages: int) -> list[int]:
    found = {int(m) for group in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", text) for m in re.split(r"\s*,\s*", group)}
    return sorted(i - 1 for i in found if 1 <= i <= n_passages)


def provider() -> str:
    s = get_settings()
    if s.llm_provider == "gemini" and not s.gemini_api_key:
        log.warning("LLM_PROVIDER=gemini but GEMINI_API_KEY is empty; using extractive answers")
        return "none"
    return s.llm_provider


def gemini_chat():
    from langchain_google_genai import ChatGoogleGenerativeAI

    s = get_settings()
    return ChatGoogleGenerativeAI(model=s.gemini_model, google_api_key=s.gemini_api_key, temperature=0)


def generate(question: str, facts: list[str], passages: list[Passage]) -> str | None:
    """Returns the LLM answer, or None when no provider is configured or the call fails."""
    p = provider()
    if p == "none":
        return None
    user = build_user_prompt(question, facts, passages)
    try:
        if p == "gemini":
            from langchain_core.messages import HumanMessage, SystemMessage

            msg = gemini_chat().invoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(user)])
            return msg.content if isinstance(msg.content, str) else "".join(
                part.get("text", "") for part in msg.content if isinstance(part, dict))
        if p == "ollama":
            s = get_settings()
            r = httpx.post(f"{s.ollama_url}/api/chat", timeout=120, json={
                "model": s.ollama_model, "stream": False, "options": {"temperature": 0},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]})
            r.raise_for_status()
            return r.json()["message"]["content"]
    except Exception:
        log.exception("LLM call failed (%s); falling back to extractive answer", p)
    return None
