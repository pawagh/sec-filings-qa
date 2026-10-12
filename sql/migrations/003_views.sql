-- v_metric_values: one value per (company, fiscal year, canonical metric).
--   1. keep annual durations (350-380 days) or instants, matching the concept map's period type
--   2. per company-year-metric, prefer the highest-priority concept, then the latest filing
--      (so restated values win), then the latest period_end (for instants)
CREATE OR REPLACE VIEW v_metric_values AS
WITH candidates AS (
    SELECT f.cik,
           f.fiscal_year,
           m.metric,
           m.label,
           f.concept,
           f.unit,
           f.value,
           f.period_start,
           f.period_end,
           f.accession,
           f.filed_date,
           ROW_NUMBER() OVER (
               PARTITION BY f.cik, f.fiscal_year, m.metric
               ORDER BY m.priority, f.filed_date DESC, f.period_end DESC
           ) AS rn
    FROM financial_facts f
    JOIN metric_concept_map m
      ON m.taxonomy = f.taxonomy AND m.concept = f.concept AND m.unit = f.unit
    WHERE (m.period_type = 'duration'
           AND f.period_start IS NOT NULL
           AND (f.period_end - f.period_start) BETWEEN 350 AND 380)
       OR (m.period_type = 'instant' AND f.period_start IS NULL)
)
SELECT cik, fiscal_year, metric, label, concept, unit, value, period_start, period_end, accession, filed_date
FROM candidates
WHERE rn = 1;

-- Wide company-year table with derived ratios; refreshed CONCURRENTLY at the end of each ETL run.
DROP MATERIALIZED VIEW IF EXISTS mv_company_year_metrics;
CREATE MATERIALIZED VIEW mv_company_year_metrics AS
WITH wide AS (
    SELECT v.cik,
           v.fiscal_year,
           MAX(v.value) FILTER (WHERE metric = 'revenue')             AS revenue,
           MAX(v.value) FILTER (WHERE metric = 'cost_of_revenue')     AS cost_of_revenue,
           MAX(v.value) FILTER (WHERE metric = 'gross_profit')        AS gross_profit_reported,
           MAX(v.value) FILTER (WHERE metric = 'rd_expense')          AS rd_expense,
           MAX(v.value) FILTER (WHERE metric = 'sga_expense')         AS sga_expense,
           MAX(v.value) FILTER (WHERE metric = 'operating_income')    AS operating_income,
           MAX(v.value) FILTER (WHERE metric = 'net_income')          AS net_income,
           MAX(v.value) FILTER (WHERE metric = 'eps_diluted')         AS eps_diluted,
           MAX(v.value) FILTER (WHERE metric = 'diluted_shares')      AS diluted_shares,
           MAX(v.value) FILTER (WHERE metric = 'operating_cash_flow') AS operating_cash_flow,
           MAX(v.value) FILTER (WHERE metric = 'capex')               AS capex,
           MAX(v.value) FILTER (WHERE metric = 'dividends_paid')      AS dividends_paid,
           MAX(v.value) FILTER (WHERE metric = 'share_repurchases')   AS share_repurchases,
           MAX(v.value) FILTER (WHERE metric = 'total_assets')        AS total_assets,
           MAX(v.value) FILTER (WHERE metric = 'total_liabilities')   AS total_liabilities,
           MAX(v.value) FILTER (WHERE metric = 'stockholders_equity') AS stockholders_equity,
           MAX(v.value) FILTER (WHERE metric = 'cash')                AS cash,
           MAX(v.value) FILTER (WHERE metric = 'long_term_debt')      AS long_term_debt
    FROM v_metric_values v
    GROUP BY v.cik, v.fiscal_year
)
SELECT c.ticker,
       c.name,
       c.sector,
       w.*,
       COALESCE(w.gross_profit_reported, w.revenue - w.cost_of_revenue)            AS gross_profit,
       COALESCE(w.gross_profit_reported, w.revenue - w.cost_of_revenue)
           / NULLIF(w.revenue, 0)                                                  AS gross_margin,
       w.operating_income / NULLIF(w.revenue, 0)                                   AS operating_margin,
       w.net_income / NULLIF(w.revenue, 0)                                         AS net_margin,
       w.rd_expense / NULLIF(w.revenue, 0)                                         AS rd_intensity,
       w.operating_cash_flow - w.capex                                             AS free_cash_flow,
       w.total_liabilities / NULLIF(w.stockholders_equity, 0)                      AS liabilities_to_equity,
       w.revenue / NULLIF(LAG(w.revenue) OVER (PARTITION BY w.cik ORDER BY w.fiscal_year), 0) - 1
                                                                                   AS revenue_yoy
FROM wide w
JOIN companies c USING (cik);

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_company_year ON mv_company_year_metrics (cik, fiscal_year);
CREATE INDEX IF NOT EXISTS ix_mv_ticker ON mv_company_year_metrics (ticker, fiscal_year);
