"""整条制品链路跑真 PostgreSQL（52号，真库门禁）。

在这之前，服务层测试全部跑内存仓储，PG 用例只覆盖仓储层 SQL——
「建制品 → 写草稿 → 人审 → 试用 → 发布 → 消费」这条链**在真库上一次没走通过**。
中间任何一处「内存实现宽容、真库不宽容」的差异都不会被发现（已经吃过两次：
``asset_storage_objects.created_at`` 是 timestamptz 而非 TEXT，
``asset_raw_segments`` 有四列非空）。

对象存储仍用 FakeObjectStore（那一侧有自己的契约测试），但**存储对象登记表走真
PG**——``ArtifactContentStore`` 的内容寻址与去重要靠它。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from knowledge_mining.mining.agent_creation.repository import (
    CreationRepository,
    PgSegmentLocator,
)
from knowledge_mining.mining.agent_creation.service import AgentCreationService
from knowledge_mining.mining.knowledge_product.consume_service import ProductConsumeService
from knowledge_mining.mining.knowledge_product.content_store import (
    ArtifactContentStore,
    bucket_for,
)
from knowledge_mining.mining.knowledge_product.repository import (
    KnowledgeProductRepository,
    ScopeItem,
)
from knowledge_mining.mining.knowledge_product.service import KnowledgeProductService

pytestmark = pytest.mark.skipif(
    os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1",
    reason="set KB_RUN_POSTGRES_ACCEPTANCE=1 to run PostgreSQL acceptance",
)

NOW = datetime.now(timezone.utc).isoformat()


def _hex(n: int = 8) -> str:
    return uuid.uuid4().hex[:n]


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
async def source(pg_pool):
    """一份真实的资料：文档 → 快照 → 两个段落。证据外键要靠它们成立。"""
    document_id = f"doc_e2e{_hex()}"
    snapshot_id = f"snap_e2e{_hex()}"
    segments = [f"seg_e2e{_hex()}", f"seg_e2e{_hex()}"]

    async with pg_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_documents
               (id, domain, document_key, document_name, created_at)
               VALUES (%s, 'test', %s, 'e2e', %s)""",
            (document_id, document_id, NOW),
        )
        await conn.execute(
            """INSERT INTO asset_document_snapshots
               (id, domain, normalized_content_hash, raw_content_hash,
                mime_type, created_at)
               VALUES (%s, 'test', %s, %s, 'text/markdown', %s)""",
            (snapshot_id, uuid.uuid4().hex, uuid.uuid4().hex, NOW),
        )
        await conn.execute(
            """INSERT INTO asset_document_snapshot_links
               (id, document_id, document_snapshot_id, relative_path,
                source_uri, linked_at)
               VALUES (%s, %s, %s, 'p', 'u', %s)""",
            (uuid.uuid4().hex, document_id, snapshot_id, NOW),
        )
        for index, segment_id in enumerate(segments):
            await conn.execute(
                """INSERT INTO asset_raw_segments
                   (id, document_snapshot_id, segment_key, segment_index,
                    section_path, raw_text, normalized_text,
                    content_hash, normalized_hash)
                   VALUES (%s, %s, %s, %s, %s, 't', 't', %s, %s)""",
                (
                    segment_id, snapshot_id, f"k{index}", index,
                    '[{"level": 1, "title": "3 硬件规格"}]',
                    uuid.uuid4().hex, uuid.uuid4().hex,
                ),
            )
    yield {"document_id": document_id, "snapshot_id": snapshot_id, "segments": segments}


@pytest.fixture
def product_id() -> str:
    return f"e2e-{_hex()}"


@pytest_asyncio.fixture
async def stack(pg_pool, tmp_path, product_id, source):
    """真 PG 仓储 + 真存储对象登记 + Fake 对象存储。"""
    from knowledge_mining.mining.file_management.repositories_pg import (
        PgStorageObjectRepository,
    )
    from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore

    repo = KnowledgeProductRepository(pg_pool)
    content = ArtifactContentStore(
        FakeObjectStore(root_path=str(tmp_path / "objects")),
        PgStorageObjectRepository(pg_pool),
        bucket_for("agentickb-e2e-"),
    )
    products = KnowledgeProductService(repo, content)
    creation = AgentCreationService(
        CreationRepository(pg_pool), products, repo, PgSegmentLocator(pg_pool),
    )
    consume = ProductConsumeService(repo, content)

    await products.create_product(
        product_id=product_id,
        product_type="specification_table",
        name="端到端规格矩阵",
        owner="tester",
        purpose="验证整条链路在真库上走得通",
        fields={"model": {"required": True}},
        object_rules={"row_identity_rule": "同一型号一行"},
        scope_items=[ScopeItem(source["document_id"], source["snapshot_id"], ["3"])],
    )
    try:
        yield {"products": products, "creation": creation, "consume": consume, "repo": repo}
    finally:
        async with pg_pool.connection() as conn:
            await conn.execute("DELETE FROM kp_products WHERE id = %s", (product_id,))


def _card(product_id: str, model: str, source: dict, segment_index: int = 0) -> str:
    """一张对象卡，证据指向真实存在的段落。

    local 段由 registry 的 ``local_from: [model, product_version]`` 决定——
    必须等于「型号 空格 版本」，不能自己拼（52号 F4）。
    """
    return f"""---
id: "{product_id}@DomainFactSet@{model} V1"
type: DomainFactSet
product: {product_id}
name: {model} V1
fields:
  model:
    value: {model}
    evidence:
      - document_id: {source["document_id"]}
        snapshot_id: {source["snapshot_id"]}
        segment_id: {source["segments"][segment_index]}
        anchor: 3 硬件规格
        quoted_value: {model}
  product_version:
    value: V1
    evidence:
      - document_id: {source["document_id"]}
        snapshot_id: {source["snapshot_id"]}
        segment_id: {source["segments"][segment_index]}
        anchor: 3 硬件规格
        quoted_value: V1
---
# {model} V1

真库端到端用例的对象卡。

## 边
- 所属制品: [[DataProduct@{product_id}]]
"""


def _overview(product_id: str, models: list[str]) -> str:
    # 引用必须和对象卡的完整逻辑 ID 一致（local = 型号 空格 版本）——
    # 对不上就是悬挂边，制作期允许，但发布门禁会拦下
    links = "\n".join(
        f"- 包含对象卡: [[{product_id}@DomainFactSet@{model} V1]]" for model in models
    )
    return f"""---
id: DataProduct@{product_id}
type: DataProduct
name: 端到端规格矩阵
owner: tester
purpose: 验证整条链路在真库上走得通
lifecycle_status: draft
revision: 1
---
# 端到端规格矩阵

## 边
{links}
"""


# ----------------------------------------------------------------- 整条链路


@pytest.mark.asyncio
async def test_full_lifecycle_on_real_postgres(stack, product_id, source) -> None:
    """建制品 → Agent 提交 → 人审 → 试用 → 发布 → 消费，一路真库。"""
    products = stack["products"]
    creation = stack["creation"]
    consume = stack["consume"]

    models = ["M1", "M2"]
    documents = [_overview(product_id, models)] + [
        _card(product_id, model, source, index) for index, model in enumerate(models)
    ]

    # ① Agent 起制作并提交
    _instance, ticket = await creation.start_instance(product_id, created_by="tester")
    product = await products.get_product(product_id)
    receipt = await creation.submit(
        ticket.token,
        submission_id=str(uuid.uuid4()),
        based_on_draft_revision=int(product["current_draft_revision"]),
        documents=documents,
    )
    assert receipt.outcome == "accepted", receipt.message
    assert receipt.accepted_count == 3

    # ② 证据真的落进了 kp_evidence，且外键成立
    evidence = await stack["repo"].evidence_of_revision(
        product_id, int(receipt.written_revision),
    )
    assert evidence, "每个字段都该留下出处"
    assert {row["snapshot_id"] for row in evidence} == {source["snapshot_id"]}

    # ③ 人审全部确认
    rows = await products.current_rows(product_id)
    await products.review_objects(
        product_id, {oid: "approve" for oid in rows}, reviewer="tester",
    )

    # ④ 试用
    await products.record_trial(
        product_id, question="有哪些型号？", verdict="passed", tried_by="tester",
    )

    # ⑤ 门禁放行并发布
    gate = await products.publish_gate(product_id)
    assert gate.passed, [check.detail for check in gate.blocking]
    published = await products.publish(product_id)

    # ⑥ 消费面读到同一个发布修订（50号 §7.4 的验收线）
    catalog = await consume.catalog()
    mine = [row for row in catalog if row["product_id"] == product_id]
    assert mine and mine[0]["released_revision"] == published["released_revision"]

    card_id = f"{product_id}@DomainFactSet@M1 V1"  # local = 型号 空格 版本
    fetched = await consume.fetch([card_id])
    assert fetched[card_id]["ok"] is True
    assert "M1 V1" in fetched[card_id]["md"]
    assert fetched[card_id]["revision"] == published["released_revision"]

    hits = await consume.search(["M1"])
    assert any(hit.object_id == card_id for hit in hits.hits)

    outline = await consume.outline(product_id)
    assert outline["source_status"]["state"] == "ok"


@pytest.mark.asyncio
async def test_evidence_outside_the_ticket_scope_is_rejected_on_real_pg(
    stack, pg_pool, product_id, source,
) -> None:
    """越权来源要被真库的段落表反查挡住，而不是靠内存里的假数据。"""
    creation = stack["creation"]
    products = stack["products"]

    # 另造一份**不在资料范围内**的快照与段落
    other_snapshot = f"snap_out{_hex()}"
    other_segment = f"seg_out{_hex()}"
    async with pg_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_document_snapshots
               (id, domain, normalized_content_hash, raw_content_hash,
                mime_type, created_at)
               VALUES (%s, 'test', %s, %s, 'text/markdown', %s)""",
            (other_snapshot, uuid.uuid4().hex, uuid.uuid4().hex, NOW),
        )
        await conn.execute(
            """INSERT INTO asset_raw_segments
               (id, document_snapshot_id, segment_key, segment_index,
                raw_text, normalized_text, content_hash, normalized_hash)
               VALUES (%s, %s, 'k', 0, 't', 't', %s, %s)""",
            (other_segment, other_snapshot, uuid.uuid4().hex, uuid.uuid4().hex),
        )

    tampered = _card(product_id, "M9", source).replace(
        source["snapshot_id"], other_snapshot,
    ).replace(source["segments"][0], other_segment)

    _instance, ticket = await creation.start_instance(product_id, created_by="tester")
    product = await products.get_product(product_id)
    receipt = await creation.submit(
        ticket.token,
        submission_id=str(uuid.uuid4()),
        based_on_draft_revision=int(product["current_draft_revision"]),
        documents=[tampered],
    )

    assert receipt.outcome == "rejected"
    assert {row.code for row in receipt.rejected} == {"evidence_out_of_scope"}


@pytest.mark.asyncio
async def test_identical_bodies_share_one_storage_object(stack, product_id, source) -> None:
    """内容寻址去重要在真 PG 的登记表上成立，不只是在内存 fake 里。"""
    products = stack["products"]
    card = _card(product_id, "M1", source)

    first = await products.replace_draft(product_id, [card])
    second = await products.replace_draft(product_id, [card])

    before = {
        row["object_id"]: row["storage_object_id"]
        for row in await stack["repo"].list_objects(product_id, first.revision_no)
    }
    after = {
        row["object_id"]: row["storage_object_id"]
        for row in await stack["repo"].list_objects(product_id, second.revision_no)
    }
    assert before and before == after, "同一份正文重复写入应复用同一个存储对象"
