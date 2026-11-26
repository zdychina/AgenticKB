"""Tests for POST /api/v1/tasks/retry-batch (BUG-06 批量重放).

锁定契约：dry_run 只预览不动库；ids 模式只重排可重试终态；筛选模式
status/task_type/created_after/limit 圈定；重排语义与单任务 retry_task
完全一致（queued + attempt_count=0 + 清 started/finished/lease）；圈定
与执行之间的状态漂移计入 skipped；非可重试 status 与非法时间戳 400。
"""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from llm_service.api.tasks import retry_batch_tasks
from llm_service.models import RetryBatchRequest


def _task_row(task_id: str, status: str, **overrides) -> dict:
    row = {
        "id": task_id,
        "task_type": "embedding",
        "status": status,
        "attempt_count": 3,
        "created_at": "2026-11-10T00:00:00+00:00",
    }
    row.update(overrides)
    return row


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, db):
        self._db = db

    async def execute(self, sql, params):
        return _FakeCursor(self._db.apply_update(sql, params))


class _FakePool:
    def __init__(self, db):
        self._db = db

    def connection(self):
        return self._PoolCtx(self._db)

    class _PoolCtx:
        def __init__(self, db):
            self._db = db

        async def __aenter__(self):
            return _FakeConnection(self._db)

        async def __aexit__(self, *exc):
            return None


class _FakeDB:
    """Dispatches the route's two SELECT shapes and the guarded UPDATE."""

    def __init__(self, tasks=()):
        self._tasks = {t["id"]: dict(t) for t in tasks}
        self.updates = []
        self.pool = _FakePool(self)

    async def fetchall(self, sql, params):
        rows = list(self._tasks.values())
        if sql.startswith("SELECT id FROM agent_llm_tasks"):
            if "id = ANY(%s)" in sql:
                ids, statuses = params
                rows = [t for t in rows if t["id"] in ids and t["status"] in statuses]
            else:
                statuses = params[0]
                idx = 1
                rows = [t for t in rows if t["status"] in statuses]
                if "task_type = %s" in sql:
                    task_type = params[idx]
                    idx += 1
                    rows = [t for t in rows if t["task_type"] == task_type]
                if "created_at >= %s" in sql:
                    from datetime import datetime
                    cutoff = params[idx]
                    idx += 1
                    rows = [
                        t for t in rows
                        if datetime.fromisoformat(t["created_at"]) >= cutoff
                    ]
                limit = params[idx]
                rows = rows[:limit]
            rows = sorted(rows, key=lambda t: t["created_at"])
            return [{"id": t["id"]} for t in rows]
        raise AssertionError(f"unexpected SQL in retry_batch: {sql}")

    def apply_update(self, sql, params):
        self.updates.append((sql, params))
        assert "SET status = 'queued', attempt_count = 0" in sql
        assert "started_at = NULL" in sql and "lease_expires_at = NULL" in sql
        matched_ids, guard = params[2], params[3]
        updated = []
        for task_id in matched_ids:
            task = self._tasks.get(task_id)
            if task is None or task["status"] not in guard:
                continue
            task["status"] = "queued"
            task["attempt_count"] = 0
            updated.append({"id": task_id})
        return updated


class _FakeBus:
    def __init__(self):
        self.events = []

    async def emit(self, task_id, event_type, message):
        self.events.append((task_id, event_type, message))


def _request(db, bus):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(db=db, llm_service=SimpleNamespace(_bus=bus))),
    )


@pytest.fixture
def bus():
    return _FakeBus()


pytestmark = pytest.mark.asyncio


async def test_dry_run_previews_matched_without_touching_db(bus):
    db = _FakeDB(tasks=[
        _task_row("t-dl-1", "dead_letter"),
        _task_row("t-dl-2", "dead_letter"),
        _task_row("t-ok", "succeeded"),
        _task_row("t-run", "running"),
    ])
    out = await retry_batch_tasks(
        RetryBatchRequest(status=["dead_letter"], dry_run=True), _request(db, bus)
    )
    data = out["data"]
    assert data["dry_run"] is True
    assert data["matched"] == 2
    assert data["requeued"] == 0
    assert sorted(data["task_ids"]) == ["t-dl-1", "t-dl-2"]
    assert db.updates == []  # 预览不得动库
    assert bus.events == []
    assert db._tasks["t-dl-1"]["status"] == "dead_letter"


async def test_ids_mode_requeues_only_retryable_and_resets_attempts(bus):
    db = _FakeDB(tasks=[
        _task_row("t-failed", "failed", attempt_count=2),
        _task_row("t-dl", "dead_letter", attempt_count=3),
        _task_row("t-run", "running", attempt_count=1),
        _task_row("t-ok", "succeeded", attempt_count=1),
    ])
    out = await retry_batch_tasks(
        RetryBatchRequest(
            task_ids=["t-failed", "t-dl", "t-run", "t-ok", "t-missing"], dry_run=False
        ),
        _request(db, bus),
    )
    data = out["data"]
    # matched SELECT 已过滤：不可重试/不存在的 id 不进集合
    assert data["matched"] == 2
    assert data["requeued"] == 2
    assert data["skipped"] == 0
    assert sorted(data["task_ids"]) == ["t-dl", "t-failed"]
    assert db._tasks["t-failed"]["status"] == "queued"
    assert db._tasks["t-failed"]["attempt_count"] == 0
    assert db._tasks["t-dl"]["attempt_count"] == 0
    # 未圈定的运行中/成功任务原样
    assert db._tasks["t-run"]["status"] == "running"
    assert db._tasks["t-ok"]["status"] == "succeeded"
    assert [(t, e) for t, e, _ in bus.events] == [("t-failed", "submitted"), ("t-dl", "submitted")]
    assert all(msg == "task retried (batch)" for _, _, msg in bus.events)


async def test_status_drift_between_select_and_update_counts_as_skipped(bus):
    class _DriftDB(_FakeDB):
        async def fetchall(self, sql, params):
            rows = await super().fetchall(sql, params)
            if "id = ANY(%s)" in sql:
                # 模拟圈定后、UPDATE 前任务被 worker 认领
                self._tasks["t-failed"]["status"] = "running"
            return rows

    db = _DriftDB(tasks=[_task_row("t-failed", "failed"), _task_row("t-dl", "dead_letter")])
    out = await retry_batch_tasks(
        RetryBatchRequest(task_ids=["t-failed", "t-dl"], dry_run=False), _request(db, bus)
    )
    data = out["data"]
    assert data["matched"] == 2
    assert data["requeued"] == 1
    assert data["skipped"] == 1
    assert data["task_ids"] == ["t-dl"]  # 漂移任务不出现在重排结果里
    assert db._tasks["t-failed"]["status"] == "running"


async def test_filter_mode_applies_type_time_window_and_limit(bus):
    db = _FakeDB(tasks=[
        _task_row("t-old-emb", "failed", created_at="2026-11-01T00:00:00+00:00"),
        _task_row("t-new-emb-1", "failed", created_at="2026-11-20T00:00:00+00:00"),
        _task_row("t-new-emb-2", "dead_letter", created_at="2026-11-21T00:00:00+00:00"),
        _task_row("t-new-chat", "failed", task_type="chat", created_at="2026-11-22T00:00:00+00:00"),
    ])
    out = await retry_batch_tasks(
        RetryBatchRequest(
            task_type="embedding",
            created_after="2026-11-10T00:00:00Z",
            limit=2,
            dry_run=True,
        ),
        _request(db, bus),
    )
    data = out["data"]
    assert data["matched"] == 2  # 窗口外/异类型排除，limit 截断
    assert data["task_ids"] == ["t-new-emb-1", "t-new-emb-2"]


async def test_real_run_with_no_match_skips_update_entirely(bus):
    db = _FakeDB(tasks=[_task_row("t-ok", "succeeded")])
    out = await retry_batch_tasks(
        RetryBatchRequest(status=["failed"], dry_run=False), _request(db, bus)
    )
    assert out["data"]["matched"] == 0
    assert out["data"]["requeued"] == 0
    assert db.updates == []


async def test_rejects_non_retryable_status_filter(bus):
    from fastapi import HTTPException

    db = _FakeDB()
    with pytest.raises(HTTPException) as exc_info:
        await retry_batch_tasks(
            RetryBatchRequest(status=["running", "failed"]), _request(db, bus)
        )
    assert exc_info.value.status_code == 400
    assert "running" in exc_info.value.detail


async def test_rejects_malformed_created_after(bus):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await retry_batch_tasks(
            RetryBatchRequest(created_after="not-a-date"), _request(_FakeDB(), bus)
        )
    assert exc_info.value.status_code == 400


def test_request_model_rejects_empty_task_ids_and_caps():
    with pytest.raises(ValidationError):
        RetryBatchRequest(task_ids=[])
    with pytest.raises(ValidationError):
        RetryBatchRequest(task_ids=[f"t-{i}" for i in range(257)])
    # 默认值：dry_run 必须默认 True（漏传只预览），status 默认死信+失败
    defaults = RetryBatchRequest()
    assert defaults.dry_run is True
    assert defaults.status == ["dead_letter", "failed"]
    assert defaults.limit == 500
