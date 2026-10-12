from secqa.etl.sections import extract_sections, html_to_text

FILLER = "<p>" + ("Lorem ipsum dolor sit amet. " * 20) + "</p>"


def doc():
    toc = """<table>
      <tr><td>Item 1.</td><td>Business</td><td>1</td></tr>
      <tr><td>Item 1A.</td><td>Risk Factors</td><td>5</td></tr>
      <tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>17</td></tr>
      <tr><td>Item 2.</td><td>Properties</td><td>18</td></tr>
      <tr><td>Item 7.</td><td>Management's Discussion and Analysis</td><td>20</td></tr>
      <tr><td>Item 7A.</td><td>Quantitative and Qualitative Disclosures About Market Risk</td><td>30</td></tr>
      <tr><td>Item 8.</td><td>Financial Statements and Supplementary Data</td><td>31</td></tr>
    </table>"""
    body = f"""
      <div><span>Item</span><span> 1.</span><span>   Business</span></div>
      <p>BUSINESS_BODY We make widgets.</p>{FILLER}
      <p>For risks, see</p><p><a href="#rf">Item 1A</a></p><p>of this report.</p>
      <div>Item 1A.</div><div>Risk Factors</div>
      <p>RISK_BODY Supply could be disrupted.</p>{FILLER * 2}
      <div>Item 1B. Unresolved Staff Comments</div><p>None.</p>
      <div>Item 2. Properties</div><p>We own buildings.</p>
      <div>Item 7. Management&#8217;s Discussion and Analysis of Financial Condition</div>
      <p>MDA_BODY Revenue grew.</p>{FILLER}
      <div>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</div><p>FX.</p>
      <div>Item 8. Financial Statements and Supplementary Data</div><p>Numbers.</p>"""
    return f"<html><body>{toc}{body}</body></html>"


def test_extracts_body_not_toc():
    secs = {s.section: s.content for s in extract_sections(doc())}
    assert set(secs) == {"item1", "item1a", "item7"}
    assert secs["item1"].startswith("BUSINESS_BODY")
    assert secs["item1a"].startswith("RISK_BODY") and "Unresolved" not in secs["item1a"]
    assert secs["item7"].startswith("MDA_BODY") and "Quantitative" not in secs["item7"]


def test_hyperlinked_cross_reference_is_not_a_heading():
    secs = {s.section: s.content for s in extract_sections(doc())}
    assert "see" in secs["item1"]  # the cross-ref stays inside Item 1 instead of starting Item 1A


def test_spans_in_heading_stay_on_one_line():
    assert "Item 1. Business" in html_to_text(doc())


def test_running_footers_removed():
    html = "<html><body>" + "".join(f"<p>Para {i} text.</p><p>Acme Corp | 2024 Form 10-K | {i}</p>"
                                    for i in range(10)) + "</body></html>"
    assert "Form 10-K |" not in html_to_text(html)
