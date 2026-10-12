-- Revenue CAGR over each company's loaded window (FIRST_VALUE / LAST_VALUE over a full frame).
SELECT DISTINCT ticker,
       first_year, last_year,
       round(100 * (power(last_rev / first_rev, 1.0 / (last_year - first_year)) - 1), 1) AS revenue_cagr_pct
FROM (
    SELECT ticker,
           FIRST_VALUE(revenue)     OVER w AS first_rev,
           LAST_VALUE(revenue)      OVER w AS last_rev,
           FIRST_VALUE(fiscal_year) OVER w AS first_year,
           LAST_VALUE(fiscal_year)  OVER w AS last_year
    FROM mv_company_year_metrics
    WHERE revenue > 0
    WINDOW w AS (PARTITION BY ticker ORDER BY fiscal_year ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
) s
WHERE last_year > first_year
ORDER BY revenue_cagr_pct DESC;
