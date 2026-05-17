# Skill: gridline-deal-memo-builder

## Purpose
Generate structured deal memos from MAO analysis + lead data. This is the highest-approval task in the pipeline.

## Inputs
- Lead ID from PostgreSQL
- MAO Analysis ID from PostgreSQL

## Outputs
- Deal memo record in deal_memos table
- Structured JSON + human-readable summary

## Compliance
- **Financial**: Involves money and offers
- **Irreversible**: Contract execution cannot be undone
- **External Action**: Communicates offer to seller

## Approval Level
2 (require approval before execution — ALWAYS)

## Execution Engine
hermes

## Compliance Flags
["financial", "irreversible", "external_action"]
