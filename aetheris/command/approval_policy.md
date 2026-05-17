# FSH Approval Policy

## Overview
The FSH Command Center implements a three-tier approval system. All tasks are assigned an `approval_level` that determines the human oversight required before execution.

## Approval Levels

### Level 0: No Approval Required
- **Who**: Automated execution, no human intervention
- **Scope**: Read-only operations, data processing, scoring, internal calculations
- **Examples**: lead_ingest, lead_scoring, csv_ingestion, property_dedup
- **Audit**: All actions still logged to audit_trail (immutable)

### Level 1: Notify After Execution
- **Who**: Operator notified via Telegram/Slack after task completes
- **Scope**: Operations that affect external systems but are reversible
- **Examples**: seller_outreach (first contact), mao_analysis, distress_detection
- **Audit**: Notification includes task summary, result, and compliance flags

### Level 2: Require Approval Before Execution
- **Who**: Human must explicitly approve before task proceeds
- **Scope**: Irreversible actions, financial commitments, external contracts
- **Examples**: deal_memo (offer submission), contract_signing, payment_authorization
- **Audit**: Full context provided to approver; approval token tracked; decision logged to hm_decision_logs

## Approval Workflow

```
Task submitted with approval_level=2
    ↓
Orchestrator creates approval_gate record
    ↓
Status callback sent to n8n webhook
    ↓
n8n sends notification to operator (Telegram/Slack)
    ↓
Operator clicks approve/reject URL (contains approval_token)
    ↓
If approved: orchestrator.resume_after_approval(task_id, token)
    ↓
Adapter receives approval_token, proceeds with execution
    ↓
Result written to Postgres + audit_trail + hm_decision_logs
```

## Protected Skill Prefixes
The following skill prefixes ALWAYS require approval regardless of the task's stated approval_level:
- `gridline-deal-memo-*` → minimum approval_level=2
- `gridline-seller-outreach-*` → minimum approval_level=1
- `forge-*` → minimum approval_level=1

## Approval Token Security
- Tokens are single-use (invalidate after first approval/rejection)
- Tokens expire after 24 hours
- Tokens are UUID v4, cryptographically random
- Approved/rejected tokens are logged with timestamp and IP

## Timeout and Escalation
- Level 2 approvals expire after 24 hours
- After expiration, task status moves to `expired`
- Expired tasks trigger escalation notification to operator
- Operator can re-submit the task to restart the approval cycle

## Override Policy
- No automated overrides for Level 2 approvals
- System administrator can override via direct database update (logged to hm_decision_logs as `override`)
- Override requires explicit rationale field
