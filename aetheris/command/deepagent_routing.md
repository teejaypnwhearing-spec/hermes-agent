# DeepAgent Routing Configuration

## Overview
This document defines how the FSH Command Interface routes tasks through the Abacus DeepAgent adapter. DeepAgent serves as both an execution engine and the primary control plane for the FSH system.

## Routing Matrix

### Inbound Routes (Task → DeepAgent)
| Source              | Trigger                           | Route To                    |
|---------------------|-----------------------------------|-----------------------------|
| Telegram message    | `/task <json>` command            | DeepAgent task intake       |
| Webhook POST        | `/tasks/submit` endpoint          | DeepAgent task intake       |
| Cron trigger        | Scheduled job definition          | DeepAgent batch execution   |
| n8n callback        | Status update from adapter        | DeepAgent state update      |

### Outbound Routes (DeepAgent → Adapter)
| Task Engine | Route                                        | Conditions                     |
|-------------|----------------------------------------------|-------------------------------|
| hermes      | `HermesAdapter.execute(task)`                | `execution_engine == "hermes"` |
| abacus      | `AbacusAdapter.execute(task)`                | `execution_engine == "abacus"` |
| claude      | `ClaudeAdapter.execute(task)`                | `execution_engine == "claude"` |
| manus       | `ManusAdapter.execute(task)`                 | `execution_engine == "manus"`  |

### Special Routing
| Condition                              | Override Route                | Reason                           |
|----------------------------------------|-------------------------------|----------------------------------|
| `approval_level >= 2`                  | Pause + approval gate first   | Human must approve before exec   |
| `compliance_flags` contains `"pii"`    | Log access + restrict storage | Privacy compliance               |
| `compliance_flags` contains `"rcw_18_85"` | Add legal review step      | WA state licensing requirement   |
| Adapter returns `ApprovalRequiredError` | Route to approval pipeline  | Adapter-level gate triggered     |

## DeepAgent Task Lifecycle
1. **Intake**: Receive task object, validate schema, write to Postgres (status=PENDING)
2. **Routing**: Select adapter based on `execution_engine` field
3. **Execution**: Adapter processes task, writes result to Postgres
4. **Callback**: Adapter emits status callback to n8n webhook
5. **Notification**: n8n routes to Telegram/Slack/Email based on approval_level
6. **Completion**: Result available via `/tasks/{task_id}` endpoint

## Error Recovery
- If DeepAgent crashes mid-task, n8n retry queue picks it up within 5 minutes
- If adapter returns `FAILED`, task enters retry_queue (max 3 attempts)
- If 3 retries fail, task moves to dead_letter_queue and operator is notified
