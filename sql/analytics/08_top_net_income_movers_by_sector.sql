-- Top 3 net-income gainers per sector in the latest year (DENSE_RANK over the YoY delta).
WITH deltas AS (
    SELECT sector, ticker, fiscal_year, net_income,
           net_income - LAG(net_income) OVER (PARTITION BY ticker ORDER BY fiscal_year) AS delta,
           LAG(fiscal_year) OVER (PARTITION BY ticker ORDER BY fiscal_year)             AS prev_year
    FROM mv_company_year_metrics
    WHERE net_income IS NOT NULL
),
latest AS (SELECT MAX(fiscal_year) - 1 AS fy FROM mv_company_year_metrics),
ranked AS (
    SELECT d.*, DENSE_RANK() OVER (PARTITION BY sector ORDER BY delta DESC) AS rk
    FROM deltas d, latest
    WHERE d.fiscal_year = latest.fy AND d.prev_year = d.fiscal_year - 1
)
SELECT sector, rk, ticker, fiscal_year, round(net_income / 1e9, 2) AS net_income_bn, round(delta / 1e9, 2) AS delta_bn
FROM ranked
WHERE rk <= 3
ORDER BY sector, rk;
