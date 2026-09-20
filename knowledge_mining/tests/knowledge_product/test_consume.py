"""消费面：只读已发布制品（52号 P6）。

覆盖 48号 §七 的核心承诺——网页和 MCP 读到同一个发布结果，草稿不对外；以及从
newsfc 照搬的调用纪律：批量上限、单项失败不阻断整批、响应总量护栏、零结果给
恢复码、snippet 不充当权威依据。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.knowledge_product import consume
from knowledge_mining.mining.knowledge_product.consume import (
    MATCH_ALL,
    REMOVE_OR_REPHRASE_TERM,
    USE_MATCH_ANY,
    ConsumeRejected,
)
from knowledge_mining.mining.knowledge_product.consume_service import ProductConsumeService
from knowledge_mining.mining.knowledge_product.content_store import (
    ArtifactContentStore,
    bucket_for,
)
from knowledge_mining.mining.knowledge_product.repository_memory import (
    MemoryKnowledgeProductRepository,
)
from knowledge_mining.mining.knowledge_product.service import KnowledgeProductService

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_product"
OBJECTS = FIXTURE / "objects"

PRODUCT_ID = "spec-ne8000"
X8_CARD = "spec-ne8000@DomainFactSet@NE8000-X8 V300R022"
OVERVIEW = "DataProduct@spec-ne8000"


def _documents() -> list[str]:
    return [p.read_text("utf-8") for p in sorted(OBJECTS.glob("*.md"))]


@pytest.fixture
def repo() -> MemoryKnowledgeProductRepository:
    return MemoryKnowledgeProductRepository()


@pytest.fixture
def content(tmp_path) -> ArtifactContentStore:
    return ArtifactContentStore(
        FakeObjectStore(root_path=str(tmp_path / "objects")),
        MemoryStorageObjectRepository(),
        bucket_for("agentickb-test-"),
    )


@pytest.fixture
def consume_service(repo, content) -> ProductConsumeService:
    return ProductConsumeService(repo, content)


@pytest_asyncio.fixture
async def drafted(repo, content) -> KnowledgeProductService:
    service = KnowledgeProductService(repo, content)
    await service.create_product(
        product_id=PRODUCT_ID, product_type="specification_table",
        name="NE8000 产品规格矩阵", owner="zhangsan",
        purpose="比较这些型号的能力", fields={"model": {"required": True}},
        object_rules={}, scope_items=[],
    )
    await service.replace_draft(PRODUCT_ID, _documents())
    return service


@pytest_asyncio.fixture
async def published(drafted, repo) -> KnowledgeProductService:
    product = await drafted.get_product(PRODUCT_ID)
    await repo.publish_revision(PRODUCT_ID, int(product["current_draft_revision"]))
    return drafted


# ----------------------------------------------------------------- 只读已发布


@pytest.mark.asyncio
async def test_draft_only_product_is_invisible(consume_service, drafted) -> None:
    """草稿是负责人的在制品，不是对外内容。"""
    assert await consume_service.catalog() == []
    with pytest.raises(ConsumeRejected) as exc:
        await consume_service.outline(PRODUCT_ID)
    assert exc.value.code == consume.OBJECT_NOT_FOUND

    result = await consume_service.fetch([X8_CARD])
    assert result[X8_CARD]["ok"] is False


@pytest.mark.asyncio
async def test_published_product_shows_up_in_the_catalog(consume_service, published) -> None:
    catalog = await consume_service.catalog()
    assert len(catalog) == 1
    assert catalog[0]["product_id"] == PRODUCT_ID
    assert catalog[0]["object_count"] == 7
    assert catalog[0]["released_revision"] is not None


@pytest.mark.asyncio
async def test_outline_lists_the_published_objects(consume_service, published) -> None:
    outline = await consume_service.outline(PRODUCT_ID)
    assert len(outline["objects"]) == 7
    assert {o["id"] for o in outline["objects"]} >= {X8_CARD, OVERVIEW}


@pytest.mark.asyncio
async def test_new_draft_does_not_change_what_consumers_see(
    consume_service, published, repo
) -> None:
    """草稿失败或新版本未发布时，旧的可用版本继续服务（48号 §七）。"""
    before = await consume_service.fetch([X8_CARD])
    await published.merge_draft(
        PRODUCT_ID, published.parse([_documents()[0]]),
    )
    after = await consume_service.fetch([X8_CARD])
    assert after[X8_CARD]["md"] == before[X8_CARD]["md"]
    assert after[X8_CARD]["revision"] == before[X8_CARD]["revision"]


# ----------------------------------------------------------------- 取原文


@pytest.mark.asyncio
async def test_fetch_returns_md_and_extracted_references(consume_service, published) -> None:
    result = await consume_service.fetch([X8_CARD])
    entry = result[X8_CARD]
    assert entry["ok"] is True
    assert entry["md"].startswith("---")
    # references 已经替 Agent 抽好，直接拿去下一轮 ids
    assert "DataProduct@spec-ne8000" in entry["references"]


@pytest.mark.asyncio
async def test_one_missing_id_does_not_spoil_the_batch(consume_service, published) -> None:
    result = await consume_service.fetch([X8_CARD, "no@such@object"])
    assert result[X8_CARD]["ok"] is True
    assert result["no@such@object"]["ok"] is False
    assert result["no@such@object"]["error_code"] == consume.OBJECT_NOT_FOUND


@pytest.mark.asyncio
async def test_duplicate_ids_are_deduped_once(consume_service, published) -> None:
    result = await consume_service.fetch([X8_CARD, X8_CARD, X8_CARD])
    assert list(result) == [X8_CARD]


@pytest.mark.asyncio
async def test_fetch_rejects_an_oversized_batch(consume_service, published) -> None:
    with pytest.raises(ConsumeRejected) as exc:
        await consume_service.fetch([f"p@T@{i}" for i in range(consume.MAX_FETCH_IDS + 1)])
    assert exc.value.code == consume.INVALID_ARGUMENT
    assert "分批" in exc.value.message


@pytest.mark.asyncio
async def test_fetch_rejects_an_empty_id_list(consume_service, published) -> None:
    with pytest.raises(ConsumeRejected):
        await consume_service.fetch([])


def test_response_size_guard_fails_the_whole_call() -> None:
    """宁可让 Agent 多跑一轮，也不要塞爆它的上下文。"""
    with pytest.raises(ConsumeRejected) as exc:
        consume.guard_response_size({"big": "x" * (consume.MAX_RESPONSE_BYTES + 1)})
    assert exc.value.code == consume.RESULT_TOO_LARGE


# ----------------------------------------------------------------- 搜索


@pytest.mark.asyncio
async def test_search_finds_by_object_id(consume_service, published) -> None:
    result = await consume_service.search(["NE8000-X8"])
    assert result.total >= 1
    assert any(hit.object_id == X8_CARD for hit in result.hits)


@pytest.mark.asyncio
async def test_search_finds_values_inside_structured_fields(consume_service, published) -> None:
    """事实型制品真正的数据值在 frontmatter 的 fields 里——那是能搜到的。"""
    result = await consume_service.search(["4200"])
    assert any(hit.object_id == X8_CARD for hit in result.hits)
    assert "fields" in result.hits[0].matched_in


@pytest.mark.asyncio
async def test_exact_id_match_outranks_a_mere_field_hit(consume_service, published) -> None:
    result = await consume_service.search([X8_CARD])
    assert result.hits[0].object_id == X8_CARD


@pytest.mark.asyncio
async def test_match_all_requires_every_term(consume_service, published) -> None:
    both = await consume_service.search(["NE8000-X8", "4200"], match=MATCH_ALL)
    assert both.total >= 1
    impossible = await consume_service.search(
        ["NE8000-X8", "绝不可能出现的词"], match=MATCH_ALL,
    )
    assert impossible.total == 0


@pytest.mark.asyncio
async def test_zero_results_carry_recovery_codes(consume_service, published) -> None:
    """零结果要告诉 Agent 下一步怎么救，而不是丢个空数组。"""
    result = await consume_service.search(
        ["NE8000-X8", "绝不可能出现的词"], match=MATCH_ALL,
    )
    assert USE_MATCH_ANY in result.recovery
    assert REMOVE_OR_REPHRASE_TERM in result.recovery
    # term_counts 指出是哪个词没救
    assert result.term_counts["绝不可能出现的词"] == 0
    assert result.term_counts["ne8000-x8"] > 0


@pytest.mark.asyncio
async def test_search_is_case_and_width_insensitive(consume_service, published) -> None:
    lower = await consume_service.search(["ne8000-x8"])
    upper = await consume_service.search(["NE8000-X8"])
    assert [h.object_id for h in lower.hits] == [h.object_id for h in upper.hits]


@pytest.mark.asyncio
async def test_facets_describe_the_whole_result_not_the_page(consume_service, published) -> None:
    result = await consume_service.search(["NE8000"], size=1)
    assert len(result.hits) == 1
    assert sum(result.facets["type"].values()) == result.total
    assert result.has_more


@pytest.mark.asyncio
async def test_paging_is_stable_across_calls(consume_service, published) -> None:
    """同一次查询两次调用必须同序，否则 Agent 的分页会错乱。"""
    first = await consume_service.search(["NE8000"], size=3, page=1)
    again = await consume_service.search(["NE8000"], size=3, page=1)
    assert [h.object_id for h in first.hits] == [h.object_id for h in again.hits]

    second = await consume_service.search(["NE8000"], size=3, page=2)
    assert not ({h.object_id for h in first.hits} & {h.object_id for h in second.hits})


@pytest.mark.asyncio
async def test_type_filter_narrows_the_result(consume_service, published) -> None:
    all_hits = await consume_service.search(["NE8000"])
    cards = await consume_service.search(["NE8000"], type_name="DomainFactSet")
    assert 0 < cards.total < all_hits.total
    assert {h.type for h in cards.hits} == {"DomainFactSet"}


@pytest.mark.asyncio
async def test_search_payload_says_snippets_are_not_authoritative(
    consume_service, published
) -> None:
    payload = (await consume_service.search(["NE8000-X8"])).as_dict()
    assert "get_product" in payload["note"]


@pytest.mark.parametrize("bad", [[], ["   "], ["x" * (consume.MAX_TERM_LEN + 1)]])
def test_term_validation_rejects_garbage(bad) -> None:
    with pytest.raises(ConsumeRejected):
        consume.normalize_terms(bad)


def test_too_many_terms_are_rejected() -> None:
    with pytest.raises(ConsumeRejected):
        consume.normalize_terms([f"t{i}" for i in range(consume.MAX_TERMS + 1)])


@pytest.mark.asyncio
async def test_bad_paging_arguments_are_rejected(consume_service, published) -> None:
    with pytest.raises(ConsumeRejected):
        await consume_service.search(["x"], page=0)
    with pytest.raises(ConsumeRejected):
        await consume_service.search(["x"], size=consume.MAX_PAGE_SIZE + 1)
    with pytest.raises(ConsumeRejected):
        await consume_service.search(["x"], match="maybe")


# ----------------------------------------------------------------- 资料状态（P7）


@pytest_asyncio.fixture
async def with_sources(repo, published):
    for snapshot in ("snap_0007", "snap_0011", "snap_0003"):
        repo.set_snapshot_state(snapshot)
    return published


@pytest.mark.asyncio
async def test_outline_reports_healthy_sources(consume_service, repo, with_sources) -> None:
    status = (await consume_service.outline(PRODUCT_ID))["source_status"]
    assert status["state"] == "ok"
    assert status["alerts"] == []


@pytest.mark.asyncio
async def test_outline_warns_when_a_source_was_updated(
    consume_service, repo, with_sources
) -> None:
    repo.mark_document_updated("doc_ne8000_power_guide")
    status = (await consume_service.outline(PRODUCT_ID))["source_status"]
    assert status["state"] == "stale"
    assert "过时" in status["detail"]


@pytest.mark.asyncio
async def test_outline_blocks_when_a_source_was_revoked(
    consume_service, repo, with_sources
) -> None:
    """48号 §八：资料失效时不能只挂待办继续暴露——消费方要看得见。"""
    repo.set_snapshot_state("snap_0007", lifecycle_status="REVOKED")
    status = (await consume_service.outline(PRODUCT_ID))["source_status"]
    assert status["state"] == "blocked"
    assert "请勿据此作出判断" in status["detail"]
    assert any(a["blocking"] for a in status["alerts"])


@pytest.mark.asyncio
async def test_unknown_snapshot_state_is_not_reported_as_healthy(
    consume_service, published
) -> None:
    """查不到快照现状就如实说，不要假装健康——这里快照压根没登记过。"""
    status = (await consume_service.outline(PRODUCT_ID))["source_status"]
    assert status["state"] == "blocked"
