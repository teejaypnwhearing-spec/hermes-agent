# Adapter: n8n Status Callback

## Overview
Defines the webhook contract for status callbacks from all FSH adapters to the n8n automation platform.

## Webhook Endpoint
```
POST https://n8n.example.com/webhook/fsh-status-callback
Content-Type: application/json
```

## Callback Payload Schema
```json
{
  "task_id": "uuid-v4",
  "execution_engine": "hermes|abacus|claude|manus",
  "status": "COMPLETED|FAILED|PENDING_APPROVAL|RETRYING",
  "result_artifact_url": "postgres://fsh_command/{table}/{id}",
  "error_detail": null,
  "timestamp": "2026-05-14T15:00:00Z",
  "metadata": {
    "skill_name": "gridline-lead-ingest",
    "pillar": "gridline",
    "duration_seconds": 12,
    "approval_level": 0
  }
}
```

## Status Values
| Status             | Meaning                                        | n8n Action                    |
|--------------------|------------------------------------------------|-------------------------------|
| COMPLETED          | Task executed successfully                     | Notify if approval_level >= 1 |
| FAILED             | Task execution failed                          | Notify operator + add to retry_queue |
| PENDING_APPROVAL   | Task requires human approval before proceeding | Trigger approval gate workflow |
| RETRYING           | Task failed and is being retried               | Log, no notification          |

## n8n Workflow Processing
1. Receive callback payload
2. Update PostgreSQL task status
3. Route based on status:
   - COMPLETED → If approval_level >= 1, send Telegram notification with summary
   - FAILED → Add to retry_queue (if attempts < 3) or dead_letter_queue (if exhausted)
   - PENDING_APPROVAL → Trigger fsh-approval-gate workflow → send Claw notification
   - RETRYING → Log only
4. If Notion sync is enabled, trigger sync workflow

## Retry Logic
```
Attempt 1 → Wait 1 minute
Attempt 2 → Wait 5 minutes
Attempt 3 → Wait 15 minutes
If all 3 fail → dead_letter_queue + urgent notification
```

## Security
- Webhook URL is not publicly documented
- Payload includes task_id for correlation (no sensitive data in callback)
- All callbacks logged to audit_trail
