# Gridline Operational Playbook — Part 3: Lead Scoring (S2)

## Objective
Apply the v1 lead scoring model to qualified leads. Scoring determines tier assignment (A/B/C/D) and prioritization for downstream pipeline stages.

## Scoring Formula (v1 — Confirmed)
```
equity_component = equity_pct × 0.70
motivation_component = motivation_score × 3.50
distress_component = len(distress_flags) × 5.0
raw_score = equity_component + motivation_component + distress_component
market_adj = raw_score × 0.10  (10% Spokane County market adjustment)
score = raw_score - market_adj
score = min(max(score, 0.0), 100.0)
```

## Tier Assignment (PostgreSQL generated column)
| Tier | Score Range | Priority | Expected Action              |
|------|-------------|----------|------------------------------|
| A    | ≥ 75        | Highest  | Immediate outreach + MAO     |
| B    | ≥ 50        | High     | Outreach within 48h          |
| C    | ≥ 25        | Medium   | Outreach within 1 week       |
| D    | < 25        | Low      | Park for future nurture      |

## Confirmed Score Verification
| Lead            | Equity | Motivation | Flags | Score (Confirmed) | Tier |
|-----------------|--------|------------|-------|--------------------|------|
| Eleanor Vance   | 62%    | 8          | 2     | 73.57              | B    |
| Sarah Jenkins   | 45%    | 7          | 1     | 63.25              | C    |

**IMPORTANT**: The original test run produced scores of 50.07 / 38.40. Those were computed WITHOUT the market adjustment factor. The confirmed scores (73.57 / 63.25) INCLUDE the market adjustment. The stale scores are superseded.

## Score Recalculation
Scores are recalculated automatically when:
- A lead's `equity_pct`, `motivation_score`, or `distress_flags` are updated
- The `compute_lead_score_v1()` PostgreSQL function is called directly
- The lead_ingest.py Python scoring function is run

Both the PostgreSQL trigger (`trg_auto_score_lead`) and the Python function (`compute_lead_score()`) must produce identical results.

## Skill Registration
```
skill_name: gridline-lead-scoring
pillar: gridline
approval_level: 0
compliance_flags: []
```
