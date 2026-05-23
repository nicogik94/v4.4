-- v4 Multi-Agent System — Database Schema
-- PostgreSQL 16+

-- ═══ Projects ═══

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    brief TEXT NOT NULL DEFAULT '',
    data TEXT DEFAULT '',
    current_phase VARCHAR(50) DEFAULT 'classify',
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_projects_status ON projects(status);

-- ═══ Workflow Runs (v5 runtime hardening) ═══

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',  -- queued, running, succeeded, failed
    current_phase VARCHAR(50) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_summary TEXT NOT NULL DEFAULT '',
    code_version VARCHAR(50) NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_active_project
ON workflow_runs(project_id)
WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS idx_workflow_runs_project_created
ON workflow_runs(project_id, created_at DESC);

-- ═══ Phase Outputs ═══

CREATE TABLE IF NOT EXISTS phase_outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase VARCHAR(50) NOT NULL,
    output_json JSONB NOT NULL,
    summary TEXT DEFAULT '',
    confidence FLOAT DEFAULT 0.0,
    status VARCHAR(20) DEFAULT 'completed',  -- completed, stale, failed
    model_used VARCHAR(100) DEFAULT '',
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    cache_tokens INT DEFAULT 0,
    latency_ms FLOAT DEFAULT 0.0,
    cost_usd FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    superseded_at TIMESTAMPTZ  -- set when invalidated by upstream change
);

CREATE INDEX idx_phase_outputs_project ON phase_outputs(project_id, phase);
CREATE INDEX idx_phase_outputs_status ON phase_outputs(project_id, status);

-- ═══ Predictions (for Brier scoring) ═══

CREATE TABLE IF NOT EXISTS predictions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    hypothesis_id VARCHAR(20) NOT NULL,
    phase VARCHAR(50) NOT NULL,
    predicted_probability FLOAT NOT NULL CHECK (predicted_probability BETWEEN 0 AND 1),
    actual_outcome BOOLEAN,  -- NULL until resolved
    framework_used VARCHAR(100) DEFAULT '',
    project_type VARCHAR(100) DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX idx_predictions_project ON predictions(project_id);
CREATE INDEX idx_predictions_unresolved ON predictions(actual_outcome) WHERE actual_outcome IS NULL;

-- ═══ Calibration Metrics (aggregated) ═══

CREATE TABLE IF NOT EXISTS calibration_metrics (
    id SERIAL PRIMARY KEY,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    phase VARCHAR(50) NOT NULL,
    project_type VARCHAR(100) DEFAULT 'all',
    brier_score FLOAT,
    expected_calibration_error FLOAT,
    num_predictions INT DEFAULT 0,
    computed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_calibration_period ON calibration_metrics(period_start, period_end);

-- ═══ Framework Effectiveness ═══

CREATE TABLE IF NOT EXISTS framework_effectiveness (
    id SERIAL PRIMARY KEY,
    framework_tag VARCHAR(50) NOT NULL,  -- e.g., "[#7] FMEA"
    phase VARCHAR(50) NOT NULL,
    project_type VARCHAR(100) DEFAULT '',
    times_used INT DEFAULT 0,
    times_useful INT DEFAULT 0,  -- led to actionable finding
    avg_confidence_boost FLOAT DEFAULT 0.0,
    last_used_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_framework_phase ON framework_effectiveness(framework_tag, phase, project_type);

-- ═══ Re-entry Events ═══

CREATE TABLE IF NOT EXISTS reentry_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trigger_code VARCHAR(10) NOT NULL,  -- R1-R8
    trigger_condition VARCHAR(255) NOT NULL,
    source_phase VARCHAR(50) NOT NULL,
    target_phase VARCHAR(50) NOT NULL,
    detail TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_reentry_project ON reentry_events(project_id);

-- ═══ Agent Execution Log ═══

CREATE TABLE IF NOT EXISTS agent_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase VARCHAR(50) NOT NULL,
    agent_name VARCHAR(100) NOT NULL,
    model_used VARCHAR(100) NOT NULL,
    provider VARCHAR(20) NOT NULL,  -- anthropic, openai
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    cache_read_tokens INT DEFAULT 0,
    latency_ms FLOAT DEFAULT 0.0,
    cost_usd FLOAT DEFAULT 0.0,
    status VARCHAR(20) DEFAULT 'success',  -- success, retry, fallback, failed
    error_type VARCHAR(50),
    error_message TEXT,
    attempt_number INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_agent_logs_project ON agent_logs(project_id, phase);
CREATE INDEX idx_agent_logs_status ON agent_logs(status);

-- ═══ Cross-Project Intelligence ═══

CREATE TABLE IF NOT EXISTS project_patterns (
    id SERIAL PRIMARY KEY,
    domain VARCHAR(100) NOT NULL,  -- Cynefin domain
    project_type VARCHAR(100) DEFAULT '',
    total_projects INT DEFAULT 0,
    avg_phases_to_complete FLOAT DEFAULT 6.0,
    avg_reentries FLOAT DEFAULT 0.0,
    avg_brier_score FLOAT,
    common_reentry_trigger VARCHAR(10),
    recommended_model_override JSONB,  -- {phase: model} overrides
    notes TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_patterns_domain ON project_patterns(domain, project_type);

-- ═══ Views ═══

CREATE OR REPLACE VIEW v_project_summary AS
SELECT
    p.id, p.name, p.current_phase, p.status,
    p.created_at, p.completed_at,
    COUNT(DISTINCT po.phase) FILTER (WHERE po.status = 'completed') AS phases_completed,
    COUNT(DISTINCT re.id) AS reentry_count,
    AVG(al.cost_usd) AS avg_phase_cost,
    SUM(al.cost_usd) AS total_cost
FROM projects p
LEFT JOIN phase_outputs po ON po.project_id = p.id
LEFT JOIN reentry_events re ON re.project_id = p.id
LEFT JOIN agent_logs al ON al.project_id = p.id
GROUP BY p.id;

CREATE OR REPLACE VIEW v_calibration_dashboard AS
SELECT
    phase,
    project_type,
    ROUND(AVG(brier_score)::numeric, 4) AS avg_brier,
    ROUND(AVG(expected_calibration_error)::numeric, 4) AS avg_ece,
    SUM(num_predictions) AS total_predictions,
    MAX(period_end) AS latest_period
FROM calibration_metrics
GROUP BY phase, project_type;
