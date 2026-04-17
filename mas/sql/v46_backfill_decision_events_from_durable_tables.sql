-- v4.6 incident backfill — minimal historical reconstruction for decision_events
--
-- Scope:
-- - backfill only what can be grounded from durable tables
--   1) project.created  <- projects (+ optional project_type from state_snapshots)
--   2) outcome.recorded <- outcomes
--
-- Safety:
-- - non-destructive (INSERT only)
-- - idempotent (NOT EXISTS guards)
-- - does not invent unsupported fields, timestamps, or hash-chain links
--
-- Usage:
--   psql -U workflow -d workflow_v4 -f sql/v46_backfill_decision_events_from_durable_tables.sql

-- 1) Backfill one project.created event per project when missing.
INSERT INTO decision_events (
    event_id,
    project_id,
    event_type,
    event_time,
    actor_type,
    actor_id,
    payload,
    trace_id,
    phase,
    model_provider,
    model_name,
    cost_usd,
    latency_ms,
    prev_event_id,
    event_hash
)
SELECT
    gen_random_uuid(),
    p.id,
    'project.created',
    COALESCE(p.created_at, NOW()),
    'backfill',
    'v46_backfill',
    jsonb_strip_nulls(
        jsonb_build_object(
            'project_name', p.name,
            'project_type', NULLIF(ss.state_json ->> 'project_type', '')
        )
    ),
    '',
    '',
    '',
    '',
    0.0,
    0.0,
    NULL,
    ''
FROM projects p
LEFT JOIN state_snapshots ss ON ss.project_id = p.id
WHERE NOT EXISTS (
    SELECT 1
    FROM decision_events de
    WHERE de.project_id = p.id
      AND de.event_type = 'project.created'
);

-- 2) Backfill one outcome.recorded event per outcomes(project_id, hypothesis_id) row when missing.
INSERT INTO decision_events (
    event_id,
    project_id,
    event_type,
    event_time,
    actor_type,
    actor_id,
    payload,
    trace_id,
    phase,
    model_provider,
    model_name,
    cost_usd,
    latency_ms,
    prev_event_id,
    event_hash
)
SELECT
    gen_random_uuid(),
    o.project_id,
    'outcome.recorded',
    COALESCE(o.resolution_date, o.created_at, NOW()),
    'backfill',
    COALESCE(NULLIF(o.recorded_by, ''), 'v46_backfill'),
    jsonb_build_object(
        'hypothesis_id', o.hypothesis_id,
        'phase', o.phase,
        'predicted_probability', o.predicted_probability,
        'realized', o.realized,
        'realized_value', o.realized_value,
        'recorded_by', COALESCE(NULLIF(o.recorded_by, ''), 'v46_backfill'),
        'project_type', COALESCE(NULLIF(o.project_type, ''), 'strategic_audit')
    ),
    '',
    COALESCE(o.phase, ''),
    '',
    '',
    0.0,
    0.0,
    NULL,
    ''
FROM outcomes o
WHERE NOT EXISTS (
    SELECT 1
    FROM decision_events de
    WHERE de.project_id = o.project_id
      AND de.event_type = 'outcome.recorded'
      AND de.payload ->> 'hypothesis_id' = o.hypothesis_id
);

