-- Operating-margin change (percentage points) from each company's first to last loaded year.
WITH bounds AS (
    SELECT ticker, MIN(fiscal_year) AS y0, MAX(fiscal_year) AS y1
    FROM mv_company_year_metrics
    WHERE operating_margin IS NOT NULL
    GROUP BY ticker
)
SELECT b.ticker, b.y0, b.y1,
       round(100 * a.operating_margin, 1)                          AS margin_start_pct,
       round(100 * z.operating_margin, 1)                          AS margin_end_pct,
       round(100 * (z.operating_margin - a.operating_margin), 1)   AS change_pp
FROM bounds b
JOIN mv_company_year_metrics a ON a.ticker = b.ticker AND a.fiscal_year = b.y0
JOIN mv_company_year_metrics z ON z.ticker = b.ticker AND z.fiscal_year = b.y1
WHERE b.y1 > b.y0
ORDER BY change_pp DESC;
