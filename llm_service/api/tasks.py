from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from llm_service.models import (
    BatchStatusRequest,
    EmbeddingTaskRequest,
    RetryBatchRequest,
    RerankTaskRequest,
    TaskSubmitRequest,
)

router = APIRouter(prefix="/api/v1")


@router.post("/tasks")
async def submit_task(body: TaskSubmitRequest, request: Request):
    svc = request.app.state.llm_service
    task_id = await svc.submit(
        body.caller_service,
        body.knowledge_domain,
        body.pipeline_stage,
        template_key=body.template_key,
        input=body.input,
        messages=body.messages,
        params=body.params,
        expected_output_type=body.expected_output_type,
        output_schema=body.output_schema,
        idempotency_key=body.idempotency_key,
        metadata=body.metadata,
        max_attempts=body.max_attempts,
        priority=body.priority,
        model=body.model,
    )
    task = await svc.get_task(task_id)
    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "status": task["status"],
            "idempotency_key": task.get("idempotency_key"),
            "created_at": task["created_at"],
        },
    }


@router.post("/tasks/embed")
async def submit_embedding_task(body: EmbeddingTaskRequest, request: Request):
    svc = request.app.state.llm_service
    texts = body.input if isinstance(body.input, list) else [body.input]
    task_id = await svc.submit_embedding(
        texts,
        model=body.model,
        dimensions=body.dimensions,
        caller_service=body.caller_service,
        knowledge_domain=body.knowledge_domain,
        pipeline_stage=body.pipeline_stage,
        idempotency_key=body.idempotency_key,
        max_attempts=body.max_attempts,
        priority=body.priority,
    )
    task = await svc.get_task(task_id)
    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "status": task["status"],
            "created_at": task["created_at"],
        },
    }


@router.post("/tasks/rerank")
async def submit_rerank_task(body: RerankTaskRequest, request: Request):
    svc = request.app.state.llm_service
    task_id = await svc.submit_rerank(
        body.query,
        body.documents,
        model=body.model,
        top_n=body.top_n,
        caller_service=body.caller_service,
        knowledge_domain=body.knowledge_domain,
        pipeline_stage=body.pipeline_stage,
        idempotency_key=body.idempotency_key,
        max_attempts=body.max_attempts,
        priority=body.priority,
    )
    task = await svc.get_task(task_id)
    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "status": task["status"],
            "created_at": task["created_at"],
        },
    }


@router.post("/execute")
async def execute_task(body: TaskSubmitRequest, request: Request):
    svc = request.app.state.llm_service
    result = await svc.execute(
        body.caller_service,
        body.knowledge_domain,
        body.pipeline_stage,
        template_key=body.template_key,
        input=body.input,
        messages=body.messages,
        params=body.params,
        expected_output_type=body.expected_output_type,
        output_schema=body.output_schema,
        idempotency_key=body.idempotency_key,
        metadata=body.metadata,
        max_attempts=body.max_attempts,
        priority=body.priority,
        model=body.model,
    )
    return {"success": True, "data": result}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    svc = request.app.state.llm_service
    task = await svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    # Include related data for task detail view
    db = request.app.state.db
    result = await svc.get_result(task_id)
    attempts = await svc.get_attempts(task_id)
    events = await svc.get_events(task_id)
    req_row = await db.fetchone("SELECT * FROM agent_llm_requests WHERE task_id = %s", (task_id,))
    return {
        "success": True,
        "data": {
            "task": dict(task),
            "request": dict(req_row) if req_row else None,
            "result": result,
            "attempts": attempts,
            "events": events,
        },
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    svc = request.app.state.llm_service
    await svc.cancel(task_id)
    return {"success": True, "data": {"task_id": task_id, "status": "cancelled"}}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, request: Request):
    """Retry a failed/dead_letter task by re-queuing it."""
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT id, status, attempt_count, max_attempts FROM agent_llm_tasks WHERE id = %s",
        (task_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    if row["status"] not in ("failed", "dead_letter", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot retry task in '{row['status']}' status")

    import json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """UPDATE agent_llm_tasks
           SET status = 'queued', attempt_count = 0, available_at = %s,
               started_at = NULL, finished_at = NULL, lease_expires_at = NULL, updated_at = %s
           WHERE id = %s""",
        (now, now, task_id),
    )
    svc = request.app.state.llm_service
    await svc._bus.emit(task_id, "submitted", "task retried")
    return {"success": True, "data": {"task_id": task_id, "status": "queued"}}


class BatchCancelRequest(BaseModel):
    task_ids: list[str]


@router.post("/tasks/batch-cancel")
async def batch_cancel_tasks(body: BatchCancelRequest, request: Request):
    """Cancel multiple queued tasks at once."""
    if not body.task_ids:
        raise HTTPException(status_code=400, detail="task_ids cannot be empty")
    db = request.app.state.db
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ", ".join(["%s"] * len(body.task_ids))
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            f"UPDATE agent_llm_tasks SET status = 'cancelled', finished_at = %s, updated_at = %s "
            f"WHERE id IN ({placeholders}) AND status = 'queued'",
            (now, now, *body.task_ids),
        )
        cancelled_count = cur.rowcount
    return {"success": True, "data": {"cancelled_count": cancelled_count}}


# 与 retry_task 的单任务守卫保持同集：只有这三种终态允许重排。
_RETRYABLE_STATUSES = ("failed", "dead_letter", "cancelled")


@router.post("/tasks/retry-batch")
async def retry_batch_tasks(body: RetryBatchRequest, request: Request):
    """Batch replay: requeue terminal tasks with retry_task's exact semantics.

    两种圈定方式：task_ids（UI 多选/本页）或 status / task_type /
    created_after 筛选 + limit 封顶（运维批量重放路径）。dry_run=true 只回
    matched/task_ids 预览。重排 UPDATE 带 status 守卫——圈定与执行之间状态
    漂移的任务计入 skipped，不会把 running/succeeded 任务拉回队列。
    """
    from datetime import datetime, timezone

    invalid = [s for s in body.status if s not in _RETRYABLE_STATUSES]
    if invalid:
        raise HTTPException(400, f"status filter contains non-retryable values: {invalid}")
    created_after = None
    if body.created_after:
        try:
            created_after = datetime.fromisoformat(body.created_after.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "created_after must be an ISO-8601 timestamp")

    db = request.app.state.db
    if body.task_ids is not None:
        matched_rows = await db.fetchall(
            "SELECT id FROM agent_llm_tasks "
            "WHERE id = ANY(%s) AND status = ANY(%s) ORDER BY created_at",
            (body.task_ids, list(_RETRYABLE_STATUSES)),
        )
        guard = list(_RETRYABLE_STATUSES)
    else:
        conditions = ["status = ANY(%s)"]
        params: list = [list(body.status)]
        if body.task_type:
            conditions.append("task_type = %s")
            params.append(body.task_type)
        if created_after is not None:
            conditions.append("created_at >= %s")
            params.append(created_after)
        matched_rows = await db.fetchall(
            f"SELECT id FROM agent_llm_tasks WHERE {' AND '.join(conditions)} "
            f"ORDER BY created_at LIMIT %s",
            tuple(params) + (body.limit,),
        )
        guard = list(body.status)

    matched_ids = [row["id"] for row in matched_rows]
    if body.dry_run or not matched_ids:
        return {
            "success": True,
            "data": {
                "dry_run": body.dry_run,
                "matched": len(matched_ids),
                "requeued": 0,
                "skipped": 0,
                "task_ids": matched_ids,
            },
        }

    now = datetime.now(timezone.utc).isoformat()
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            """UPDATE agent_llm_tasks
               SET status = 'queued', attempt_count = 0, available_at = %s,
                   started_at = NULL, finished_at = NULL, lease_expires_at = NULL, updated_at = %s
               WHERE id = ANY(%s) AND status = ANY(%s)
               RETURNING id""",
            (now, now, matched_ids, guard),
        )
        requeued_ids = [row["id"] for row in await cur.fetchall()]
    svc = request.app.state.llm_service
    for task_id in requeued_ids:
        await svc._bus.emit(task_id, "submitted", "task retried (batch)")
    return {
        "success": True,
        "data": {
            "dry_run": False,
            "matched": len(matched_ids),
            "requeued": len(requeued_ids),
            "skipped": len(matched_ids) - len(requeued_ids),
            "task_ids": requeued_ids,
        },
    }


@router.post("/tasks/batch-status")
async def batch_status_tasks(body: BatchStatusRequest, request: Request):
    """Batch task status for polling callers — one request replaces N x GET /tasks/{id}.

    Succeeded tasks carry their result inline (include_results=false omits it),
    failed/dead_letter tasks carry the latest failed attempt's error, and
    unknown ids land in not_found.
    """
    if not body.task_ids:
        raise HTTPException(status_code=400, detail="task_ids cannot be empty")
    svc = request.app.state.llm_service
    data = await svc.get_tasks_batch(body.task_ids, include_results=body.include_results)
    return {"success": True, "data": data}
