-- Text + numbers: how much each company's Risk Factors section grew year over year, next to revenue growth.
WITH rf AS (
    SELECT fl.cik, fl.fiscal_year, s.char_count
    FROM filing_sections s JOIN filings fl USING (accession)
    WHERE s.section = 'item1a'
)
SELECT c.ticker, rf.fiscal_year, rf.char_count AS risk_factor_chars,
       round(100 * (rf.char_count::numeric / NULLIF(LAG(rf.char_count) OVER w, 0) - 1), 1) AS rf_growth_pct,
       round(100 * mv.revenue_yoy, 1)                                                      AS revenue_yoy_pct
FROM rf
JOIN companies c USING (cik)
LEFT JOIN mv_company_year_metrics mv ON mv.cik = rf.cik AND mv.fiscal_year = rf.fiscal_year
WINDOW w AS (PARTITION BY rf.cik ORDER BY rf.fiscal_year)
ORDER BY c.ticker, rf.fiscal_year;
