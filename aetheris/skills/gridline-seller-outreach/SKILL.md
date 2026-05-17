# Skill: gridline-seller-outreach

## Purpose
Generate and send initial outreach communications for qualified leads. First human-facing action in the pipeline.

## Inputs
- Lead ID from PostgreSQL
- Outreach channel preference (direct_mail, email, phone, SMS)

## Outputs
- Outreach record updated on lead (outreach_status: 'contacted')
- Communication content stored in artifacts table

## Compliance
- **PII**: Owner name and address used in communications
- **External Action**: Sends physical/digital mail to property owner
- **RCW 18.85**: Must identify sender as licensed wholesaler

## Approval Level
1 (notify after execution)

## Execution Engine
hermes

## Compliance Flags
["pii", "external_action"]
