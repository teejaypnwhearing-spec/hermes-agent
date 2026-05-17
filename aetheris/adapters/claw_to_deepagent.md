# Adapter: Claw → DeepAgent

## Overview
Defines how Abacus Claw (command router / notification layer) communicates approval decisions back to the DeepAgent control plane.

## Flow
1. Operator receives approval notification via Claw (Telegram/Slack)
2. Operator clicks approve/reject URL
3. Claw routes the decision to the FSH `/approve/{id}` or `/reject/{id}` endpoint
4. FSH webhook_server processes the approval
5. `orchestrator.resume_after_approval()` is called
6. Task proceeds or is aborted based on the decision

## Claw Notification Format
```json
{
  "notification_type": "approval_request",
  "task_id": "uuid-v4",
  "gate_id": "uuid-v4",
  "approval_level": 2,
  "task_summary": {
    "task_type": "gridline-deal-memo-builder",
    "pillar": "gridline",
    "objective": "Generate deal memo for 1234 N Oak St",
    "compliance_flags": ["financial", "irreversible"],
    "risk_level": "medium"
  },
  "approve_url": "https://fsh.example.com/approve/{token}",
  "reject_url": "https://fsh.example.com/reject/{token}",
  "expires_at": "2026-05-15T15:00:00Z"
}
```

## Decision Callback
```json
{
  "decision_type": "approve|reject",
  "gate_id": "uuid-v4",
  "approval_token": "one-time-token",
  "decided_by": "operator_tj",
  "rationale": "MAO within acceptable range, comps verified",
  "timestamp": "2026-05-14T16:30:00Z"
}
```

## Escalation Rules
- If no response within 4 hours: send follow-up notification
- If no response within 24 hours: mark approval as expired, notify with urgency
- If task deadline is within 2 hours: send urgent notification
