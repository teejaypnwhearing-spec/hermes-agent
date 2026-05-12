# FSH Command Center

**Fermier Sovereign Holdings — Multi-Agent Orchestration Layer**

> Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) runtime.
> Branch: `fsh-command-center-spec`

---

## Overview

The FSH Command Center routes autonomous tasks across six business pillars through
a **federated adapter pattern** — each runtime (Hermes, Abacus, Claude, Manus)
maintains its own session and permission boundary.

```
Inbound Task (JSON)
      │
      ▼
 Orchestrator ──► Claude (PLAN / REVIEW hub)
      │
      ├──► HermesAdapter   → skills/ (content, gridline research)
      ├──► AbacusAdapter   → Abacus DeepAgent (gridline, commerce, trading)
      ├──► ClaudeAdapter   → Anthropic API (logic, forge, analysis)
      └──► ManusAdapter    → Manus browser automation (commerce, research)
```

All tasks share a **canonical task schema** (`schema/task_schema_v1.0.1.json`) with
strict pillar isolation enforced at the Postgres row-level security (RLS) layer.

---

## Directory Structure

```
fsh-command-center/
├── schema/
│   ├── task_schema_v1.0.0.json     ← baseline (documents known gaps)
│   └── task_schema_v1.0.1.json     ← corrected schema (use this)
├── database/
│   └── core_schema.sql             ← full Postgres DDL
├── adapters/
│   ├── __init__.py
│   ├── base.py                     ← FSHAdapterBase + FSHTask + exceptions
│   ├── hermes_adapter.py           ← Hermes skill execution
│   ├── abacus_adapter.py           ← Abacus DeepAgent (P0 fix: no blocking poll)
│   ├── claude_adapter.py           ← Claude reasoning hub
│   └── manus_adapter.py            ← Manus browser automation
├── config/
│   └── pillar_defaults.py          ← per-pillar engine/compliance/approval defaults
├── skills/
│   └── gridline/
│       ├── gridline-csv-ingestion/SKILL.md
│       ├── gridline-lead-scoring/SKILL.md
│       └── gridline-seller-outreach-draft/SKILL.md
├── monitoring/
│   └── queries.sql                 ← Grafana/psql operational queries
└── docs/
    └── ARCHITECTURE_REVIEW.md      ← full review with 19 prioritised recommendations
```

---

## Six Business Pillars

| Pillar    | Engine   | Approval | Key Compliance Flags                 |
|-----------|----------|----------|--------------------------------------|
| Gridline  | Abacus   | Level 1  | `rcw_18_85`, `pii`                   |
| Logic     | Claude   | Level 0  | `pii`                                |
| Commerce  | Abacus   | Level 1  | `external_action`, `affiliate_disclosure` |
| Content   | Hermes   | Level 0  | —                                    |
| Forge     | Claude   | Level 2  | `irreversible`                       |
| Trading   | Abacus   | Level 2  | `financial`, `external_action`       |

---

## Critical Fixes (vs. original spec)

### P0 — Blocking Approval Poll Eliminated
The original `AbacusAdapter.execute()` contained:
```python
while True:
    status = postgres.query_one(...)
    if status in ["approved", "rejected"]: break
    time.sleep(60)   # held DB connection for up to 48 hours
```
**Fix:** `AbacusAdapter.execute()` now raises `ApprovalRequiredError` immediately.
The orchestrator creates an `approval_requests` row. `pg_notify` fires when a
human decides → n8n picks up the callback → re-enqueues the task with an
`approval_token`. No thread blocks. No DB connection held open.

### Schema Gaps (v1.0.0 → v1.0.1)
- Added `task_type` to required fields (was causing Phase 1 validation failures)
- Added `priority` (1/2/3), `idempotency_key`, `retry_policy`, `parent_task_id`
- JSON Schema conditional: `idempotency_key` required when `external_action` or `financial` in flags

### Database Fixes
- `rcw_compliant BOOLEAN` → `rcw_status TEXT` state machine
- Trading signals: version-tied approval guard trigger
- Dead-letter queue table added
- Five missing indexes added
- Row-level security enabled on `tasks` and `audit_trail`

### Pillar Default Fixes
- **Forge**: `approval_level` raised 0→2; `compliance_flags` gains `irreversible`
- **Trading**: `approval_level` raised 1→2
- **Commerce**: `affiliate_disclosure` flag added

---

## Getting Started

### 1. Apply database schema
```bash
psql $DATABASE_URL -f fsh-command-center/database/core_schema.sql
```

### 2. Configure environment
```bash
cp .env.example .env
# Fill in ABACUS_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL, etc.
```

### 3. Submit a task
```python
from fsh_command_center.config.pillar_defaults import apply_defaults
from fsh_command_center.adapters import AbacusAdapter

raw = {
    "task_type":   "gridline_daily_review",
    "pillar":      "gridline",
    "objective":   "Review today's new leads and score top 50",
}

payload  = apply_defaults(raw)           # merges pillar defaults
adapter  = AbacusAdapter()
task     = adapter.translate_in(payload)
result   = adapter.execute(task)
envelope = adapter.translate_out(result)
```

---

## Architecture Review

See [`docs/ARCHITECTURE_REVIEW.md`](docs/ARCHITECTURE_REVIEW.md) for the full
19-recommendation production readiness review.

---

## Related

- [Hermes Agent Runtime](https://github.com/NousResearch/hermes-agent)
- [FSH Architecture Spec](docs/ARCHITECTURE_REVIEW.md)
- [Monitoring Queries](monitoring/queries.sql)
