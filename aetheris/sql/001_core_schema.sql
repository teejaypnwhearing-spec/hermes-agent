-- ============================================================================
-- AETHERIS Gridline Runtime — Core Schema (Migration 001)
-- ============================================================================
-- Database: fsh_command
-- Schema Version: 1.0.0
-- Created: 2026-05-14
-- Description: Core tables for the FSH Command Center lead pipeline.
--              Leads, audit trail, approval gates, and artifacts.
-- ============================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- LEADS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS leads (
    lead_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_address TEXT NOT NULL,
    owner_name       TEXT,
    equity_pct       NUMERIC(5,2),
    motivation_score INTEGER DEFAULT 0,
    distress_flags   JSONB DEFAULT '[]'::jsonb,
    tier             CHAR(1) GENERATED ALWAYS AS (
        CASE
            WHEN equity_pct >= 75 THEN 'A'
            WHEN equity_pct >= 50 THEN 'B'
            WHEN equity_pct >= 25 THEN 'C'
            ELSE 'D'
        END
    ) STORED,
    compliance_status  TEXT DEFAULT 'pending_review' CHECK (compliance_status IN ('pending_review', 'approved', 'rejected', 'escalated')),
    outreach_status     TEXT DEFAULT 'not_started' CHECK (outreach_status IN ('not_started', 'in_progress', 'contacted', 'responded', 'closed')),
    score               NUMERIC(6,2),
    source              TEXT DEFAULT 'investra',
    batch_id            TEXT,
    raw_payload         JSONB,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_leads_tier ON leads(tier);
CREATE INDEX IF NOT EXISTS idx_leads_compliance ON leads(compliance_status);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_address ON leads(property_address);
CREATE INDEX IF NOT EXISTS idx_leads_outreach ON leads(outreach_status);
CREATE INDEX IF NOT EXISTS idx_leads_batch ON leads(batch_id);

-- Unique constraint to prevent duplicate leads by address
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_address_unique ON leads(property_address);

-- ============================================================================
-- AUDIT TRAIL TABLE (Immutable)
-- ============================================================================
CREATE TABLE IF NOT EXISTS audit_trail (
    audit_id    BIGSERIAL PRIMARY KEY,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    target_type TEXT,
    target_id   UUID,
    details     JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Audit trail is append-only — no updates or deletes
REVOKE UPDATE, DELETE ON audit_trail FROM PUBLIC;

CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_trail(action);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_trail(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_trail(created_at DESC);

-- ============================================================================
-- APPROVAL GATES TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS approval_gates (
    gate_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID,
    approval_level  INTEGER NOT NULL DEFAULT 0,
    status          TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    approver        TEXT,
    approval_token  TEXT,
    requested_at    TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_gates(status);
CREATE INDEX IF NOT EXISTS idx_approval_task ON approval_gates(task_id);

-- ============================================================================
-- ARTIFACTS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID,
    artifact_type   TEXT NOT NULL,
    storage_path    TEXT,
    content_hash    TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);

-- ============================================================================
-- VIEWS
-- ============================================================================

-- Leads by tier view
CREATE OR REPLACE VIEW v_leads_by_tier AS
SELECT tier, COUNT(*) AS total,
       ROUND(AVG(score), 2) AS avg_score,
       ROUND(AVG(equity_pct), 2) AS avg_equity
FROM leads
GROUP BY tier
ORDER BY tier;

-- Pending approvals view
CREATE OR REPLACE VIEW v_pending_approvals AS
SELECT ag.*, te.task_type, te.pillar
FROM approval_gates ag
LEFT JOIN task_events te ON ag.task_id = te.task_id
WHERE ag.status = 'pending'
ORDER BY ag.requested_at DESC;

-- Recent audit view
CREATE OR REPLACE VIEW v_recent_audit AS
SELECT * FROM audit_trail
ORDER BY created_at DESC
LIMIT 100;

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Auto-update updated_at on leads
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_leads_updated
    BEFORE UPDATE ON leads
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- INITIAL DATA
-- ============================================================================
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO schema_migrations (version) VALUES ('001_core_schema');

-- Grant permissions
GRANT SELECT, INSERT, UPDATE ON leads TO postgres;
GRANT SELECT, INSERT ON audit_trail TO postgres;
GRANT SELECT, INSERT, UPDATE ON approval_gates TO postgres;
GRANT SELECT, INSERT ON artifacts TO postgres;
GRANT SELECT ON v_leads_by_tier TO postgres;
GRANT SELECT ON v_pending_approvals TO postgres;
GRANT SELECT ON v_recent_audit TO postgres;
