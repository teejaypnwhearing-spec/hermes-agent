---
name: gridline-seller-outreach-draft
description: Draft personalised seller outreach messages for RCW-compliant gridline leads. Generates email/SMS drafts via Claude. Does NOT send — requires human review and approval_level=1 before any contact attempt.
version: 1.0.0
author: FSH Command Center
license: proprietary
metadata:
  hermes:
    tags: [gridline, real-estate, outreach, RCW, compliance, Claude]
    related_skills: [gridline-lead-scoring, gridline-csv-ingestion]
    pillar: gridline
    compliance_flags: [pii, rcw_18_85]
    approval_level: 1
---

# Gridline Seller Outreach Draft

Generate personalised, RCW 18.85-compliant outreach drafts for motivated sellers.
This skill **drafts only** — it does not send messages. All drafts require human
review and `approval_level = 1` before the `contacted` status is set.

## RCW 18.85 compliance gate

```
rcw_status MUST be 'compliant' before ANY draft is generated.
The skill hard-fails if rcw_status != 'compliant'.
```

---

## 1. Pre-flight compliance check

```python
#!/usr/bin/env python3
"""
Check leads are RCW-compliant before generating drafts.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ["DATABASE_URL"]

def get_compliant_leads(min_score: float = 60.0, limit: int = 50) -> list:
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT lead_id, address, owner_name, owner_email, county, lead_score, rcw_status
        FROM gridline_leads
        WHERE rcw_status = 'compliant'           -- hard gate
          AND outreach_status = 'new'
          AND lead_score >= %s
        ORDER BY lead_score DESC
        LIMIT %s
    """, (min_score, limit))
    leads = cur.fetchall()

    cur.close()
    conn.close()
    return leads
```

---

## 2. Generate drafts via Claude

```python
#!/usr/bin/env python3
"""
gridline_draft_outreach.py
Generates email + SMS drafts for compliant leads.
"""
import json
import os
from anthropic import Anthropic

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

OUTREACH_SYSTEM_PROMPT = """You are drafting seller outreach for Fermier Sovereign Holdings
(Gridline pillar). All outreach must comply with RCW 18.85 (WA real estate law).

Requirements:
- Identify FSH as the buyer/investor (no deceptive business names)
- State the purpose clearly: we are interested in purchasing the property
- Do NOT make specific price offers in initial outreach
- Include opt-out language for SMS: "Reply STOP to opt out"
- Keep email under 150 words; SMS under 160 characters
- Personalise using owner_name and address

Return a JSON object with keys: email_subject, email_body, sms_draft, compliance_notes
"""

def generate_outreach_draft(lead: dict) -> dict:
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    user_msg = f"""
Owner: {lead['owner_name']}
Property: {lead['address']}, {lead.get('county', '')} County, WA
Lead score: {lead.get('lead_score', 'N/A')}

Generate RCW 18.85-compliant email and SMS outreach drafts.
"""

    response = client.messages.create(
        model      = os.environ.get("CLAUDE_MODEL", "claude-opus-4-5"),
        max_tokens = 1024,
        system     = OUTREACH_SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": user_msg}],
    )

    content = response.content[0].text
    try:
        draft = json.loads(content)
    except json.JSONDecodeError:
        draft = {"raw_response": content}

    return {
        "lead_id":       lead["lead_id"],
        "owner_name":    lead["owner_name"],
        "address":       lead["address"],
        "draft":         draft,
        "status":        "pending_review",   # human must approve before send
    }


def run(min_score: float = 60.0, limit: int = 10):
    from get_compliant_leads import get_compliant_leads   # from step 1
    leads = get_compliant_leads(min_score=min_score, limit=limit)
    print(f"Generating drafts for {len(leads)} compliant leads...")

    drafts = []
    for lead in leads:
        draft = generate_outreach_draft(lead)
        drafts.append(draft)
        print(f"  ✓ Draft for {lead['address']}")

    # Save drafts to file for human review — do NOT write to DB until approved
    output_path = f"outreach_drafts_{os.getenv('FSH_TASK_ID', 'batch')}.json"
    with open(output_path, "w") as f:
        json.dump(drafts, f, indent=2)

    print(f"\nDrafts saved to: {output_path}")
    print(f"⚠️  These drafts require approval_level=1 human review before any contact.")
    return output_path


if __name__ == "__main__":
    run()
```

---

## 3. Human review → mark approved

```bash
# After human reviews and approves drafts, update via the approval flow:
# 1. Reviewer submits approval via FSH dashboard or n8n form
# 2. approval_requests row is updated → pg_notify fires
# 3. n8n callback re-queues task with approval_token
# 4. AbacusAdapter.execute() receives approval_token and proceeds to send

# NEVER set outreach_status='contacted' directly — always go through approval flow
```

---

## 4. Verify no messages sent without approval

```sql
-- Should always be 0
SELECT COUNT(*) AS violation_count
FROM gridline_leads
WHERE rcw_status != 'compliant'
  AND outreach_status = 'contacted';

-- Audit: all contacted leads should have an approved approval_request
SELECT g.lead_id, g.address, g.rcw_status, g.outreach_status,
       ar.status AS approval_status
FROM gridline_leads g
LEFT JOIN tasks t ON t.task_type = 'seller_outreach_draft'
LEFT JOIN approval_requests ar ON ar.task_id = t.task_id AND ar.status = 'approved'
WHERE g.outreach_status = 'contacted'
  AND ar.approval_id IS NULL;
-- Expected: 0 rows
```

---

## Environment variables required

| Variable             | Description                        |
|----------------------|------------------------------------|
| `DATABASE_URL`       | Postgres connection string         |
| `ANTHROPIC_API_KEY`  | Anthropic Claude API key           |
| `CLAUDE_MODEL`       | Claude model (default: claude-opus-4-5) |

---

## ⚠️ Compliance reminder

> This skill drafts messages only. The FSH database enforces RCW 18.85 compliance
> via `trg_rcw_outreach_gate` — any attempt to set `outreach_status = 'contacted'`
> on a non-compliant lead will raise a Postgres exception. Always obtain
> `approval_level = 1` sign-off via the FSH approval workflow before any contact.
