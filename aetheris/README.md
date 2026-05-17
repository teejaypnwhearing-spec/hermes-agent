# AETHERIS Gridline Runtime

**Version**: 1.0.0  
**Branch**: `feature/aetheris-gridline-runtime-v1`  
**Status**: Active Development  
**Canonical Source**: Abacus VM → GitHub (this branch)

## Overview

AETHERIS (Automated Execution & Transaction Handling for Enhanced Real-estate Intelligence Systems) is the execution layer for the Fermier Sovereign Holdings (FSH) Command Center. It provides the skills, schemas, SQL migrations, operational playbooks, and adapter routing specifications that power the wholesale real-estate pipeline.

## Directory Structure

```
aetheris/
├── skills/              # Executable skill modules
│   └── gridline-lead-ingest/
│       ├── SKILL.md     # Skill specification
│       └── lead_ingest.py  # Production lead ingestion pipeline
├── sql/                 # PostgreSQL migrations (001-004)
│   ├── 001_core_schema.sql          # Core tables: leads, audit_trail, approval_gates, artifacts
│   ├── 002_gridline_extensions.sql  # Task events, handoffs, retry queue, dead letter queue
│   ├── 003_lead_scoring_functions.sql # Scoring model v1 + skills registry
│   └── 004_mao_analysis_schema.sql  # MAO analyses, deal memos, HM decision logs
├── schemas/             # JSON schemas for all packet types
│   ├── task_packet.json     # Canonical 10-field task object
│   ├── handoff_packet.json  # Adapter-to-adapter handoff
│   ├── approval_gate.json   # Human approval workflow
│   ├── hm_decision_log.json # Human decision audit
│   ├── lead_batch.json      # Lead import batch
│   ├── mao_analysis.json    # MAO calculation result
│   └── deal_memo.json       # Structured deal memo
├── command/             # FSH Command system configuration
│   ├── system_prompt.md      # DeepAgent system prompt
│   ├── deepagent_routing.md  # Task routing rules
│   └── approval_policy.md    # Approval level definitions
├── gridline/            # 8-part operational playbook
│   ├── 01_overview.md              # Pipeline overview
│   ├── 02_lead_ingest.md           # S1: Lead ingestion
│   ├── 03_lead_scoring.md          # S2: Lead scoring
│   ├── 04_seller_outreach.md       # S3: Seller outreach
│   ├── 05_mao_analysis.md          # S4: MAO calculation
│   ├── 06_deal_memo.md             # S5: Deal memo generation
│   ├── 07_compliance_audit.md      # S6: Compliance & audit
│   └── 08_data_flow_integrations.md # S7: Data flow & integrations
├── adapters/            # Adapter routing specifications
│   ├── deepagent_to_hermes.md    # DeepAgent → Hermes routing
│   ├── claw_to_deepagent.md      # Claw → DeepAgent approval flow
│   ├── hermes_to_postgres.md     # Hermes → PostgreSQL writes
│   ├── postgres_to_notion.md     # PostgreSQL → Notion sync
│   └── n8n_status_callback.md    # n8n webhook contract
├── jobs/                # Scheduled job definitions
│   ├── investra_daily_import.md      # Daily Investra batch import
│   ├── daily_score_recalc.md         # Daily score recalculation
│   ├── daily_distress_detection.md   # Daily distress signal scan
│   ├── daily_report.md               # Daily pipeline report
│   └── weekly_compliance_review.md   # Weekly RCW 18.85 audit
├── samples/             # Sample payloads for testing
│   ├── sample_task_packet.json
│   ├── sample_lead_batch.json
│   ├── sample_approval_gate.json
│   ├── sample_mao_analysis.json
│   ├── sample_deal_memo.json
│   └── sample_handoff_packet.json
└── README.md            # This file
```

## Architecture

AETHERIS is the **execution layer** that lives alongside the **control plane** (`fsh-command-center/`). The relationship:

- `fsh-command-center/` = Orchestrator, adapters (Python), API endpoints, Docker, tests
- `aetheris/` = Skills, SQL, schemas, playbooks, routing specs, samples

They are complementary layers, not competitors. The orchestrator dispatches tasks; AETHERIS defines what those tasks do and how data flows.

## Scoring Formula (v1 — Confirmed)

```
equity_component = equity_pct × 0.70
motivation_component = motivation_score × 3.50
distress_component = len(distress_flags) × 5.0
raw_score = equity_component + motivation_component + distress_component
market_adj = raw_score × 0.10
score = raw_score - market_adj
```

**Confirmed outputs**: Eleanor Vance → 73.57, Sarah Jenkins → 63.25  
**STALE (DO NOT USE)**: 50.07 / 38.40

## Compliance

- RCW 18.85 (Washington State wholesale real-estate licensing)
- PII handling for owner names and addresses
- All leads enter as `pending_review` — never auto-approved
- Approval gates for financial and external actions

## Environment Variables

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=fsh_command
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<secure_password>
OPENROUTER_API_KEY=<key>
ANTHROPIC_API_KEY=<key>
```

## Next Steps

1. Fix HermesAdapter (`hermes run` → `hermes-agent --query`)
2. Wire real Investra export → lead_ingest.py
3. Build gridline-mao-analysis skill
4. Build gridline-deal-memo-builder skill
5. Wire DeepAgent + Claw approval loops
