-- Data lineage: which XBRL concept supplied each canonical metric, and companies that switched tags
-- between years (a common source of silent breaks in financial pipelines).
WITH chosen AS (
    SELECT c.ticker, v.metric, v.fiscal_year, v.concept
    FROM v_metric_values v JOIN companies c USING (cik)
)
SELECT ticker, metric,
       string_agg(DISTINCT concept, ', ')                          AS concepts_used,
       count(DISTINCT concept)                                     AS n_concepts,
       min(fiscal_year) FILTER (WHERE concept <> first_concept)    AS first_year_after_switch
FROM (
    SELECT *, FIRST_VALUE(concept) OVER (PARTITION BY ticker, metric ORDER BY fiscal_year) AS first_concept
    FROM chosen
) s
GROUP BY ticker, metric
HAVING count(DISTINCT concept) > 1
ORDER BY ticker, metric;
