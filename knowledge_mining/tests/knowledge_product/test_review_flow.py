"""P4 端到端：Agent 产出 → 人改 / 人审 → 试用 → 发布（或驳回续跑）。

不依赖 DSH，也不依赖 PG。补的是 50号 §2.3「人工在何处介入」那三次判断里的后两次。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.knowledge_product.content_store import (
    ArtifactContentStore,
    bucket_for,
)
from knowledge_mining.mining.knowledge_product.repository import ScopeItem
from knowledge_mining.mining.knowledge_product.repository_memory import (
    MemoryKnowledgeProductRepository,
)
from knowledge_mining.mining.knowledge_product.review import (
    AGENT_SUBMITTED,
    ALL_REVIEWED,
    HUMAN_CONFIRMED,
    NO_HIDDEN_CONFLICT,
    REJECTED,
    TRIAL_PASSED,
    UNRESOLVED,
    ReviewRejected,
)
from knowledge_mining.mining.knowledge_product.service import (
    KnowledgeProductService,
    ValidationRejected,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_product"
OBJECTS = FIXTURE / "objects"

PRODUCT_ID = "spec-ne8000"
X8_CARD = "spec-ne8000@DomainFactSet@NE8000-X8 V300R022"
CONFLICTED = "spec-ne8000@DomainFactSet@NE8000-M16 V300R022"
OVERVIEW = "DataProduct@spec-ne8000"


def _documents() -> list[str]:
    return [p.read_text("utf-8") for p in sorted(OBJECTS.glob("*.md"))]


def _document_for(object_id: str) -> str:
    """按 frontmatter 的 id 精确选。带空格的 id 在 fixture 里带引号，不带空格的没有。"""
    quoted = 'id: "' + object_id + '"\n'
    bare = "id: " + object_id + "\n"
    return next(doc for doc in _documents() if quoted in doc or bare in doc)


@pytest.fixture
def repo() -> MemoryKnowledgeProductRepository:
    return MemoryKnowledgeProductRepository()


@pytest.fixture
def service(repo, tmp_path) -> KnowledgeProductService:
    return KnowledgeProductService(
        repo,
        ArtifactContentStore(
            FakeObjectStore(root_path=str(tmp_path / "objects")),
            MemoryStorageObjectRepository(),
            bucket_for("agentickb-test-"),
        ),
    )


@pytest_asyncio.fixture
async def drafted(service) -> KnowledgeProductService:
    await service.create_product(
        product_id=PRODUCT_ID,
        product_type="specification_table",
        name="NE8000 产品规格矩阵",
        owner="zhangsan",
        purpose="比较这些型号的能力，支持专业问答与方案准备",
        fields={"model": {"required": True}, "product_version": {"required": True}},
        object_rules={"row_identity_rule": "同一型号和产品版本为同一行"},
        scope_items=[ScopeItem("doc_ne8000_hwinfo", "snap_0007", ["3"])],
    )
    await service.replace_draft(PRODUCT_ID, _documents())
    return service


async def _approve_all_but_conflicted(service) -> None:
    rows = await service.current_rows(PRODUCT_ID)
    await service.review_objects(
        PRODUCT_ID,
        {oid: "approve" for oid, row in rows.items()
         if row["review_status"] != UNRESOLVED},
        reviewer="zhangsan",
    )


# ----------------------------------------------------------------- 人工编辑


@pytest.mark.asyncio
async def test_human_edit_is_recorded_with_who_what_why(drafted) -> None:
    """48号 §六：至少记录谁改了什么、为什么、依据是什么。"""
    before = await drafted.get_object_md(PRODUCT_ID, X8_CARD)
    await drafted.edit_object(
        PRODUCT_ID, X8_CARD, before.replace("4200", "4250"),
        editor="zhangsan", reason="按满配口径更正", basis="功耗指南 2.2 表2-1",
    )

    edits = await drafted.list_object_edits(PRODUCT_ID, X8_CARD)
    assert len(edits) == 1
    assert edits[0]["editor"] == "zhangsan"
    assert edits[0]["reason"] == "按满配口径更正"
    assert edits[0]["basis"] == "功耗指南 2.2 表2-1"
    assert "4200" in edits[0]["before_md"] and "4250" in edits[0]["after_md"]


@pytest.mark.asyncio
async def test_human_edit_marks_the_object_confirmed(drafted) -> None:
    """人亲手写的内容不需要再被自己确认一遍。"""
    before = await drafted.get_object_md(PRODUCT_ID, X8_CARD)
    await drafted.edit_object(
        PRODUCT_ID, X8_CARD, before.replace("4200", "4250"), editor="zhangsan",
    )
    rows = await drafted.current_rows(PRODUCT_ID)
    assert rows[X8_CARD]["review_status"] == HUMAN_CONFIRMED
    assert "4250" in await drafted.get_object_md(PRODUCT_ID, X8_CARD)


@pytest.mark.asyncio
async def test_human_edit_keeps_the_other_objects(drafted) -> None:
    before = await drafted.get_object_md(PRODUCT_ID, X8_CARD)
    await drafted.edit_object(
        PRODUCT_ID, X8_CARD, before.replace("4200", "4250"), editor="zhangsan",
    )
    assert len(await drafted.list_objects(PRODUCT_ID)) == 7


@pytest.mark.asyncio
async def test_human_edit_with_a_mismatched_id_is_refused(drafted) -> None:
    other = await drafted.get_object_md(PRODUCT_ID, OVERVIEW)
    with pytest.raises(ValueError, match="不符"):
        await drafted.edit_object(PRODUCT_ID, X8_CARD, other, editor="zhangsan")


@pytest.mark.asyncio
async def test_structurally_broken_edit_is_refused(drafted) -> None:
    broken = f'---\nid: "{X8_CARD}"\ntype: DomainFactSet\n---\n# 缺东西\n'
    with pytest.raises(ValidationRejected):
        await drafted.edit_object(PRODUCT_ID, X8_CARD, broken, editor="zhangsan")


# ----------------------------------------------------------------- 人审


@pytest.mark.asyncio
async def test_approving_objects_records_a_review(drafted, repo) -> None:
    await _approve_all_but_conflicted(drafted)

    reviews = await drafted.list_reviews(PRODUCT_ID)
    assert len(reviews) == 1
    assert reviews[0]["reviewer"] == "zhangsan"
    assert reviews[0]["decision"] == "approved"

    rows = await drafted.current_rows(PRODUCT_ID)
    assert rows[X8_CARD]["review_status"] == HUMAN_CONFIRMED
    # 冲突项没被碰过
    assert rows[CONFLICTED]["review_status"] == UNRESOLVED


@pytest.mark.asyncio
async def test_rejecting_one_object_makes_the_batch_rejected(drafted) -> None:
    await drafted.review_objects(
        PRODUCT_ID, {X8_CARD: "reject"}, reviewer="zhangsan", notes="口径不对",
    )
    reviews = await drafted.list_reviews(PRODUCT_ID)
    assert reviews[0]["decision"] == "rejected"
    assert reviews[0]["notes"] == "口径不对"
    rows = await drafted.current_rows(PRODUCT_ID)
    assert rows[X8_CARD]["review_status"] == REJECTED


@pytest.mark.asyncio
async def test_confirming_a_conflicted_object_is_refused(drafted) -> None:
    with pytest.raises(ReviewRejected, match="冲突"):
        await drafted.review_objects(
            PRODUCT_ID, {CONFLICTED: "approve"}, reviewer="zhangsan"
        )


@pytest.mark.asyncio
async def test_an_illegal_decision_lands_nothing(drafted) -> None:
    """整批不落——审核记录必须与实际状态一致。"""
    before = await drafted.current_rows(PRODUCT_ID)
    with pytest.raises(ReviewRejected):
        await drafted.review_objects(
            PRODUCT_ID, {X8_CARD: "approve", CONFLICTED: "approve"}, reviewer="zhangsan",
        )
    after = await drafted.current_rows(PRODUCT_ID)
    assert after[X8_CARD]["review_status"] == before[X8_CARD]["review_status"]
    assert await drafted.list_reviews(PRODUCT_ID) == []


@pytest.mark.asyncio
async def test_reviewing_an_unknown_object_is_refused(drafted) -> None:
    with pytest.raises(ReviewRejected, match="不在当前草稿"):
        await drafted.review_objects(
            PRODUCT_ID, {"no@Such@object": "approve"}, reviewer="zhangsan"
        )


@pytest.mark.asyncio
async def test_review_survives_the_next_revision(drafted) -> None:
    """人刚确认完，下一次写修订不能把结论抹掉。"""
    await _approve_all_but_conflicted(drafted)
    await drafted.merge_draft(PRODUCT_ID, drafted.parse([_document_for(OVERVIEW)]))

    rows = await drafted.current_rows(PRODUCT_ID)
    assert rows[X8_CARD]["review_status"] == HUMAN_CONFIRMED


@pytest.mark.asyncio
async def test_changed_content_loses_its_confirmation(drafted) -> None:
    """内容变了就得重审——沿用旧结论等于确认了没看过的东西。"""
    await _approve_all_but_conflicted(drafted)
    changed = _document_for(X8_CARD).replace("4200", "9999")
    await drafted.merge_draft(PRODUCT_ID, drafted.parse([changed]))

    rows = await drafted.current_rows(PRODUCT_ID)
    assert rows[X8_CARD]["review_status"] == AGENT_SUBMITTED


# ----------------------------------------------------------------- 试用


@pytest.mark.asyncio
async def test_trials_are_bound_to_a_revision(drafted) -> None:
    await drafted.record_trial(
        PRODUCT_ID, question="哪些型号功耗不超过 3000 W？",
        answer="M8 两个版本", verdict="passed", tried_by="zhangsan",
    )
    first_revision = (await drafted.get_product(PRODUCT_ID))["current_draft_revision"]
    assert len(await drafted.list_trials(PRODUCT_ID, first_revision)) == 1

    # 换了修订，旧的试用结论不自动继承
    await drafted.merge_draft(PRODUCT_ID, drafted.parse([_document_for(OVERVIEW)]))
    assert await drafted.list_trials(PRODUCT_ID) == []


# ----------------------------------------------------------------- 发布


@pytest.mark.asyncio
async def test_publish_is_blocked_until_everything_is_settled(drafted) -> None:
    gate = await drafted.publish_gate(PRODUCT_ID)
    assert not gate.passed
    blocked = {c.code for c in gate.blocking}
    assert NO_HIDDEN_CONFLICT in blocked     # 那张冲突卡
    assert ALL_REVIEWED in blocked           # 还没人审
    assert TRIAL_PASSED in blocked           # 还没试用

    with pytest.raises(ReviewRejected, match="发布门禁未通过"):
        await drafted.publish(PRODUCT_ID)
    assert (await drafted.get_product(PRODUCT_ID))["released_revision"] is None


async def _drop_dangling_reference(service) -> None:
    """发布前把总览里那条「待接入规则」摘掉——它指向本制品还没产出的对象。

    制作期间悬挂边是正常态，发布时不是：引用得拿得到。
    """
    overview = await service.get_object_md(PRODUCT_ID, OVERVIEW)
    kept = [
        line for line in overview.splitlines()
        if "RulePackage@power-budget-check" not in line
    ]
    cleaned = "\n".join(kept) + "\n"
    await service.edit_object(
        PRODUCT_ID, OVERVIEW, cleaned,
        editor="zhangsan", reason="规则包尚未产出，发布前摘除引用",
    )


@pytest.mark.asyncio
async def test_full_path_to_a_release(drafted) -> None:
    # ① 消解冲突：人工改掉那张卡，定下口径
    conflicted_md = await drafted.get_object_md(PRODUCT_ID, CONFLICTED)
    resolved = _document_for(X8_CARD).replace(X8_CARD, CONFLICTED).replace(
        "NE8000-X8", "NE8000-M16"
    )
    assert conflicted_md != resolved
    await drafted.edit_object(
        PRODUCT_ID, CONFLICTED, resolved,
        editor="zhangsan", reason="按满配口径定论", basis="功耗指南 2.2",
    )

    # ② 摘掉指向未产出对象的引用
    await _drop_dangling_reference(drafted)

    # ③ 人审其余对象
    await _approve_all_but_conflicted(drafted)

    # ④ 试用
    await drafted.record_trial(
        PRODUCT_ID, question="哪些型号功耗不超过 3000 W？",
        verdict="passed", tried_by="zhangsan",
    )

    gate = await drafted.publish_gate(PRODUCT_ID)
    assert gate.passed, [c.detail for c in gate.blocking]

    result = await drafted.publish(PRODUCT_ID)
    product = await drafted.get_product(PRODUCT_ID)
    assert product["released_revision"] == result["released_revision"]
    assert product["lifecycle_status"] == "published"
    assert result["forced"] is False


@pytest.mark.asyncio
async def test_force_publish_is_recorded_as_forced(drafted) -> None:
    """负责人明确要带着已知缺口发布时，门禁结果原样留痕。"""
    result = await drafted.publish(PRODUCT_ID, force=True)
    assert result["forced"] is True
    assert result["gate"]["passed"] is False
    assert (await drafted.get_product(PRODUCT_ID))["released_revision"] == result[
        "released_revision"
    ]


@pytest.mark.asyncio
async def test_publishing_again_supersedes_the_previous_release(drafted, repo) -> None:
    first = await drafted.publish(PRODUCT_ID, force=True)
    await drafted.merge_draft(PRODUCT_ID, drafted.parse([_document_for(OVERVIEW)]))
    second = await drafted.publish(PRODUCT_ID, force=True)

    statuses = {r["revision_no"]: r["status"] for r in await repo.list_revisions(PRODUCT_ID)}
    assert statuses[first["released_revision"]] == "superseded"
    assert statuses[second["released_revision"]] == "published"
    assert list(statuses.values()).count("published") == 1
