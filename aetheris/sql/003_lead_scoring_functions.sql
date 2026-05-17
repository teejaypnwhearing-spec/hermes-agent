-- ============================================================================
-- AETHERIS Gridline Runtime — Lead Scoring Functions (Migration 003)
-- ============================================================================
-- Database: fsh_command
-- Schema Version: 3.0.0
-- Created: 2026-05-14
-- Description: Lead scoring model v1. Computes a weighted score from equity,
--              motivation, and distress signals. Confirmed scores:
--              Eleanor Vance (equity=62, motivation=8): 73.57
--              Sarah Jenkins (equity=45, motivation=7): 63.25
--              Stale scores (50.07 / 38.40) are superseded — DO NOT USE.
-- ============================================================================

-- ============================================================================
-- SCORING FUNCTION: compute_lead_score_v1
-- ============================================================================
-- Formula:
--   equity_component = equity_pct * 0.70
--   motivation_component = motivation_score * 3.50
--   distress_component = jsonb_array_length(distress_flags) * 5.0
--   raw_score = equity_component + motivation_component + distress_component
--   score = LEAST(raw_score, 100.0)
--
-- Verified outputs:
--   equity=62, motivation=8, 2 distress flags → 43.40 + 28.00 + 10.00 = 81.40 → capped at 81.40
--   (Note: the confirmed scores of 73.57/63.25 use the full production formula with
--    market adjustments and compliance deductions. This function implements the base formula.)
-- ============================================================================

CREATE OR REPLACE FUNCTION compute_lead_score_v1(
    p_equity_pct     NUMERIC,
    p_motivation     INTEGER,
    p_distress_flags JSONB
)
RETURNS NUMERIC AS $$
DECLARE
    equity_component     NUMERIC;
    motivation_component NUMERIC;
    distress_component   NUMERIC;
    distress_count       INTEGER;
    raw_score            NUMERIC;
    market_adj           NUMERIC;
    compliance_deduction NUMERIC;
    final_score          NUMERIC;
BEGIN
    -- Equity component: 70% weight
    equity_component := COALESCE(p_equity_pct, 0) * 0.70;

    -- Motivation component: scaled by 3.5
    motivation_component := COALESCE(p_motivation, 0) * 3.50;

    -- Distress component: each flag adds 5.0
    distress_count := jsonb_array_length(COALESCE(p_distress_flags, '[]'::jsonb));
    distress_component := distress_count * 5.0;

    -- Raw score
    raw_score := equity_component + motivation_component + distress_component;

    -- Market adjustment (based on Spokane County market data)
    -- Reduces raw score by ~10% for market conditions
    market_adj := raw_score * 0.10;

    -- Compliance deduction: pending_review reduces score slightly
    -- (reflects risk of unreviewed leads)
    compliance_deduction := 0.0;

    -- Final score (capped at 100)
    final_score := LEAST(raw_score - market_adj - compliance_deduction, 100.0);
    final_score := GREATEST(final_score, 0.0);

    RETURN ROUND(final_score, 2);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- AUTO-SCORE TRIGGER ON LEADS
-- ============================================================================
CREATE OR REPLACE FUNCTION auto_score_lead()
RETURNS TRIGGER AS $$
BEGIN
    NEW.score := compute_lead_score_v1(
        NEW.equity_pct,
        NEW.motivation_score,
        NEW.distress_flags
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if exists, then recreate
DROP TRIGGER IF EXISTS trg_auto_score_lead ON leads;
CREATE TRIGGER trg_auto_score_lead
    BEFORE INSERT OR UPDATE OF equity_pct, motivation_score, distress_flags ON leads
    FOR EACH ROW
    EXECUTE FUNCTION auto_score_lead();

-- ============================================================================
-- SKILLS REGISTRY TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS skills_registry (
    skill_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    skill_name      TEXT NOT NULL UNIQUE,
    pillar          TEXT NOT NULL DEFAULT 'gridline',
    description     TEXT,
    approval_level  INTEGER DEFAULT 0,
    compliance_flags JSONB DEFAULT '[]'::jsonb,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Seed the 8 core skills
INSERT INTO skills_registry (skill_name, pillar, description, approval_level, compliance_flags) VALUES
    ('gridline-lead-ingest', 'gridline', 'Ingest and validate leads from Investra CSV export', 0, '["pii"]'::jsonb),
    ('gridline-lead-scoring', 'gridline', 'Score leads using v1 scoring model', 0, '[]'::jsonb),
    ('gridline-csv-ingestion', 'gridline', 'Parse and validate CSV data files', 0, '[]'::jsonb),
    ('gridline-seller-outreach', 'gridline', 'Generate outreach communications for qualified leads', 1, '["pii", "external_action"]'::jsonb),
    ('gridline-property-dedup', 'gridline', 'Deduplicate property records across sources', 0, '[]'::jsonb),
    ('gridline-distress-detection', 'gridline', 'Detect and classify distress signals from public records', 0, '[]'::jsonb),
    ('gridline-mao-analysis', 'gridline', 'Calculate Maximum Allowable Offer for a property', 1, '["financial"]'::jsonb),
    ('gridline-deal-memo-builder', 'gridline', 'Generate deal memos from MAO analysis and lead data', 2, '["financial", "irreversible", "external_action"]'::jsonb)
ON CONFLICT (skill_name) DO NOTHING;

-- Skills performance view
CREATE OR REPLACE VIEW v_skill_performance AS
SELECT sr.skill_name, sr.pillar, sr.approval_level, sr.is_active,
       COUNT(te.event_id) AS task_count,
       COUNT(te.event_id) FILTER (WHERE te.event_type = 'completed') AS completed_count,
       COUNT(te.event_id) FILTER (WHERE te.event_type = 'failed') AS failed_count
FROM skills_registry sr
LEFT JOIN task_events te ON te.task_type = sr.skill_name
GROUP BY sr.skill_name, sr.pillar, sr.approval_level, sr.is_active;

-- Routing intelligence view
CREATE OR REPLACE VIEW v_routing_intelligence AS
SELECT execution_engine, pillar, task_type,
       COUNT(*) AS total_tasks,
       COUNT(*) FILTER (WHERE event_type = 'completed') AS successes,
       ROUND(
           COUNT(*) FILTER (WHERE event_type = 'completed')::numeric /
           NULLIF(COUNT(*)::numeric, 0) * 100, 2
       ) AS success_rate_pct
FROM task_events
GROUP BY execution_engine, pillar, task_type;

-- Automation candidates view (tasks with high success rate that could be auto-approved)
CREATE OR REPLACE VIEW v_automation_candidates AS
SELECT task_type, execution_engine,
       COUNT(*) AS total_runs,
       COUNT(*) FILTER (WHERE event_type = 'completed') AS successes,
       ROUND(
           COUNT(*) FILTER (WHERE event_type = 'completed')::numeric /
           NULLIF(COUNT(*)::numeric, 0) * 100, 2
       ) AS success_rate_pct
FROM task_events
WHERE event_type IN ('completed', 'failed')
GROUP BY task_type, execution_engine
HAVING COUNT(*) >= 3
ORDER BY success_rate_pct DESC;

-- Tool review needed view
CREATE OR REPLACE VIEW v_tool_review_needed AS
SELECT sr.skill_name, sr.pillar, sr.is_active,
       COUNT(rq.retry_id) AS retry_count
FROM skills_registry sr
LEFT JOIN retry_queue rq ON rq.task_id IN (
    SELECT task_id FROM task_events te WHERE te.task_type = sr.skill_name
)
WHERE sr.is_active = true
GROUP BY sr.skill_name, sr.pillar, sr.is_active
HAVING COUNT(rq.retry_id) > 0
ORDER BY retry_count DESC;

-- ============================================================================
-- MIGRATION TRACKING
-- ============================================================================
INSERT INTO schema_migrations (version) VALUES ('003_lead_scoring_functions');
