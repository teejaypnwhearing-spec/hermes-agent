# Scheduled Job: Daily Report

## Schedule
- **Cron**: `0 18 * * *` (18:00 UTC daily)
- **Skill**: gridline-daily-report (internal)
- **Execution Engine**: hermes

## Job Definition
```json
{
  "job_id": "daily-report",
  "schedule": "0 18 * * *",
  "task_type": "gridline-daily-report",
  "pillar": "gridline",
  "execution_engine": "hermes",
  "objective": "Generate daily pipeline summary report for operator review",
  "approval_level": 0,
  "compliance_flags": [],
  "input_artifacts": []
}
```

## Report Contents
1. **Pipeline Summary**: Leads ingested, scored, contacted, deal-memoed today
2. **Tier Distribution**: Current A/B/C/D lead counts
3. **Approval Queue**: Pending approvals with time waiting
4. **Error Summary**: Failed tasks, dead-lettered tasks
5. **Top Opportunities**: Top 5 leads by score with outreach status
6. **Compliance Status**: Any pending compliance reviews

## Delivery
- Telegram message to operator channel
- Notion page update in Daily Reports database
- Email summary to configured recipients

## SQL Queries Used
```sql
-- Pipeline summary
SELECT * FROM v_active_tasks WHERE created_at >= CURRENT_DATE;

-- Tier distribution
SELECT * FROM v_leads_by_tier;

-- Pending approvals
SELECT * FROM v_pending_approvals;

-- Error summary
SELECT * FROM v_dead_letter_summary WHERE dead_lettered_at >= CURRENT_DATE;
```
