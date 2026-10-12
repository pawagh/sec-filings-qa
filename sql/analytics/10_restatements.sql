-- Restatements: the same period/concept reported with different values in different 10-Ks.
-- Possible only because financial_facts keeps every as-reported version. Share-based concepts that
-- move by a clean integer factor are retroactive stock-split adjustments (e.g. NVDA, AVGO 10-for-1).
WITH versions AS (
    SELECT ff.cik, ff.concept, ff.unit, ff.fiscal_year, ff.period_start, ff.period_end, ff.accession, ff.value,
           FIRST_VALUE(ff.value) OVER k AS original_value,
           LAST_VALUE(ff.value)  OVER k AS latest_value
    FROM financial_facts ff
    WINDOW k AS (PARTITION BY cik, taxonomy, concept, unit, period_start, period_end
                 ORDER BY filed_date, accession ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
),
changed AS (
    SELECT cik, concept, unit, fiscal_year, period_end,
           count(DISTINCT accession) AS filings_reporting,
           min(original_value)       AS original_value,
           min(latest_value)         AS latest_value
    FROM versions
    GROUP BY cik, concept, unit, fiscal_year, period_start, period_end
    HAVING count(DISTINCT value) > 1
)
SELECT c.ticker, ch.concept, ch.fiscal_year, ch.period_end, ch.filings_reporting,
       ch.original_value, ch.latest_value,
       round(100 * (ch.latest_value / NULLIF(ch.original_value, 0) - 1), 2) AS change_pct,
       CASE
           WHEN ch.unit IN ('shares', 'USD/shares')
                AND greatest(ch.latest_value / NULLIF(ch.original_value, 0),
                             ch.original_value / NULLIF(ch.latest_value, 0)) >= 2
                AND abs(greatest(ch.latest_value / NULLIF(ch.original_value, 0),
                                 ch.original_value / NULLIF(ch.latest_value, 0))
                        - round(greatest(ch.latest_value / NULLIF(ch.original_value, 0),
                                         ch.original_value / NULLIF(ch.latest_value, 0)))) < 0.02
               THEN 'stock split (retroactive)'
           ELSE 'restatement / reclassification'
       END AS likely_cause
FROM changed ch
JOIN companies c USING (cik)
ORDER BY abs(ch.latest_value / NULLIF(ch.original_value, 0) - 1) DESC NULLS LAST
LIMIT 50;
