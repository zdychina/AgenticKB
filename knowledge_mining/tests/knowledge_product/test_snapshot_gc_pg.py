"""R6：已发布制品引用的快照不能拖垮快照 GC（52号，真库门禁）。

这组用例来自一次真库复现：一个被已发布制品证据引用的废弃快照，会让
``reclaim_deprecated_snapshots`` 整轮抛 ForeignKeyViolation——**同批次里无人引用
的快照也不再被回收**，异常穿出 for 循环，孤儿对象回收也不执行；``app.py`` 的 GC
循环捕获后睡 24h，次日同样死在这个快照上。

所以断言的不只是「被引用的跳过」，更要紧的是「同批无引用的照常回收」——那才是
「整轮没死」的证据。

只在有一次性测试库时跑：这里要真的 DELETE 快照，内存里假不出来。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from knowledge_mining.mining.kb.services.purge_service import PurgeService

pytestmark = pytest.mark.skipif(
    os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1",
    reason="set KB_RUN_POSTGRES_ACCEPTANCE=1 to run PostgreSQL acceptance",
)

DEPRECATED_30D = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
NOW = datetime.now(timezone.utc).isoformat()


def _hex() -> str:
    return uuid.uuid4().hex


@pytest_asyncio.fixture
async def pg_pool(db_config, _ensure_schema):
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        db_config.conninfo, min_size=1, max_size=2, open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _deprecated_snapshot(pool) -> str:
    snapshot_id = f"snap_gc{_hex()[:8]}"
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_document_snapshots
               (id, domain, normalized_content_hash, raw_content_hash, mime_type,
                lifecycle_status, deprecated_at, created_at)
               VALUES (%s, 'test', %s, %s, 'text/markdown',
                       'DEPRECATED', %s, %s)""",
            (snapshot_id, _hex(), _hex(), DEPRECATED_30D, DEPRECATED_30D),
        )
    return snapshot_id


async def _publish_product_citing(pool, snapshot_id: str) -> str:
    """建一个**已发布**制品，它的证据引用这个快照。"""
    product_id = f"gc-{_hex()[:8]}"
    segment_id = f"seg_gc{_hex()[:8]}"
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_raw_segments
               (id, document_snapshot_id, segment_key, segment_index,
                raw_text, normalized_text, content_hash, normalized_hash)
               VALUES (%s, %s, 'k', 0, 't', 't', 'h', 'h')""",
            (segment_id, snapshot_id),
        )
        await conn.execute(
            """INSERT INTO kp_products
               (id, product_type, name, owner, lifecycle_status,
                current_draft_revision, released_revision, created_at, updated_at)
               VALUES (%s, 'specification_table', 'gc', 'tester', 'published',
                       1, 1, %s, %s)""",
            (product_id, NOW, NOW),
        )
        await conn.execute(
            """INSERT INTO kp_product_definitions
               (id, product_id, definition_revision, created_at, created_by)
               VALUES (%s, %s, 1, %s, 'tester')""",
            (f"kpd_{_hex()}", product_id, NOW),
        )
        await conn.execute(
            """INSERT INTO kp_revisions
               (id, product_id, revision_no, status, definition_revision, created_at)
               VALUES (%s, %s, 1, 'published', 1, %s)""",
            (f"kpr_{_hex()}", product_id, NOW),
        )
        await conn.execute(
            """INSERT INTO kp_objects
               (product_id, object_id, revision_no, type, layer, scope,
                frontmatter_json, review_status, created_at)
               VALUES (%s, 'DataProduct@a', 1, 'DataProduct', 'Data', 'cross',
                       '{}'::jsonb, 'human_confirmed', %s)""",
            (product_id, NOW),
        )
        await conn.execute(
            """INSERT INTO kp_evidence
               (id, product_id, revision_no, object_id, field_name,
                document_id, snapshot_id, segment_id, created_at)
               VALUES (%s, %s, 1, 'DataProduct@a', 'model', 'doc_gc', %s, %s, %s)""",
            (f"kpe_{_hex()}", product_id, snapshot_id, segment_id, NOW),
        )
    return product_id


async def _alive(pool, snapshot_ids: list[str]) -> set[str]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id FROM asset_document_snapshots WHERE id = ANY(%s)",
            (snapshot_ids,),
        )
        return {row["id"] for row in await cur.fetchall()}


async def _cleanup(pool, *, products: list[str], snapshots: list[str]) -> None:
    async with pool.connection() as conn:
        for product_id in products:
            await conn.execute("DELETE FROM kp_products WHERE id = %s", (product_id,))
        await conn.execute(
            "DELETE FROM asset_raw_segments WHERE document_snapshot_id = ANY(%s)",
            (snapshots,),
        )
        await conn.execute(
            "DELETE FROM asset_document_snapshots WHERE id = ANY(%s)", (snapshots,)
        )


@pytest.mark.asyncio
async def test_cited_snapshot_does_not_kill_the_whole_round(pg_pool) -> None:
    """R6 的核心断言：被引用的跳过，**同批无引用的照常回收**。

    后半句才是「整轮没死」的证据——修复前它和被引用的那个一起活了下来。
    """
    cited = await _deprecated_snapshot(pg_pool)
    free = await _deprecated_snapshot(pg_pool)
    product_id = await _publish_product_citing(pg_pool, cited)

    try:
        result = await PurgeService(pg_pool, None).reclaim_deprecated_snapshots(
            older_than_days=7,
        )
        assert result["skipped_cited"] >= 1
        assert result["reclaimed"] >= 1

        alive = await _alive(pg_pool, [cited, free])
        assert cited in alive, "被已发布制品引用的快照必须留着——制品要可回源"
        assert free not in alive, "同批无引用的快照该照常回收；还在就说明整轮死了"
    finally:
        await _cleanup(pg_pool, products=[product_id], snapshots=[cited, free])


@pytest.mark.asyncio
async def test_draft_only_citation_does_not_pin_a_snapshot(pg_pool) -> None:
    """只被草稿引用不钉住回收——草稿没有对外承诺。"""
    snapshot_id = await _deprecated_snapshot(pg_pool)
    product_id = await _publish_product_citing(pg_pool, snapshot_id)

    async with pg_pool.connection() as conn:
        # 把制品退回未发布：证据行还在，但已经不是对外那一份
        await conn.execute(
            "UPDATE kp_products SET released_revision = NULL WHERE id = %s",
            (product_id,),
        )

    try:
        service = PurgeService(pg_pool, None)
        cited = await service._snapshots_cited_by_products([snapshot_id])
        assert cited == set(), "只有已发布修订的证据才钉住快照"
    finally:
        await _cleanup(pg_pool, products=[product_id], snapshots=[snapshot_id])


@pytest.mark.asyncio
async def test_guard_query_tolerates_an_empty_batch(pg_pool) -> None:
    assert await PurgeService(pg_pool, None)._snapshots_cited_by_products([]) == set()
