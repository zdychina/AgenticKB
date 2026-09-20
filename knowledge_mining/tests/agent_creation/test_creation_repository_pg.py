"""PG 门禁 smoke：019 的 DDL 与 ``CreationRepository`` 的 SQL 是否对齐。

只在有一次性测试库时跑。这里专测**只有真库才能验证**的：认领式幂等真的靠主键挡住
并发、票据哈希唯一、级联删除、``PgSegmentLocator`` 读得到 ``asset_raw_segments``。
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio

from knowledge_mining.mining.agent_creation.repository import (
    CreationRepository,
    PgSegmentLocator,
)
from knowledge_mining.mining.knowledge_product.repository import (
    KnowledgeProductRepository,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1",
    reason="set KB_RUN_POSTGRES_ACCEPTANCE=1 to run PostgreSQL acceptance",
)


@pytest_asyncio.fixture
async def pg_pool(db_config, _ensure_schema):
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        db_config.conninfo, min_size=1, max_size=4, open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def instance(pg_pool):
    products = KnowledgeProductRepository(pg_pool)
    creation = CreationRepository(pg_pool)
    product_id = f"pg-p2-{uuid.uuid4().hex[:8]}"
    await products.create_product(
        product_id=product_id, product_type="specification_table",
        name="PG P2 smoke", owner="tester", purpose=None,
        fields={}, object_rules={}, examples=[], trial_questions=[], scope_items=(),
    )
    row = await creation.create_instance(
        product_id=product_id, definition_revision=1,
        base_draft_revision=1, created_by="tester",
    )
    return creation, row


@pytest.mark.asyncio
async def test_instance_round_trip(instance) -> None:
    repo, row = instance
    assert row["status"] == "pending"

    await repo.set_instance_status(row["id"], "running")
    await repo.bind_harness_session(row["id"], "dsh-session-1")
    fetched = await repo.get_instance(row["id"])
    assert fetched["status"] == "running"
    assert fetched["harness_session_id"] == "dsh-session-1"


@pytest.mark.asyncio
async def test_ticket_hash_is_unique(instance) -> None:
    import psycopg

    repo, row = instance
    token_hash = uuid.uuid4().hex
    await repo.insert_ticket(
        ticket_id=f"kpt_{uuid.uuid4().hex}", instance_id=row["id"],
        product_id=row["product_id"], token_hash=token_hash,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        await repo.insert_ticket(
            ticket_id=f"kpt_{uuid.uuid4().hex}", instance_id=row["id"],
            product_id=row["product_id"], token_hash=token_hash,
            expires_at="2099-01-01T00:00:00+00:00",
        )


@pytest.mark.asyncio
async def test_revoking_marks_only_active_tickets(instance) -> None:
    repo, row = instance
    for _ in range(2):
        await repo.insert_ticket(
            ticket_id=f"kpt_{uuid.uuid4().hex}", instance_id=row["id"],
            product_id=row["product_id"], token_hash=uuid.uuid4().hex,
            expires_at="2099-01-01T00:00:00+00:00",
        )
    assert await repo.revoke_tickets(row["id"], "任务取消") == 2
    assert await repo.revoke_tickets(row["id"], "再撤一次") == 0


@pytest.mark.asyncio
async def test_claim_is_idempotent_under_true_concurrency(instance) -> None:
    """认领式幂等：并发抢同一个 submission_id，只有一个抢到。"""
    repo, row = instance
    submission_id = str(uuid.uuid4())

    results = await asyncio.gather(*[
        repo.claim_submission(
            product_id=row["product_id"], submission_id=submission_id,
            instance_id=row["id"], based_on_draft_revision=1,
        )
        for _ in range(6)
    ])
    assert sum(1 for claimed in results if claimed) == 1

    stored = await repo.get_submission(row["product_id"], submission_id)
    assert stored["outcome"] == "pending"


@pytest.mark.asyncio
async def test_finalize_only_touches_the_pending_row(instance) -> None:
    repo, row = instance
    submission_id = str(uuid.uuid4())
    await repo.claim_submission(
        product_id=row["product_id"], submission_id=submission_id,
        instance_id=row["id"], based_on_draft_revision=1,
    )
    await repo.finalize_submission(
        product_id=row["product_id"], submission_id=submission_id,
        outcome="accepted", written_revision=2, accepted_count=3,
        rejected_count=0, receipt={"message": "ok"},
    )
    # 第二次 finalize 不该覆盖已终结的回执
    await repo.finalize_submission(
        product_id=row["product_id"], submission_id=submission_id,
        outcome="rejected", written_revision=None, accepted_count=0,
        rejected_count=9, receipt={"message": "should not land"},
    )
    stored = await repo.get_submission(row["product_id"], submission_id)
    assert stored["outcome"] == "accepted"
    assert stored["accepted_count"] == 3
    assert stored["receipt_json"]["message"] == "ok"


@pytest.mark.asyncio
async def test_cancelling_the_product_cascades_to_creation_rows(pg_pool, instance) -> None:
    repo, row = instance
    await repo.insert_ticket(
        ticket_id=f"kpt_{uuid.uuid4().hex}", instance_id=row["id"],
        product_id=row["product_id"], token_hash=uuid.uuid4().hex,
        expires_at="2099-01-01T00:00:00+00:00",
    )
    async with pg_pool.connection() as conn:
        await conn.execute("DELETE FROM kp_products WHERE id = %s", (row["product_id"],))

    assert await repo.get_instance(row["id"]) is None


@pytest.mark.asyncio
async def test_segment_locator_reads_real_segments(pg_pool) -> None:
    """PgSegmentLocator 是「不信任 Agent 自报来源」的落点——它必须读到真表。"""
    import json

    snapshot_id = f"snap_{uuid.uuid4().hex[:8]}"
    segment_id = f"seg_{uuid.uuid4().hex[:8]}"
    async with pg_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_document_snapshots
               (id, domain, normalized_content_hash, raw_content_hash, mime_type, created_at)
               VALUES (%s, 'test', %s, %s, 'text/markdown', now()::text)""",
            (snapshot_id, uuid.uuid4().hex, uuid.uuid4().hex),
        )
        await conn.execute(
            """INSERT INTO asset_raw_segments
               (id, document_snapshot_id, segment_key, segment_index, section_path)
               VALUES (%s, %s, 'k', 0, %s)""",
            (
                segment_id, snapshot_id,
                json.dumps([{"level": 1, "title": "3 硬件"},
                            {"level": 2, "title": "3.1 硬件规格"}]),
            ),
        )

    located = await PgSegmentLocator(pg_pool).locate([segment_id, "seg_missing"])
    assert set(located) == {segment_id}
    assert located[segment_id].snapshot_id == snapshot_id
    assert located[segment_id].section_titles == ("3 硬件", "3.1 硬件规格")
