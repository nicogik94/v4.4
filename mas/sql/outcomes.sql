-- v4.1 MAS — Realized Outcomes Schema
-- Closes the Bayesian loop: feeds actual client outcomes back into the meta-learner
-- so that phase priors get calibrated over time. Without this, the Brier/ECE infrastructure
-- in the Meta-Learner Database is decorative.
--
-- Run AFTER sql/init.sql.

-- ═══ Outcomes ═══
-- One row per hypothesis per project once reality has resolved the prediction.
-- Filled in by the client (via API) or by the consultant post-engagement.

CREATE TABLE IF NOT EXISTS outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    hypothesis_id VARCHAR(20) NOT NULL,           -- H1, H2, ...
    phase VARCHAR(50) NOT NULL,                    -- which phase generated the prediction
    predicted_probability FLOAT NOT NULL CHECK (predicted_probability BETWEEN 0 AND 1),
    realized BOOLEAN,                              -- NULL = unresolved, TRUE/FALSE = actual outcome
    realized_value FLOAT,                          -- the actual metric value (optional)
    resolution_date TIMESTAMPTZ,
    notes TEXT DEFAULT '',
    recorded_by VARCHAR(100) DEFAULT '',           -- 'client' | 'consultant' | 'auto'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_outcomes_project ON outcomes(project_id);
CREATE INDEX idx_outcomes_phase ON outcomes(phase) WHERE realized IS NOT NULL;
CREATE UNIQUE INDEX idx_outcomes_unique ON outcomes(project_id, hypothesis_id);

-- ═══ Prior Snapshots ═══
-- Rolling calibrated priors per phase, updated nightly by jobs/update_priors.py.
-- The orchestrator reads the latest row for each phase to seed new projects.

CREATE TABLE IF NOT EXISTS prior_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phase VARCHAR(50) NOT NULL,
    snapshot_date DATE NOT NULL,
    n_outcomes INT NOT NULL,                       -- how many resolved outcomes this snapshot is based on
    brier_score FLOAT NOT NULL,                    -- mean Brier on all resolved outcomes
    ece FLOAT NOT NULL,                            -- Expected Calibration Error (10 bins)
    recommended_alpha FLOAT NOT NULL,              -- new Beta(alpha) for the prior
    recommended_beta FLOAT NOT NULL,               -- new Beta(beta) for the prior
    direction VARCHAR(20) DEFAULT 'none',          -- 'more_confident' | 'less_confident' | 'none'
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_prior_snapshots_phase_date ON prior_snapshots(phase, snapshot_date DESC);

-- ═══ Framework Performance ═══
-- Which frameworks actually moved verdicts in resolved projects?
-- Used by update_priors.py to suggest framework reweighting per phase.
--
-- FAIL-SOFT CONTRACT (v4.1):
-- update_priors.py writes one row per (framework_name, phase) pair on every run.
-- The minimal stub populates only n_uses (a count of phase_outputs in the rolling
-- window that mention the framework). The Brier-weighted columns
-- (avg_brier_when_used, avg_brier_when_absent, value_score) and n_verdict_changes
-- remain NULL until the operator implements the deployment-specific join between
-- phase_outputs.output_json and the outcomes table. Dashboards MUST treat NULL as
-- "data not yet available," not as "framework had no value." This contract exists
-- so dashboards never 404 on this table during the bootstrap phase, while never
-- fabricating Brier deltas the system cannot honestly support.

CREATE TABLE IF NOT EXISTS framework_performance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    framework_name VARCHAR(100) NOT NULL,          -- 'STEELMAN', 'PREMORTEM', etc.
    phase VARCHAR(50) NOT NULL,
    snapshot_date DATE NOT NULL,
    n_uses INT NOT NULL,
    n_verdict_changes INT NOT NULL,                -- how often this framework flipped a verdict
    avg_brier_when_used FLOAT,                      -- Brier of hypotheses that invoked this framework
    avg_brier_when_absent FLOAT,                    -- counterfactual
    value_score FLOAT,                              -- (brier_absent - brier_used) — higher = more valuable
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_framework_perf ON framework_performance(phase, snapshot_date DESC);

-- ═══ Calibration Deltas ═══
-- Per-project view: how well did this project's stated confidence match reality?
-- This is what shows up on the Dashboard as "Calibration δ: -0.03" etc.

CREATE OR REPLACE VIEW calibration_deltas AS
SELECT
    p.id AS project_id,
    p.name,
    p.completed_at,
    COUNT(o.id) AS n_predictions,
    COUNT(o.id) FILTER (WHERE o.realized IS NOT NULL) AS n_resolved,
    AVG(
        CASE
            WHEN o.realized IS NOT NULL
            THEN POWER(o.predicted_probability - CASE WHEN o.realized THEN 1.0 ELSE 0.0 END, 2)
            ELSE NULL
        END
    ) AS mean_brier,
    AVG(o.predicted_probability) FILTER (WHERE o.realized IS NOT NULL) AS mean_predicted,
    AVG(CASE WHEN o.realized THEN 1.0 ELSE 0.0 END) FILTER (WHERE o.realized IS NOT NULL) AS mean_realized,
    AVG(o.predicted_probability) FILTER (WHERE o.realized IS NOT NULL)
      - AVG(CASE WHEN o.realized THEN 1.0 ELSE 0.0 END) FILTER (WHERE o.realized IS NOT NULL)
      AS calibration_delta
FROM projects p
LEFT JOIN outcomes o ON o.project_id = p.id
GROUP BY p.id, p.name, p.completed_at;
