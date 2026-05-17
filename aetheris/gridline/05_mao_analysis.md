# Gridline Operational Playbook — Part 5: MAO Analysis (S4)

## Objective
Calculate the Maximum Allowable Offer (MAO) for a property. This is a financial decision that requires approval_level=1.

## MAO Formula
```
MAO = ARV × mao_multiplier - repair_estimate

Where:
  ARV = After Repair Value (fair market value after repairs)
  mao_multiplier = 0.70 (standard wholesale multiplier)
  repair_estimate = Estimated cost of necessary repairs
```

### Variations
| Strategy         | Multiplier | Use Case                     |
|------------------|------------|------------------------------|
| Conservative     | 0.65       | High-risk markets, unknown repairs |
| Standard         | 0.70       | Default for most properties  |
| Aggressive       | 0.75       | Low-risk, well-known market  |
| Assignment       | 0.80       | Assignment contracts (lower risk) |

## ARV Determination
ARV is calculated from comparable sales (comps):
1. Identify 3-5 comparable properties sold within 6 months, within 0.5 miles
2. Adjust for square footage, bedrooms, bathrooms, lot size
3. Weight recent sales more heavily
4. Document all comps and adjustments in `comp_sources` field

## Confidence Levels
| Confidence | Criteria                                        |
|------------|-------------------------------------------------|
| 0.8-1.0    | 5+ verified comps, known market, inspection done |
| 0.5-0.79   | 3-4 comps, market estimate, no inspection        |
| 0.0-0.49   | Fewer than 3 comps, estimated ARV, high uncertainty |

## Compliance
- Financial decision → `approval_level >= 1`
- All assumptions must be documented in `assumptions` field
- Comp sources must be traceable to public records or MLS data
- Flag: `["financial"]`

## Task Packet Example
```json
{
  "task_type": "gridline-mao-analysis",
  "pillar": "gridline",
  "objective": "Calculate MAO for 1234 N Oak St, Spokane, WA 99201",
  "execution_engine": "hermes",
  "approval_level": 1,
  "compliance_flags": ["financial"],
  "input_artifacts": [
    {"source": "postgres", "path": "leads/{lead_id}"},
    {"source": "comps", "path": "/data/comps/spokane_99201.json"}
  ]
}
```

## Skill Registration
```
skill_name: gridline-mao-analysis
pillar: gridline
approval_level: 1
compliance_flags: ["financial"]
```
