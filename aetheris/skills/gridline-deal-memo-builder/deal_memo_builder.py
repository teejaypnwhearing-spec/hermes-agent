#!/usr/bin/env python3
"""
AETHERIS Gridline Runtime — Deal Memo Builder Skill (S5)

Generates structured deal memos from MAO analysis and lead data.
Always requires approval_level=2.
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


def build_deal_memo(lead_id: str, mao_id: str):
    """Build a deal memo from lead + MAO data."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            # Fetch lead data
            cur.execute("SELECT property_address, owner_name, equity_pct, score, tier, distress_flags FROM leads WHERE lead_id = %s", (lead_id,))
            lead = cur.fetchone()
            if not lead:
                print(f"Lead not found: {lead_id}", file=sys.stderr)
                sys.exit(1)

            property_address, owner_name, equity_pct, score, tier, distress_flags = lead

            # Fetch MAO data
            cur.execute("SELECT arv, repair_estimate, mao_value, confidence FROM mao_analyses WHERE mao_id = %s", (mao_id,))
            mao = cur.fetchone()
            if not mao:
                print(f"MAO analysis not found: {mao_id}", file=sys.stderr)
                sys.exit(1)

            arv, repair_estimate, mao_value, confidence = mao

            # Calculate projected profit
            offer_amount = mao_value * 0.97  # 3% below MAO for negotiation room
            projected_profit = arv - offer_amount - repair_estimate
            profit_margin_pct = (projected_profit / arv * 100) if arv > 0 else 0

            # Risk assessment
            risk_assessment = {
                "market_risk": "medium",
                "repair_risk": "medium" if repair_estimate > 20000 else "low",
                "timeline_risk": "low",
                "overall_risk": "medium",
                "mitigation_notes": "Recommend formal inspection before contract execution"
            }

            # Build memo content
            memo_content = {
                "executive_summary": f"Opportunity to acquire {property_address} at ${offer_amount:,.0f} ({offer_amount/arv*100:.0f}% of ARV). Projected profit: ${projected_profit:,.0f}.",
                "property_overview": {
                    "address": property_address,
                    "owner": owner_name,
                    "equity_pct": float(equity_pct) if equity_pct else 0,
                    "tier": tier,
                    "score": float(score) if score else 0,
                },
                "financial_analysis": {
                    "arv": float(arv) if arv else 0,
                    "mao": float(mao_value) if mao_value else 0,
                    "proposed_offer": round(offer_amount, 2),
                    "repair_estimate": float(repair_estimate) if repair_estimate else 0,
                    "projected_profit": round(projected_profit, 2),
                    "roi_pct": round(profit_margin_pct, 1),
                },
                "recommendation": "PROCEED" if projected_profit > 0 else "PASS",
                "next_steps": [
                    "Submit offer at ${:,.0f}".format(offer_amount),
                    "Schedule property inspection",
                    "Prepare assignment contract if wholesale exit",
                    "Notify legal for RCW 18.85 compliance review"
                ]
            }

            # Insert deal memo
            cur.execute("""
                INSERT INTO deal_memos (lead_id, mao_id, property_address, offer_amount, arv,
                    repair_estimate, projected_profit, risk_assessment, compliance_flags,
                    approval_level, status, memo_content, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 2, 'pending_approval', %s, 'gridline-deal-memo-builder')
                RETURNING memo_id
            """, (
                lead_id, mao_id, property_address, round(offer_amount, 2),
                arv, repair_estimate, round(projected_profit, 2),
                json.dumps(risk_assessment),
                json.dumps(["financial", "irreversible", "external_action"]),
                json.dumps(memo_content),
            ))
            memo_id = cur.fetchone()[0]
            conn.commit()

        print(f"Deal Memo created: {memo_id}")
        print(f"  Property: {property_address}")
        print(f"  Offer: ${offer_amount:,.0f}")
        print(f"  Projected Profit: ${projected_profit:,.0f}")
        print(f"  Status: pending_approval (requires Level 2 approval)")
        return memo_id

    except Exception as e:
        conn.rollback()
        print(f"Deal memo failed: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FSH Gridline Deal Memo Builder (S5)")
    parser.add_argument("--lead-id", required=True, help="Lead UUID")
    parser.add_argument("--mao-id", required=True, help="MAO Analysis UUID")
    args = parser.parse_args()

    build_deal_memo(args.lead_id, args.mao_id)
