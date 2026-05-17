# Scheduled Job: Distress Detection

## Schedule
- **Cron**: `0 8 * * *` (08:00 UTC daily, 2 hours after import)
- **Skill**: gridline-distress-detection
- **Execution Engine**: hermes

## Job Definition
```json
{
  "job_id": "daily-distress-detection",
  "schedule": "0 8 * * *",
  "task_type": "gridline-distress-detection",
  "pillar": "gridline",
  "execution_engine": "hermes",
  "objective": "Scan public records for new distress signals on active leads",
  "approval_level": 0,
  "compliance_flags": ["pii"],
  "input_artifacts": [
    {
      "source": "postgres",
      "path": "leads?compliance_status=approved&outreach_status=not_started"
    }
  ]
}
```

## Behavior
- Queries public records APIs for tax delinquency, foreclosure filings, code violations
- Updates `distress_flags` on existing leads
- Adds new distress signals discovered
- Logs all changes to audit_trail
- Triggers score recalculation via PostgreSQL trigger

## Distress Signal Sources
| Source          | Signal Types                                    | API           |
|-----------------|-------------------------------------------------|---------------|
| Spokane County  | Tax delinquency, code violations               | County API    |
| WA State        | Foreclosure filings, lis pendens               | State API     |
| USPS            | Vacancy indicators                              | CASS API      |
| Utility records | Vacancy indicators (no water/electric usage)    | Utility API   |
