-- R&D intensity (R&D / revenue) ranked within each sector for the latest common fiscal year.
WITH latest AS (
    SELECT MAX(fiscal_year) - 1 AS fy FROM mv_company_year_metrics  -- latest year most companies have filed
)
SELECT sector,
       ticker,
       round(100 * rd_intensity, 1)                                         AS rd_pct_of_revenue,
       RANK()       OVER (PARTITION BY sector ORDER BY rd_intensity DESC)   AS sector_rank,
       round(100 * AVG(rd_intensity) OVER (PARTITION BY sector), 1)         AS sector_avg_pct
FROM mv_company_year_metrics, latest
WHERE fiscal_year = latest.fy AND rd_intensity IS NOT NULL
ORDER BY sector, sector_rank;
