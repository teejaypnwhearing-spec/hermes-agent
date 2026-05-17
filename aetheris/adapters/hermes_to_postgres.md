# Adapter: Hermes → PostgreSQL

## Overview
Defines how the Hermes Agent runtime writes execution results to the canonical PostgreSQL state store. All Hermes skill outputs are persisted here.

## Write Pattern
```python
# After Hermes execution completes
result = FSHTaskResult(
    task_id=task.task_id,
    success=exit_code == 0,
    result_summary=output[:2000],
    error_detail=None if success else {"stderr": stderr[:500]},
)

# Write to PostgreSQL
with conn.cursor() as cur:
    cur.execute("""
        UPDATE task_events
        SET status = %s, result_payload = %s, updated_at = NOW()
        WHERE task_id = %s
    """, (
        'completed' if result.success else 'failed',
        json.dumps(result.__dict__),
        task.task_id,
    ))
    conn.commit()
```

## Tables Written To
| Table             | When                          | What                    |
|-------------------|-------------------------------|-------------------------|
| leads             | After S1 lead_ingest          | New lead records        |
| audit_trail       | After every pipeline action   | Action log (immutable)  |
| approval_gates    | When approval required        | Gate request + status   |
| mao_analyses      | After S4 MAO calculation      | Analysis + MAO value    |
| deal_memos        | After S5 deal memo generation | Full deal memo          |
| hm_decision_logs  | After human approval/rejection| Decision record         |
| task_events       | After every task state change  | Event log               |

## Connection Management
- Use connection pooling in production (psycopg2.pool)
- Always commit on success, rollback on failure
- Close connections in finally block
- Set `app.current_pillar` for RLS (when implemented)

## Notification Trigger
After writing to PostgreSQL, emit a status callback to n8n:
```json
{
  "task_id": "uuid-v4",
  "execution_engine": "hermes",
  "status": "COMPLETED|FAILED|PENDING_APPROVAL",
  "result_artifact_url": "postgres://fsh_command/leads/{lead_id}",
  "timestamp": "2026-05-14T15:00:00Z"
}
```
