"""制品服务端到端（52号 P1 验收）。

用 fixture 的那批真实 md 走完：建制品 → 整批写草稿 → 列对象 → 读正文 → 出 diff →
反查反向边 → 发布。内存仓储 + FakeObjectStore，不需要 PG 和 MinIO。
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
from knowledge_mining.mining.knowledge_product.service import (
    KnowledgeProductService,
    NotFound,
    ValidationRejected,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_product"
OBJECTS = FIXTURE / "objects"

PRODUCT_ID = "spec-ne8000"
OVERVIEW = "DataProduct@spec-ne8000"
ONTOLOGY = "OntologyModule@device-spec-terms"
CONFLICTED = "spec-ne8000@DomainFactSet@NE8000-M16 V300R022"


def _fixture_documents() -> list[str]:
    return [p.read_text("utf-8") for p in sorted(OBJECTS.glob("*.md"))]


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
async def product(service: KnowledgeProductService) -> dict:
    return await service.create_product(
        product_id=PRODUCT_ID,
        product_type="specification_table",
        name="NE8000 产品规格矩阵",
        owner="zhangsan",
        purpose="比较这些型号的能力，支持专业问答与方案准备",
        object_rules={"row_identity": ["model", "product_version"]},
        scope_items=[
            ScopeItem("doc_ne8000_hwinfo", "snap_0007", ["3", "3.1"]),
            ScopeItem("doc_ne8000_power_guide", "snap_0003", None),
        ],
    )


# ----------------------------------------------------------------- 建制品


@pytest.mark.asyncio
async def test_create_product_starts_at_draft_revision_one(product) -> None:
    assert product["lifecycle_status"] == "draft"
    assert product["current_draft_revision"] == 1
    assert product["released_revision"] is None
    # 51号批次3：制品身份不含 domain，归属由 owner 表达
    assert "domain" not in product
    assert product["owner"] == "zhangsan"


@pytest.mark.asyncio
async def test_get_unknown_product_raises(service) -> None:
    with pytest.raises(NotFound):
        await service.get_product("no-such-product")


# ----------------------------------------------------------------- 写草稿


@pytest.mark.asyncio
async def test_replace_draft_writes_all_objects(service, product) -> None:
    result = await service.replace_draft(PRODUCT_ID, _fixture_documents())

    assert result.revision_no == 2  # 建制品时已有修订 1
    assert result.object_count == 7
    # 首次写草稿：对上一版（空的修订 1）全是新增
    assert result.diff.total == 7
    assert len(result.diff.added) == 7


@pytest.mark.asyncio
async def test_dangling_edges_are_reported_not_blocking(service, product) -> None:
    """悬挂边是制作中的正常态（52号 A5）——照写不误，但要报出来。"""
    result = await service.replace_draft(PRODUCT_ID, _fixture_documents())
    assert result.dangling == ("RulePackage@power-budget-check",)


@pytest.mark.asyncio
async def test_structural_issues_reject_the_whole_batch(service, product) -> None:
    """不落半份：一个对象不合格，整批拒收。"""
    documents = _fixture_documents()
    documents.append("---\nid: broken@NoSuchType@x\ntype: NoSuchType\n---\n# 坏的\n")

    with pytest.raises(ValidationRejected) as exc:
        await service.replace_draft(PRODUCT_ID, documents)
    assert any(issue.code == "unknown_type" for issue in exc.value.issues)

    # 拒收后草稿修订号不动
    assert (await service.get_product(PRODUCT_ID))["current_draft_revision"] == 1


@pytest.mark.asyncio
async def test_conflicted_object_is_marked_unresolved(service, product) -> None:
    await service.replace_draft(PRODUCT_ID, _fixture_documents())
    rows = {r["object_id"]: r for r in await service.list_objects(PRODUCT_ID)}

    assert rows[CONFLICTED]["review_status"] == "unresolved"
    assert rows[OVERVIEW]["review_status"] == "agent_submitted"


# ----------------------------------------------------------------- 读


@pytest.mark.asyncio
async def test_object_md_round_trips_through_object_store(service, product) -> None:
    await service.replace_draft(PRODUCT_ID, _fixture_documents())
    md = await service.get_object_md(PRODUCT_ID, OVERVIEW)
    assert md.startswith("---")
    assert "# NE8000 产品规格矩阵" in md
    assert "## 边" in md


@pytest.mark.asyncio
async def test_list_objects_defaults_to_current_draft(service, product) -> None:
    await service.replace_draft(PRODUCT_ID, _fixture_documents())
    assert len(await service.list_objects(PRODUCT_ID)) == 7
    assert await service.list_objects(PRODUCT_ID, 1) == []


@pytest.mark.asyncio
async def test_backlinks_come_from_the_index_not_the_md(service, product) -> None:
    """F5：跨制品共享对象不逐条反向回填，引用方只能从索引反查。"""
    await service.replace_draft(PRODUCT_ID, _fixture_documents())

    backlinks = await service.list_backlinks(ONTOLOGY)
    referrers = {link["from_id"] for link in backlinks}
    assert len(referrers) == 6  # 总览 + 5 张对象卡

    # 而本体模块自己的 md 只回指制品身份对象
    md = await service.get_object_md(PRODUCT_ID, ONTOLOGY)
    assert md.count("[[") == 1 and OVERVIEW in md


# ----------------------------------------------------------------- diff


@pytest.mark.asyncio
async def test_second_draft_diffs_against_the_first(service, product) -> None:
    documents = _fixture_documents()
    first = await service.replace_draft(PRODUCT_ID, documents)

    edited = [
        doc.replace("满配功耗 4200 W", "满配功耗 4250 W") if "NE8000-X8" in doc else doc
        for doc in documents
    ]
    second = await service.replace_draft(PRODUCT_ID, edited)

    assert second.diff.modified == ("spec-ne8000@DomainFactSet@NE8000-X8 V300R022",)
    assert second.diff.added == () and second.diff.removed == ()

    explicit = await service.diff(PRODUCT_ID, first.revision_no, second.revision_no)
    assert explicit.modified == second.diff.modified


@pytest.mark.asyncio
async def test_resubmitting_the_same_batch_shows_no_change(service, product) -> None:
    documents = _fixture_documents()
    await service.replace_draft(PRODUCT_ID, documents)
    again = await service.replace_draft(PRODUCT_ID, documents)
    assert again.diff.is_empty


@pytest.mark.asyncio
async def test_removing_an_object_is_reported(service, product) -> None:
    removed_id = "spec-ne8000@DomainFactSet@NE8000-X16 V300R023"
    documents = _fixture_documents()
    first = await service.replace_draft(PRODUCT_ID, documents)
    # 按 frontmatter 的 id 精确剔除——总览的表格里也提到这个型号，不能按型号名过滤
    kept = [doc for doc in documents if f'id: "{removed_id}"' not in doc]
    second = await service.replace_draft(PRODUCT_ID, kept)

    assert second.diff.removed == (removed_id,)
    assert first.object_count - second.object_count == 1
    # 总览仍引用着它 → 变成悬挂边，报告但不阻断
    assert removed_id in second.dangling


@pytest.mark.asyncio
async def test_single_object_unified_diff(service, product) -> None:
    documents = _fixture_documents()
    first = await service.replace_draft(PRODUCT_ID, documents)
    edited = [
        doc.replace("满配功耗 4200 W", "满配功耗 4250 W") if "NE8000-X8" in doc else doc
        for doc in documents
    ]
    second = await service.replace_draft(PRODUCT_ID, edited)

    text = await service.diff_one(
        PRODUCT_ID,
        "spec-ne8000@DomainFactSet@NE8000-X8 V300R022",
        first.revision_no,
        second.revision_no,
    )
    lines = text.replace("\r", "").splitlines()
    assert any(line.startswith("-") and "满配功耗 4200 W" in line for line in lines)
    assert any(line.startswith("+") and "满配功耗 4250 W" in line for line in lines)


# ----------------------------------------------------------------- 证据


@pytest.mark.asyncio
async def test_evidence_is_extracted_per_field(service, repo, product) -> None:
    result = await service.replace_draft(PRODUCT_ID, _fixture_documents())
    evidence = await repo.list_evidence(PRODUCT_ID, result.revision_no)

    assert evidence, "对象卡的每个字段都该留下出处"
    # F3：segment_id 必须落库，否则无法 FK 校验「来源在允许范围内」
    assert all(row["segment_id"] for row in evidence)

    conflicted = [r for r in evidence if r["object_id"] == CONFLICTED]
    # 冲突字段的两个候选各自带出处——「口径不同」这件事本身要可回源
    assert len([r for r in conflicted if r["field_name"] == "key_specification"]) == 2


# ----------------------------------------------------------------- 发布


@pytest.mark.asyncio
async def test_publish_supersedes_the_previous_release(service, repo, product) -> None:
    first = await service.replace_draft(PRODUCT_ID, _fixture_documents())
    await repo.publish_revision(PRODUCT_ID, first.revision_no)

    second = await service.replace_draft(PRODUCT_ID, _fixture_documents()[:3])
    await repo.publish_revision(PRODUCT_ID, second.revision_no)

    statuses = {r["revision_no"]: r["status"] for r in await repo.list_revisions(PRODUCT_ID)}
    assert statuses[first.revision_no] == "superseded"
    assert statuses[second.revision_no] == "published"
    # 018 的部分唯一索引所保证的不变量：至多一个已发布修订
    assert list(statuses.values()).count("published") == 1

    assert (await service.get_product(PRODUCT_ID))["released_revision"] == second.revision_no
