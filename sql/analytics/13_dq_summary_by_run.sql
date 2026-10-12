-- Data-quality dashboard: issue counts per ETL run, check and severity (pivot with FILTER).
SELECT r.run_id, r.started_at::date AS run_date, r.status, a.check_name,
       count(*) FILTER (WHERE a.severity = 'error')   AS errors,
       count(*) FILTER (WHERE a.severity = 'warning') AS warnings,
       count(*) FILTER (WHERE a.severity = 'info')    AS infos
FROM etl_runs r
JOIN dq_audit a USING (run_id)
GROUP BY r.run_id, r.started_at, r.status, a.check_name
ORDER BY r.run_id DESC, errors DESC, warnings DESC;
