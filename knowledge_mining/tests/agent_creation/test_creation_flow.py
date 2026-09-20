"""P2 验收：脚本模拟 Agent 走完制作闭环（不经 DSH）。

52号 P2 的验收线：有效票据取上下文 → 提交一批对象 → 越权票据被拒 → 重复提交幂等
→ 修订冲突被拒。外加 50号 §5.3 余下几道校验各自的负例。

全内存：``MemoryKnowledgeProductRepository`` + ``MemoryCreationRepository`` +
``FakeObjectStore``，不需要 PG 和 MinIO。
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from knowledge_mining.mining.agent_creation.models import (
    ACCEPTED,
    CONFLICT,
    EVIDENCE_OUT_OF_SCOPE,
    REJECTED,
    STRUCTURE_INVALID,
    TicketRejected,
)
from knowledge_mining.mining.agent_creation.repository_memory import (
    MemoryCreationRepository,
    MemorySegmentLocator,
)
from knowledge_mining.mining.agent_creation.service import (
    HUMAN_CONFIRMED,
    AgentCreationService,
)
from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.knowledge_product.content_store import (
    ArtifactContentStore,
    bucket_for,
)
from knowledge_mining.mining.knowledge_product.evidence import extract_evidence
from knowledge_mining.mining.knowledge_product.repository import ScopeItem
from knowledge_mining.mining.knowledge_product.repository_memory import (
    MemoryKnowledgeProductRepository,
)
from knowledge_mining.mining.knowledge_product.service import KnowledgeProductService

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_product"
OBJECTS = FIXTURE / "objects"

PRODUCT_ID = "spec-ne8000"
X8_CARD = "spec-ne8000@DomainFactSet@NE8000-X8 V300R022"

# 与 fixture 的 product.yaml 一致
SCOPE = [
    ScopeItem("doc_ne8000_hwinfo", "snap_0007", ["3"]),
    ScopeItem("doc_ne8000_release_notes", "snap_0011", ["2.4"]),
    ScopeItem("doc_ne8000_power_guide", "snap_0003", None),
]


def _documents() -> list[str]:
    return [p.read_text("utf-8") for p in sorted(OBJECTS.glob("*.md"))]


def _document_for(object_id: str) -> str:
    return next(doc for doc in _documents() if f'id: "{object_id}"' in doc)


@pytest.fixture
def product_repo() -> MemoryKnowledgeProductRepository:
    return MemoryKnowledgeProductRepository()


@pytest.fixture
def creation_repo() -> MemoryCreationRepository:
    return MemoryCreationRepository()


@pytest.fixture
def locator(product_repo) -> MemorySegmentLocator:
    """按 fixture 自身声明登记「平台已知的真相」。

    fixture 内部是自洽的，所以这样登记等于「Agent 报的就是真的」；越权的负例在各自
    的用例里显式构造。
    """
    from knowledge_mining.mining.knowledge_product.loader import build_object
    from knowledge_mining.mining.knowledge_product.registry import Registry

    registry = Registry.load()
    located = MemorySegmentLocator()
    for doc in _documents():
        for ref in extract_evidence(build_object(doc, registry)):
            # anchor 形如「3.1 硬件规格/表3-2」，取到章节那一段作为标题链末级
            titles = tuple((ref.anchor or "").split("/")) if ref.anchor else ()
            located.add(ref.segment_id, ref.snapshot_id, titles)
    return located


@pytest.fixture
def products(product_repo, tmp_path) -> KnowledgeProductService:
    return KnowledgeProductService(
        product_repo,
        ArtifactContentStore(
            FakeObjectStore(root_path=str(tmp_path / "objects")),
            MemoryStorageObjectRepository(),
            bucket_for("agentickb-test-"),
        ),
    )


@pytest.fixture
def creation(creation_repo, products, product_repo, locator) -> AgentCreationService:
    return AgentCreationService(creation_repo, products, product_repo, locator)


@pytest_asyncio.fixture
async def started(creation, products):
    await products.create_product(
        product_id=PRODUCT_ID,
        product_type="specification_table",
        name="NE8000 产品规格矩阵",
        owner="zhangsan",
        purpose="比较这些型号的能力",
        fields={"model": {"required": True}, "unit": {"required": False}},
        object_rules={
            "row_identity_rule": "同一型号和产品版本为同一行",
            "human_decisions": ["容量以额定值为准"],
        },
        examples=[{"object_id": X8_CARD}],
        scope_items=SCOPE,
    )
    instance, ticket = await creation.start_instance(PRODUCT_ID, created_by="zhangsan")
    return instance, ticket


# ----------------------------------------------------------------- 取上下文


@pytest.mark.asyncio
async def test_context_tells_what_to_do_and_where_to_read(creation, started) -> None:
    _instance, ticket = started
    context = await creation.get_context(ticket.token)

    assert context.creation_instance_id == ticket.instance_id
    assert context.product["id"] == PRODUCT_ID
    assert context.output_contract["required_fields"] == ["model"]
    assert context.output_contract["source_required"] is True
    # F3：segment_id 必填才能硬校验来源
    assert "segment_id" in context.output_contract["evidence_required_keys"]
    assert context.human_decisions == ["容量以额定值为准"]
    assert context.next_action


@pytest.mark.asyncio
async def test_context_lists_only_the_allowed_material(creation, started) -> None:
    _instance, ticket = started
    context = await creation.get_context(ticket.token)

    assert {i["snapshot_id"] for i in context.inputs} == {
        "snap_0007", "snap_0011", "snap_0003",
    }
    hwinfo = next(i for i in context.inputs if i["snapshot_id"] == "snap_0007")
    assert hwinfo["allowed_sections"] == ["3"]


@pytest.mark.asyncio
async def test_context_requires_a_valid_ticket(creation) -> None:
    with pytest.raises(TicketRejected):
        await creation.get_context("kpt_not-a-real-token")


# ----------------------------------------------------------------- 提交成功


@pytest.mark.asyncio
async def test_submit_writes_the_batch_into_the_draft(creation, products, started) -> None:
    _instance, ticket = started
    receipt = await creation.submit(
        ticket.token,
        submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1,
        documents=_documents(),
    )

    assert receipt.outcome == ACCEPTED
    assert receipt.accepted_count == 7
    assert receipt.rejected == ()
    assert receipt.written_revision == 2
    assert len(await products.list_objects(PRODUCT_ID)) == 7


@pytest.mark.asyncio
async def test_submission_merges_rather_than_replaces(creation, products, started) -> None:
    """Agent 是「完成一批内容后提交」——没提到的对象必须留着。"""
    _instance, ticket = started
    await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=_documents(),
    )
    product = await products.get_product(PRODUCT_ID)

    await creation.submit(
        ticket.token,
        submission_id=str(uuid.uuid4()),
        based_on_draft_revision=product["current_draft_revision"],
        documents=[_document_for(X8_CARD).replace("4200", "4250")],
    )

    rows = await products.list_objects(PRODUCT_ID)
    assert len(rows) == 7, "只交了一个对象，其余六个不该消失"
    assert "4250" in await products.get_object_md(PRODUCT_ID, X8_CARD)


# ----------------------------------------------------------------- 第一道：票据


@pytest.mark.asyncio
async def test_revoked_ticket_writes_nothing(creation, creation_repo, products, started) -> None:
    instance, ticket = started
    await creation.cancel_instance(instance["id"], reason="负责人取消")

    with pytest.raises(TicketRejected):
        await creation.submit(
            ticket.token, submission_id=str(uuid.uuid4()),
            based_on_draft_revision=1, documents=_documents(),
        )
    assert await products.list_objects(PRODUCT_ID) == []


@pytest.mark.asyncio
async def test_expired_ticket_is_rejected(creation, products, started) -> None:
    instance, _ticket = started
    stale = await creation._issue_ticket(instance, ttl=timedelta(seconds=-1))

    with pytest.raises(TicketRejected):
        await creation.submit(
            stale.token, submission_id=str(uuid.uuid4()),
            based_on_draft_revision=1, documents=_documents(),
        )
    assert await products.list_objects(PRODUCT_ID) == []


@pytest.mark.asyncio
async def test_ticket_bound_to_another_product_is_rejected(creation, started) -> None:
    _instance, ticket = started
    with pytest.raises(TicketRejected):
        await creation.submit(
            ticket.token, submission_id=str(uuid.uuid4()),
            based_on_draft_revision=1, documents=_documents(),
            product_id="some-other-product",
        )


@pytest.mark.asyncio
async def test_shrinking_the_scope_invalidates_outstanding_tickets(
    creation, products, started
) -> None:
    """资料范围收缩后，旧票不能按旧范围继续提交（50号 §7.1）。"""
    _instance, ticket = started
    await creation.invalidate_product_tickets(PRODUCT_ID, reason="资料范围已收缩")

    with pytest.raises(TicketRejected):
        await creation.get_context(ticket.token)
    assert await products.list_objects(PRODUCT_ID) == []


# ----------------------------------------------------------------- 第二道：幂等


@pytest.mark.asyncio
async def test_retrying_the_same_submission_returns_the_original_receipt(
    creation, products, started
) -> None:
    _instance, ticket = started
    submission_id = str(uuid.uuid4())

    first = await creation.submit(
        ticket.token, submission_id=submission_id,
        based_on_draft_revision=1, documents=_documents(),
    )
    second = await creation.submit(
        ticket.token, submission_id=submission_id,
        based_on_draft_revision=1, documents=_documents(),
    )

    assert second.outcome == first.outcome
    assert second.written_revision == first.written_revision
    assert "未重复写入" in second.message
    # 草稿只前进了一步
    assert (await products.get_product(PRODUCT_ID))["current_draft_revision"] == 2


@pytest.mark.asyncio
async def test_rejected_submissions_are_also_idempotent(creation, started) -> None:
    """被拒的提交同样留痕，否则「Agent 说交了但草稿里没有」无从对账。"""
    _instance, ticket = started
    submission_id = str(uuid.uuid4())
    bad = ["---\nid: p@NoSuchType@x\ntype: NoSuchType\n---\n# 坏的\n\n## 边\n"]

    first = await creation.submit(
        ticket.token, submission_id=submission_id,
        based_on_draft_revision=1, documents=bad,
    )
    second = await creation.submit(
        ticket.token, submission_id=submission_id,
        based_on_draft_revision=1, documents=bad,
    )
    assert first.outcome == REJECTED and second.outcome == REJECTED
    assert [r.object_id for r in second.rejected] == [r.object_id for r in first.rejected]


# ----------------------------------------------------------------- 第三道：修订冲突


@pytest.mark.asyncio
async def test_stale_base_revision_conflicts_without_writing(
    creation, products, started
) -> None:
    _instance, ticket = started
    await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=_documents(),
    )

    receipt = await creation.submit(
        ticket.token,
        submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1,  # 已经过时
        documents=[_document_for(X8_CARD)],
    )

    assert receipt.outcome == CONFLICT
    assert receipt.current_draft_revision == 2
    assert receipt.written_revision is None
    assert "get_creation_context" in receipt.message
    assert (await products.get_product(PRODUCT_ID))["current_draft_revision"] == 2


# ----------------------------------------------------------------- 第四道：结构


@pytest.mark.asyncio
async def test_bad_object_is_rejected_but_the_good_ones_land(
    creation, products, started
) -> None:
    """逐对象拒，不整批毙——这是提交与人工整篇替换的区别。"""
    _instance, ticket = started
    documents = _documents() + [
        "---\nid: p@NoSuchType@x\ntype: NoSuchType\n---\n# 坏的\n\n## 边\n"
    ]

    receipt = await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=documents,
    )

    assert receipt.outcome == ACCEPTED
    assert receipt.accepted_count == 7
    assert [r.code for r in receipt.rejected] == [STRUCTURE_INVALID]
    assert len(await products.list_objects(PRODUCT_ID)) == 7


# ----------------------------------------------------------------- 第五道：来源


@pytest.mark.asyncio
async def test_evidence_from_an_unlisted_snapshot_is_rejected(
    creation, products, started
) -> None:
    _instance, ticket = started
    tampered = _document_for(X8_CARD).replace("snapshot_id: snap_0003", "snapshot_id: snap_evil")

    receipt = await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=[tampered],
    )

    assert receipt.outcome == REJECTED
    assert {r.code for r in receipt.rejected} == {EVIDENCE_OUT_OF_SCOPE}
    assert await products.list_objects(PRODUCT_ID) == []


@pytest.mark.asyncio
async def test_evidence_without_segment_id_is_rejected(creation, products, started) -> None:
    """没有 segment_id 就没法 FK 回原文，这条证据不成立（52号 F3）。"""
    _instance, ticket = started
    tampered = _document_for(X8_CARD).replace("        segment_id: seg_00102\n", "", 1)

    receipt = await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=[tampered],
    )
    assert receipt.outcome == REJECTED
    assert {r.code for r in receipt.rejected} == {STRUCTURE_INVALID}


@pytest.mark.asyncio
async def test_evidence_pointing_at_an_unknown_segment_is_rejected(
    creation, products, started
) -> None:
    _instance, ticket = started
    tampered = _document_for(X8_CARD).replace("seg_00102", "seg_made_up")

    receipt = await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=[tampered],
    )
    assert receipt.outcome == REJECTED
    assert {r.code for r in receipt.rejected} == {EVIDENCE_OUT_OF_SCOPE}


@pytest.mark.asyncio
async def test_evidence_outside_the_allowed_sections_is_rejected(
    creation, locator, products, started
) -> None:
    """hwinfo 只放行「3」开头的章节；把那一段改挂到 4.2 就该被拒。"""
    _instance, ticket = started
    locator.add("seg_00431", "snap_0007", ["4 运维", "4.2 告警"])

    receipt = await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=[_document_for(X8_CARD)],
    )
    assert receipt.outcome == REJECTED
    assert {r.code for r in receipt.rejected} == {EVIDENCE_OUT_OF_SCOPE}


# ----------------------------------------------------------------- 第六道：人工修改


@pytest.mark.asyncio
async def test_human_confirmed_object_is_held_for_merge_not_overwritten(
    creation, products, product_repo, started
) -> None:
    _instance, ticket = started
    await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=_documents(),
    )

    # 人把这张卡确认了
    product = await products.get_product(PRODUCT_ID)
    revision = int(product["current_draft_revision"])
    for row in product_repo._objects[(PRODUCT_ID, revision)]:
        if row.object_id == X8_CARD:
            product_repo._objects[(PRODUCT_ID, revision)].remove(row)
            product_repo._objects[(PRODUCT_ID, revision)].append(
                type(row)(**{**row.__dict__, "review_status": HUMAN_CONFIRMED})
            )
            break
    before = await products.get_object_md(PRODUCT_ID, X8_CARD)

    receipt = await creation.submit(
        ticket.token,
        submission_id=str(uuid.uuid4()),
        based_on_draft_revision=revision,
        documents=[_document_for(X8_CARD).replace("4200", "9999")],
    )

    assert receipt.pending_merge == (X8_CARD,)
    assert receipt.outcome == REJECTED  # 本批只有这一个对象，没有可接收的
    assert await products.get_object_md(PRODUCT_ID, X8_CARD) == before


@pytest.mark.asyncio
async def test_identical_resubmit_of_a_human_confirmed_object_is_not_held(
    creation, products, product_repo, started
) -> None:
    """内容没变就不算「覆盖人工结果」，不该卡住。"""
    _instance, ticket = started
    await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=1, documents=_documents(),
    )
    product = await products.get_product(PRODUCT_ID)
    revision = int(product["current_draft_revision"])
    for row in list(product_repo._objects[(PRODUCT_ID, revision)]):
        if row.object_id == X8_CARD:
            product_repo._objects[(PRODUCT_ID, revision)].remove(row)
            product_repo._objects[(PRODUCT_ID, revision)].append(
                type(row)(**{**row.__dict__, "review_status": HUMAN_CONFIRMED})
            )
            break

    receipt = await creation.submit(
        ticket.token, submission_id=str(uuid.uuid4()),
        based_on_draft_revision=revision, documents=[_document_for(X8_CARD)],
    )
    assert receipt.pending_merge == ()
    assert receipt.outcome == ACCEPTED
