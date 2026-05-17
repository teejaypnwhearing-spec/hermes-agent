# Gridline Operational Playbook — Part 8: Data Flow & Integrations (S8)

## Objective
Define the data flow between all Gridline pipeline components and external integrations.

## Canonical Data Flow
```
Investra Export (CSV/JSON)
    ↓
lead_ingest.py (S1 — Ingest, validate, dedupe, score)
    ↓
PostgreSQL: leads table (canonical state store)
    ↓ pg_notify
n8n webhook (approval gate / notification)
    ↓
Telegram/Slack notification to operator
    ↓
Human approves via /approve/{id} endpoint
    ↓
orchestrator.resume_after_approval()
    ↓
Adapter executes (HermesAdapter → hermes-agent)
    ↓
Result written to PostgreSQL
    ↓
n8n status callback
    ↓
Notion sync (optional, secondary view)
```

## External Integrations

### Investra
- **Direction**: Inbound (data source)
- **Format**: CSV export with columns: property_address, owner_name, equity_pct, motivation_score, distress_flags, arv, repair_estimate
- **Frequency**: Daily batch export (automated via cron)
- **Storage**: `/data/investra/` on VM filesystem

### PostgreSQL
- **Role**: Canonical state store (non-negotiable)
- **Connection**: `localhost:5432`, database `fsh_command`
- **Tables**: leads, audit_trail, approval_gates, artifacts, task_events, handoff_records, retry_queue, dead_letter_queue, skills_registry, hm_decision_logs, mao_analyses, deal_memos

### n8n
- **Role**: Webhook receiver, approval notifier, status router
- **Workflows**: fsh-approval-gate.json, fsh-retry-queue.json
- **Polling**: retry_queue every 5 minutes

### Telegram/Slack
- **Role**: Operator notification channel
- **Messages**: Task completion, approval requests, error alerts, daily digest

### Notion
- **Role**: Secondary view (human-readable dashboard)
- **Sync**: One-way (PostgreSQL → Notion)
- **Pages**: Lead tracker, deal pipeline, compliance log

### Hermes Agent
- **Role**: Execution engine for Gridline skills
- **Entry point**: `hermes-agent --query "Execute skill {skill_name}: {objective}"`
- **Output**: Plain text response with structured result

## Data Refresh Schedule
| Job                   | Schedule  | Skill                    |
|-----------------------|-----------|--------------------------|
| Investra daily import | 06:00 UTC | gridline-lead-ingest     |
| Score recalculation   | 07:00 UTC | gridline-lead-scoring    |
| Distress detection    | 08:00 UTC | gridline-distress-detection |
| Daily report          | 18:00 UTC | gridline-daily-report    |
| Notion sync           | 19:00 UTC | (internal)               |

## Backup Strategy
- PostgreSQL: Daily pg_dump to `/backups/`
- Retention: 30 days rolling
- Restore test: Monthly (automated)
