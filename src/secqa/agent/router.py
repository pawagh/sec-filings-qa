"""Deterministic question router: classifies sql / rag / both and extracts entities.

This is the always-available router (no API key, fully testable). The LangChain tool-calling
router in `lc_agent.py` is the LLM alternative; both call the same SQL tools and retriever.
"""

import re
from dataclasses import asdict, dataclass, field

# Ordered longest/most-specific first; matched spans are consumed so "gross profit" isn't also "profit".
METRIC_PATTERNS: list[tuple[str, str]] = [
    (r"free cash flows?|fcf", "free_cash_flow"),
    (r"operating cash flows?|cash (?:flows? )?from operations|cash provided by operating", "operating_cash_flow"),
    (r"gross margins?", "gross_margin"),
    (r"operating margins?", "operating_margin"),
    (r"net (?:profit )?margins?|profit margins?", "net_margin"),
    (r"r&d intensity|r&d as a (?:percent|percentage|share)|r&d to revenue", "rd_intensity"),
    (r"revenue growth|sales growth|growth in (?:revenue|sales)", "revenue_yoy"),
    (r"research and development|r\s?&\s?d", "rd_expense"),
    (r"gross profits?", "gross_profit"),
    (r"operating income|operating profits?|operating loss", "operating_income"),
    (r"diluted eps|earnings per share|\beps\b", "eps_diluted"),
    (r"net income|net profits?|net earnings|net loss|\bprofits?\b|\bearnings\b", "net_income"),
    (r"cost of (?:revenue|sales|goods sold)|cogs", "cost_of_revenue"),
    (r"sg&a|selling, general and administrative", "sga_expense"),
    (r"capital expenditures?|capex", "capex"),
    (r"dividends?", "dividends_paid"),
    (r"buybacks?|share repurchases?|repurchases? of (?:common )?stock|stock repurchases?", "share_repurchases"),
    (r"total assets|\bassets\b", "total_assets"),
    (r"total liabilities|\bliabilities\b", "total_liabilities"),
    (r"liabilities[- ]to[- ]equity|leverage", "liabilities_to_equity"),
    (r"(?:stockholders|shareholders)'? equity|book value", "stockholders_equity"),
    (r"long[- ]term debt|\bdebt\b", "long_term_debt"),
    (r"cash and (?:cash )?equivalents|cash on hand|cash balance", "cash"),
    (r"diluted shares|share count|shares outstanding", "diluted_shares"),
    (r"net sales|\brevenues?\b|\bsales\b|top[- ]line", "revenue"),
]

NARRATIVE_CUES = re.compile(
    r"\b(why|explain|risks?|cite[ds]?|describe[sd]?|discuss(?:es|ed)?|strateg\w*|compet\w*|factors?|drivers?|drove|"
    r"attribut\w*|reasons?|outlook|mention\w*|say|said|according|challenges?|uncertaint\w*|regulat\w*|"
    r"supply chain|employees|human capital|segments?|products?|business model|cybersecurity|litigation|"
    r"what does .* do|how does .* make money|tariffs?|macroeconomic|ai\b|artificial intelligence)",
    re.IGNORECASE)
NUMERIC_CUES = re.compile(
    r"\b(how much|what was|what were|change[ds]?|grow(?:th|n)?|grew|increase[ds]?|decrease[ds]?|declin\w*|"
    r"compare[ds]?|comparison|versus|vs\.?|highest|lowest|rank\w*|top \d+|largest|smallest|most|least|total|"
    r"percent(?:age)?|ratio|trend)\b", re.IGNORECASE)
RANK_CUES = re.compile(
    r"\b(which (?:company|companies|firm)|who had|highest|lowest|largest|smallest|top \d+|bottom \d+|rank\w*|"
    r"most|least|leader)\b", re.IGNORECASE)
ASCENDING_CUES = re.compile(r"\b(lowest|smallest|least|bottom)\b", re.IGNORECASE)

SECTION_CUES = [
    ("item1a", re.compile(r"\b(risks?|uncertaint\w*|threats?|could adversely|vulnerab\w*|cybersecurity|"
                          r"regulat\w*|litigation|tariffs?)\b", re.I)),
    ("item1", re.compile(r"\b(business model|products?|segments?|compet\w*|employees|human capital|customers|"
                         r"what does .* do|how does .* make money|strategy|seasonal\w*)\b", re.I)),
    ("item7", re.compile(r"\b(why|drivers?|drove|attribut\w*|explain\w*|results of operations|md&a|management|"
                         r"liquidity|outlook|reasons?|due to)\b", re.I)),
]

_YEAR = re.compile(r"\b(?:fy\s?'?)?(20\d{2})\b|\bfy\s?'?(\d{2})\b", re.IGNORECASE)
_RANGE = re.compile(r"(?:from|between)?\s*(?:fy\s?)?(20\d{2})\s*(?:to|through|thru|and|-|–)\s*(?:fy\s?)?(20\d{2})", re.I)
_LAST_N = re.compile(r"\b(?:last|past|previous|prior)\s+(two|three|four|2|3|4)\s+(?:fiscal\s+)?years\b", re.I)
_LATEST = re.compile(r"\b(latest|most recent|this year|current)\b", re.I)
_TOP_N = re.compile(r"\b(?:top|bottom)\s+(\d+)\b", re.I)
_WORDS = {"two": 2, "three": 3, "four": 4}


@dataclass
class Company:
    ticker: str
    name: str
    sector: str | None
    aliases: list[str] = field(default_factory=list)


@dataclass
class Plan:
    route: str  # sql | rag | both
    tickers: list[str]
    years: list[int]
    metrics: list[str]
    sections: list[str]
    rank: dict | None = None
    sector: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def extract_metrics(q: str) -> list[str]:
    text = q.lower()
    found: list[str] = []
    for pattern, metric in METRIC_PATTERNS:
        for m in re.finditer(pattern, text):
            if metric not in found:
                found.append(metric)
            text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    return found


def extract_years(q: str, available: list[int]) -> list[int]:
    if m := _RANGE.search(q):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return list(range(a, b + 1))
    if m := _LAST_N.search(q):
        n = _WORDS.get(m.group(1).lower()) or int(m.group(1))
        return available[-n:]
    years = []
    for m in _YEAR.finditer(q):
        y = int(m.group(1)) if m.group(1) else 2000 + int(m.group(2))
        if y not in years:
            years.append(y)
    if not years and _LATEST.search(q) and available:
        return [available[-1]]
    return sorted(years)


def extract_companies(q: str, companies: list[Company]) -> list[str]:
    hits: list[tuple[int, str]] = []
    for c in companies:
        positions = [m.start() for m in re.finditer(rf"(?<![\w$]){re.escape(c.ticker)}(?![\w])", q)]
        for alias in c.aliases or [c.name]:
            flags = 0 if len(alias.split()) == 1 else re.IGNORECASE  # "Target"/"Visa" must be capitalized
            positions += [m.start() for m in re.finditer(rf"\b{re.escape(alias)}\b", q, flags)]
        if positions:
            hits.append((min(positions), c.ticker))
    return [t for _, t in sorted(hits)]


def extract_sector(q: str, sectors: list[str]) -> str | None:
    ql = q.lower()
    for s in sectors:
        if s.lower() in ql or (s.lower().split()[0] in ql and len(s.split()[0]) > 6):
            return s
    synonyms = {"tech": "Information Technology", "bank": "Financials", "banks": "Financials",
                "pharma": "Health Care", "healthcare": "Health Care", "retail": "Consumer Staples",
                "oil": "Energy"}
    for word, sector in synonyms.items():
        if re.search(rf"\b{word}\b", ql) and sector in sectors:
            return sector
    return None


def route(question: str, companies: list[Company], available_years: list[int], sectors: list[str]) -> Plan:
    metrics = extract_metrics(question)
    tickers = extract_companies(question, companies)
    years = extract_years(question, available_years)
    narrative = bool(NARRATIVE_CUES.search(question))
    numeric = bool(metrics) and (bool(NUMERIC_CUES.search(question)) or not narrative or bool(years))

    if numeric and narrative:
        r = "both"
    elif numeric:
        r = "sql"
    else:
        r = "rag"

    sections = [name for name, pat in SECTION_CUES if pat.search(question)] if r != "sql" else []
    plan = Plan(route=r, tickers=tickers, years=years, metrics=metrics, sections=sections)

    if r in ("sql", "both") and metrics and RANK_CUES.search(question) and len(tickers) != 1:
        top = _TOP_N.search(question)
        plan.sector = extract_sector(question, sectors)
        plan.rank = {
            "metric": metrics[0],
            "fiscal_year": (years or available_years or [None])[-1],
            "sector": plan.sector,
            "descending": not ASCENDING_CUES.search(question),
            "limit": int(top.group(1)) if top else 5,
        }
    return plan
