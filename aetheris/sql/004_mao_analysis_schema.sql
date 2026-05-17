-- ============================================================================
-- AETHERIS Gridline Runtime — MAO Analysis Schema (Migration 004)
-- ============================================================================
-- Database: fsh_command
-- Schema Version: 4.0.0
-- Created: 2026-05-14
-- Description: MAO (Maximum Allowable Offer) analysis tables and HM decision
--              logging. Supports the deal pipeline from MAO calculation through
--              deal memo generation and human-in-the-loop approval.
-- ============================================================================

-- ============================================================================
-- MAO ANALYSES TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS mao_analyses (
    mao_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id         UUID NOT NULL REFERENCES leads(lead_id),
    arv             NUMERIC(12,2) NOT NULL,           -- After Repair Value
    repair_estimate NUMERIC(12,2) DEFAULT 0,          -- Estimated repair cost
    mao_value       NUMERIC(12,2) NOT NULL,           -- Maximum Allowable Offer
    mao_multiplier  NUMERIC(3,2) DEFAULT 0.70,        -- MAO multiplier (typically 70%)
    desired_profit  NUMERIC(12,2) DEFAULT 0,          -- Target profit margin
    confidence      NUMERIC(3,2) DEFAULT 0.50,        -- Confidence level (0.0-1.0)
    assumptions     JSONB DEFAULT '{}'::jsonb,         -- Key assumptions
    comp_sources    JSONB DEFAULT '[]'::jsonb,         -- Comparable property sources
    status          TEXT DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed', 'approved', 'rejected')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mao_lead ON mao_analyses(lead_id);
CREATE INDEX IF NOT EXISTS idx_mao_status ON mao_analyses(status);

-- ============================================================================
-- DEAL MEMOS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS deal_memos (
    memo_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id         UUID NOT NULL REFERENCES leads(lead_id),
    mao_id          UUID REFERENCES mao_analyses(mao_id),
    property_address TEXT NOT NULL,
    offer_amount    NUMERIC(12,2),
    arv             NUMERIC(12,2),
    repair_estimate NUMERIC(12,2),
    projected_profit NUMERIC(12,2),
    risk_assessment JSONB DEFAULT '{}'::jsonb,
    compliance_flags JSONB DEFAULT '[]'::jsonb,
    approval_level  INTEGER DEFAULT 2,
    status          TEXT DEFAULT 'draft' CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected', 'sent')),
    memo_content    JSONB,                             -- Full structured deal memo
    created_by      TEXT DEFAULT 'system',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deal_lead ON deal_memos(lead_id);
CREATE INDEX IF NOT EXISTS idx_deal_mao ON deal_memos(mao_id);
CREATE INDEX IF NOT EXISTS idx_deal_status ON deal_memos(status);

-- ============================================================================
-- HM DECISION LOGS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS hm_decision_logs (
    decision_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID,
    decision_type   TEXT NOT NULL CHECK (decision_type IN (
        'approve', 'reject', 'escalate', 'override', 'info_request'
    )),
    decision_rationale TEXT,
    decided_by      TEXT NOT NULL,
    context_snapshot JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hm_decision_task ON hm_decision_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_hm_decision_type ON hm_decision_logs(decision_type);
CREATE INDEX IF NOT EXISTS idx_hm_decision_by ON hm_decision_logs(decided_by);

-- ============================================================================
-- VIEWS
-- ============================================================================

-- MAO pipeline view: lead → MAO → deal memo status
CREATE OR REPLACE VIEW v_mao_pipeline AS
SELECT
    l.lead_id, l.property_address, l.owner_name, l.equity_pct, l.score, l.tier,
    m.mao_id, m.arv, m.repair_estimate, m.mao_value, m.confidence AS mao_confidence, m.status AS mao_status,
    d.memo_id, d.offer_amount, d.projected_profit, d.status AS deal_status
FROM leads l
LEFT JOIN mao_analyses m ON l.lead_id = m.lead_id
LEFT JOIN deal_memos d ON l.lead_id = d.lead_id AND d.mao_id = m.mao_id
ORDER BY l.score DESC;

-- Decision audit view
CREATE OR REPLACE VIEW v_decision_audit AS
SELECT hdl.decision_id, hdl.task_id, hdl.decision_type, hdl.decided_by,
       hdl.decision_rationale, hdl.created_at,
       te.task_type, te.pillar, te.execution_engine
FROM hm_decision_logs hdl
LEFT JOIN task_events te ON hdl.task_id = te.task_id
ORDER BY hdl.created_at DESC;

-- Approval bottleneck view
CREATE OR REPLACE VIEW v_approval_bottleneck AS
SELECT ag.approval_level, ag.status, ag.approver,
       COUNT(*) AS pending_count,
       MIN(ag.requested_at) AS oldest_pending,
       EXTRACT(EPOCH FROM NOW() - MIN(ag.requested_at)) / 3600 AS hours_waiting
FROM approval_gates ag
WHERE ag.status = 'pending'
GROUP BY ag.approval_level, ag.status, ag.approver
ORDER BY oldest_pending;

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at on mao_analyses
CREATE TRIGGER trg_mao_updated
    BEFORE UPDATE ON mao_analyses
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- Auto-update updated_at on deal_memos
CREATE TRIGGER trg_deal_updated
    BEFORE UPDATE ON deal_memos
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- MIGRATION TRACKING
-- ============================================================================
INSERT INTO schema_migrations (version) VALUES ('004_mao_analysis_schema');
