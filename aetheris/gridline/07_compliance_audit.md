# Gridline Operational Playbook — Part 7: Compliance & Audit (S6)

## Objective
Ensure all Gridline pipeline operations comply with RCW 18.85 (Washington State wholesale real-estate licensing) and internal Fermier Sovereign Holdings policies.

## RCW 18.85 Requirements
1. **Licensing**: All wholesale real-estate activity must be conducted by or under the supervision of a licensed broker
2. **Disclosure**: All communications with property owners must disclose the wholesaler's role and intent
3. **Assignment**: Assignment contracts must clearly state the assignor's intent to assign the contract
4. **Marketing**: Property marketing must not misrepresent the wholesaler's ownership interest
5. **Records**: All transactions must maintain complete records for 5 years

## Internal Compliance Rules
- **PII Protection**: Owner names, addresses, phone numbers, and financial data are PII
- **Audit Trail**: All pipeline actions logged to `audit_trail` (immutable, append-only)
- **Approval Chain**: Financial and external actions require human approval
- **Data Retention**: Lead data retained for 5 years per RCW 18.85
- **Opt-out**: Owner opt-out requests honored within 24 hours

## Compliance Check Workflow
```
Task submitted with compliance_flags
    ↓
Orchestrator validates flags against schema
    ↓
If "rcw_18_85" in flags → add legal review step
If "pii" in flags → log PII access to audit_trail
If "financial" in flags → ensure approval_level >= 1
If "irreversible" in flags → ensure approval_level >= 2
If "external_action" in flags → ensure approval_level >= 1
    ↓
Execute task with compliance guardrails
    ↓
Log compliance outcome to audit_trail
```

## Audit Trail Schema
Every audit event includes:
- `action`: What happened (e.g., s1_ingest_complete, approval_granted)
- `actor`: Who or what performed the action (e.g., hermes_lead_ingest, operator_tj)
- `target_type`: What entity was affected (e.g., lead_batch, approval_gate)
- `target_id`: UUID of the affected entity
- `details`: JSONB with contextual information
- `created_at`: Immutable timestamp

## Quarterly Compliance Review
1. Pull all audit_trail entries for the quarter
2. Verify all Level 2 approvals have corresponding hm_decision_logs entries
3. Check for PII access patterns outside normal pipeline
4. Verify no leads were auto-approved (all must start as pending_review)
5. Generate compliance report for legal review
