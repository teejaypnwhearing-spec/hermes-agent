# Scheduled Job: Weekly Compliance Review

## Schedule
- **Cron**: `0 10 * * 1` (10:00 UTC every Monday)
- **Skill**: (internal compliance check)
- **Execution Engine**: hermes

## Job Definition
```json
{
  "job_id": "weekly-compliance-review",
  "schedule": "0 10 * * 1",
  "task_type": "gridline-compliance-review",
  "pillar": "gridline",
  "execution_engine": "hermes",
  "objective": "Run weekly RCW 18.85 compliance audit on all pipeline activity",
  "approval_level": 0,
  "compliance_flags": [],
  "input_artifacts": []
}
```

## Review Checklist
1. Verify all Level 2 approvals have corresponding hm_decision_logs entries
2. Check for PII access patterns outside normal pipeline
3. Verify no leads were auto-approved (all must start as pending_review)
4. Confirm all outreach communications include required disclosures
5. Verify DNC list compliance
6. Check data retention compliance (5-year retention per RCW 18.85)
7. Review any override decisions for proper documentation

## Output
- Compliance report stored in `/data/reports/compliance_weekly_{date}.json`
- Summary sent to operator via Telegram
- Any violations flagged for immediate review
- Report archived in Notion Compliance Log database
