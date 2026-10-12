-- Sector x year totals, sector totals across years, and a grand total in one pass (GROUPING SETS).
SELECT COALESCE(sector, '(all sectors)')                     AS sector,
       COALESCE(fiscal_year::text, '(all years)')            AS fiscal_year,
       count(*)                                              AS company_years,
       round(SUM(revenue) / 1e9, 1)                          AS revenue_bn,
       round(SUM(net_income) / 1e9, 1)                       AS net_income_bn,
       round(100 * SUM(net_income) / NULLIF(SUM(revenue), 0), 1) AS aggregate_net_margin_pct,
       GROUPING(sector, fiscal_year)                         AS grouping_level
FROM mv_company_year_metrics
WHERE revenue IS NOT NULL AND net_income IS NOT NULL
GROUP BY GROUPING SETS ((sector, fiscal_year), (sector), ())
ORDER BY grouping_level, sector, fiscal_year;
