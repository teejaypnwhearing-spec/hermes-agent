# Skill: gridline-property-dedup

## Purpose
Deduplicate property records across data sources. Ensures no duplicate leads exist in the pipeline.

## Inputs
- Property addresses from multiple sources (Investra, public records, manual entry)

## Outputs
- Deduplicated lead list
- Duplicate reports for audit

## Deduplication Strategy
1. **Exact match**: property_address string comparison (case-insensitive, trimmed)
2. **Fuzzy match**: Levenshtein distance on normalized addresses (threshold: 2 edits)
3. **Geocoding match**: Same lat/lng within 50m radius

## Priority for Duplicates
When duplicates are found, keep the record with:
1. Higher score
2. More recent source data
3. More complete information (more non-null fields)

## Approval Level
0

## Execution Engine
hermes

## Compliance Flags
[]
