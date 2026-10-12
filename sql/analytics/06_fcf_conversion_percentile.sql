-- Free-cash-flow conversion (FCF / net income) with a percentile rank across all company-years.
SELECT ticker, fiscal_year,
       round(free_cash_flow / 1e9, 2)                                       AS fcf_bn,
       round(free_cash_flow / NULLIF(net_income, 0), 2)                     AS fcf_conversion,
       round((PERCENT_RANK() OVER (PARTITION BY fiscal_year
                                   ORDER BY free_cash_flow / NULLIF(net_income, 0)))::numeric, 2) AS pct_rank_in_year
FROM mv_company_year_metrics
WHERE free_cash_flow IS NOT NULL AND net_income > 0
ORDER BY fiscal_year DESC, fcf_conversion DESC;
