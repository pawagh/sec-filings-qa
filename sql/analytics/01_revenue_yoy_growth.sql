-- Year-over-year revenue growth per company (LAG window), only between consecutive fiscal years.
SELECT ticker,
       fiscal_year,
       round(revenue / 1e9, 2)                                             AS revenue_bn,
       round(100 * (revenue / NULLIF(prev_revenue, 0) - 1), 1)             AS yoy_pct
FROM (
    SELECT ticker, fiscal_year, revenue,
           LAG(revenue)     OVER w AS prev_revenue,
           LAG(fiscal_year) OVER w AS prev_year
    FROM mv_company_year_metrics
    WHERE revenue IS NOT NULL
    WINDOW w AS (PARTITION BY ticker ORDER BY fiscal_year)
) s
WHERE prev_year = fiscal_year - 1
ORDER BY ticker, fiscal_year;
