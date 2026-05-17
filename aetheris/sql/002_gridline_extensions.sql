-- ============================================================================
-- AETHERIS Gridline Runtime — Gridline Extensions (Migration 002)
-- ============================================================================
-- Database: fsh_command
-- Schema Version: 2.0.0
-- Created: 2026-05-14
-- Description: Task events, handoff records, retry queue, dead letter queue.
--              Extends the core schema for the FSH task lifecycle.
-- ============================================================================

-- ============================================================================
-- TASK EVENTS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS task_events (
    event_id        BIGSERIAL PRIMARY KEY,
    task_id         UUID NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN (
        'submitted', 'routing', 'executing', 'approval_required',
        'approved', 'rejected', 'completed', 'failed', 'retried', 'dead_lettered'
    )),
    pillar          TEXT,
    task_type       TEXT,
    execution_engine TEXT,
    actor           TEXT DEFAULT 'system',
    details         JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_task_events_type ON task_events(event_type);
CREATE INDEX IF NOT EXISTS idx_task_events_created ON task_events(created_at DESC);

-- ============================================================================
-- HANDOFF RECORDS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS handoff_records (
    handoff_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID NOT NULL,
    from_adapter    TEXT NOT NULL,
    to_adapter      TEXT NOT NULL,
    reason          TEXT,
    context_payload JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_handoff_task ON handoff_records(task_id);
CREATE INDEX IF NOT EXISTS idx_handoff_from ON handoff_records(from_adapter);

-- ============================================================================
-- RETRY QUEUE TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS retry_queue (
    retry_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID NOT NULL,
    attempt         INTEGER DEFAULT 1,
    max_attempts    INTEGER DEFAULT 3,
    next_retry_at   TIMESTAMPTZ DEFAULT NOW(),
    last_error      TEXT,
    status          TEXT DEFAULT 'queued' CHECK (status IN ('queued', 'in_progress', 'completed', 'dead_lettered')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_retry_status ON retry_queue(status);
CREATE INDEX IF NOT EXISTS idx_retry_next ON retry_queue(next_retry_at) WHERE status = 'queued';

-- ============================================================================
-- DEAD LETTER QUEUE TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    dlq_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID NOT NULL,
    original_payload JSONB,
    final_error     TEXT,
    retry_count     INTEGER DEFAULT 0,
    dead_lettered_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dlq_task ON dead_letter_queue(task_id);
CREATE INDEX IF NOT EXISTS idx_dlq_created ON dead_letter_queue(dead_lettered_at DESC);

-- ============================================================================
-- VIEWS
-- ============================================================================

-- Active tasks view
CREATE OR REPLACE VIEW v_active_tasks AS
SELECT DISTINCT ON (task_id)
    task_id,
    event_type AS current_status,
    pillar,
    task_type,
    execution_engine,
    created_at
FROM task_events
ORDER BY task_id, created_at DESC;

-- Dead letter summary view
CREATE OR REPLACE VIEW v_dead_letter_summary AS
SELECT dlq.task_id, dlq.final_error, dlq.retry_count, dlq.dead_lettered_at,
       te.pillar, te.task_type
FROM dead_letter_queue dlq
LEFT JOIN LATERAL (
    SELECT pillar, task_type FROM task_events te2 WHERE te2.task_id = dlq.task_id LIMIT 1
) te ON true
ORDER BY dlq.dead_lettered_at DESC;

-- ============================================================================
-- MIGRATION TRACKING
-- ============================================================================
INSERT INTO schema_migrations (version) VALUES ('002_gridline_extensions');
