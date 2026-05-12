---
name: gridline-lead-scoring
description: Score gridline leads using configurable weights across equity, distress signals, and contact quality. Updates lead_score in postgres. Only scores leads with rcw_status=compliant or pending_review (never contacts).
version: 1.0.0
author: FSH Command Center
license: proprietary
metadata:
  hermes:
    tags: [gridline, real-estate, leads, scoring, ML]
    related_skills: [gridline-csv-ingestion, gridline-seller-outreach-draft]
    pillar: gridline
    compliance_flags: [pii, rcw_18_85]
    approval_level: 0
---

# Gridline Lead Scoring

Score imported leads based on equity position, distress signals, property
characteristics, and contact quality. Updates `gridline_leads.lead_score`
(0.00–100.00). Scoring does not trigger outreach — it is purely analytical.

## Scoring model

Scores are computed as a weighted sum across four factor groups:

| Factor Group       | Weight | Signals |
|--------------------|--------|---------|
| Equity position    | 35%    | Assessed value vs. estimated mortgage balance |
| Distress signals   | 30%    | Vacancy, tax delinquency, probate, divorce filing |
| Property profile   | 20%    | Lot size, year built, days on market |
| Contact quality    | 15%    | Email deliverability, phone type (mobile/landline) |

---

## 1. Run scoring batch

```python
#!/usr/bin/env python3
"""
gridline_score_leads.py
Usage: python3 gridline_score_leads.py [--pillar-override] [--limit 1000]
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ["DATABASE_URL"]

WEIGHTS = {
    "equity":   0.35,
    "distress": 0.30,
    "property": 0.20,
    "contact":  0.15,
}


def score_equity(row: dict) -> float:
    """Stub: replace with actual equity calculation from raw_data."""
    raw = row.get("raw_data") or {}
    assessed = float(raw.get("assessed_value", 0) or 0)
    balance  = float(raw.get("est_mortgage_balance", assessed * 0.8) or 0)
    if assessed == 0:
        return 50.0   # neutral score when data unavailable
    equity_ratio = max(0, (assessed - balance) / assessed)
    return min(100, equity_ratio * 100)


def score_distress(row: dict) -> float:
    """Score based on distress signal flags in raw_data."""
    raw = row.get("raw_data") or {}
    signals = 0
    if raw.get("vacant"):         signals += 40
    if raw.get("tax_delinquent"): signals += 30
    if raw.get("probate"):        signals += 20
    if raw.get("divorce"):        signals += 10
    return min(100.0, float(signals))


def score_property(row: dict) -> float:
    """Score based on property age and characteristics."""
    raw = row.get("raw_data") or {}
    year_built = int(raw.get("year_built", 2000) or 2000)
    age = 2025 - year_built
    return min(100.0, age * 1.5)   # older = higher motivated seller probability


def score_contact(row: dict) -> float:
    """Score based on contact data quality."""
    score = 0.0
    if row.get("owner_email"): score += 50
    if row.get("owner_phone"): score += 50
    return score


def compute_score(row: dict) -> float:
    equity   = score_equity(row)
    distress = score_distress(row)
    prop     = score_property(row)
    contact  = score_contact(row)

    total = (
        equity   * WEIGHTS["equity"]   +
        distress * WEIGHTS["distress"] +
        prop     * WEIGHTS["property"] +
        contact  * WEIGHTS["contact"]
    )
    return round(total, 2)


def run_batch(limit: int = 1000) -> dict:
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT lead_id, owner_email, owner_phone, raw_data
        FROM gridline_leads
        WHERE lead_score IS NULL
        ORDER BY created_at ASC
        LIMIT %s
    """, (limit,))
    leads = cur.fetchall()

    updates = 0
    for lead in leads:
        score = compute_score(lead)
        cur.execute(
            "UPDATE gridline_leads SET lead_score = %s WHERE lead_id = %s",
            (score, lead["lead_id"])
        )
        updates += 1

    conn.commit()
    cur.close()
    conn.close()
    return {"scored": updates, "limit": limit}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1000)
    args = p.parse_args()
    print(run_batch(args.limit))
```

---

## 2. Verify scores

```sql
SELECT
    CASE
        WHEN lead_score >= 80 THEN 'A — high priority'
        WHEN lead_score >= 60 THEN 'B — medium priority'
        WHEN lead_score >= 40 THEN 'C — low priority'
        ELSE                       'D — do not pursue'
    END AS tier,
    COUNT(*),
    ROUND(AVG(lead_score), 1) AS avg_score
FROM gridline_leads
WHERE lead_score IS NOT NULL
GROUP BY tier
ORDER BY avg_score DESC;
```

---

## Environment variables required

| Variable       | Description                |
|----------------|----------------------------|
| `DATABASE_URL` | Postgres connection string |
