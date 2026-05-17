# Skill: gridline-lead-scoring

## Purpose
Apply the v1 lead scoring model to qualified leads. Scoring determines tier assignment (A/B/C/D) and prioritization for downstream pipeline stages.

## Inputs
- Lead records in PostgreSQL (equity_pct, motivation_score, distress_flags)

## Outputs
- Updated `score` and `tier` fields on leads

## Scoring Formula (v1 — Confirmed)
```
equity_component = equity_pct × 0.70
motivation_component = motivation_score × 3.50
distress_component = len(distress_flags) × 5.0
raw_score = equity_component + motivation_component + distress_component
market_adj = raw_score × 0.10
score = raw_score - market_adj
```

## Tier Assignment
| Tier | Score Range |
|------|-------------|
| A    | ≥ 75        |
| B    | ≥ 50        |
| C    | ≥ 25        |
| D    | < 25        |

## Approval Level
0

## Execution Engine
hermes

## Compliance Flags
[]
