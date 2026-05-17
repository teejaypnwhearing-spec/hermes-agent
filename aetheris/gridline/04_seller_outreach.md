# Gridline Operational Playbook — Part 4: Seller Outreach (S3)

## Objective
Generate and send initial outreach communications to qualified leads. This is the first human-facing action in the pipeline and requires approval_level=1 (notify after execution).

## Outreach Criteria
- Lead must have `compliance_status` in ('pending_review', 'approved')
- Lead must have `outreach_status = 'not_started'`
- Lead tier must be A, B, or C (D-tier leads are not contacted)
- Lead must have passed the S1 quality thresholds

## Outreach Channels
| Channel    | Priority | Use Case                         |
|------------|----------|----------------------------------|
| Direct mail| Default  | First contact for all leads      |
| Email      | Secondary| If email address is available    |
| Phone      | Tertiary | If phone number and opt-in exist |
| SMS        | Last     | Only with explicit opt-in        |

## Compliance Requirements
- **RCW 18.85**: All communications must identify the sender as a licensed wholesaler
- **PII**: Owner name and address are used in communications (flag: `["pii", "external_action"]`)
- **Do Not Contact**: Check DNC registry before any outreach
- **Opt-out**: All communications must include opt-out instructions

## Outreach Sequence
1. **Day 0**: Lead approved for outreach → generate personalized letter
2. **Day 0**: Send first direct mail piece
3. **Day 7**: If no response, send follow-up postcard
4. **Day 14**: If no response, attempt phone contact (if number available)
5. **Day 30**: If no response, mark `outreach_status = 'closed'` and move to nurture queue

## Task Packet Example
```json
{
  "task_type": "gridline-seller-outreach",
  "pillar": "gridline",
  "objective": "Generate and send outreach letter for lead at 1234 N Oak St",
  "execution_engine": "hermes",
  "approval_level": 1,
  "compliance_flags": ["pii", "external_action", "rcw_18_85"],
  "input_artifacts": [{"source": "postgres", "path": "leads/{lead_id}"}]
}
```

## Skill Registration
```
skill_name: gridline-seller-outreach
pillar: gridline
approval_level: 1
compliance_flags: ["pii", "external_action"]
```
