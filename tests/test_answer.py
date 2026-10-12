from secqa.agent.answer import describe_sql_result, fmt_value
from secqa.agent.llm import cited_markers


def test_fmt_value():
    assert fmt_value(31_370_000_000, "money") == "$31.37B"
    assert fmt_value(-5_500_000, "money") == "-$5.5M"
    assert fmt_value(0.4621, "ratio") == "46.2%"
    assert fmt_value(6.11, "per_share") == "$6.11"


def test_cited_markers():
    assert cited_markers("A [1]. B [2, 3]. C [9].", 3) == [0, 1, 2]


def test_describe_compare():
    res = {"tool": "compare_metric_over_years", "args": {}, "result": {
        "ticker": "AAPL", "metric": "rd_expense", "label": "R&D expense", "kind": "money",
        "series": [{"fiscal_year": 2022, "value": 26.251e9, "yoy": None},
                   {"fiscal_year": 2024, "value": 31.370e9, "yoy": 0.0487}],
        "change": {"from_year": 2022, "to_year": 2024, "absolute": 5.119e9, "percent": 0.195, "cagr": 0.0932}}}
    line = describe_sql_result(res)[0]
    assert "FY2022 $26.25B" in line and "$5.12B (+19.5%)" in line and "CAGR +9.3%" in line
