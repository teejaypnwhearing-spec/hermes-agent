# Gridline Operational Playbook — Part 6: Deal Memo Builder (S5)

## Objective
Generate a structured deal memo from MAO analysis and lead data. This is the highest-approval task in the pipeline (approval_level=2) because it represents an irreversible financial decision.

## Deal Memo Structure
A deal memo combines:
1. **Lead data**: Property address, owner info, equity, distress signals
2. **MAO analysis**: ARV, repair estimate, MAO value, confidence level, comps
3. **Risk assessment**: Market risk, repair risk, timeline risk, overall risk
4. **Recommendation**: Buy/Pass/Conditional with rationale
5. **Next steps**: Action items if approved

## Approval Requirements
- **ALWAYS approval_level=2** (require approval before execution)
- Approver must review full deal memo content
- Approval token is single-use, expires in 24 hours
- Rejection requires rationale

## Risk Assessment Matrix
| Factor       | Low                     | Medium                    | High                       |
|-------------|-------------------------|---------------------------|----------------------------|
| Market risk  | Appreciating market     | Stable market             | Declining market           |
| Repair risk  | Cosmetic only (<$10k)   | Moderate ($10k-$30k)      | Major ($30k+)              |
| Timeline risk| No urgency, flexible    | Some deadline pressure    | Hard deadline (auction etc)|

## Compliance Flags
- `financial`: Involves money and offers
- `irreversible`: Contract execution cannot be undone
- `external_action`: Communicates offer to seller
- `rcw_18_85`: WA state wholesale licensing compliance

## Task Packet Example
```json
{
  "task_type": "gridline-deal-memo-builder",
  "pillar": "gridline",
  "objective": "Generate deal memo for lead {lead_id} with MAO {mao_id}",
  "execution_engine": "hermes",
  "approval_level": 2,
  "compliance_flags": ["financial", "irreversible", "external_action", "rcw_18_85"],
  "input_artifacts": [
    {"source": "postgres", "path": "leads/{lead_id}"},
    {"source": "postgres", "path": "mao_analyses/{mao_id}"}
  ]
}
```

## Skill Registration
```
skill_name: gridline-deal-memo-builder
pillar: gridline
approval_level: 2
compliance_flags: ["financial", "irreversible", "external_action"]
```
