# Adapter: PostgreSQL → Notion

## Overview
Defines the one-way sync from the canonical PostgreSQL state store to Notion, which serves as a human-readable secondary view of the Gridline pipeline.

## Sync Direction
**PostgreSQL → Notion ONLY.** Never reverse. PostgreSQL is the source of truth.

## Synced Tables → Notion Databases
| PostgreSQL Table | Notion Database         | Sync Frequency |
|------------------|-------------------------|----------------|
| leads            | Lead Tracker            | Near-real-time |
| mao_analyses     | MAO Pipeline            | Near-real-time |
| deal_memos       | Deal Pipeline           | Near-real-time |
| approval_gates   | Approval Queue          | On change      |
| audit_trail      | Compliance Log          | Hourly batch   |

## Sync Mechanism
1. PostgreSQL `pg_notify` triggers on INSERT/UPDATE for key tables
2. n8n workflow listens for notifications
3. n8n formats data into Notion API payload
4. Notion page created/updated via API
5. Notion page ID stored back in PostgreSQL `artifacts` table

## Data Mapping: leads → Notion Lead Tracker
| PostgreSQL Column   | Notion Property      | Type          |
|---------------------|----------------------|---------------|
| lead_id             | Lead ID              | Title         |
| property_address    | Property Address     | Text          |
| owner_name          | Owner                | Text          |
| equity_pct          | Equity %             | Number        |
| score               | Score                | Number        |
| tier                | Tier                 | Select        |
| compliance_status   | Compliance Status    | Status        |
| outreach_status     | Outreach Status      | Status        |
| distress_flags      | Distress Signals     | Multi-select  |
| batch_id            | Source Batch         | Text          |
| created_at          | Date Added           | Date          |

## Error Handling
- Notion API rate limit (3 req/s): Implement backoff, queue failed syncs
- Notion API timeout: Retry 3 times, then log to dead_letter_queue
- Schema mismatch: Log error, skip record, alert operator
- Duplicate page: Update existing page instead of creating new one
