"""Extract 10-K Items 1 (Business), 1A (Risk Factors) and 7 (MD&A) from filing HTML.

10-K HTML is messy: every heading also appears in the table of contents, cross-references are
hyperlinked ("see Item 1A"), and inline-XBRL splits headings across many <span>s. The approach:
  1. Render block-level elements as lines (so spans inside a heading stay on one line).
  2. A heading is a line "Item <n>[.] <expected title>" or a bare "Item <n>." followed by a line
     starting with the expected title. This rejects most cross-references.
  3. For each item, take the *longest* span from one of its headings to the next following item
     heading. The TOC entry yields a tiny span, so the body wins.
"""

import re
import warnings
from collections import Counter
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BLOCK_TAGS = ["p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"]

# item number -> expected title prefix (normalized lowercase, ASCII apostrophes)
ITEM_TITLES = {
    "1": "business",
    "1a": "risk factors",
    "1b": "unresolved staff comments",
    "1c": "cybersecurity",
    "2": "properties",
    "7": "management's discussion and analysis",
    "7a": "quantitative and qualitative disclosure",
    "8": "financial statements",
}
TARGETS = {
    "item1": ("1", ["1a", "1b", "1c", "2"]),
    "item1a": ("1a", ["1b", "1c", "2"]),
    "item7": ("7", ["7a", "8"]),
}
SECTION_TITLES = {"item1": "Item 1. Business", "item1a": "Item 1A. Risk Factors",
                  "item7": "Item 7. Management's Discussion and Analysis"}

_HEADING = re.compile(r"^\s*item\s*(\d{1,2}[a-c]?)\s*[\.:\-–—]*\s*(.*)$", re.IGNORECASE)


@dataclass
class Section:
    section: str
    title: str
    content: str


def html_to_text(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "ix:header", "head"]):
        tag.decompose()
    for td in soup.find_all(["td", "th"]):
        td.append(" ")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text("")
    text = text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    # Drop page numbers and running headers/footers (short lines repeated many times).
    # Digits are masked so "Apple Inc. | 2024 Form 10-K | 17" and "... | 18" count as the same footer.
    def key(ln: str) -> str:
        return re.sub(r"\d+", "#", ln)

    counts = Counter(key(ln) for ln in lines)
    lines = [ln for ln in lines
             if not re.fullmatch(r"(page\s*)?\d{1,3}", ln, re.IGNORECASE)
             and not (counts[key(ln)] >= 5 and len(ln) < 120 and re.search(r"[A-Za-z]{2}", ln)
                      and not ln.lower().startswith("item"))]  # numeric table cells are never "footers"
    return "\n".join(lines)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("’", "'")).strip(" .:-–—")


def _find_headings(lines: list[str]) -> list[tuple[int, str, bool]]:
    """Return (line_index, item_number, bare) for lines that look like real item headings.

    `bare` means the heading line was just "Item 7." and the title sits on the next line.
    """
    found = []
    for i, line in enumerate(lines):
        if len(line) > 200:
            continue
        m = _HEADING.match(line)
        if not m:
            continue
        num, rest = m.group(1).lower(), _norm(m.group(2))
        title = ITEM_TITLES.get(num)
        if title is None:
            continue
        if rest:
            ok = rest.startswith(title)
        else:
            nxt = next((ln for ln in lines[i + 1:i + 3] if ln.strip()), "")
            ok = _norm(nxt).startswith(title)
        if ok:
            found.append((i, num, not rest))
    return found


# Fallback for "cross-reference index" 10-Ks (GE, Honeywell, McDonald's, Intel, ...): the body has no
# "Item N" labels, only plain headings like "RISK FACTORS", with an index mapping items to pages at the end.
MAJOR_HEADINGS = [
    "risk factors", "management's discussion and analysis", "management discussion", "quantitative and qualitative disclosure",
    "financial statements and supplementary data", "consolidated financial statements",
    "report of independent registered public accounting firm", "legal proceedings", "properties",
    "unresolved staff comments", "cybersecurity", "controls and procedures",
    "market for registrant's common equity", "management's discussion of financial responsibility",
    "management's report on internal control", "selected financial data", "executive officers",
    "exhibits and financial statement schedules", "form 10-k cross-reference index",
]
FALLBACK_TARGETS = {"item1a": {"risk factors"},
                    "item7": {"management's discussion and analysis", "management discussion"}}  # IBM: no "and analysis"


def _title_heading(line: str) -> str | None:
    """Return the major heading a line starts, if it looks like a standalone (or ALL-CAPS run-in) heading."""
    n = _norm(line)
    for h in MAJOR_HEADINGS:
        if n == h or (n.startswith(h) and (len(n) - len(h) < 60 or line[:len(h)].isupper())):
            # "Risk Factors 28" is a TOC entry (trailing page number), not a heading
            if re.search(r"\s\d{1,3}(?:\s*[-–]\s*\d{1,3})?$", line):
                return None
            return h
    return None


def _fallback_sections(lines: list[str], missing: list[str]) -> list[Section]:
    heads = [(i, h) for i, ln in enumerate(lines) if (h := _title_heading(ln))]
    out = []
    for section in missing:
        targets = FALLBACK_TARGETS.get(section)
        if not targets:
            continue
        best: tuple[int, int, str] | None = None
        for k, (i, h) in enumerate(heads):
            if h not in targets:
                continue
            # The section ends at the next heading of a *different* kind ("Management Discussion
            # Snapshot" inside IBM's MD&A doesn't end it).
            end = next((j for j, h2 in heads[k + 1:] if h2 not in targets), None)
            if end is None:
                continue
            size = sum(len(ln) for ln in lines[i:end])
            if best is None or size > sum(len(ln) for ln in lines[best[0]:best[1]]):
                best = (i, end, h)
        if best is None:
            continue
        first = lines[best[0]]
        # Keep the text of a run-in heading ("RISK FACTORS. The following discussion...").
        lead = first[len(best[2]):].lstrip(" .:-–—") if len(first) > 150 else ""
        content = "\n".join(([lead] if lead else []) + lines[best[0] + 1:best[1]]).strip()
        if content:
            out.append(Section(section=section, title=SECTION_TITLES[section], content=content))
    return out


def extract_from_exhibit(html: bytes | str, missing: list[str], min_chars: int = 2000) -> list[Section]:
    """Title-heading extraction on another document in the filing (e.g. an Exhibit 13 annual report
    that the 10-K incorporates by reference: IBM, Wells Fargo)."""
    lines = html_to_text(html).split("\n")
    return [s for s in _fallback_sections(lines, missing) if len(s.content) >= min_chars]


def extract_sections(html: bytes | str, min_chars: int = 2000) -> list[Section]:
    text = html_to_text(html)
    lines = text.split("\n")
    headings = _find_headings(lines)
    out = []
    for section, (num, enders) in TARGETS.items():
        best: tuple[int, int, bool] | None = None
        for start_idx, n, bare in headings:
            if n != num:
                continue
            end_idx = next((i for i, m, _ in headings if i > start_idx and m in enders), None)
            if end_idx is None:
                continue
            if best is None or (end_idx - start_idx) > (best[1] - best[0]):
                best = (start_idx, end_idx, bare)
        if best is None:
            continue
        body = lines[best[0] + 1:best[1]]
        if best[2] and body:  # drop the title line under a bare "Item 7." heading
            body = body[1:]
        content = "\n".join(body).strip()
        if content:
            out.append(Section(section=section, title=SECTION_TITLES[section], content=content))

    missing = [sec for sec in FALLBACK_TARGETS if sum(len(s.content) for s in out if s.section == sec) < min_chars]
    if missing:
        recovered = {s.section: s for s in _fallback_sections(lines, missing) if len(s.content) >= min_chars}
        out = [s for s in out if s.section not in recovered] + list(recovered.values())
    return sorted(out, key=lambda s: list(TARGETS).index(s.section))
