-- Days from fiscal year end to 10-K filing, median and p90 by sector (ordered-set aggregates).
SELECT c.sector,
       count(*)                                                                    AS filings,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY f.filed_date - f.period_end)    AS median_days,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY f.filed_date - f.period_end)    AS p90_days,
       min(f.filed_date - f.period_end)                                            AS fastest_days
FROM filings f JOIN companies c USING (cik)
GROUP BY c.sector
ORDER BY median_days;
