"""
FSH Command Center — Webhook Server
=====================================
FastAPI server providing:
  - Task CRUD and submission
  - Approval queue management
  - Audit trail queries
  - Agent runtime status
  - Dashboard data endpoints

Supports two storage backends:
  1. Postgres (production) — set DATABASE_URL
  2. SQLite (development)  — auto-created at fsh_command_center.db if no DATABASE_URL

Run:
    cd fsh-command-center
    uvicorn orchestrator.webhook_server:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    GET  /health                     ← health check
    POST /tasks/submit               ← submit new task
    GET  /tasks                      ← list tasks (with filtering)
    GET  /tasks/{task_id}            ← single task lookup
    POST /webhook/approval-decision  ← n8n posts approval result
    POST /webhook/retry              ← n8n retry queue trigger
    GET  /approvals                  ← list pending approvals
    GET  /audit                      ← audit trail (filterable)
    GET  /agents/status              ← agent runtime status
    GET  /dashboard/stats            ← pillar health + summary stats
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure fsh-command-center/ is on sys.path so adapters/config can be imported
_FSH_ROOT = str(Path(__file__).parent.parent)
if _FSH_ROOT not in sys.path:
    sys.path.insert(0, _FSH_ROOT)

from pathlib import Path as _Path
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orchestrator.orchestrator import FSHOrchestrator

app = FastAPI(
    title="FSH Command Center",
    description="Multi-agent orchestration layer for Fermier Sovereign Holdings",
    version="1.1.0",
)

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator: FSHOrchestrator | None = None
_db_backend: str = "none"  # "postgres", "sqlite", or "none"


def get_orchestrator() -> FSHOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        db_url = os.environ.get("DATABASE_URL")
        _orchestrator = FSHOrchestrator(
            db_url       = db_url,
            n8n_base_url = os.environ.get("N8N_BASE_URL", ""),
        )
    return _orchestrator


# ── SQLite fallback store ───────────────────────────────────────────────────
_SQLITE_PATH = Path(_FSH_ROOT) / "fsh_command_center.db"


def _get_sqlite() -> sqlite3.Connection:
    """Get SQLite connection for development mode (no Postgres required)."""
    conn = sqlite3.connect(str(_SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_sqlite() -> None:
    """Create SQLite tables if they don't exist (development mode)."""
    conn = _get_sqlite()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id           TEXT PRIMARY KEY,
            schema_version    TEXT NOT NULL DEFAULT '1.0.1',
            task_type         TEXT NOT NULL,
            pillar            TEXT NOT NULL,
            objective         TEXT NOT NULL,
            priority          INTEGER NOT NULL DEFAULT 2,
            approval_level    INTEGER NOT NULL DEFAULT 0,
            execution_engine  TEXT NOT NULL DEFAULT 'hermes',
            compliance_flags  TEXT NOT NULL DEFAULT '[]',
            idempotency_key   TEXT,
            context_artifacts TEXT NOT NULL DEFAULT '[]',
            retry_policy      TEXT NOT NULL DEFAULT '{}',
            parent_task_id    TEXT,
            status            TEXT NOT NULL DEFAULT 'queued',
            assigned_to       TEXT,
            result_summary    TEXT,
            error_detail      TEXT,
            expires_at        TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            completed_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS approval_requests (
            approval_id     TEXT PRIMARY KEY,
            task_id         TEXT NOT NULL,
            requested_by    TEXT NOT NULL,
            approval_level  INTEGER NOT NULL,
            reason          TEXT NOT NULL,
            context_snapshot TEXT NOT NULL DEFAULT '{}',
            status          TEXT NOT NULL DEFAULT 'pending',
            decided_by      TEXT,
            decision_reason TEXT,
            callback_url    TEXT,
            notified_at     TEXT,
            decided_at      TEXT,
            expires_at      TEXT,
            created_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_trail (
            audit_id    TEXT PRIMARY KEY,
            task_id     TEXT,
            pillar      TEXT,
            action      TEXT NOT NULL,
            actor       TEXT NOT NULL,
            detail      TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_pillar_status ON tasks(pillar, status);
        CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_requests(status);
        CREATE INDEX IF NOT EXISTS idx_audit_pillar ON audit_trail(pillar, created_at DESC);
    """)
    conn.commit()
    conn.close()


def _detect_db_backend() -> str:
    """Detect which storage backend to use."""
    if os.environ.get("DATABASE_URL"):
        return "postgres"
    else:
        _init_sqlite()
        return "sqlite"


def _store() -> str:
    global _db_backend
    if _db_backend == "none":
        _db_backend = _detect_db_backend()
    return _db_backend


# ── SQLite helper functions ─────────────────────────────────────────────────

def _sqlite_list_tasks(
    pillar: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    conn = _get_sqlite()
    clauses = []
    params = []
    if pillar:
        clauses.append("pillar = ?")
        params.append(pillar)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _sqlite_list_approvals(status: str = "pending") -> list[dict]:
    conn = _get_sqlite()
    rows = conn.execute(
        "SELECT a.*, t.pillar, t.task_type, t.objective "
        "FROM approval_requests a LEFT JOIN tasks t ON a.task_id = t.task_id "
        "WHERE a.status = ? ORDER BY a.created_at DESC",
        (status,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _sqlite_list_audit(
    pillar: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    conn = _get_sqlite()
    if pillar:
        rows = conn.execute(
            "SELECT * FROM audit_trail WHERE pillar = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (pillar, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_trail ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _sqlite_dashboard_stats() -> dict:
    conn = _get_sqlite()
    # Per-pillar counts
    pillars = ["gridline", "logic", "commerce", "content", "forge", "trading"]
    pillar_stats = {}
    for p in pillars:
        row = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks WHERE pillar = ? GROUP BY status",
            (p,),
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in row}
        pillar_stats[p] = {
            "active": counts.get("executing", 0) + counts.get("planning", 0) + counts.get("review", 0),
            "pending": counts.get("queued", 0) + counts.get("awaiting_approval", 0),
            "failed": counts.get("failed", 0) + counts.get("dead_lettered", 0),
            "completed_24h": counts.get("completed", 0),  # simplified
        }

    # Total counts
    total = conn.execute("SELECT COUNT(*) as c FROM tasks").fetchone()["c"]
    pending_approvals = conn.execute(
        "SELECT COUNT(*) as c FROM approval_requests WHERE status = 'pending'"
    ).fetchone()["c"]

    conn.close()
    return {
        "pillars": pillar_stats,
        "total_tasks": total,
        "pending_approvals": pending_approvals,
    }


# ── Request models ──────────────────────────────────────────────────────────

class ApprovalDecisionPayload(BaseModel):
    approval_id:     str
    task_id:         str
    decision:        str          # "approved" | "rejected"
    decided_by:      str
    decision_reason: str | None = None
    approval_token:  str | None = None


class TaskSubmitPayload(BaseModel):
    task_type:         str
    pillar:            str
    objective:         str
    execution_engine:  str | None = None
    approval_level:    int | None = None
    priority:          int | None = None
    compliance_flags:  list[str] | None = None
    idempotency_key:   str | None = None
    context_artifacts: list[dict] | None = None


# ── Startup event ───────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    backend = _store()
    print(f"[FSH] Storage backend: {backend}")
    if backend == "sqlite":
        print(f"[FSH] SQLite database: {_SQLITE_PATH}")
        _seed_if_empty()
    print("[FSH] Command Center ready — http://localhost:8000")


def _seed_if_empty():
    """Seed SQLite with sample data if the tasks table is empty."""
    conn = _get_sqlite()
    count = conn.execute("SELECT COUNT(*) as c FROM tasks").fetchone()["c"]
    if count > 0:
        conn.close()
        return

    now = datetime.now(timezone.utc).isoformat()
    sample_tasks = [
        {
            "task_id": str(uuid.uuid4()), "task_type": "gridline_daily_review",
            "pillar": "gridline", "objective": "Review today's new leads and score top 50",
            "priority": 1, "approval_level": 1, "execution_engine": "abacus",
            "status": "executing", "compliance_flags": '["rcw_18_85", "pii"]',
            "idempotency_key": "gridline-daily-2025-01-15",
        },
        {
            "task_id": str(uuid.uuid4()), "task_type": "content_script_gen",
            "pillar": "content", "objective": "Generate TikTok script for wholesale real estate tips",
            "priority": 2, "approval_level": 0, "execution_engine": "hermes",
            "status": "completed", "compliance_flags": '[]',
            "result_summary": "Script generated: 3 hooks, 2 transitions, CTA",
        },
        {
            "task_id": str(uuid.uuid4()), "task_type": "seller_outreach_draft",
            "pillar": "gridline", "objective": "Draft outreach for 123 Main St, Spokane",
            "priority": 2, "approval_level": 1, "execution_engine": "abacus",
            "status": "awaiting_approval", "compliance_flags": '["rcw_18_85", "pii"]',
            "idempotency_key": "outreach-123-main-st",
        },
        {
            "task_id": str(uuid.uuid4()), "task_type": "signal_analysis",
            "pillar": "trading", "objective": "Analyze AAPL buy signal confidence",
            "priority": 1, "approval_level": 2, "execution_engine": "abacus",
            "status": "queued", "compliance_flags": '["financial", "external_action"]',
            "idempotency_key": "signal-aapl-2025-01-15",
        },
        {
            "task_id": str(uuid.uuid4()), "task_type": "sop_publish",
            "pillar": "forge", "objective": "Publish RCW compliance SOP v2.1",
            "priority": 2, "approval_level": 2, "execution_engine": "claude",
            "status": "awaiting_approval", "compliance_flags": '["irreversible"]',
            "idempotency_key": "sop-rcw-v2.1",
        },
        {
            "task_id": str(uuid.uuid4()), "task_type": "product_research",
            "pillar": "commerce", "objective": "Research top Amazon listings for wholesale tools",
            "priority": 3, "approval_level": 1, "execution_engine": "manus",
            "status": "completed", "compliance_flags": '["external_action", "affiliate_disclosure"]',
            "result_summary": "Found 23 products with >4.5 rating in wholesale tools category",
        },
        {
            "task_id": str(uuid.uuid4()), "task_type": "identity_verification",
            "pillar": "logic", "objective": "Verify credential document for new partner onboarding",
            "priority": 2, "approval_level": 0, "execution_engine": "claude",
            "status": "completed", "compliance_flags": '["pii"]',
            "result_summary": "Credential verified: document authentic, matches partner profile",
        },
        {
            "task_id": str(uuid.uuid4()), "task_type": "gridline_csv_import",
            "pillar": "gridline", "objective": "Import 384-property batch from Spokane County",
            "priority": 1, "approval_level": 1, "execution_engine": "abacus",
            "status": "failed", "compliance_flags": '["rcw_18_85", "pii"]',
            "error_detail": '{"error_class": "timeout", "message": "Import timed out after 300s"}',
        },
    ]

    for t in sample_tasks:
        conn.execute(
            "INSERT INTO tasks (task_id, schema_version, task_type, pillar, objective, "
            "priority, approval_level, execution_engine, status, compliance_flags, "
            "idempotency_key, result_summary, error_detail, created_at, updated_at) "
            "VALUES (?, '1.0.1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t["task_id"], t["task_type"], t["pillar"], t["objective"],
                t["priority"], t["approval_level"], t["execution_engine"],
                t["status"], t["compliance_flags"], t.get("idempotency_key"),
                t.get("result_summary"), t.get("error_detail"),
                now, now,
            ),
        )

    # Seed approval requests for awaiting_approval tasks
    awaiting = conn.execute(
        "SELECT task_id FROM tasks WHERE status = 'awaiting_approval'"
    ).fetchall()
    for i, row in enumerate(awaiting):
        approval_id = str(uuid.uuid4())
        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        conn.execute(
            "INSERT INTO approval_requests (approval_id, task_id, requested_by, "
            "approval_level, reason, status, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (
                approval_id, row["task_id"], "orchestrator",
                1 if i == 0 else 2,
                "pending", expires, now,
            ),
        )

    # Seed audit trail
    sample_audit = [
        {"task_id": sample_tasks[0]["task_id"], "pillar": "gridline", "action": "task_submitted", "actor": "orchestrator", "detail": '{"engine": "abacus"}'},
        {"task_id": sample_tasks[0]["task_id"], "pillar": "gridline", "action": "task_executing", "actor": "abacus", "detail": '{}'},
        {"task_id": sample_tasks[1]["task_id"], "pillar": "content", "action": "task_submitted", "actor": "orchestrator", "detail": '{"engine": "hermes"}'},
        {"task_id": sample_tasks[1]["task_id"], "pillar": "content", "action": "task_completed", "actor": "hermes", "detail": '{"success": true}'},
        {"task_id": sample_tasks[2]["task_id"], "pillar": "gridline", "action": "approval_requested", "actor": "orchestrator", "detail": '{"approval_level": 1}'},
        {"task_id": sample_tasks[4]["task_id"], "pillar": "forge", "action": "approval_requested", "actor": "orchestrator", "detail": '{"approval_level": 2, "reason": "irreversible"}'},
        {"task_id": sample_tasks[7]["task_id"], "pillar": "gridline", "action": "task_failed", "actor": "abacus", "detail": '{"error_class": "timeout"}'},
    ]
    for a in sample_audit:
        conn.execute(
            "INSERT INTO audit_trail (audit_id, task_id, pillar, action, actor, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), a["task_id"], a["pillar"], a["action"],
             a["actor"], a["detail"], now),
        )

    conn.commit()
    conn.close()
    print(f"[FSH] Seeded {len(sample_tasks)} sample tasks")


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "fsh-command-center",
        "version": "1.1.0",
        "backend": _store(),
    }


@app.post("/tasks/submit")
async def submit_task(payload: TaskSubmitPayload):
    """Submit a new task to the FSH orchestrator."""
    orch = get_orchestrator()

    if _store() == "sqlite":
        # SQLite path: write directly
        from config.pillar_defaults import apply_defaults
        merged = apply_defaults(payload.model_dump(exclude_none=True))
        now = datetime.now(timezone.utc).isoformat()
        task_id = str(uuid.uuid4())
        conn = _get_sqlite()
        conn.execute(
            "INSERT INTO tasks (task_id, schema_version, task_type, pillar, objective, "
            "priority, approval_level, execution_engine, status, compliance_flags, "
            "idempotency_key, context_artifacts, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, "1.0.1", merged["task_type"], merged["pillar"],
                merged["objective"], merged["priority"], merged["approval_level"],
                merged["execution_engine"],
                "awaiting_approval" if merged["approval_level"] >= 1 else "queued",
                json.dumps(merged.get("compliance_flags", [])),
                merged.get("idempotency_key"),
                json.dumps(merged.get("context_artifacts", [])),
                now, now,
            ),
        )
        # Create approval request if needed
        if merged["approval_level"] >= 1:
            from datetime import timedelta
            approval_id = str(uuid.uuid4())
            expires = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
            conn.execute(
                "INSERT INTO approval_requests (approval_id, task_id, requested_by, "
                "approval_level, reason, status, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (approval_id, task_id, "orchestrator", merged["approval_level"],
                 f"Task requires approval_level={merged['approval_level']}",
                 expires, now),
            )
        # Audit
        conn.execute(
            "INSERT INTO audit_trail (audit_id, task_id, pillar, action, actor, detail, created_at) "
            "VALUES (?, ?, ?, 'task_submitted', 'dashboard', ?, ?)",
            (str(uuid.uuid4()), task_id, merged["pillar"],
             json.dumps({"engine": merged["execution_engine"]}), now),
        )
        conn.commit()
        conn.close()
        return JSONResponse(
            content={"task_id": task_id, "status": "queued", "pillar": merged["pillar"]},
            status_code=202 if merged["approval_level"] >= 1 else 200,
        )

    result = orch.submit(payload.model_dump(exclude_none=True))
    status_code = 202 if result.get("status") == "awaiting_approval" else 200
    return JSONResponse(content=result, status_code=status_code)


@app.get("/tasks")
async def list_tasks(
    pillar: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List tasks with optional filtering by pillar and status."""
    if _store() == "sqlite":
        tasks = _sqlite_list_tasks(pillar=pillar, status=status, limit=limit, offset=offset)
        for t in tasks:
            if t.get("compliance_flags"):
                try:
                    t["compliance_flags"] = json.loads(t["compliance_flags"])
                except (json.JSONDecodeError, TypeError):
                    t["compliance_flags"] = []
            if t.get("context_artifacts"):
                try:
                    t["context_artifacts"] = json.loads(t["context_artifacts"])
                except (json.JSONDecodeError, TypeError):
                    t["context_artifacts"] = []
            if t.get("error_detail"):
                try:
                    t["error_detail"] = json.loads(t["error_detail"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if t.get("retry_policy"):
                try:
                    t["retry_policy"] = json.loads(t["retry_policy"])
                except (json.JSONDecodeError, TypeError):
                    t["retry_policy"] = {}
        return JSONResponse(content={"tasks": tasks, "count": len(tasks)})

    # Postgres path
    orch = get_orchestrator()
    if not orch.db_url:
        return JSONResponse(content={"tasks": [], "count": 0})

    conn = orch._get_conn()
    if not conn:
        return JSONResponse(content={"tasks": [], "count": 0})

    import psycopg2.extras
    clauses = []
    params = []
    if pillar:
        clauses.append("pillar = %s::pillar_enum")
        params.append(pillar)
    if status:
        clauses.append("status = %s::task_status_enum")
        params.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()

    return JSONResponse(content={"tasks": rows, "count": len(rows)})


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Look up task status by ID."""
    if _store() == "sqlite":
        conn = _get_sqlite()
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        result = dict(row)
        for key in ("compliance_flags", "context_artifacts", "error_detail", "retry_policy"):
            if result.get(key):
                try:
                    result[key] = json.loads(result[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return JSONResponse(content=result)

    orch = get_orchestrator()
    task_row = orch._fetch_task(task_id)
    if not task_row:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    for k, v in task_row.items():
        if hasattr(v, "isoformat"):
            task_row[k] = v.isoformat()
    return JSONResponse(content=task_row)


@app.post("/webhook/approval-decision")
async def approval_decision(payload: ApprovalDecisionPayload):
    """
    Called by n8n or dashboard when a human approves or rejects a task.
    """
    if _store() == "sqlite":
        conn = _get_sqlite()
        now = datetime.now(timezone.utc).isoformat()
        if payload.decision == "approved":
            conn.execute(
                "UPDATE approval_requests SET status = 'approved', decided_by = ?, "
                "decision_reason = ?, decided_at = ? WHERE approval_id = ?",
                (payload.decided_by, payload.decision_reason, now, payload.approval_id),
            )
            conn.execute(
                "UPDATE tasks SET status = 'executing', updated_at = ? WHERE task_id = ?",
                (now, payload.task_id),
            )
            conn.execute(
                "INSERT INTO audit_trail (audit_id, task_id, pillar, action, actor, detail, created_at) "
                "SELECT ?, ?, pillar, 'approval_approved', ?, ?, ? FROM tasks WHERE task_id = ?",
                (str(uuid.uuid4()), payload.task_id, payload.decided_by,
                 json.dumps({"approval_id": payload.approval_id}), now, payload.task_id),
            )
        elif payload.decision == "rejected":
            conn.execute(
                "UPDATE approval_requests SET status = 'rejected', decided_by = ?, "
                "decision_reason = ?, decided_at = ? WHERE approval_id = ?",
                (payload.decided_by, payload.decision_reason, now, payload.approval_id),
            )
            conn.execute(
                "UPDATE tasks SET status = 'rejected', updated_at = ? WHERE task_id = ?",
                (now, payload.task_id),
            )
            conn.execute(
                "INSERT INTO audit_trail (audit_id, task_id, pillar, action, actor, detail, created_at) "
                "SELECT ?, ?, pillar, 'approval_rejected', ?, ?, ? FROM tasks WHERE task_id = ?",
                (str(uuid.uuid4()), payload.task_id, payload.decided_by,
                 json.dumps({"approval_id": payload.approval_id, "reason": payload.decision_reason}),
                 now, payload.task_id),
            )
        else:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Unknown decision: {payload.decision}")
        conn.commit()
        conn.close()
        return JSONResponse(content={"task_id": payload.task_id, "status": payload.decision})

    # Postgres path
    orch = get_orchestrator()
    if payload.decision == "approved":
        token = payload.approval_token or f"approved:{payload.approval_id}"
        result = orch.resume_after_approval(
            task_id        = payload.task_id,
            approval_token = token,
        )
        return JSONResponse(content=result)
    elif payload.decision == "rejected":
        orch._update_task_status(payload.task_id, "rejected")
        return JSONResponse({"task_id": payload.task_id, "status": "rejected"})
    else:
        raise HTTPException(status_code=400, detail=f"Unknown decision: {payload.decision}")


@app.post("/webhook/retry")
async def retry_task(request: Request):
    """Called by n8n retry queue to re-attempt a failed task."""
    body = await request.json()
    task_id = body.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")

    if _store() == "sqlite":
        conn = _get_sqlite()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE tasks SET status = 'queued', updated_at = ? WHERE task_id = ?",
            (now, task_id),
        )
        conn.commit()
        conn.close()
        return JSONResponse(content={"task_id": task_id, "status": "queued"})

    orch = get_orchestrator()
    task_row = orch._fetch_task(task_id)
    if not task_row:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    result = orch.submit(task_row)
    return JSONResponse(content=result)


@app.get("/approvals")
async def list_approvals(status: str = "pending"):
    """List approval requests, defaulting to pending."""
    if _store() == "sqlite":
        approvals = _sqlite_list_approvals(status=status)
        for a in approvals:
            if a.get("context_snapshot"):
                try:
                    a["context_snapshot"] = json.loads(a["context_snapshot"])
                except (json.JSONDecodeError, TypeError):
                    pass
            # Compute hours remaining
            if a.get("expires_at"):
                try:
                    expires = datetime.fromisoformat(a["expires_at"])
                    remaining = (expires - datetime.now(timezone.utc)).total_seconds() / 3600
                    a["hours_remaining"] = round(max(0, remaining), 1)
                except (ValueError, TypeError):
                    a["hours_remaining"] = None
        return JSONResponse(content={"approvals": approvals, "count": len(approvals)})

    # Postgres path — use v_pending_approvals view
    orch = get_orchestrator()
    if not orch.db_url:
        return JSONResponse(content={"approvals": [], "count": 0})
    conn = orch._get_conn()
    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM v_pending_approvals")
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
    return JSONResponse(content={"approvals": rows, "count": len(rows)})


@app.get("/audit")
async def list_audit(
    pillar: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Audit trail with optional pillar filtering."""
    if _store() == "sqlite":
        entries = _sqlite_list_audit(pillar=pillar, limit=limit, offset=offset)
        for e in entries:
            if e.get("detail"):
                try:
                    e["detail"] = json.loads(e["detail"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return JSONResponse(content={"audit": entries, "count": len(entries)})

    # Postgres path
    orch = get_orchestrator()
    if not orch.db_url:
        return JSONResponse(content={"audit": [], "count": 0})
    conn = orch._get_conn()
    import psycopg2.extras
    clauses = []
    params = []
    if pillar:
        clauses.append("pillar = %s::pillar_enum")
        params.append(pillar)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT * FROM audit_trail{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
    return JSONResponse(content={"audit": rows, "count": len(rows)})


@app.get("/agents/status")
async def agents_status():
    """Return runtime status for all 4 FSH engines + adapter contract status."""
    engines = [
        {
            "id": "claude",
            "name": "Claude Hub",
            "role": "PLAN / REVIEW reasoning hub",
            "pillars": ["logic", "content", "forge", "gridline"],
            "status": "online" if os.environ.get("ANTHROPIC_API_KEY") else "stub",
            "adapter_wired": True,
        },
        {
            "id": "hermes",
            "name": "Hermes Runtime",
            "role": "Skill execution (content, gridline)",
            "pillars": ["content", "gridline", "forge"],
            "status": "online",
            "adapter_wired": True,
        },
        {
            "id": "abacus",
            "name": "Abacus DeepAgent",
            "role": "Gridline, commerce, trading automation",
            "pillars": ["gridline", "commerce", "trading"],
            "status": "online" if os.environ.get("ABACUS_API_KEY") else "stub",
            "adapter_wired": True,
        },
        {
            "id": "manus",
            "name": "Manus Operator",
            "role": "Browser automation, web research",
            "pillars": ["commerce", "content", "gridline"],
            "status": "online" if os.environ.get("MANUS_API_KEY") else "stub",
            "adapter_wired": True,
        },
    ]

    if _store() == "sqlite":
        conn = _get_sqlite()
        for engine in engines:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM tasks WHERE execution_engine = ? AND status = 'executing'",
                (engine["id"],),
            ).fetchone()["c"]
            engine["busy"] = count > 0
            engine["active_tasks"] = count
        conn.close()

    return JSONResponse(content={"agents": engines})


@app.get("/dashboard/stats")
async def dashboard_stats():
    """Aggregated pillar health + summary for the Mission Control view."""
    if _store() == "sqlite":
        stats = _sqlite_dashboard_stats()
        return JSONResponse(content=stats)

    # Postgres path
    orch = get_orchestrator()
    if not orch.db_url:
        return JSONResponse(content={"pillars": {}, "total_tasks": 0, "pending_approvals": 0})
    conn = orch._get_conn()
    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT pillar, status, COUNT(*) as cnt
            FROM tasks
            GROUP BY pillar, status
            ORDER BY pillar, status
        """)
        rows = [dict(r) for r in cur.fetchall()]

    pillars = {}
    for r in rows:
        p = r["pillar"]
        if p not in pillars:
            pillars[p] = {"active": 0, "pending": 0, "failed": 0, "completed_24h": 0}
        s = r["status"]
        if s in ("executing", "planning", "review"):
            pillars[p]["active"] += r["cnt"]
        elif s in ("queued", "awaiting_approval"):
            pillars[p]["pending"] += r["cnt"]
        elif s in ("failed", "dead_lettered"):
            pillars[p]["failed"] += r["cnt"]
        elif s == "completed":
            pillars[p]["completed_24h"] += r["cnt"]

    return JSONResponse(content={
        "pillars": pillars,
        "total_tasks": sum(r["cnt"] for r in rows),
        "pending_approvals": sum(
            r["cnt"] for r in rows if r["status"] == "awaiting_approval"
        ),
    })


# ── Serve Dashboard Static Files ──────────────────────────────────────
_DASHBOARD_DIR = _Path(__file__).resolve().parent.parent / "dashboard"

@app.get("/")
async def dashboard_index():
    """Serve the dashboard SPA."""
    idx = _DASHBOARD_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return JSONResponse(content={"message": "FSH Command Center API running. Dashboard not found."})

# Mount static assets (CSS, JS, images) — must be LAST mount
if _DASHBOARD_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=str(_DASHBOARD_DIR), html=True), name="dashboard-static")
