#!/usr/bin/env python3
"""
AETHERIS Gridline Runtime — Lead Scoring Skill (S2)

Applies the v1 scoring model to all active leads.
Can be called standalone or triggered by the daily score recalculation job.
"""

import json
import os
import sys
from dotenv import load_dotenv
import psycopg2

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "fsh_command"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}


def compute_lead_score(equity_pct, motivation_score, distress_flags):
    """v1 scoring model — mirrors compute_lead_score_v1() in PostgreSQL."""
    equity_component = equity_pct * 0.70
    motivation_component = motivation_score * 3.50
    distress_component = len(distress_flags) * 5.0
    raw_score = equity_component + motivation_component + distress_component
    market_adj = raw_score * 0.10
    score = raw_score - market_adj
    return round(min(max(score, 0.0), 100.0), 2)


def run_scoring():
    """Score all active leads that need recalculation."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            # The PostgreSQL trigger handles score updates automatically
            # This skill forces a recalculation by touching the relevant columns
            cur.execute("""
                UPDATE leads
                SET score = compute_lead_score_v1(equity_pct, motivation_score, distress_flags)
                WHERE compliance_status IN ('pending_review', 'approved')
                RETURNING lead_id, property_address, score, tier
            """)
            results = cur.fetchall()
            conn.commit()

        print(f"Scored {len(results)} leads")
        for lead_id, address, score, tier in results[:10]:
            print(f"  {address}: score={score}, tier={tier}")

        return len(results)
    except Exception as e:
        conn.rollback()
        print(f"Scoring failed: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_scoring()
