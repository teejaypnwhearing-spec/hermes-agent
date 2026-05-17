# Skill: gridline-distress-detection

## Purpose
Scan public records for distress signals on active leads. Updates distress_flags on leads in PostgreSQL.

## Inputs
- Lead records in PostgreSQL (active leads with outreach_status = 'not_started')

## Outputs
- Updated distress_flags on leads
- New distress signals added to existing leads

## Distress Signal Types
| Signal            | Source                    | Weight |
|-------------------|---------------------------|--------|
| tax_delinquent    | County tax records        | High   |
| absentee_owner    | USPS forwarding records   | Medium |
| pre_foreclosure   | WA state court filings    | High   |
| code_violation    | County code enforcement   | Medium |
| vacant            | Utility records / USPS    | High   |
| recent_listing    | MLS / public records      | Low    |
| inheritance       | Probate court records     | High   |
| divorce           | Court records             | Medium |
| relocation        | Employer records          | Low    |
| bankruptcy        | Federal court records     | High   |

## Approval Level
0

## Execution Engine
hermes

## Compliance Flags
["pii"]
