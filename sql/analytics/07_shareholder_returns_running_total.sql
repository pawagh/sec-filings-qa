-- Dividends + buybacks vs free cash flow, with cumulative totals per company (SUM() OVER).
SELECT ticker, fiscal_year,
       round(COALESCE(dividends_paid, 0) / 1e9, 2)                                AS dividends_bn,
       round(COALESCE(share_repurchases, 0) / 1e9, 2)                             AS buybacks_bn,
       round(free_cash_flow / 1e9, 2)                                             AS fcf_bn,
       round(100 * (COALESCE(dividends_paid, 0) + COALESCE(share_repurchases, 0))
             / NULLIF(free_cash_flow, 0), 0)                                      AS payout_of_fcf_pct,
       round(SUM(COALESCE(dividends_paid, 0) + COALESCE(share_repurchases, 0))
             OVER (PARTITION BY ticker ORDER BY fiscal_year) / 1e9, 2)            AS cumulative_returned_bn
FROM mv_company_year_metrics
WHERE free_cash_flow IS NOT NULL
ORDER BY ticker, fiscal_year;
