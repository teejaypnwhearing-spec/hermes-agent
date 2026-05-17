# Skill: gridline-mao-analysis

## Purpose
Calculate Maximum Allowable Offer (MAO) for a property. MAO = ARV × 0.70 - repair_estimate.

## Inputs
- Lead ID from PostgreSQL
- After Repair Value (ARV) estimate
- Repair cost estimate
- MAO multiplier (default: 0.70)

## Outputs
- MAO analysis record in mao_analyses table
- MAO value, confidence level, assumptions, comp sources

## Compliance
- **Financial**: Involves money/offer calculation

## Approval Level
1 (notify after execution)

## Execution Engine
hermes

## Compliance Flags
["financial"]
