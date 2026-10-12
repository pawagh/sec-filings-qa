import pytest

from secqa.agent.router import Company, extract_metrics, extract_years, route

COMPANIES = [Company("AAPL", "Apple Inc.", "Information Technology", ["Apple"]),
             Company("MSFT", "Microsoft Corp", "Information Technology", ["Microsoft"]),
             Company("TGT", "Target Corp", "Consumer Staples", ["Target"]),
             Company("V", "Visa Inc.", "Financials", ["Visa"]),
             Company("JPM", "JPMorgan Chase", "Financials", ["JPMorgan", "JP Morgan"])]
YEARS = [2023, 2024, 2025]
SECTORS = ["Consumer Staples", "Financials", "Information Technology"]


def r(q):
    return route(q, COMPANIES, YEARS, SECTORS)


@pytest.mark.parametrize("q,expected", [
    ("What was Apple's revenue in 2024?", "sql"),
    ("What risks does Microsoft cite about AI?", "rag"),
    ("How did Apple's R&D spending change from 2022 to 2024, and what risks did it cite?", "both"),
    ("Which company had the highest net income in 2024?", "sql"),
    ("Describe JPMorgan's business segments", "rag"),
])
def test_route(q, expected):
    assert r(q).route == expected


def test_entities_from_example_question():
    p = r("How did Apple's R&D spending change from 2022 to 2024, and what risks did it cite?")
    assert p.tickers == ["AAPL"] and p.years == [2022, 2023, 2024] and p.metrics == ["rd_expense"]
    assert "item1a" in p.sections


def test_single_word_aliases_are_case_sensitive():
    assert r("Did revenue hit the target in 2024 with a visa?").tickers == []
    assert r("Compare Target and Visa revenue in 2024").tickers == ["TGT", "V"]


def test_specific_metric_wins_over_generic():
    assert extract_metrics("What was the gross profit and operating margin?") == ["operating_margin", "gross_profit"]
    assert extract_metrics("net income and free cash flow") == ["free_cash_flow", "net_income"]


def test_years():
    assert extract_years("FY24 vs FY2025", YEARS) == [2024, 2025]
    assert extract_years("over the last two years", YEARS) == [2024, 2025]
    assert extract_years("the most recent year", YEARS) == [2025]


def test_rank_plan():
    p = r("Which financials company had the lowest net margin in 2024? top 3")
    assert p.rank == {"metric": "net_margin", "fiscal_year": 2024, "sector": "Financials",
                      "descending": False, "limit": 3}
