# Gridline Operational Playbook — Part 1: Overview

## Gridline Pipeline
The Gridline pillar manages the entire wholesale real-estate lead lifecycle from data ingestion through deal closure.

## Pipeline Stages
1. **S1 — Lead Ingest**: Import raw lead data from Investra CSV, validate quality thresholds, deduplicate, score, insert into PostgreSQL
2. **S2 — Lead Scoring**: Apply v1 scoring model (equity × 0.70 + motivation × 3.50 + distress × 5.0 - market adjustment)
3. **S3 — Seller Outreach**: Generate and send initial outreach communications for qualified leads (approval_level=1)
4. **S4 — MAO Analysis**: Calculate Maximum Allowable Offer using ARV × 0.70 - repair estimate (approval_level=1)
5. **S5 — Deal Memo**: Generate structured deal memo from MAO + lead data (approval_level=2)
6. **S6 — Compliance Review**: RCW 18.85 compliance check before any contract execution
7. **S7 — Contract Execution**: Send approved offers and manage contract workflow (approval_level=2)
8. **S8 — Post-Close**: Record outcome, update analytics, sync to Notion dashboard

## Key Metrics
- **Pipeline velocity**: Days from ingest to close
- **Conversion rate**: % of ingested leads that reach deal memo
- **Score accuracy**: Correlation between lead score and actual deal outcome
- **Compliance rate**: % of tasks with proper approval chain
