-- =============================================================================
-- FSH Command Center — Operational Monitoring Queries
-- =============================================================================
-- Run these against the FSH Postgres instance.
-- Compatible with: Grafana (postgres datasource), psql, Metabase
-- Review-ref: fsh_architecture_review.md §RISK MATRIX (Operational section)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. SYSTEM HEALTH DASHBOARD
-- ---------------------------------------------------------------------------

-- 1a. Task throughput by pillar and status (last 24h)
SELECT
    pillar,
    status,
    COUNT(*)                         AS task_count,
    AVG(EXTRACT(EPOCH FROM (completed_at - created_at)))::INT AS avg_duration_sec
FROM tasks
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY pillar, status
ORDER BY pillar, status;

-- 1b. Hourly task volume (Grafana time-series)
SELECT
    DATE_TRUNC('hour', created_at)   AS hour_bucket,
    pillar,
    COUNT(*)                         AS tasks_created,
    COUNT(*) FILTER (WHERE status = 'completed')  AS tasks_completed,
    COUNT(*) FILTER (WHERE status = 'failed')     AS tasks_failed
FROM tasks
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY hour_bucket, pillar
ORDER BY hour_bucket DESC;

-- ---------------------------------------------------------------------------
-- 2. APPROVAL QUEUE MONITORING  (P0 fix — was blocking poll)
-- ---------------------------------------------------------------------------

-- 2a. Pending approvals with time remaining
SELECT
    ar.approval_id,
    ar.task_id,
    t.pillar,
    t.task_type,
    ar.approval_level,
    ar.requested_by,
    ar.reason,
    ar.expires_at,
    ROUND(EXTRACT(EPOCH FROM (ar.expires_at - NOW()))/3600, 1) AS hours_remaining,
    ar.created_at
FROM approval_requests ar
JOIN tasks t ON t.task_id = ar.task_id
WHERE ar.status = 'pending'
  AND ar.expires_at > NOW()
ORDER BY ar.expires_at ASC;

-- 2b. Approvals expiring in next 4 hours (alert threshold)
SELECT COUNT(*) AS expiring_soon
FROM approval_requests
WHERE status = 'pending'
  AND expires_at BETWEEN NOW() AND NOW() + INTERVAL '4 hours';

-- 2c. Approval decision times (SLA tracking)
SELECT
    t.pillar,
    ar.approval_level,
    COUNT(*)                                                    AS total,
    AVG(EXTRACT(EPOCH FROM (ar.decided_at - ar.created_at))/3600)::NUMERIC(8,1)
                                                                AS avg_decision_hours,
    PERCENTILE_CONT(0.95) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (ar.decided_at - ar.created_at))/3600
    )::NUMERIC(8,1)                                             AS p95_decision_hours
FROM approval_requests ar
JOIN tasks t ON t.task_id = ar.task_id
WHERE ar.status IN ('approved','rejected')
  AND ar.decided_at >= NOW() - INTERVAL '30 days'
GROUP BY t.pillar, ar.approval_level
ORDER BY t.pillar;

-- ---------------------------------------------------------------------------
-- 3. DEAD-LETTER QUEUE MONITORING  [FIX-DB-04]
-- ---------------------------------------------------------------------------

-- 3a. DLQ backlog by pillar and task_type
SELECT
    pillar,
    task_type,
    COUNT(*)                                AS dlq_count,
    MAX(created_at)                         AS latest_failure,
    COUNT(*) FILTER (WHERE resolution IS NULL) AS unresolved
FROM dead_letter_queue
GROUP BY pillar, task_type
ORDER BY unresolved DESC, dlq_count DESC;

-- 3b. DLQ growth rate (alert if > 10 new entries in 1 hour)
SELECT COUNT(*) AS new_dlq_entries_1h
FROM dead_letter_queue
WHERE created_at >= NOW() - INTERVAL '1 hour'
  AND resolution IS NULL;

-- 3c. Top error classes in DLQ
SELECT
    final_error->>'error_class'             AS error_class,
    COUNT(*)                                AS occurrences,
    MAX(created_at)                         AS last_seen
FROM dead_letter_queue
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY error_class
ORDER BY occurrences DESC
LIMIT 20;

-- ---------------------------------------------------------------------------
-- 4. RETRY QUEUE HEALTH
-- ---------------------------------------------------------------------------

-- 4a. Retry queue depth by status
SELECT
    status,
    COUNT(*)                    AS count,
    MIN(retry_after)            AS next_retry
FROM retry_queue
WHERE status IN ('pending', 'processing', 'failed')
GROUP BY status;

-- 4b. Tasks with high retry counts (approaching DLQ)
SELECT
    rq.task_id,
    t.pillar,
    t.task_type,
    rq.attempt_number,
    rq.max_retries,
    rq.error_class,
    rq.retry_after
FROM retry_queue rq
JOIN tasks t ON t.task_id = rq.task_id
WHERE rq.status = 'pending'
  AND rq.attempt_number >= rq.max_retries - 1   -- one attempt left
ORDER BY rq.attempt_number DESC;

-- ---------------------------------------------------------------------------
-- 5. GRIDLINE — RCW 18.85 COMPLIANCE  [FIX-DB-01]
-- ---------------------------------------------------------------------------

-- 5a. RCW status distribution (should have near-zero non_compliant)
SELECT
    rcw_status,
    COUNT(*)                    AS lead_count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct
FROM gridline_leads
GROUP BY rcw_status
ORDER BY lead_count DESC;

-- 5b. Leads pending RCW review for > 48h (SLA breach)
SELECT
    lead_id,
    address,
    owner_name,
    outreach_status,
    rcw_status,
    created_at,
    ROUND(EXTRACT(EPOCH FROM (NOW() - created_at))/3600, 1) AS hours_pending
FROM gridline_leads
WHERE rcw_status = 'pending_review'
  AND created_at < NOW() - INTERVAL '48 hours'
ORDER BY created_at ASC;

-- 5c. Attempted outreach on non-compliant leads (CRITICAL — should be 0)
SELECT COUNT(*) AS compliance_violations
FROM gridline_leads
WHERE rcw_status = 'non_compliant'
  AND outreach_status = 'contacted';

-- 5d. Outreach volume by status (weekly)
SELECT
    DATE_TRUNC('week', last_contact_at) AS week,
    outreach_status,
    COUNT(*)                            AS lead_count
FROM gridline_leads
WHERE last_contact_at >= NOW() - INTERVAL '12 weeks'
GROUP BY week, outreach_status
ORDER BY week DESC;

-- ---------------------------------------------------------------------------
-- 6. TRADING — Signal Approval Integrity  [FIX-DB-06]
-- ---------------------------------------------------------------------------

-- 6a. Signals invalidated after approval (should be 0)
SELECT COUNT(*) AS post_approval_modifications
FROM trading_signals
WHERE invalidated_at IS NOT NULL
  AND created_at >= NOW() - INTERVAL '30 days';

-- 6b. Signals awaiting approval
SELECT
    signal_id,
    ticker,
    signal_type,
    signal_value,
    confidence,
    version,
    created_at,
    ROUND(EXTRACT(EPOCH FROM (NOW() - created_at))/3600, 1) AS hours_pending
FROM trading_signals
WHERE approved_at IS NULL
  AND invalidated_at IS NULL
ORDER BY confidence DESC, created_at ASC;

-- 6c. Signal approval latency (p95)
SELECT
    signal_type,
    PERCENTILE_CONT(0.95) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (approved_at - created_at))/60
    )::NUMERIC(8,1) AS p95_approval_minutes
FROM trading_signals
WHERE approved_at IS NOT NULL
  AND created_at >= NOW() - INTERVAL '30 days'
GROUP BY signal_type;

-- ---------------------------------------------------------------------------
-- 7. AUDIT TRAIL ANALYTICS
-- ---------------------------------------------------------------------------

-- 7a. Action frequency by pillar (last 7 days)
SELECT
    pillar,
    action,
    COUNT(*)            AS occurrences,
    COUNT(DISTINCT actor) AS unique_actors
FROM audit_trail
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY pillar, action
ORDER BY pillar, occurrences DESC;

-- 7b. Unusual off-hours activity (outside 6am–10pm PT)
SELECT
    audit_id,
    task_id,
    pillar,
    action,
    actor,
    created_at,
    created_at AT TIME ZONE 'America/Los_Angeles' AS local_time
FROM audit_trail
WHERE EXTRACT(HOUR FROM created_at AT TIME ZONE 'America/Los_Angeles')
      NOT BETWEEN 6 AND 22
  AND created_at >= NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;

-- ---------------------------------------------------------------------------
-- 8. PERFORMANCE BASELINES
-- ---------------------------------------------------------------------------

-- 8a. Adapter p50/p95/p99 execution times (last 7 days)
SELECT
    t.execution_engine,
    t.pillar,
    COUNT(*)                                                    AS task_count,
    PERCENTILE_CONT(0.50) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (t.completed_at - t.created_at))
    )::INT AS p50_sec,
    PERCENTILE_CONT(0.95) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (t.completed_at - t.created_at))
    )::INT AS p95_sec,
    PERCENTILE_CONT(0.99) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (t.completed_at - t.created_at))
    )::INT AS p99_sec
FROM tasks t
WHERE t.status = 'completed'
  AND t.completed_at >= NOW() - INTERVAL '7 days'
GROUP BY t.execution_engine, t.pillar
ORDER BY p95_sec DESC;

-- 8b. Claude hub latency check (upgrade to LangGraph if p95 > threshold)
-- Alert threshold: p95 > 30s for claude adapter
SELECT
    PERCENTILE_CONT(0.95) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (completed_at - created_at))
    )::INT AS claude_p95_sec,
    CASE WHEN PERCENTILE_CONT(0.95) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (completed_at - created_at))
    ) > 30 THEN 'CONSIDER_LANGGRAPH_UPGRADE' ELSE 'OK' END AS recommendation
FROM tasks
WHERE execution_engine = 'claude'
  AND status = 'completed'
  AND completed_at >= NOW() - INTERVAL '7 days';

-- ---------------------------------------------------------------------------
-- 9. SCHEDULED MAINTENANCE JOBS
-- (Run these via pg_cron or n8n schedule — daily at 02:00 PT)
-- ---------------------------------------------------------------------------

-- 9a. Expire stale handoff_records
UPDATE handoff_records
SET status = 'expired'
WHERE status = 'pending'
  AND expires_at < NOW();

-- 9b. Expire stale approval_requests and fail their tasks
WITH expired_approvals AS (
    UPDATE approval_requests
    SET status = 'expired'
    WHERE status = 'pending'
      AND expires_at < NOW()
    RETURNING task_id
)
UPDATE tasks
SET status = 'failed',
    error_detail = '{"error_class":"approval_expired","message":"Approval window closed without decision"}'
WHERE task_id IN (SELECT task_id FROM expired_approvals)
  AND status = 'awaiting_approval';

-- 9c. Promote failed retries to DLQ
INSERT INTO dead_letter_queue (task_id, retry_id, pillar, task_type, objective, final_error, total_attempts)
SELECT
    rq.task_id,
    rq.retry_id,
    t.pillar,
    t.task_type,
    t.objective,
    t.error_detail,
    rq.attempt_number
FROM retry_queue rq
JOIN tasks t ON t.task_id = rq.task_id
WHERE rq.status = 'dead_lettered'
  AND NOT EXISTS (
      SELECT 1 FROM dead_letter_queue dlq WHERE dlq.task_id = rq.task_id
  );

-- =============================================================================
-- END MONITORING QUERIES
-- Schedule via: pg_cron, n8n Postgres node, or Grafana alerting
-- =============================================================================
