# Skill: gridline-lead-ingest

## Purpose
Ingest and validate leads from Investra CSV/JSON exports into the FSH Command Center pipeline.

## Inputs
- **Source file**: CSV or JSON export from Investra (or other data source)
- **Batch ID**: Optional identifier for the import batch

## Outputs
- New lead records in PostgreSQL `leads` table
- Audit trail entries in `audit_trail` table

## Pipeline Stages
1. Parse source data (CSV or JSON)
2. Auto-reject filter (equity < 40%, motivation < 3)
3. In-batch deduplication (by property_address)
4. DB-level deduplication (by property_address)
5. Score using compute_lead_score_v1
6. Bulk insert with compliance_status = 'pending_review'
7. Log to audit_trail

## Compliance
- **PII**: Owner names and addresses are personally identifiable information
- **RCW 18.85**: Wholesale real-estate activity compliance
- **No auto-approval**: All leads enter as 'pending_review'

## Approval Level
0 (no approval required — this is a data ingestion operation)

## Execution Engine
hermes

## Entry Point
```
python aetheris/skills/gridline-lead-ingest/lead_ingest.py [--source FILE] [--batch-id ID]
```

## Task Packet Example
```json
{
  "task_type": "gridline-lead-ingest",
  "pillar": "gridline",
  "objective": "Ingest leads from Investra export at /data/investra/export_2026-05-14.csv",
  "execution_engine": "hermes",
  "approval_level": 0,
  "compliance_flags": ["pii", "rcw_18_85"],
  "input_artifacts": [{"source": "filesystem", "path": "/data/investra/export_2026-05-14.csv"}]
}
```
