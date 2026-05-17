#!/usr/bin/env python3
"""
AETHERIS Gridline Runtime — MAO Analysis Skill (S4)

Calculates Maximum Allowable Offer for a property.
MAO = ARV × mao_multiplier - repair_estimate
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
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

DEFAULT_MAO_MULTIPLIER = 0.70


def calculate_mao(arv: float, repair_estimate: float = 0, multiplier: float = DEFAULT_MAO_MULTIPLIER) -> dict:
    """Calculate MAO and return structured result."""
    mao_value = arv * multiplier - repair_estimate

    # Determine confidence based on input quality
    confidence = 0.5  # Default medium confidence
    if arv > 0 and repair_estimate >= 0:
        confidence = 0.7
    if arv > 0 and repair_estimate > 0:
        confidence = 0.8

    return {
        "mao_value": round(mao_value, 2),
        "arv": arv,
        "repair_estimate": repair_estimate,
        "mao_multiplier": multiplier,
        "confidence": confidence,
    }


def run_mao_analysis(lead_id: str, arv: float, repair_estimate: float = 0, multiplier: float = DEFAULT_MAO_MULTIPLIER):
    """Run MAO analysis for a lead and persist to database."""
    result = calculate_mao(arv, repair_estimate, multiplier)

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mao_analyses (lead_id, arv, repair_estimate, mao_value, mao_multiplier, confidence, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'draft')
                RETURNING mao_id
            """, (lead_id, result["arv"], result["repair_estimate"],
                  result["mao_value"], result["mao_multiplier"], result["confidence"]))
            mao_id = cur.fetchone()[0]
            conn.commit()

        print(f"MAO Analysis created: {mao_id}")
        print(f"  ARV: ${arv:,.0f}")
        print(f"  Repairs: ${repair_estimate:,.0f}")
        print(f"  MAO: ${result['mao_value']:,.0f}")
        print(f"  Confidence: {result['confidence']:.0%}")
        return mao_id

    except Exception as e:
        conn.rollback()
        print(f"MAO analysis failed: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FSH Gridline MAO Analysis (S4)")
    parser.add_argument("--lead-id", required=True, help="Lead UUID")
    parser.add_argument("--arv", required=True, type=float, help="After Repair Value")
    parser.add_argument("--repair-estimate", type=float, default=0, help="Estimated repair cost")
    parser.add_argument("--multiplier", type=float, default=0.70, help="MAO multiplier")
    args = parser.parse_args()

    run_mao_analysis(args.lead_id, args.arv, args.repair_estimate, args.multiplier)
