-- =============================================================================
-- FSH COMMAND CENTER — CORE DATABASE SCHEMA
-- Version: 1.0.1  (matches task_schema_v1.0.1.json)
-- Review-ref: fsh_architecture_review.md, R1–R19
-- =============================================================================
-- Change-log vs spec baseline (v1.0.0):
--   [FIX-DB-01] rcw_compliant BOOLEAN → rcw_status TEXT state machine
--   [FIX-DB-02] tasks.priority column added (enum 1/2/3)
--   [FIX-DB-03] retry_queue gains max_retries + status columns
--   [FIX-DB-04] dead_letter_queue table added
--   [FIX-DB-05] handoff_records.expires_at + auto-reject trigger
--   [FIX-DB-06] signals version-tied approval trigger
--   [FIX-DB-07] Five missing indexes added
--   [FIX-DB-08] Row-level security (RLS) on tasks + audit_trail
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions & setup
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";       -- for fuzzy lead search
CREATE EXTENSION IF NOT EXISTS "btree_gin";     -- composite indexes on jsonb

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

CREATE TYPE pillar_enum AS ENUM (
    'gridline', 'logic', 'commerce', 'content', 'forge', 'trading'
);

CREATE TYPE execution_engine_enum AS ENUM (
    'hermes', 'abacus', 'claude', 'manus', 'human'
);

CREATE TYPE approval_level_enum AS ENUM ('0', '1', '2');

-- [FIX-DB-01] RCW status state machine replaces boolean
-- State transitions:  not_applicable → n/a
--                     pending_review → compliant | non_compliant
--                     compliant      → (terminal)
--                     non_compliant  → pending_review (after remediation)
CREATE TYPE rcw_status_enum AS ENUM (
    'not_applicable',
    'pending_review',
    'compliant',
    'non_compliant'
);

CREATE TYPE task_status_enum AS ENUM (
    'queued', 'planning', 'executing', 'review', 'awaiting_approval',
    'approved', 'rejected', 'completed', 'failed', 'dead_lettered'
);

CREATE TYPE skill_state_enum AS ENUM (
    'draft', 'testing', 'approved', 'locked', 'deprecated'
);

-- ---------------------------------------------------------------------------
-- TASKS
-- ---------------------------------------------------------------------------
CREATE TABLE tasks (
    task_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    schema_version      TEXT NOT NULL DEFAULT '1.0.1',
    task_type           TEXT NOT NULL CHECK (task_type ~ '^[a-z][a-z0-9_]*$'),
    pillar              pillar_enum NOT NULL,
    objective           TEXT NOT NULL,
    -- [FIX-DB-02] priority column: 1=critical, 2=standard, 3=low
    priority            SMALLINT NOT NULL DEFAULT 2 CHECK (priority IN (1, 2, 3)),
    approval_level      approval_level_enum NOT NULL DEFAULT '0',
    execution_engine    execution_engine_enum NOT NULL,
    compliance_flags    TEXT[] NOT NULL DEFAULT '{}',
    idempotency_key     TEXT UNIQUE,          -- required when external_action|financial
    context_artifacts   JSONB NOT NULL DEFAULT '[]',
    retry_policy        JSONB NOT NULL DEFAULT '{"max_attempts":3,"backoff_strategy":"exponential","retry_on":["timeout","rate_limit"]}',
    parent_task_id      UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
    status              task_status_enum NOT NULL DEFAULT 'queued',
    assigned_to         TEXT,                 -- agent/user identifier
    result_summary      TEXT,
    error_detail        JSONB,
    expires_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    -- pillar-level row ownership for RLS
    pillar_owner        TEXT GENERATED ALWAYS AS (pillar::TEXT) STORED
);

-- updated_at trigger
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;
CREATE TRIGGER trg_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- GRIDLINE — leads
-- ---------------------------------------------------------------------------
CREATE TABLE gridline_leads (
    lead_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_file     TEXT,
    address         TEXT NOT NULL,
    owner_name      TEXT,
    owner_email     TEXT,
    owner_phone     TEXT,
    county          TEXT,
    state           TEXT DEFAULT 'WA',
    lead_score      NUMERIC(5,2),
    outreach_status TEXT NOT NULL DEFAULT 'new'
                    CHECK (outreach_status IN ('new','contacted','responded','qualified','disqualified','do_not_contact')),
    -- [FIX-DB-01] rcw state machine
    rcw_status      rcw_status_enum NOT NULL DEFAULT 'pending_review',
    rcw_reviewed_by TEXT,
    rcw_reviewed_at TIMESTAMPTZ,
    last_contact_at TIMESTAMPTZ,
    notes           TEXT,
    raw_data        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TRIGGER trg_leads_updated_at
    BEFORE UPDATE ON gridline_leads
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Enforce: non_compliant leads cannot be contacted
CREATE OR REPLACE FUNCTION enforce_rcw_outreach_gate()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.outreach_status IN ('contacted') AND NEW.rcw_status = 'non_compliant' THEN
        RAISE EXCEPTION 'Cannot mark lead as contacted while rcw_status=non_compliant (lead_id=%)', NEW.lead_id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_rcw_outreach_gate
    BEFORE UPDATE ON gridline_leads
    FOR EACH ROW EXECUTE FUNCTION enforce_rcw_outreach_gate();

-- ---------------------------------------------------------------------------
-- HANDOFF RECORDS  (cross-agent handoffs)
-- ---------------------------------------------------------------------------
CREATE TABLE handoff_records (
    handoff_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    from_agent      TEXT NOT NULL,
    to_agent        TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','accepted','rejected','expired')),
    -- [FIX-DB-05] 48-hour auto-expiry window
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '48 hours'),
    responded_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-reject expired handoffs via a scheduled job (see monitoring/queries.sql)
-- Trigger sets status='expired' when queried past expires_at
CREATE OR REPLACE FUNCTION auto_expire_handoffs()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'pending' AND NEW.expires_at < NOW() THEN
        NEW.status = 'expired';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_handoff_auto_expire
    BEFORE UPDATE ON handoff_records
    FOR EACH ROW EXECUTE FUNCTION auto_expire_handoffs();

-- ---------------------------------------------------------------------------
-- APPROVAL REQUESTS  (event-driven — replaces blocking poll)
-- [FIX: P0] AbacusAdapter no longer polls; approval_requests drives callbacks
-- ---------------------------------------------------------------------------
CREATE TABLE approval_requests (
    approval_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id             UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    requested_by        TEXT NOT NULL,
    approval_level      approval_level_enum NOT NULL,
    reason              TEXT NOT NULL,
    context_snapshot    JSONB NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','approved','rejected','expired','recalled')),
    decided_by          TEXT,
    decision_reason     TEXT,
    callback_url        TEXT,        -- n8n webhook URL for async notification
    notified_at         TIMESTAMPTZ,
    decided_at          TIMESTAMPTZ,
    -- approval window: default 48h, configurable per task
    expires_at          TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '48 hours'),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_approval_task_id  ON approval_requests(task_id);
CREATE INDEX idx_approval_pending  ON approval_requests(status) WHERE status = 'pending';

-- Notify n8n webhook when approval decision is recorded
CREATE OR REPLACE FUNCTION notify_approval_decision()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('approved','rejected') AND OLD.status = 'pending' THEN
        -- pg_notify picked up by n8n Postgres trigger or listener
        PERFORM pg_notify(
            'approval_decisions',
            json_build_object(
                'approval_id', NEW.approval_id,
                'task_id',     NEW.task_id,
                'status',      NEW.status,
                'decided_by',  NEW.decided_by,
                'callback_url', NEW.callback_url
            )::TEXT
        );
        NEW.decided_at = NOW();
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_approval_notify
    BEFORE UPDATE ON approval_requests
    FOR EACH ROW EXECUTE FUNCTION notify_approval_decision();

-- ---------------------------------------------------------------------------
-- AUDIT TRAIL
-- ---------------------------------------------------------------------------
CREATE TABLE audit_trail (
    audit_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id     UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
    pillar      pillar_enum,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}',
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- CONTEXT ARTIFACTS  (files, URLs, intermediate outputs)
-- ---------------------------------------------------------------------------
CREATE TABLE context_artifacts (
    artifact_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    artifact_type   TEXT NOT NULL CHECK (artifact_type IN ('file','url','text','json','image')),
    label           TEXT,
    storage_target  TEXT NOT NULL CHECK (storage_target IN ('postgres','notion','git','s3')),
    content_text    TEXT,
    content_jsonb   JSONB,
    external_ref    TEXT,            -- URL / git SHA / Notion page ID
    mime_type       TEXT,
    byte_size       BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- RETRY QUEUE  [FIX-DB-03]
-- ---------------------------------------------------------------------------
CREATE TABLE retry_queue (
    retry_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    attempt_number  SMALLINT NOT NULL DEFAULT 1,
    max_retries     SMALLINT NOT NULL DEFAULT 3,
    -- [FIX-DB-03] status column — spec omitted this
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','processing','succeeded','failed','dead_lettered')),
    error_class     TEXT,
    error_message   TEXT,
    retry_after     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempted  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_retry_queue_ready ON retry_queue(retry_after)
    WHERE status = 'pending';

-- Auto-promote to dead_letter when max_retries exceeded
CREATE OR REPLACE FUNCTION check_max_retries()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'failed' AND NEW.attempt_number >= NEW.max_retries THEN
        NEW.status = 'dead_lettered';
        -- also update the parent task
        UPDATE tasks SET status = 'dead_lettered' WHERE task_id = NEW.task_id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_retry_max_check
    BEFORE UPDATE ON retry_queue
    FOR EACH ROW EXECUTE FUNCTION check_max_retries();

-- ---------------------------------------------------------------------------
-- DEAD-LETTER QUEUE  [FIX-DB-04]
-- ---------------------------------------------------------------------------
CREATE TABLE dead_letter_queue (
    dlq_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    retry_id        UUID REFERENCES retry_queue(retry_id),
    pillar          pillar_enum,
    task_type       TEXT,
    objective       TEXT,
    final_error     JSONB NOT NULL DEFAULT '{}',
    total_attempts  SMALLINT NOT NULL DEFAULT 0,
    resolution      TEXT CHECK (resolution IN ('requeued','cancelled','manual_override', NULL)),
    resolved_by     TEXT,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_dlq_unresolved ON dead_letter_queue(created_at)
    WHERE resolution IS NULL;

-- ---------------------------------------------------------------------------
-- TRADING — signals
-- ---------------------------------------------------------------------------
CREATE TABLE trading_signals (
    signal_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
    ticker          TEXT NOT NULL,
    signal_type     TEXT NOT NULL CHECK (signal_type IN ('buy','sell','hold','alert')),
    signal_value    NUMERIC(18,8),
    confidence      NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
    -- [FIX-DB-06] version for approval fingerprinting
    version         SMALLINT NOT NULL DEFAULT 1,
    approved_at     TIMESTAMPTZ,
    approved_by     TEXT,
    invalidated_at  TIMESTAMPTZ,   -- set when signal modified after approval
    source_model    TEXT,
    raw_payload     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- [FIX-DB-06] Invalidate approval when signal data changes post-approval
CREATE OR REPLACE FUNCTION flag_signal_modification()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.approved_at IS NOT NULL AND (
        OLD.signal_value  IS DISTINCT FROM NEW.signal_value  OR
        OLD.signal_type   IS DISTINCT FROM NEW.signal_type   OR
        OLD.ticker        IS DISTINCT FROM NEW.ticker
    ) THEN
        NEW.invalidated_at = NOW();
        NEW.approved_at    = NULL;
        NEW.approved_by    = NULL;
        NEW.version        = OLD.version + 1;
        RAISE WARNING 'Signal % modified after approval — invalidated (was approved by %)',
            NEW.signal_id, OLD.approved_by;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_signal_approval_guard
    BEFORE UPDATE ON trading_signals
    FOR EACH ROW EXECUTE FUNCTION flag_signal_modification();

-- ---------------------------------------------------------------------------
-- SKILLS REGISTRY
-- ---------------------------------------------------------------------------
CREATE TABLE skills_registry (
    skill_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    skill_name      TEXT UNIQUE NOT NULL,
    pillar          pillar_enum NOT NULL,
    category        TEXT,
    state           skill_state_enum NOT NULL DEFAULT 'draft',
    description     TEXT,
    skill_md_path   TEXT,           -- relative path in hermes skills/ tree
    version         TEXT NOT NULL DEFAULT '0.1.0',
    promoted_by     TEXT,
    promoted_at     TIMESTAMPTZ,
    deprecated_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TRIGGER trg_skills_updated_at
    BEFORE UPDATE ON skills_registry
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- INDEXES  [FIX-DB-07] — five missing indexes from spec + extras
-- ---------------------------------------------------------------------------

-- tasks: most common query patterns
CREATE INDEX idx_tasks_pillar_status    ON tasks(pillar, status);
CREATE INDEX idx_tasks_priority         ON tasks(priority, created_at DESC);
CREATE INDEX idx_tasks_parent           ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;
CREATE INDEX idx_tasks_idempotency      ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- context_artifacts
CREATE INDEX idx_artifacts_task_id      ON context_artifacts(task_id);       -- [FIX-DB-07 #1]

-- audit_trail
CREATE INDEX idx_audit_trail_task_action ON audit_trail(task_id, action);    -- [FIX-DB-07 #2]
CREATE INDEX idx_audit_trail_pillar_time ON audit_trail(pillar, created_at DESC);

-- gridline_leads
CREATE INDEX idx_leads_outreach_status  ON gridline_leads(outreach_status);  -- [FIX-DB-07 #3]
CREATE INDEX idx_leads_score            ON gridline_leads(lead_score DESC);  -- [FIX-DB-07 #4]
CREATE INDEX idx_leads_rcw_status       ON gridline_leads(rcw_status);       -- [FIX-DB-07 #5]

-- trading_signals
CREATE INDEX idx_signals_ticker_type    ON trading_signals(ticker, signal_type, created_at DESC);

-- ---------------------------------------------------------------------------
-- ROW-LEVEL SECURITY  [FIX-DB-08]
-- ---------------------------------------------------------------------------

ALTER TABLE tasks       ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_trail ENABLE ROW LEVEL SECURITY;

-- Each pillar's service role sees only its own rows.
-- Set via: SET LOCAL app.current_pillar = 'gridline';
CREATE POLICY pillar_isolation_tasks ON tasks
    USING (pillar_owner = current_setting('app.current_pillar', TRUE));

CREATE POLICY pillar_isolation_audit ON audit_trail
    USING (pillar::TEXT = current_setting('app.current_pillar', TRUE));

-- Superuser / orchestrator role bypasses RLS
-- GRANT fsh_orchestrator TO hermes_service;
-- ALTER ROLE fsh_orchestrator BYPASSRLS;

-- ---------------------------------------------------------------------------
-- HELPER VIEWS
-- ---------------------------------------------------------------------------

CREATE VIEW v_active_tasks AS
SELECT t.task_id, t.task_type, t.pillar, t.priority, t.status,
       t.approval_level, t.execution_engine, t.assigned_to,
       t.created_at, t.expires_at,
       (SELECT COUNT(*) FROM context_artifacts ca WHERE ca.task_id = t.task_id) AS artifact_count,
       (SELECT COUNT(*) FROM retry_queue rq WHERE rq.task_id = t.task_id AND rq.status = 'pending') AS pending_retries
FROM tasks t
WHERE t.status NOT IN ('completed','failed','dead_lettered','rejected');

CREATE VIEW v_pending_approvals AS
SELECT ar.approval_id, ar.task_id, t.pillar, t.task_type,
       ar.approval_level, ar.requested_by, ar.reason,
       ar.expires_at, ar.created_at,
       EXTRACT(EPOCH FROM (ar.expires_at - NOW()))/3600 AS hours_remaining
FROM approval_requests ar
JOIN tasks t ON t.task_id = ar.task_id
WHERE ar.status = 'pending' AND ar.expires_at > NOW()
ORDER BY ar.expires_at ASC;

CREATE VIEW v_dead_letter_summary AS
SELECT d.pillar, d.task_type,
       COUNT(*) AS dlq_count,
       MAX(d.created_at) AS latest_failure,
       COUNT(*) FILTER (WHERE d.resolution IS NULL) AS unresolved
FROM dead_letter_queue d
GROUP BY d.pillar, d.task_type
ORDER BY dlq_count DESC;

-- ---------------------------------------------------------------------------
-- SEED: pillar defaults reference table
-- (Full defaults live in config/pillar_defaults.py — this mirrors them for SQL queries)
-- ---------------------------------------------------------------------------
CREATE TABLE pillar_defaults (
    pillar              pillar_enum PRIMARY KEY,
    default_engine      execution_engine_enum NOT NULL,
    default_approval    approval_level_enum NOT NULL,
    compliance_flags    TEXT[] NOT NULL DEFAULT '{}',
    storage_targets     TEXT[] NOT NULL DEFAULT '{"postgres"}',
    notes               TEXT
);

INSERT INTO pillar_defaults VALUES
('gridline', 'abacus',  '1', ARRAY['rcw_18_85','pii'],                      ARRAY['postgres','notion','git'], 'RCW 18.85 governs all seller outreach'),
('logic',    'claude',  '0', ARRAY['pii'],                                   ARRAY['postgres','notion'],       'Digital identity workflows'),
('commerce', 'abacus',  '1', ARRAY['external_action','affiliate_disclosure'],ARRAY['postgres','notion'],       'Amazon/TikTok/Shopify integrations'),
('content',  'hermes',  '0', ARRAY[],                                        ARRAY['postgres','git'],          'Content generation pipeline'),
('forge',    'claude',  '2', ARRAY['irreversible'],                          ARRAY['postgres','git'],          'IP/SOP extraction — approval_level=2 required'),
('trading',  'abacus',  '2', ARRAY['financial','external_action'],           ARRAY['postgres'],                'Trading signals — highest approval gate');

-- ---------------------------------------------------------------------------
-- SCHEMA COMPLETE
-- Apply with: psql $DATABASE_URL -f core_schema.sql
-- Rollback:   psql $DATABASE_URL -f rollback_schema.sql  (see docs/)
-- ---------------------------------------------------------------------------
