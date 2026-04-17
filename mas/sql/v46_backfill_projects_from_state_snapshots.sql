-- v4.6 incident backfill — recover missing parent rows in `projects`
--
-- Incident context:
-- Some snapshots were persisted without a matching parent row in `projects`,
-- which later caused FK failures for child writes (outcomes, decision_events,
-- approvals, policy_decisions).
--
-- Contract:
-- - non-destructive
-- - idempotent (safe to re-run)
-- - inserts ONLY rows missing from `projects`
--
-- Usage:
--   psql -U workflow -d workflow_v4 -f sql/v46_backfill_projects_from_state_snapshots.sql

WITH missing_projects AS (
    SELECT
        ss.project_id AS id,
        COALESCE(NULLIF(ss.state_json ->> 'project_name', ''), 'Recovered Project') AS name,
        COALESCE(ss.state_json ->> 'brief', '') AS brief,
        COALESCE(ss.state_json ->> 'data', '') AS data,
        COALESCE(NULLIF(ss.state_json ->> 'current_phase', ''), 'classify') AS current_phase,
        CASE
            WHEN COALESCE(ss.state_json ->> 'created_at', '') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
                THEN (ss.state_json ->> 'created_at')::timestamptz
            ELSE NOW()
        END AS created_at
    FROM state_snapshots ss
    LEFT JOIN projects p ON p.id = ss.project_id
    WHERE p.id IS NULL
)
INSERT INTO projects (
    id,
    name,
    brief,
    data,
    current_phase,
    status,
    created_at,
    updated_at
)
SELECT
    mp.id,
    mp.name,
    mp.brief,
    mp.data,
    mp.current_phase,
    'active',
    mp.created_at,
    NOW()
FROM missing_projects mp
ON CONFLICT (id) DO NOTHING;

