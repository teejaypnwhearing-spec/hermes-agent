"""
FSH Command Center — Approval Confirmation Endpoints
=====================================================
FastAPI router that handles the approve/reject links sent by n8n notifications.

Endpoints:
    GET  /approve/{approval_id}          → confirmation page (shows task details)
    GET  /reject/{approval_id}           → confirmation page (shows task details)
    POST /approve/{approval_id}/confirm  → submits approval decision
    POST /reject/{approval_id}/confirm   → submits rejection decision

The GET pages render a mobile-friendly HTML form so operators can confirm
their decision before it is committed. The POST handlers call the existing
/webhook/approval-decision endpoint internally to close the loop.

Mount this router in webhook_server.py:
    from orchestrator.approval_endpoints import router as approval_router
    app.include_router(approval_router)
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["approvals"])

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_BASE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FSH — {title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f1117; color: #e2e8f0; min-height: 100vh;
            display: flex; align-items: center; justify-content: center; padding: 1rem; }}
    .card {{ background: #1a1f2e; border: 1px solid #2d3748; border-radius: 12px;
             padding: 2rem; max-width: 520px; width: 100%; }}
    .badge {{ display: inline-block; padding: 0.25rem 0.75rem; border-radius: 9999px;
              font-size: 0.75rem; font-weight: 600; letter-spacing: 0.05em; margin-bottom: 1.5rem; }}
    .badge-approve {{ background: #1a4731; color: #4ade80; border: 1px solid #166534; }}
    .badge-reject  {{ background: #4a1a1a; color: #f87171; border: 1px solid #7f1d1d; }}
    h1 {{ font-size: 1.25rem; font-weight: 700; margin-bottom: 1rem; }}
    .meta {{ background: #0f1117; border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem; }}
    .meta-row {{ display: flex; justify-content: space-between; padding: 0.35rem 0;
                 border-bottom: 1px solid #2d3748; font-size: 0.875rem; }}
    .meta-row:last-child {{ border-bottom: none; }}
    .meta-label {{ color: #94a3b8; }}
    .meta-value {{ color: #e2e8f0; font-weight: 500; text-align: right; max-width: 60%; word-break: break-word; }}
    .flags {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }}
    .flag {{ background: #2d1b00; color: #fb923c; border: 1px solid #7c2d12;
             padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }}
    textarea {{ width: 100%; background: #0f1117; border: 1px solid #2d3748; color: #e2e8f0;
                border-radius: 6px; padding: 0.75rem; font-size: 0.875rem; resize: vertical;
                min-height: 80px; margin-bottom: 1rem; }}
    textarea:focus {{ outline: none; border-color: #4f46e5; }}
    .btn {{ width: 100%; padding: 0.75rem; border: none; border-radius: 8px; font-size: 1rem;
            font-weight: 600; cursor: pointer; transition: opacity 0.15s; }}
    .btn:hover {{ opacity: 0.85; }}
    .btn-approve {{ background: #16a34a; color: #fff; }}
    .btn-reject  {{ background: #dc2626; color: #fff; }}
    .result {{ text-align: center; padding: 2rem 0; }}
    .result-icon {{ font-size: 3rem; margin-bottom: 1rem; }}
    .result-msg {{ color: #94a3b8; font-size: 0.9rem; margin-top: 0.5rem; }}
  </style>
</head>
<body>
  <div class="card">
    {body}
  </div>
</body>
</html>"""


def _render_confirmation_page(
    approval_id: str,
    task_info:   dict[str, Any],
    action:      str,          # "approve" | "reject"
) -> str:
    pillar        = task_info.get("pillar", "—")
    task_type     = task_info.get("task_type", "—")
    objective     = task_info.get("objective", "—")
    approval_level = task_info.get("approval_level", "—")
    flags         = task_info.get("compliance_flags", [])
    reason        = task_info.get("reason", "")

    flag_html = "".join(f'<span class="flag">{f}</span>' for f in flags) if flags else "—"
    badge_cls = "badge-approve" if action == "approve" else "badge-reject"
    badge_txt = "✅ Approve Task" if action == "approve" else "❌ Reject Task"
    btn_cls   = "btn-approve"    if action == "approve" else "btn-reject"
    btn_txt   = "Confirm Approval" if action == "approve" else "Confirm Rejection"
    title     = "Approve Task"   if action == "approve" else "Reject Task"

    body = f"""
    <span class="badge {badge_cls}">{badge_txt}</span>
    <h1>FSH Command Center</h1>
    <div class="meta">
      <div class="meta-row">
        <span class="meta-label">Approval ID</span>
        <span class="meta-value" style="font-family:monospace;font-size:0.75rem">{approval_id}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">Pillar</span>
        <span class="meta-value">{pillar}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">Task type</span>
        <span class="meta-value">{task_type}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">Approval level</span>
        <span class="meta-value">{approval_level}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">Objective</span>
        <span class="meta-value">{objective[:120]}</span>
      </div>
      <div class="meta-row">
        <span class="meta-label">Compliance flags</span>
        <span class="meta-value"><div class="flags">{flag_html}</div></span>
      </div>
      {"<div class='meta-row'><span class='meta-label'>Reason</span><span class='meta-value'>" + reason[:200] + "</span></div>" if reason else ""}
    </div>
    <form method="POST" action="/{action}/{approval_id}/confirm">
      <textarea name="decision_reason" placeholder="Optional: add a note about this decision..."></textarea>
      <button type="submit" class="btn {btn_cls}">{btn_txt}</button>
    </form>
    """
    return _BASE_HTML.format(title=title, body=body)


def _render_result_page(action: str, approval_id: str, task_id: str) -> str:
    if action == "approved":
        icon = "✅"
        msg  = "Task approved and queued for execution."
        color = "#4ade80"
    else:
        icon = "❌"
        msg  = "Task rejected and logged to audit trail."
        color = "#f87171"

    body = f"""
    <div class="result">
      <div class="result-icon">{icon}</div>
      <h1 style="color:{color}">{action.capitalize()}</h1>
      <p class="result-msg">{msg}</p>
      <p class="result-msg" style="margin-top:0.5rem;font-family:monospace;font-size:0.75rem">
        approval_id: {approval_id}<br>task_id: {task_id}
      </p>
    </div>
    """
    return _BASE_HTML.format(title=action.capitalize(), body=body)


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------

def _get_task_info(approval_id: str) -> dict[str, Any]:
    """
    Fetch task details for the approval confirmation page.
    Falls back to minimal info if the DB is unavailable (e.g., during tests).
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return {"approval_id": approval_id}

    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(db_url)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT ar.approval_id, ar.task_id, ar.approval_level, ar.reason,
                   t.pillar, t.task_type, t.objective, t.compliance_flags
            FROM   approval_requests ar
            JOIN   tasks t ON t.task_id = ar.task_id
            WHERE  ar.approval_id = %s
            """,
            (approval_id,),
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else {"approval_id": approval_id}
    except Exception:
        return {"approval_id": approval_id}


async def _post_decision(
    request:         Request,
    approval_id:     str,
    decision:        str,
    decision_reason: str,
) -> dict[str, Any]:
    """
    Call the existing /webhook/approval-decision endpoint internally.
    """
    from orchestrator.orchestrator import FSHOrchestrator

    db_url = os.environ.get("DATABASE_URL")
    n8n_url = os.environ.get("N8N_BASE_URL", "")

    # Fetch task_id from DB
    task_info = _get_task_info(approval_id)
    task_id   = task_info.get("task_id", "unknown")

    if db_url:
        try:
            orch = FSHOrchestrator(db_url=db_url, n8n_base_url=n8n_url)
            if decision == "approved":
                token = f"approved:{approval_id}"
                return orch.resume_after_approval(task_id=task_id, approval_token=token)
            else:
                orch._update_task_status(task_id, "rejected")
                return {"task_id": task_id, "status": "rejected"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # No DB — return a stub response (useful in tests / local dev without Postgres)
    return {"task_id": task_id, "approval_id": approval_id, "status": decision}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/approve/{approval_id}", response_class=HTMLResponse)
async def approve_page(approval_id: str) -> HTMLResponse:
    """Render the approval confirmation page."""
    task_info = _get_task_info(approval_id)
    html = _render_confirmation_page(approval_id, task_info, action="approve")
    return HTMLResponse(content=html)


@router.get("/reject/{approval_id}", response_class=HTMLResponse)
async def reject_page(approval_id: str) -> HTMLResponse:
    """Render the rejection confirmation page."""
    task_info = _get_task_info(approval_id)
    html = _render_confirmation_page(approval_id, task_info, action="reject")
    return HTMLResponse(content=html)


@router.post("/approve/{approval_id}/confirm", response_class=HTMLResponse)
async def approve_confirm(
    request:         Request,
    approval_id:     str,
    decision_reason: str = Form(default=""),
) -> HTMLResponse:
    """Process the approval and render the result page."""
    result = await _post_decision(request, approval_id, "approved", decision_reason)
    task_id = result.get("task_id", "unknown")
    html = _render_result_page("approved", approval_id, task_id)
    return HTMLResponse(content=html)


@router.post("/reject/{approval_id}/confirm", response_class=HTMLResponse)
async def reject_confirm(
    request:         Request,
    approval_id:     str,
    decision_reason: str = Form(default=""),
) -> HTMLResponse:
    """Process the rejection and render the result page."""
    result = await _post_decision(request, approval_id, "rejected", decision_reason)
    task_id = result.get("task_id", "unknown")
    html = _render_result_page("rejected", approval_id, task_id)
    return HTMLResponse(content=html)
