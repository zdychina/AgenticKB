"""不经 DSH 的端到端演练（52号 P3 的「不依赖 DSH 那半」）。

跑的是平台侧整条链路：起实例 → 签票 → 建会话 → 发启动指令 → 脚本化 Agent 取上下文
并提交 → 过程事件回流并投影 → 取消时撤票。

**它验证不了什么**：DSH 的原生协议、内网模型是否真支持工具调用（50号 T0/T1）。
那两件只有真 DSH 能验，所以本文件全绿 ≠ 可以上线。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from knowledge_mining.mining.agent_creation.models import TicketRejected
from knowledge_mining.mining.agent_creation.repository_memory import (
    MemoryCreationRepository,
    MemorySegmentLocator,
)
from knowledge_mining.mining.agent_creation.service import AgentCreationService
from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.harness_adapter import (
    ERROR,
    MESSAGE,
    STATUS,
    TOOL_CALL,
    TOOL_RESULT,
    CreationDriver,
    FakeHarness,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.knowledge_product.content_store import (
    ArtifactContentStore,
    bucket_for,
)
from knowledge_mining.mining.knowledge_product.evidence import extract_evidence
from knowledge_mining.mining.knowledge_product.loader import build_object
from knowledge_mining.mining.knowledge_product.registry import Registry
from knowledge_mining.mining.knowledge_product.repository import ScopeItem
from knowledge_mining.mining.knowledge_product.repository_memory import (
    MemoryKnowledgeProductRepository,
)
from knowledge_mining.mining.knowledge_product.service import KnowledgeProductService

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_product"
OBJECTS = FIXTURE / "objects"
PRODUCT_ID = "spec-ne8000"
X8_CARD = "spec-ne8000@DomainFactSet@NE8000-X8 V300R022"


def _documents() -> list[str]:
    return [p.read_text("utf-8") for p in sorted(OBJECTS.glob("*.md"))]


@pytest.fixture
def product_repo() -> MemoryKnowledgeProductRepository:
    return MemoryKnowledgeProductRepository()


@pytest.fixture
def creation_repo() -> MemoryCreationRepository:
    return MemoryCreationRepository()


@pytest.fixture
def locator() -> MemorySegmentLocator:
    registry = Registry.load()
    located = MemorySegmentLocator()
    for doc in _documents():
        for ref in extract_evidence(build_object(doc, registry)):
            titles = tuple((ref.anchor or "").split("/")) if ref.anchor else ()
            located.add(ref.segment_id, ref.snapshot_id, titles)
    return located


@pytest.fixture
def creation(creation_repo, product_repo, locator, tmp_path) -> AgentCreationService:
    products = KnowledgeProductService(
        product_repo,
        ArtifactContentStore(
            FakeObjectStore(root_path=str(tmp_path / "objects")),
            MemoryStorageObjectRepository(),
            bucket_for("agentickb-test-"),
        ),
    )
    return AgentCreationService(creation_repo, products, product_repo, locator)


@pytest_asyncio.fixture
async def product(creation) -> dict:
    return await creation._products.create_product(
        product_id=PRODUCT_ID,
        product_type="specification_table",
        name="NE8000 产品规格矩阵",
        owner="zhangsan",
        purpose="比较这些型号的能力",
        fields={"model": {"required": True}},
        object_rules={"row_identity_rule": "同一型号和产品版本为同一行"},
        scope_items=[
            ScopeItem("doc_ne8000_hwinfo", "snap_0007", ["3"]),
            ScopeItem("doc_ne8000_release_notes", "snap_0011", ["2.4"]),
            ScopeItem("doc_ne8000_power_guide", "snap_0003", None),
        ],
    )


def _script(documents):
    async def script(_context):
        return documents
    return script


# ----------------------------------------------------------------- 全链路


@pytest.mark.asyncio
async def test_full_run_lands_the_batch_in_the_draft(creation, product) -> None:
    harness = FakeHarness(creation, _script(_documents()))
    driver = CreationDriver(creation, harness)

    run = await driver.start(PRODUCT_ID, created_by="zhangsan", product_name="规格矩阵")
    rows = await driver.follow(run)

    assert [r["kind"] for r in rows][:2] == [STATUS, MESSAGE]
    assert any(r["kind"] == TOOL_CALL and r["tool"] == "get_creation_context" for r in rows)
    receipt = next(
        r for r in rows if r["kind"] == TOOL_RESULT and r["tool"] == "submit_creation_result"
    )
    assert receipt["outcome"] == "accepted"
    assert receipt["accepted_count"] == 7

    assert len(await creation._products.list_objects(PRODUCT_ID)) == 7


@pytest.mark.asyncio
async def test_ticket_travels_only_to_the_harness(creation, product) -> None:
    """票据明文只在平台→harness 这一跳流转；过程投影里不该出现它。"""
    harness = FakeHarness(creation, _script(_documents()))
    driver = CreationDriver(creation, harness)
    run = await driver.start(PRODUCT_ID, created_by="zhangsan")
    await driver.follow(run)

    start_message = next(r for r in run.visible_process() if r["kind"] == MESSAGE)
    assert "task_ticket=" in start_message["text"], "启动指令确实带了票据"
    # 但提交回执与工具结果里没有
    for row in run.visible_process():
        if row["kind"] in (TOOL_RESULT, STATUS):
            assert "task_ticket" not in str(row)


@pytest.mark.asyncio
async def test_harness_session_is_bound_to_the_instance(
    creation, creation_repo, product
) -> None:
    harness = FakeHarness(creation, _script(_documents()))
    run = await CreationDriver(creation, harness).start(PRODUCT_ID, created_by="zhangsan")

    instance = await creation_repo.get_instance(run.instance_id)
    assert instance["harness_session_id"] == run.session_id


# ----------------------------------------------------------------- 过程回流


@pytest.mark.asyncio
async def test_following_twice_does_not_duplicate(creation, product) -> None:
    harness = FakeHarness(creation, _script(_documents()))
    driver = CreationDriver(creation, harness)
    run = await driver.start(PRODUCT_ID, created_by="zhangsan")

    first = await driver.follow(run)
    second = await driver.follow(run)
    assert first and second == []
    assert len(run.visible_process()) == len(first)


@pytest.mark.asyncio
async def test_dropped_event_is_detected_and_backfilled(creation, product) -> None:
    """丢了中间一条事件——平台必须发现缺口并补读，而不是看到终态就收工。"""
    harness = FakeHarness(creation, _script(_documents()), drop_sequences=[3])
    driver = CreationDriver(creation, harness)
    run = await driver.start(PRODUCT_ID, created_by="zhangsan")

    await driver.follow(run)
    assert run.journal.needs_backfill
    assert any(r["kind"] == STATUS for r in run.visible_process()), "终态事件已经到了"

    # 补读也补不回真丢了的那条——但平台知道自己缺，不会假装过程完整
    await driver.backfill(run)
    assert run.journal.needs_backfill


@pytest.mark.asyncio
async def test_complete_run_needs_no_backfill(creation, product) -> None:
    harness = FakeHarness(creation, _script(_documents()))
    driver = CreationDriver(creation, harness)
    run = await driver.start(PRODUCT_ID, created_by="zhangsan")
    await driver.follow(run)

    assert not run.journal.needs_backfill
    assert await driver.backfill(run) == []


# ----------------------------------------------------------------- 多轮


@pytest.mark.asyncio
async def test_resending_the_same_message_reuses_its_request_id(creation, product) -> None:
    """重发必须复用 request_id，否则 harness 把重试当成新一轮，指令跑两遍。"""
    harness = FakeHarness(creation, _script(_documents()))
    driver = CreationDriver(creation, harness)
    run = await driver.start(PRODUCT_ID, created_by="zhangsan")
    await driver.follow(run)
    before = len(run.visible_process())

    await driver.send(run, "（重发同一条启动指令）", key="start")
    await driver.follow(run)

    assert len(run.visible_process()) == before, "复用 request_id 不该再跑一轮"
    assert len(await creation._products.list_objects(PRODUCT_ID)) == 7


@pytest.mark.asyncio
async def test_second_round_carries_review_feedback(creation, product) -> None:
    """D8：审核意见作为新一轮消息发进同一会话，Agent 刷新上下文后提交新修订。"""
    documents = _documents()
    # 按 frontmatter 的 id 精确选——总览的表格里也提到这个型号
    x8 = next(d for d in documents if f'id: "{X8_CARD}"' in d)

    harness = FakeHarness(creation, _script(documents))
    driver = CreationDriver(creation, harness)
    run = await driver.start(PRODUCT_ID, created_by="zhangsan")
    await driver.follow(run)
    first_revision = (await creation._products.get_product(PRODUCT_ID))[
        "current_draft_revision"
    ]

    # 人审驳回 → 脚本改口径 → 同一实例续跑
    harness._script = _script([x8.replace("4200", "4250")])
    await driver.send(run, "审核意见：X8 的功耗应按满配 4250 W。请更正后重交。", key="review-1")
    await driver.follow(run)

    product_now = await creation._products.get_product(PRODUCT_ID)
    assert product_now["current_draft_revision"] > first_revision
    md = await creation._products.get_object_md(PRODUCT_ID, X8_CARD)
    assert "4250" in md
    # 并入而非替换：其余六个对象还在
    assert len(await creation._products.list_objects(PRODUCT_ID)) == 7


# ----------------------------------------------------------------- 取消


@pytest.mark.asyncio
async def test_cancel_stops_the_session_and_revokes_the_ticket(
    creation, creation_repo, product
) -> None:
    harness = FakeHarness(creation, _script(_documents()))
    driver = CreationDriver(creation, harness)
    run = await driver.start(PRODUCT_ID, created_by="zhangsan")
    await driver.follow(run)

    await driver.cancel(run, reason="负责人取消")

    instance = await creation_repo.get_instance(run.instance_id)
    assert instance["status"] == "cancelled"
    # 撤票之后，队列里迟到的工具调用也写不回来
    ticket = next(iter(creation_repo._tickets.values()))
    assert ticket["status"] == "revoked"


@pytest.mark.asyncio
async def test_late_tool_call_after_cancel_is_refused(creation, creation_repo, product) -> None:
    harness = FakeHarness(creation, _script(_documents()))
    driver = CreationDriver(creation, harness)
    run = await driver.start(PRODUCT_ID, created_by="zhangsan")
    await driver.follow(run)

    token = None
    for row in run.visible_process():
        if row["kind"] == MESSAGE and "task_ticket=" in str(row.get("text", "")):
            token = row["text"].split("task_ticket=", 1)[1].split()[0]
    assert token

    await driver.cancel(run)
    with pytest.raises(TicketRejected):
        await creation.get_context(token)


@pytest.mark.asyncio
async def test_agent_sees_an_error_when_its_ticket_is_dead(creation, product) -> None:
    """票据先撤再开工——Agent 那侧只应看到错误事件，草稿一个字节都不动。"""
    harness = FakeHarness(creation, _script(_documents()))
    driver = CreationDriver(creation, harness)

    instance, ticket = await creation.start_instance(PRODUCT_ID, created_by="zhangsan")
    await creation.invalidate_product_tickets(PRODUCT_ID, reason="资料范围已收缩")

    session_id = await harness.create_session(instance_id=instance["id"])
    await harness.send_message(
        session_id, f"开工 task_ticket={ticket.token}", request_id="r1"
    )
    events = await harness.fetch_events(session_id)

    assert any(e.kind == ERROR for e in events)
    assert await creation._products.list_objects(PRODUCT_ID) == []
