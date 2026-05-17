# Scheduled Job: Score Recalculation

## Schedule
- **Cron**: `0 7 * * *` (07:00 UTC daily, 1 hour after import)
- **Skill**: gridline-lead-scoring
- **Execution Engine**: hermes

## Job Definition
```json
{
  "job_id": "daily-score-recalculation",
  "schedule": "0 7 * * *",
  "task_type": "gridline-lead-scoring",
  "pillar": "gridline",
  "execution_engine": "hermes",
  "objective": "Recalculate lead scores using v1 scoring model for all active leads",
  "approval_level": 0,
  "compliance_flags": [],
  "input_artifacts": []
}
```

## Behavior
- Re-runs `compute_lead_score_v1()` for all leads with `compliance_status IN ('pending_review', 'approved')`
- Uses PostgreSQL trigger (auto_score_lead) for per-record recalculation
- Logs score changes to audit_trail
- Updates tier assignment via PostgreSQL generated column

## Notes
- This is a read-only operation on existing data (no new leads created)
- Score changes may trigger tier reassignment (e.g., B→A or C→B)
- If scoring formula is updated, run this job manually after migration
