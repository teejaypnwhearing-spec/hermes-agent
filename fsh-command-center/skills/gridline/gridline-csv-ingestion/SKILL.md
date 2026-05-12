---
name: gridline-csv-ingestion
description: Ingest raw county assessor or list CSV files into the gridline_leads table. Validates columns, deduplicates on address, and sets initial rcw_status=pending_review. Supports WA county formats.
version: 1.0.0
author: FSH Command Center
license: proprietary
metadata:
  hermes:
    tags: [gridline, real-estate, leads, CSV, ingestion, RCW]
    related_skills: [gridline-lead-scoring, gridline-seller-outreach-draft]
    pillar: gridline
    compliance_flags: [pii, rcw_18_85]
    approval_level: 0
---

# Gridline CSV Ingestion

Ingest a raw county assessor or lead-list CSV file into the `gridline_leads`
Postgres table. All imported leads start with `rcw_status = 'pending_review'` —
they cannot be contacted until a compliance review sets `rcw_status = 'compliant'`.

## Prerequisites

- `DATABASE_URL` set in environment (see `.env.example`)
- Python 3.11+ with `psycopg2-binary`, `pandas`
- Source CSV with at minimum: `address`, `owner_name` columns

---

## 1. Validate CSV structure

```bash
# Check required columns are present
python3 - <<'EOF'
import pandas as pd, sys

REQUIRED = {"address", "owner_name"}
OPTIONAL = {"owner_email", "owner_phone", "county", "state", "raw_data"}

df = pd.read_csv("$INPUT_CSV")
cols = set(df.columns.str.lower().str.strip())
missing = REQUIRED - cols
if missing:
    print(f"ERROR: Missing required columns: {missing}", file=sys.stderr)
    sys.exit(1)

print(f"OK: {len(df)} rows, columns: {sorted(cols)}")
EOF
```

---

## 2. Deduplicate and ingest

```python
#!/usr/bin/env python3
"""
gridline_csv_ingest.py
Usage: python3 gridline_csv_ingest.py --file leads.csv [--dry-run]
"""
import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.environ["DATABASE_URL"]
PILLAR       = "gridline"

def load_and_clean(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.lower().str.strip()

    # Normalise required columns
    df["address"]    = df["address"].str.strip().str.upper()
    df["owner_name"] = df["owner_name"].str.strip().str.title()
    df["state"]      = df.get("state", "WA").fillna("WA").str.upper()
    df["county"]     = df.get("county", pd.Series([""] * len(df))).fillna("")

    # Drop rows missing address
    before = len(df)
    df = df.dropna(subset=["address"])
    dropped = before - len(df)
    if dropped:
        print(f"Dropped {dropped} rows with missing address")

    # Deduplicate within file
    df = df.drop_duplicates(subset=["address"])
    return df


def ingest(df: pd.DataFrame, dry_run: bool = False) -> dict:
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()

    rows_inserted  = 0
    rows_skipped   = 0

    for _, row in df.iterrows():
        # Check for existing lead (address dedup against DB)
        cur.execute(
            "SELECT lead_id FROM gridline_leads WHERE address = %s",
            (row["address"],)
        )
        existing = cur.fetchone()
        if existing:
            rows_skipped += 1
            continue

        record = {
            "lead_id":        str(uuid.uuid4()),
            "address":        row["address"],
            "owner_name":     row.get("owner_name", ""),
            "owner_email":    row.get("owner_email", None),
            "owner_phone":    row.get("owner_phone", None),
            "county":         row.get("county", ""),
            "state":          row.get("state", "WA"),
            "outreach_status":"new",
            "rcw_status":     "pending_review",   # always starts here
            "source_file":    df.attrs.get("source_file", ""),
            "raw_data":       row.to_json(),
        }

        if not dry_run:
            cur.execute("""
                INSERT INTO gridline_leads
                    (lead_id, address, owner_name, owner_email, owner_phone,
                     county, state, outreach_status, rcw_status, source_file, raw_data)
                VALUES
                    (%(lead_id)s, %(address)s, %(owner_name)s, %(owner_email)s,
                     %(owner_phone)s, %(county)s, %(state)s, %(outreach_status)s,
                     %(rcw_status)s, %(source_file)s, %(raw_data)s::jsonb)
            """, record)
        rows_inserted += 1

    if not dry_run:
        conn.commit()
    cur.close()
    conn.close()

    return {
        "total_rows":    len(df),
        "rows_inserted": rows_inserted,
        "rows_skipped":  rows_skipped,
        "dry_run":       dry_run,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file",    required=True, help="Path to input CSV")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    df = load_and_clean(args.file)
    df.attrs["source_file"] = args.file
    result = ingest(df, dry_run=args.dry_run)
    print(result)
```

---

## 3. Post-ingestion verification

```sql
-- Confirm all new leads have pending_review status
SELECT rcw_status, COUNT(*) 
FROM gridline_leads 
WHERE created_at >= NOW() - INTERVAL '1 hour'
GROUP BY rcw_status;
-- Expected: all rows show 'pending_review'

-- No new leads should have outreach_status != 'new'
SELECT COUNT(*) FROM gridline_leads 
WHERE outreach_status != 'new' 
  AND created_at >= NOW() - INTERVAL '1 hour';
-- Expected: 0
```

---

## Environment variables required

| Variable       | Description                        |
|----------------|------------------------------------|
| `DATABASE_URL` | Postgres connection string         |
| `INPUT_CSV`    | Path to source CSV file            |

---

## Compliance notes

> ⚠️ **RCW 18.85**: All leads are imported with `rcw_status = 'pending_review'`.
> The compliance team must review and set `rcw_status = 'compliant'` before
> any outreach attempt. The database enforces this via `trg_rcw_outreach_gate`.
