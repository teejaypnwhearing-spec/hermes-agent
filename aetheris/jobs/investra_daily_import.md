# Scheduled Job: Investra Daily Import

## Schedule
- **Cron**: `0 6 * * *` (06:00 UTC daily)
- **Skill**: gridline-lead-ingest
- **Execution Engine**: hermes

## Job Definition
```json
{
  "job_id": "investra-daily-import",
  "schedule": "0 6 * * *",
  "task_type": "gridline-lead-ingest",
  "pillar": "gridline",
  "execution_engine": "hermes",
  "objective": "Import daily Investra batch export for Spokane County",
  "approval_level": 0,
  "compliance_flags": ["pii"],
  "input_artifacts": [
    {
      "source": "filesystem",
      "path": "/data/investra/export_{date}.csv"
    }
  ]
}
```

## Prerequisites
- Investra export file must exist at `/data/investra/export_{date}.csv`
- PostgreSQL must be running and accessible
- `hermes-agent` binary must be in PATH

## Failure Handling
- If export file missing: Log warning, skip (not an error — may be no new data)
- If PostgreSQL unavailable: Retry 3 times with 5-minute backoff, then alert operator
- If hermes-agent fails: Enter retry_queue
