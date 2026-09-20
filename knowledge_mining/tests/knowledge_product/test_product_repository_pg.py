"""PG 门禁 smoke：018 的 DDL 与 ``KnowledgeProductRepository`` 的 SQL 是否对齐。

只在有一次性测试库时跑（``KB_RUN_POSTGRES_ACCEPTANCE=1`` + ``*_test`` 库名），
没有 PG 就整体 skip——行为覆盖在内存仓储的服务层测试里，那套与本套共享同一组
方法签名。

这里专测**只有真库才能验证**的三件事：
1. 复合外键（``kp_objects``/``kp_edges``/``kp_evidence`` → ``kp_revisions``）真的挂住；
2. ``uq_kp_revisions_published`` 部分唯一索引真的只允许一个已发布修订；
3. ``kp_edges`` 的 PK 真的对 ``(from, relation, to)`` 去重。
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from knowledge_mining.mining.knowledge_product.evidence import EvidenceRef
from knowledge_mining.mining.knowledge_product.models import Edge
from knowledge_mining.mining.knowledge_product.repository import (
    KnowledgeProductRepository,
    ObjectRow,
    ScopeItem,
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
        db_config.conninfo,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def repo(pg_pool) -> KnowledgeProductRepository:
    return KnowledgeProductRepository(pg_pool)


@pytest_asyncio.fixture
async def product_id(repo: KnowledgeProductRepository) -> str:
    """每个用例自己的制品 id，避免与别的用例/遗留数据相撞。"""
    pid = f"pg-smoke-{uuid.uuid4().hex[:8]}"
    await repo.create_product(
        product_id=pid,
        product_type="specification_table",
        name="PG smoke",
        owner="tester",
        purpose=None,
        fields={"model": {"required": True}},
        object_rules={"row_identity": ["model"]},
        examples=[],
        trial_questions=[],
        # snapshot_id 有外键，smoke 不造快照，所以资料范围留空
        scope_items=(),
    )
    return pid


def _row(object_id: str, *, scope: str = "cross") -> ObjectRow:
    return ObjectRow(
        object_id=object_id,
        type="DataProduct",
        layer="Data",
        scope=scope,
        name=object_id,
        frontmatter={"id": object_id, "type": "DataProduct"},
        storage_object_id=None,
    )


@pytest.mark.asyncio
async def test_create_product_seeds_definition_and_first_revision(repo, product_id) -> None:
    product = await repo.get_product(product_id)
    assert product is not None
    assert product["lifecycle_status"] == "draft"
    assert product["current_draft_revision"] == 1

    revisions = await repo.list_revisions(product_id)
    assert [r["revision_no"] for r in revisions] == [1]
    assert revisions[0]["status"] == "draft"


@pytest.mark.asyncio
async def test_write_draft_revision_round_trip(repo, product_id) -> None:
    revision_no = await repo.write_draft_revision(
        product_id=product_id,
        objects=[_row("DataProduct@a"), _row("DataProduct@b")],
        edges=[Edge("DataProduct@a", "依赖", "DataProduct@b")],
        evidence=[],
    )
    assert revision_no == 2

    objects = await repo.list_objects(product_id, revision_no)
    assert {o["object_id"] for o in objects} == {"DataProduct@a", "DataProduct@b"}
    assert objects[0]["frontmatter_json"]["type"] == "DataProduct"

    edges = await repo.list_edges(product_id, revision_no)
    assert edges == [
        {"from_id": "DataProduct@a", "relation": "依赖", "to_id": "DataProduct@b"}
    ]

    product = await repo.get_product(product_id)
    assert product["current_draft_revision"] == revision_no


@pytest.mark.asyncio
async def test_edge_primary_key_dedupes_same_triple(repo, product_id) -> None:
    """``ON CONFLICT DO NOTHING`` + PK：同一条 (from, relation, to) 只留一条。"""
    revision_no = await repo.write_draft_revision(
        product_id=product_id,
        objects=[_row("DataProduct@a")],
        edges=[
            Edge("DataProduct@a", "依赖", "DataProduct@b"),
            Edge("DataProduct@a", "依赖", "DataProduct@b"),
        ],
        evidence=[],
    )
    assert len(await repo.list_edges(product_id, revision_no)) == 1


@pytest.mark.asyncio
async def test_dangling_edge_target_is_allowed(repo, product_id) -> None:
    """to_id 可以指向本修订尚不存在的对象——悬挂边不该被外键挡掉（52号 A5）。"""
    revision_no = await repo.write_draft_revision(
        product_id=product_id,
        objects=[_row("DataProduct@a")],
        edges=[Edge("DataProduct@a", "待接入", "RulePackage@not-built-yet")],
        evidence=[],
    )
    edges = await repo.list_edges(product_id, revision_no)
    assert edges[0]["to_id"] == "RulePackage@not-built-yet"


@pytest.mark.asyncio
async def test_backlinks_index_answers_who_references_me(repo, product_id) -> None:
    revision_no = await repo.write_draft_revision(
        product_id=product_id,
        objects=[_row("DataProduct@a"), _row("DataProduct@b")],
        edges=[
            Edge("DataProduct@a", "依赖本体", "OntologyModule@shared"),
            Edge("DataProduct@b", "依赖本体", "OntologyModule@shared"),
        ],
        evidence=[],
    )
    backlinks = await repo.list_backlinks("OntologyModule@shared")
    mine = [b for b in backlinks if b["product_id"] == product_id
            and b["revision_no"] == revision_no]
    assert {b["from_id"] for b in mine} == {"DataProduct@a", "DataProduct@b"}


@pytest.mark.asyncio
async def test_only_one_published_revision_per_product(repo, product_id) -> None:
    """018 的部分唯一索引：一个制品至多一个已发布修订。"""
    first = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    await repo.publish_revision(product_id, first)

    second = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    await repo.publish_revision(product_id, second)

    statuses = {r["revision_no"]: r["status"] for r in await repo.list_revisions(product_id)}
    assert statuses[first] == "superseded"
    assert statuses[second] == "published"
    assert list(statuses.values()).count("published") == 1

    product = await repo.get_product(product_id)
    assert product["released_revision"] == second
    assert product["lifecycle_status"] == "published"


@pytest.mark.asyncio
async def test_object_rows_require_an_existing_revision(repo, pg_pool, product_id) -> None:
    """复合外键 (product_id, revision_no) → kp_revisions 真的挂住。"""
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        async with pg_pool.connection() as conn:
            await conn.execute(
                """INSERT INTO kp_objects
                   (product_id, object_id, revision_no, type, layer, scope,
                    frontmatter_json, review_status, created_at)
                   VALUES (%s, 'DataProduct@x', 999, 'DataProduct', 'Data', 'cross',
                           '{}'::jsonb, 'agent_submitted', now()::text)""",
                (product_id,),
            )


@pytest.mark.asyncio
async def test_evidence_requires_a_real_segment(repo, pg_pool, product_id) -> None:
    """kp_evidence.segment_id → asset_raw_segments 是硬外键（52号 F3 的支点）。"""
    import psycopg

    revision_no = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await repo.write_draft_revision(
            product_id=product_id,
            objects=[_row("DataProduct@a")],
            edges=[],
            evidence=[
                EvidenceRef(
                    object_id="DataProduct@a",
                    field_name="model",
                    document_id="doc_x",
                    snapshot_id="snap_does_not_exist",
                    segment_id="seg_does_not_exist",
                )
            ],
        )
    assert revision_no  # 上一轮写入本身是成功的


@pytest.mark.asyncio
async def test_knowledge_product_artifact_class_is_accepted(pg_pool) -> None:
    """018 的第 0 节放宽了 008 的 artifact_class CHECK。"""
    object_id = f"kpo_{uuid.uuid4().hex}"
    async with pg_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO asset_storage_objects
               (id, provider, bucket, object_key, sha256, size, mime,
                artifact_class, state, created_at)
               VALUES (%s, 'fake', 'b', %s, 'h', 1, 'text/markdown',
                       'knowledge_product', 'AVAILABLE', now()::text)""",
            (object_id, f"k/{object_id}"),
        )
        cur = await conn.execute(
            "SELECT artifact_class FROM asset_storage_objects WHERE id = %s", (object_id,)
        )
        row = await cur.fetchone()
    assert row["artifact_class"] == "knowledge_product"


# ---------------------------------------------------------------------------
# P4：人审 / 试用 / 发布（020）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_status_update_round_trip(repo, product_id) -> None:
    revision_no = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    await repo.set_review_status(product_id, "DataProduct@a", revision_no, "human_confirmed")

    row = await repo.get_object(product_id, "DataProduct@a", revision_no)
    assert row["review_status"] == "human_confirmed"


@pytest.mark.asyncio
async def test_review_record_round_trip(repo, product_id) -> None:
    revision_no = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    await repo.record_review(
        product_id=product_id, revision_no=revision_no, reviewer="tester",
        decision="approved", notes="看过了", per_object={"DataProduct@a": "approve"},
    )
    reviews = await repo.list_reviews(product_id, revision_no)
    assert len(reviews) == 1
    assert reviews[0]["decision"] == "approved"
    assert reviews[0]["per_object_json"] == {"DataProduct@a": "approve"}


@pytest.mark.asyncio
async def test_review_decision_check_rejects_garbage(repo, pg_pool, product_id) -> None:
    import psycopg

    revision_no = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        await repo.record_review(
            product_id=product_id, revision_no=revision_no, reviewer="tester",
            decision="maybe", notes=None, per_object={},
        )


@pytest.mark.asyncio
async def test_trial_round_trip_and_verdict_check(repo, product_id) -> None:
    import psycopg

    revision_no = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    await repo.record_trial(
        product_id=product_id, revision_no=revision_no, question="Q?",
        answer="A", expected_points=None, verdict="passed",
        comment=None, tried_by="tester",
    )
    trials = await repo.list_trials(product_id, revision_no)
    assert [t["verdict"] for t in trials] == ["passed"]

    with pytest.raises(psycopg.errors.CheckViolation):
        await repo.record_trial(
            product_id=product_id, revision_no=revision_no, question="Q?",
            answer=None, expected_points=None, verdict="probably",
            comment=None, tried_by="tester",
        )


@pytest.mark.asyncio
async def test_trials_are_scoped_to_their_revision(repo, product_id) -> None:
    """换了修订，旧的试用结论不自动继承。"""
    first = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    await repo.record_trial(
        product_id=product_id, revision_no=first, question="Q?", answer=None,
        expected_points=None, verdict="passed", comment=None, tried_by="tester",
    )
    second = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    assert len(await repo.list_trials(product_id, first)) == 1
    assert await repo.list_trials(product_id, second) == []


@pytest.mark.asyncio
async def test_object_edit_trail_is_kept(repo, product_id) -> None:
    revision_no = await repo.write_draft_revision(
        product_id=product_id, objects=[_row("DataProduct@a")], edges=[], evidence=[]
    )
    await repo.record_object_edit(
        product_id=product_id, revision_no=revision_no, object_id="DataProduct@a",
        editor="tester", reason="口径更正", basis="功耗指南 2.2",
        before_md="before", after_md="after",
    )
    edits = await repo.list_object_edits(product_id, "DataProduct@a")
    assert len(edits) == 1
    assert edits[0]["editor"] == "tester"
    assert edits[0]["reason"] == "口径更正"
    assert edits[0]["before_md"] == "before"


@pytest.mark.asyncio
async def test_review_needs_an_existing_revision(repo, pg_pool, product_id) -> None:
    """kp_reviews 的复合外键指向 kp_revisions。"""
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await repo.record_review(
            product_id=product_id, revision_no=999, reviewer="tester",
            decision="approved", notes=None, per_object={},
        )
