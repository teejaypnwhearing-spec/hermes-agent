"""
FSH Command Center — Webhook Server
=====================================
Lightweight FastAPI server that receives callbacks from n8n for:
  - Approval decisions (human approved/rejected a task)
  - Retry queue triggers
  - Status queries

This replaces the removed blocking poll in AbacusAdapter.

Run:
    cd fsh-command-center
    uvicorn orchestrator.webhook_server:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    GET  /approve/{approval_id}      ← confirmation page (approve)
    GET  /reject/{approval_id}       ← confirmation page (reject)
    POST /approve/{approval_id}/confirm  ← submit approval
    POST /reject/{approval_id}/confirm   ← submit rejection
    POST /webhook/approval-decision  ← n8n posts here when human decides
    POST /webhook/retry              ← n8n retry queue trigger
    GET  /health                     ← health check
    GET  /tasks/{task_id}            ← task status lookup
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure fsh-command-center/ is on sys.path so adapters/config can be imported
_FSH_ROOT = str(Path(__file__).parent.parent)
if _FSH_ROOT not in sys.path:
    sys.path.insert(0, _FSH_ROOT)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from orchestrator.orchestrator import FSHOrchestrator
from orchestrator.approval_endpoints import router as approval_router

app = FastAPI(
    title="FSH Command Center Webhook Server",
    description="Approval callbacks and async task management for FSH",
    version="1.0.1",
)

# Mount approval confirmation pages (GET /approve/{id}, GET /reject/{id}, etc.)
app.include_router(approval_router)

_orchestrator: FSHOrchestrator | None = None


def get_orchestrator() -> FSHOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = FSHOrchestrator(
            db_url       = os.environ.get("DATABASE_URL"),
            n8n_base_url = os.environ.get("N8N_BASE_URL", ""),
        )
    return _orchestrator


# ── Request models ────────────────────────────────────────────────────────────

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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "fsh-command-center", "version": "1.0.1"}


@app.post("/tasks/submit")
async def submit_task(payload: TaskSubmitPayload):
    """Submit a new task to the FSH orchestrator."""
    orch = get_orchestrator()
    result = orch.submit(payload.model_dump(exclude_none=True))
    status_code = 202 if result.get("status") == "awaiting_approval" else 200
    return JSONResponse(content=result, status_code=status_code)


@app.post("/webhook/approval-decision")
async def approval_decision(payload: ApprovalDecisionPayload):
    """
    Called by n8n when a human approves or rejects a task.
    On approval: re-submits the task with the approval_token.
    On rejection: marks task as rejected in DB.
    """
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

    orch = get_orchestrator()
    task_row = orch._fetch_task(task_id)
    if not task_row:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    result = orch.submit(task_row)
    return JSONResponse(content=result)


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Look up task status by ID."""
    orch = get_orchestrator()
    task_row = orch._fetch_task(task_id)
    if not task_row:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    for k, v in task_row.items():
        if hasattr(v, "isoformat"):
            task_row[k] = v.isoformat()
    return JSONResponse(content=task_row)
