# Skill: gridline-csv-ingestion

## Purpose
Parse and validate CSV data files for the Gridline pipeline. This is a data preparation skill that transforms raw CSV exports into the normalized format expected by gridline-lead-ingest.

## Inputs
- CSV file path (Investra export or similar)

## Outputs
- Validated JSON records ready for lead_ingest.py

## Validation Rules
1. Required columns: property_address, owner_name, equity_pct
2. equity_pct must be numeric 0-100
3. motivation_score must be integer 0-10 (default: 0)
4. distress_flags must be comma-separated (default: empty)
5. Duplicate rows within the same file are flagged but not removed (handled by lead_ingest)

## Approval Level
0

## Execution Engine
hermes

## Compliance Flags
["pii"]
