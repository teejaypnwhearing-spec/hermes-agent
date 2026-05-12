# FSH COMMAND CENTER — ARCHITECTURE REVIEW & VALIDATION
*Prepared by SuperNinja | Full-Spectrum Technical Assessment*

---

## ARCHITECTURE ASSESSMENT

### Overall Design Verdict: **Structurally Sound, Operationally Incomplete**

The federated adapter pattern is the correct architectural choice here, and the spec deserves credit for making it explicit. The core insight — "federated, not fused" — is the most important principle in the entire document, and it's stated clearly enough that a competent engineer wouldn't miss it. The hub-and-spoke topology with Claude as the reasoning layer is defensible for MVP, though it carries a real bottleneck risk that the document acknowledges but underspecifies the mitigation path for.

### Federated Adapter Pattern: Assessment

**What's sound:**
The adapter pattern (translate_in → execute → translate_out) is textbook clean. Each adapter maintains its own session and permission boundaries, which prevents the most common failure mode in multi-agent systems: context bleed and permission escalation. The fact that all inter-agent communication flows through structured artifacts (JSON task packets, shared storage, log entries, status callbacks) rather than ad-hoc model-to-model chat is correct and non-negotiable. Most amateur multi-agent specs skip this entirely and pay for it in production.

The PLAN → EXECUTE → REVIEW → LOG → HANDOFF protocol is sensible. It creates natural audit points and forces the system to externalize state at each phase transition, which is exactly what you need for replay capability and debugging.

**What's architecturally weak:**
The spec describes adapters but doesn't fully address adapter failure isolation. If the Hermes adapter throws an unhandled exception mid-execution, the spec's recovery path is underspecified. The retry_queue table exists, but the logic for *when* to retry vs. *when* to escalate vs. *when* to dead-letter a task is absent. In production, this becomes a support nightmare.

The spec also conflates "FSH Command Center" with "Abacus DeepAgent" in several places. These should be strictly separated: FSH Command is the *logical* control plane (your business rules, routing logic, compliance gates); DeepAgent is one *physical* runtime that hosts part of it. If you later migrate away from Abacus, you want the logical layer to be portable.

### Circular Dependencies & Bottlenecks

**Identified circular dependency risk:**
The Abacus Adapter's `execute()` method contains a polling loop:
```python
while True:
    status = postgres.query_one(...)
    if status in ["approved", "rejected"]:
        break
    time.sleep(60)
```
This is a blocking poll inside what should be an async callback architecture. If this runs in a synchronous thread, it will exhaust your connection pool during any period of high approval volume or delayed human responses. The spec says approval timeout is 48 hours — meaning this thread could hold a connection for 48 hours. This is a production-breaking design choice that needs to be replaced with an event-driven pattern (webhook or n8n callback) immediately.

**Hub bottleneck:**
The spec correctly identifies Claude as a potential hub bottleneck and defers LangGraph to post-MVP. That's a reasonable tradeoff IF you instrument latency from day one. The monitoring section includes a p95 latency query, which is good. What's missing is the *trigger threshold* that moves you from "Claude hub is fine" to "we need LangGraph now." The spec should define this explicitly (e.g., "if p95 > 3s sustained over 1 hour, initiate LangGraph migration").

**Bottleneck in Gridline specifically:**
The daily report job (every weekday 7AM Eastern) combined with the follow-up queue job (9AM Eastern) will both contend for Hermes execution resources and Postgres write capacity simultaneously. There's no task queue or priority system described. If a 384-property batch import is running and a follow-up job triggers, you have undefined resource contention.

### "Claude as Reasoning Hub" Design Choice

This is defensible but carries hidden costs. Claude's strengths here are:
1. Instruction-following quality for compliance checks
2. Structured output reliability (critical for the JSON task schema)
3. Extended thinking for nuanced RCW 18.85 audits

The weaknesses:
1. **Latency**: Every task in the system, regardless of complexity, pays the Claude API round-trip cost. A simple "move this artifact to Notion" shouldn't require a reasoning model.
2. **Cost at scale**: If the system processes 500 Gridline leads per day, and each requires Claude for REVIEW, you're looking at meaningful API costs that the spec doesn't budget or model.
3. **No fallback reasoning path**: If Claude API is unavailable, the entire control plane halts. The spec should define a degraded mode (e.g., "if Claude unavailable for >5min, queue tasks as PENDING_CLAUDE, execute non-reasoning tasks via direct adapter routing").

**Recommendation**: Introduce a two-tier routing rule inside the Claude adapter. Simple tasks (data movement, status updates, artifact commits) bypass Claude entirely and route via n8n directly. Only tasks with `compliance_flags` or `approval_level >= 1` actually touch Claude. This reduces cost by an estimated 60-70% and eliminates Claude as a single point of failure for operational tasks.

### Missing Error Handling

The spec has error handling documented at the n8n automation level (retry_queue, exponential backoff) but is missing:

1. **Adapter-level circuit breakers**: If Hermes fails 5 consecutive times, the system should stop sending it tasks and alert, not keep retrying indefinitely.
2. **Schema validation errors**: The spec mentions logging schema mismatches but doesn't define what happens to the task. Does it go to a dead letter queue? Does it return an error to the caller? Is it held for manual review?
3. **Partial execution recovery**: If a task completes PLAN and EXECUTE but fails at REVIEW (e.g., Claude returns a malformed response), how does the system resume? Can it re-run just the REVIEW phase against the already-written artifacts?
4. **Postgres connection failure**: The spec says "Postgres write is synchronous primary — if Postgres fails, halt entire operation." But "halt" is underspecified. Does the task fail? Does the system pause? Does the caller get a 500? What happens to in-flight Hermes executions?

---

## SCHEMA & CONTRACT REVIEW

### Canonical Task Schema: Assessment

**What's complete and correct:**
The JSON Schema Draft-07 implementation is properly formed. The `required` array correctly identifies the six fields that must be present for any valid task. The `compliance_flags` enum is the most important field in the schema and it's well-designed — explicit enumeration prevents free-form flag pollution. The `approval_level` as an integer enum (0/1/2) is clean and maps directly to business logic.

**What's missing from the schema:**

```json
// Missing fields that will be needed in production:

"priority": {
  "type": "integer",
  "enum": [1, 2, 3],
  "description": "1=critical, 2=normal, 3=low — needed for task queue ordering"
},

"parent_task_id": {
  "type": ["string", "null"],
  "format": "uuid",
  "description": "For subtask chains — enables dependency tracking"
},

"retry_policy": {
  "type": "object",
  "properties": {
    "max_attempts": {"type": "integer", "default": 3},
    "backoff_strategy": {"type": "string", "enum": ["fixed", "exponential", "none"]},
    "retry_on": {"type": "array", "items": {"type": "string"}}
  }
},

"idempotency_key": {
  "type": ["string", "null"],
  "description": "Prevents duplicate task creation from retry storms — critical for financial/external actions"
},

"context_artifacts": {
  "type": "array",
  "items": {"type": "string"},
  "description": "Read-only reference artifacts (vs input_artifacts which are consumed)"
},

"expires_at": {
  "type": ["string", "null"],
  "format": "date-time",
  "description": "Hard expiry — different from deadline. If not processed by this time, auto-reject."
}
```

The distinction between `deadline` and `expires_at` matters for Gridline specifically: a deal lead might have a 4-hour deadline for outreach approval but a 48-hour hard expiry before the opportunity is stale. The schema conflates these.

**`task_type` is missing entirely.** The Gridline workflow document references `"task_type": "gridline_daily_review"` in task objects, but this field doesn't exist in the canonical schema. This is a direct inconsistency between the schema spec and the pillar-specific workflow documentation, and it will cause validation failures when Phase 1 tasks are submitted.

### Pillar-Specific Defaults: Assessment

The defaults are well-reasoned with one significant concern:

**Forge pillar defaults are too permissive:**
```python
"forge": {
    "compliance_flags": [],  # internal IP
    "approval_level": 0,  # no gate for internal packaging
}
```
Forge AI's job is to package internal SOPs into sellable products. When those products include Gridline outreach scripts or trading signal frameworks, they inherit the compliance requirements of their source material. A Forge task that packages a Gridline seller outreach template should automatically inherit `compliance_flags: ["rcw_18_85"]`. The current defaults have no mechanism for compliance flag inheritance from source_pillar, which is a genuine compliance gap.

**Trading defaults are correct but incomplete:**
```python
"trading": {
    "approval_level": 2,  # DOUBLE gate on any external action
}
```
The "double gate" intent is right but the schema only supports a single approval_level integer. How is a double gate enforced at the schema level? The spec mentions it in the guardrails section but doesn't implement it in the schema. A proper implementation would add `"required_approvers": {"type": "integer", "default": 1}` to the schema and set it to 2 for trading tasks.

**Commerce defaults need affiliate disclosure enforcement:**
The `affiliate_disclosure` compliance flag is defined, but there's no validation that Commerce tasks with external publishing actions have this flag set. It could be omitted accidentally. A schema-level rule requiring `affiliate_disclosure` whenever `external_action` is present for Commerce pillar tasks would prevent compliance oversights.

### Database Schema: Assessment

**What's well-designed:**
The core tables are properly normalized. The separation of `tasks`, `task_events`, `artifacts`, `handoff_records`, and `audit_trail` is clean. The indexes on `(status, pillar)`, `(task_id, phase)`, and `handoff_records(status)` are the right choices for the query patterns described in the monitoring section.

**Missing indexes:**
```sql
-- The replay query (task reconstruction) joins three tables on task_id.
-- This index is critical for production replay performance:
CREATE INDEX idx_artifacts_task_id ON artifacts(task_id);

-- Gate response time query (monitoring section) filters audit_trail by task_id + action:
CREATE INDEX idx_audit_trail_task_action ON audit_trail(task_id, action);

-- The follow-up queue job queries leads by outreach_status:
CREATE INDEX idx_leads_outreach_status ON leads(outreach_status);
CREATE INDEX idx_leads_score ON leads(score DESC);
```

**The `leads` table has a data integrity gap:**
```sql
"rcw_compliant BOOLEAN"
```
A boolean `rcw_compliant` field is too coarse. Compliance isn't binary — it's a state machine. A lead can be `not_reviewed`, `pending_review`, `compliant`, `non_compliant`, or `conditionally_compliant_with_notes`. Using a boolean will cause you to set `rcw_compliant = TRUE` when you mean "we haven't checked yet (NULL)" and that's a production compliance risk, not just a data quality issue.

**The `signals` table for trading is missing version control:**
```sql
"approved_for_execution BOOLEAN DEFAULT FALSE"
```
A trading signal that was approved, then modified, should not retain its approved status. There's no `approved_version` or `last_modified_at` field. An approval should be tied to a specific version of the signal parameters. Otherwise someone edits signal parameters after approval and the system still considers it approved.

**`retry_queue` is missing a `max_retries` field.** Without it, a persistently failing Notion sync could retry indefinitely. There should be a maximum retry count after which the item is moved to a `dead_letter_queue` table (which also doesn't exist in the schema).

---

## CRITICAL GAPS

These are ranked by severity — items that would cause production failures if unaddressed are listed first.

### P0 — Production-Breaking

**1. Blocking approval poll in Abacus Adapter**
The `while True: time.sleep(60)` polling loop will exhaust database connection pools and thread pools under load. Replace with n8n webhook callback + event-driven state transition. This is not a code style issue — it's a resource exhaustion vulnerability.

**2. Abacus Adapter code is cut off**
The `translate_in` method contains a comment `# ... other pillar-specific routing` with no implementation. The routing logic for non-content, non-approval tasks (the majority of task types) is unspecified. A developer implementing this today has no guidance for Commerce, Forge, Logic, or Trading pillar routing through Abacus.

**3. No task queue / priority system**
The spec routes tasks directly to adapters with no queuing layer. Under concurrent load (multiple pillar jobs firing simultaneously), tasks will contend for adapter resources with no defined ordering. A simple priority queue backed by Postgres (using `SELECT ... FOR UPDATE SKIP LOCKED`) would solve this and is compatible with the existing schema design.

**4. `task_type` field missing from canonical schema**
Referenced in the Gridline workflow but absent from the JSON Schema definition. Any v1.0.0 task object for Gridline will fail schema validation, breaking Phase 1 from day one.

**5. No dead-letter queue**
Tasks that fail after max retries have nowhere to go except `status=FAILED` in the tasks table. There's no mechanism for human review, manual retry, or escalation of persistently failing tasks.

### P1 — Serious Operational Gaps

**6. Hermes "skill improvement" governance is underspecified**
The spec defines skill statuses (draft/testing/approved/locked/deprecated) and a weekly skill review job, but there's no defined workflow for how Hermes proposes a change, how that proposal is surfaced to the operator, and how the approval decision is recorded. The skill_version JSON example shows the structure but the process for moving between states is absent.

**7. No inter-pillar data access control**
The spec mandates: "Agents cannot read/write other pillars' data without explicit task_id linkage. Enforce via Postgres row-level security." But no RLS policies are defined. This is a one-line mention with no implementation guidance. A developer cannot implement this from the spec as written.

**8. Hermes "self-learning" risk is acknowledged but not mitigated**
The spec cites warnings about Hermes self-evaluation and skill overwriting but the only mitigation offered is the `locked` status. There's no specification for what triggers the locked status, who can change it, or how the system prevents an inadvertent status change from `locked` to `testing` that could re-enable a compliance-sensitive skill for modification.

**9. The Manus adapter is architecturally orphaned**
The Manus adapter is fully specified in the adapter contracts section, but it appears in zero pillar defaults, zero workflow descriptions, and zero phase build plans. It's unclear what tasks would actually route to Manus, and why Manus is preferred over Hermes for those tasks. Either Manus has a specific use case that should be documented, or it should be deferred to post-MVP explicitly.

**10. Approval timeout auto-reject is unimplemented**
The spec says "48h auto-reject." But the `handoff_records` table has no `expires_at` field. The n8n Human Gate automation says it will "auto-reject and log timeout to audit_trail" but doesn't specify what n8n trigger detects 48-hour inactivity. A scheduled workflow with `SELECT ... WHERE status='pending' AND timestamp < NOW() - INTERVAL '48 hours'` would work, but it's not specified.

### P2 — Documentation / Completeness Gaps

**11. No API contract for FSH Command Interface**
The spec defines what the command interface does but not how you call it. What's the endpoint? What's the authentication mechanism? What does a valid request look like? What does an error response look like? Without an API contract, the n8n → FSH Command → Postgres chain cannot be wired.

**12. No environment configuration specification**
The spec references `N8N_WEBHOOK`, `OPERATOR_TELEGRAM_ID`, `PIPELINE_MAP`, `N8N_WEBHOOK` as constants but never defines where these are stored, how they're injected, or what the `.env` structure looks like. The Phase 0 checklist says "save connection string to .env" but a developer building from this spec has no `.env.example` to reference.

**13. Git commit strategy for artifact content is undefined**
The spec says artifacts should be committed to Git. But JSONB artifact content from Postgres shouldn't go directly into Git — that's a storage antipattern. What exactly is committed? The schema definition? The artifact reference? The rendered output? The spec implies "everything" but that would make the Git repository unmanageable quickly.

**14. Content pillar execution engine mismatch**
The `content` pillar default sets `execution_engine: "claude"` for "script generation." But the Content workflow in the AETHERIS section describes a Content Agent that does ingestion, repurposing, scheduling, and distribution — tasks that are clearly Hermes/n8n territory, not Claude. Claude should handle script *generation* and *refinement*, while Hermes handles *scheduling* and *distribution*. The single `execution_engine` field can't represent this split — this is a schema design limitation that will force workarounds.

---

## RISK MATRIX

| Risk | Category | Likelihood | Impact | Current Mitigation | Gap |
|------|----------|-----------|--------|-------------------|-----|
| Blocking approval poll exhausts DB connections | Scalability | High | Critical | None specified | Replace with event-driven callbacks immediately |
| Claude API outage halts entire control plane | Operational | Medium | Critical | None | Define degraded mode; route non-reasoning tasks around Claude |
| Hermes skill self-modification overwrites locked compliance logic | Security/Compliance | Low | Critical | `locked` status field | No process for status change governance |
| RCW 18.85 violation in seller outreach | Compliance/Legal | Medium | Critical | `rcw_compliant` flag, approval gates | Flag is boolean (too coarse); no legal review workflow |
| PII in Gridline leads leaks to Notion (human-readable layer) | Security | Medium | High | None specified | Notion sync should strip or mask PII fields before write |
| Trading signal approved pre-modification fires on stale approval | Financial | Low | High | `approved_for_execution` boolean | No version-tied approval; approval not invalidated on edit |
| Concurrent pillar jobs contend for Hermes resources | Scalability | High | Medium | None | Introduce priority queue with `SELECT FOR UPDATE SKIP LOCKED` |
| Schema version drift as pillars evolve at different rates | Operational | High | Medium | `schema_versions` table | No migration framework specified; no breaking-change policy |
| Notion API rate limits (3 req/s) under high task volume | Scalability | Medium | Medium | `retry_queue` | Retry queue has no max_retries; no dead-letter handling |
| n8n webhook delivery failure silently drops task callbacks | Operational | Medium | Medium | Retry logic in n8n | No alerting if webhook repeatedly fails for same task_id |
| Hermes down during scheduled Gridline daily report | Operational | Low | Medium | None | No fallback execution path; no graceful degradation spec |
| Operator approval delayed >48h auto-rejects time-sensitive deal | Operational | Medium | Medium | Auto-reject at 48h | No secondary approver; no escalation path if primary unavailable |
| Abacus platform API changes break adapter layer | Operational | Medium | Medium | Adapter abstraction | No interface versioning; no adapter test suite spec |
| Forge AI packages compliance-sensitive Gridline SOPs without inheriting flags | Compliance | High | High | None | No flag inheritance mechanism in Forge pillar defaults |
| Git repository grows unmanageable with raw artifact JSONB commits | Operational | High | Low | None | No definition of what actually gets committed |
| Manus adapter used for undefined tasks without sandbox enforcement | Security | Low | High | `sandbox_mode` flag | Manus use cases not defined; sandbox trigger is only `pillar == "trading"` |

---

## RECOMMENDATIONS

Ranked by priority — items 1-5 are must-fix before any production code is written.

### Priority 1 — Fix Before Writing a Line of Production Code

**R1: Replace blocking approval poll with event-driven callback**
Rewrite the `AbacusAdapter.execute()` to write `status=PENDING_APPROVAL` to `handoff_records`, then return immediately. Create an n8n workflow that fires when `handoff_records.status` transitions to `approved` or `rejected` (via Postgres NOTIFY/LISTEN or polling). The adapter should expose a `/callback/{task_id}` endpoint that n8n can hit when approval is received. This turns a thread-blocking 48-hour poll into a stateless, resumable state machine.

**R2: Add `task_type` to canonical schema v1.0.0**
```json
"task_type": {
  "type": "string",
  "pattern": "^[a-z_]+$",
  "description": "Identifies the specific workflow. e.g., gridline_daily_review, seller_outreach_draft"
}
```
Add to `required` array. Update all pillar-specific examples to include this field. Bump schema to v1.0.1 and commit with migration notes.

**R3: Define Claude-bypass routing for non-reasoning tasks**
Add a `reasoning_required` boolean (default: `false`) or derive it from `compliance_flags`. Tasks with empty compliance_flags and `approval_level == 0` should route directly to their `execution_engine` adapter without touching Claude. Add a routing decision table to the FSH Command spec.

**R4: Specify Postgres RLS policies for pillar isolation**
Write out the actual policies:
```sql
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
CREATE POLICY leads_gridline_only ON leads
  USING (current_setting('app.current_pillar') = 'gridline' 
         OR current_setting('app.current_pillar') = 'admin');
```
Document the application-level mechanism for setting `app.current_pillar` on each connection. This is 20 lines of SQL but it closes a significant data isolation gap.

**R5: Add `idempotency_key` to task schema and enforce it**
Any task with `external_action=true` or `financial` in compliance_flags must include an `idempotency_key`. The FSH Command interface should reject duplicate `idempotency_key` submissions with a 409 Conflict response and return the existing task record. This prevents double-submission of seller outreach, affiliate actions, and trading signals during retry storms.

### Priority 2 — Fix Before Phase 2 (Human Gates)

**R6: Replace `rcw_compliant BOOLEAN` with a compliance state machine**
```sql
ALTER TABLE leads 
  ADD COLUMN rcw_status TEXT DEFAULT 'not_reviewed'
    CHECK (rcw_status IN ('not_reviewed', 'pending_review', 'compliant', 'non_compliant', 'conditionally_compliant')),
  ADD COLUMN rcw_reviewed_at TIMESTAMPTZ,
  ADD COLUMN rcw_reviewed_by TEXT,
  ADD COLUMN rcw_notes TEXT;
```
Update the Gridline Agent system prompt to require explicit rcw_status classification before any outreach draft is written.

**R7: Implement compliance flag inheritance for Forge tasks**
When a Forge task has a `source_pillar` reference in its input_artifacts, the FSH Command layer should automatically inherit the source pillar's compliance_flags into the Forge task. Add a `flag_inheritance_rules` configuration:
```python
FLAG_INHERITANCE = {
    "forge": {
        "inherit_from_source_pillar": True,
        "minimum_flags": []  # Forge adds nothing by default
    }
}
```

**R8: Define approval escalation path**
The spec defines 12h alert → 24h URGENT → 48h auto-reject, but there's no secondary approver or escalation target. Add `approval_escalation_contact` to the operator configuration. At 24h, route the gate notification to the escalation contact in addition to the primary operator. Document how to configure this in the n8n Human Gate automation.

**R9: Add `max_retries` and dead-letter handling to retry_queue**
```sql
ALTER TABLE retry_queue 
  ADD COLUMN max_retries INTEGER DEFAULT 5,
  ADD COLUMN status TEXT DEFAULT 'pending' 
    CHECK (status IN ('pending', 'retrying', 'dead', 'resolved'));
```
Create a `dead_letter_queue` table for items that exceed max_retries. Add a Telegram alert when any item transitions to `dead` status.

**R10: Add missing database indexes**
```sql
CREATE INDEX idx_artifacts_task_id ON artifacts(task_id);
CREATE INDEX idx_audit_trail_task_action ON audit_trail(task_id, action);
CREATE INDEX idx_leads_outreach_status ON leads(outreach_status);
CREATE INDEX idx_leads_score ON leads(score DESC);
CREATE INDEX idx_leads_rcw_status ON leads(rcw_status);
```

### Priority 3 — Fix Before Phase 3 (Data Spine)

**R11: Define Manus adapter use cases or remove from MVP**
Either document what tasks route to Manus (with specific examples and pillar assignments) or explicitly mark Manus as post-MVP. An adapter with no defined routing path is dead code that adds maintenance burden. If Manus is kept, add `manus` to at least one pillar's `execution_engine` options with a documented rationale.

**R12: Define Git commit content policy**
Specify exactly what gets committed per task:
- Schema definition changes → commit full JSON
- Prompt library changes → commit prompt files
- SOP artifacts from Forge → commit markdown output
- Lead data, trading signals, client PII → never commit to Git; reference by artifact_id only
Write this as a 10-line policy statement and add it to the guardrails section.

**R13: Add `priority` field to task schema and task queue logic**
Implement a priority queue using Postgres:
```sql
ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 2 CHECK (priority IN (1, 2, 3));
CREATE INDEX idx_tasks_priority_created ON tasks(priority, created_at) WHERE status = 'pending';
```
Workers pull tasks with `ORDER BY priority ASC, created_at ASC FOR UPDATE SKIP LOCKED`. Define pillar priority defaults: Gridline time-sensitive deals = 1, standard processing = 2, background jobs = 3.

**R14: Add trading signal version control**
```sql
ALTER TABLE signals 
  ADD COLUMN approved_version TEXT,
  ADD COLUMN last_modified_at TIMESTAMPTZ DEFAULT NOW(),
  ADD COLUMN modification_after_approval BOOLEAN DEFAULT FALSE;
  
-- Trigger to detect post-approval modification
CREATE OR REPLACE FUNCTION flag_signal_modification()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.approved_for_execution = TRUE AND NEW.parameters != OLD.parameters THEN
    NEW.modification_after_approval := TRUE;
    NEW.approved_for_execution := FALSE;  -- Revoke approval on parameter change
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER signals_modification_guard
BEFORE UPDATE ON signals
FOR EACH ROW EXECUTE FUNCTION flag_signal_modification();
```

**R15: Document the split execution model for Content pillar**
Content tasks that involve generation (scripts, hooks, messaging) should use `execution_engine: "claude"`. Content tasks that involve distribution and scheduling should use `execution_engine: "hermes"`. The schema's single `execution_engine` field needs to either be extended to an array or the Content workflow should create two linked tasks (parent content generation task → child distribution task via `parent_task_id`). The parent_task_id relationship also supports the dependency tracking needed for multi-step Gridline workflows.

### Priority 4 — Documentation Quality Improvements

**R16: Add sequence diagrams for the three most critical flows**
The spec needs visual representations of:
1. The Gridline daily review flow (from Telegram message to Notion artifact)
2. The human gate approval flow (from gate_required to approved/rejected outcome)
3. The n8n callback chain (from adapter completion to operator notification)

The current ASCII diagram in the spec is too high-level to be implementation-useful. A developer building the n8n workflows needs to see exactly which webhook fires when and what data it carries.

**R17: Add `.env.example` and infrastructure configuration**
The spec references multiple environment variables without defining them. Create a reference `.env.example`:
```
POSTGRES_URL=
NOTION_TOKEN=
NOTION_TASK_EVENTS_DB_ID=
NOTION_ARTIFACTS_DB_ID=
CLAUDE_API_KEY=
N8N_WEBHOOK_BASE_URL=
OPERATOR_TELEGRAM_ID=
TELEGRAM_BOT_TOKEN=
HERMES_API_URL=
ABACUS_CLAW_ENDPOINT=
GIT_REPO_URL=
GIT_DEPLOY_KEY=
```

**R18: Add a failure mode decision tree for the Gridline pillar**
The Gridline workflow is the most operationally complex and the most compliance-sensitive. A decision tree covering:
- "What happens if Hermes fails during lead scoring?" 
- "What happens if the operator doesn't approve outreach within 48h?"
- "What happens if Postgres is unavailable when writing lead scores?"
- "What happens if Claude flags an outreach draft as non-compliant?"

...would be more valuable than the current "test end-to-end" checklist items.

**R19: Define the Gridline Agent ↔ Hermes boundary more precisely**
The spec defines both a "Gridline Agent" (domain specialist with its own system prompt) and "Hermes" (execution runtime). In the adapter pattern, Hermes *is* the execution engine. Does the Gridline Agent run *inside* Hermes, or is it a separate Claude-powered agent that orchestrates Hermes skill calls? This is ambiguous and will cause architectural confusion during implementation. The cleanest design: Gridline Agent is a Claude adapter instance with the Gridline system prompt; Hermes executes the skill library; Claude adapter calls Hermes for execution tasks and handles review/compliance logic itself.

---

## OVERALL VERDICT

This specification is **production-ready at the conceptual level but implementation-incomplete**. A senior engineer could build the core system from this document but would need to make approximately 15-20 undocumented architectural decisions along the way, some of which (the blocking poll, the missing task_type, the binary RCW compliance field) would require refactoring immediately after deployment.

**Strengths worth preserving:**
- The federated, not fused principle is stated clearly and correctly
- The PLAN→EXECUTE→REVIEW→LOG→HANDOFF protocol is the right pattern and well-named
- The pillar-specific defaults in the schema are thoughtfully differentiated
- The monitoring SQL queries are production-grade and show operational maturity
- The skill governance system (draft/testing/approved/locked/deprecated) is the right design for a self-learning execution layer
- The guardrail section is direct and non-negotiable in tone — this is correct for a compliance-sensitive system

**The system will work if you:**
1. Fix the blocking poll (R1) before writing any adapter code
2. Add task_type to the schema (R2) before Phase 1
3. Define the Claude-bypass routing (R3) before the system processes more than 50 tasks/day
4. Implement RLS policies (R4) before any multi-pillar data coexists in the same Postgres instance
5. Treat idempotency (R5) as a day-one requirement, not a post-MVP concern

The architecture isn't trying to do too much. The discipline of the handoffs is real and present in this document. Build the spine first — the spec is correct about that.

---

*Review completed. Schema version referenced: v1.0.0. Document sections reviewed: Architecture, Adapter Contracts, Data Schema, Build Plan, Guardrails, Monitoring, MVP Criteria.*
