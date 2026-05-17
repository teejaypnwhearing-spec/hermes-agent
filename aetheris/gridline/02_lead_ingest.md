# Gridline Operational Playbook — Part 2: Lead Ingest (S1)

## Objective
Import raw lead data from Investra CSV export, validate against quality thresholds, deduplicate at batch and DB levels, score using compute_lead_score_v1, and insert qualified leads into PostgreSQL with full audit trail.

## Quality Thresholds
| Parameter             | Value | Notes                              |
|-----------------------|-------|------------------------------------|
| Equity minimum        | 40%   | Below this = auto-reject           |
| Motivation minimum    | 3     | Below this = auto-reject           |
| Minimum distress flags| 0     | No minimum (distress is additive)  |

## Pipeline Steps
1. Parse source data (CSV or JSON)
2. **Auto-reject filter**: Remove leads below quality thresholds
3. **In-batch deduplication**: Remove duplicates within the same batch (by property_address)
4. **DB-level deduplication**: Check existing leads in PostgreSQL (by property_address)
5. **Scoring**: Apply compute_lead_score_v1 to each remaining lead
6. **Insert**: Bulk insert into leads table with `compliance_status = 'pending_review'`
7. **Audit**: Log rejected leads and ingestion summary to audit_trail

## Compliance Rules
- ALL leads enter as `pending_review` — NEVER auto-approve
- Owner names and addresses are PII — handle with `compliance_flags: ["pii"]`
- RCW 18.85: Wholesale real-estate activity requires licensing verification

## Scoring Formula (v1 — Confirmed)
```
equity_component = equity_pct × 0.70
motivation_component = motivation_score × 3.50
distress_component = len(distress_flags) × 5.0
raw_score = equity_component + motivation_component + distress_component
market_adj = raw_score × 0.10
score = raw_score - market_adj
```

**Confirmed scores**: Eleanor Vance (equity=62, motivation=8, 2 flags) → 73.57
**Confirmed scores**: Sarah Jenkins (equity=45, motivation=7, 1 flag) → 63.25
**STALE scores (DO NOT USE)**: 50.07 / 38.40

## Error Handling
- Duplicate address: Skip (log to audit_trail)
- Invalid data format: Skip record, log to audit_trail
- DB connection failure: Abort batch, raise error
- Score computation error: Default score to 0.0, flag for review

## Skill Registration
```
skill_name: gridline-lead-ingest
pillar: gridline
approval_level: 0
compliance_flags: ["pii"]
```
