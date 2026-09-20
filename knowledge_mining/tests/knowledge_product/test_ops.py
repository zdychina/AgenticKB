"""制品运营：定义修订、报告问题、资料变更影响（52号 P7，48号 §八）。"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.knowledge_product import impact
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
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_product"
OBJECTS = FIXTURE / "objects"
PRODUCT_ID = "spec-ne8000"
X8_CARD = "spec-ne8000@DomainFactSet@NE8000-X8 V300R022"

# fixture 的证据引用的三个快照
HWINFO = "snap_0007"
RELEASE_NOTES = "snap_0011"
POWER_GUIDE = "snap_0003"


def _documents() -> list[str]:
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
async def drafted(service, repo) -> KnowledgeProductService:
    await service.create_product(
        product_id=PRODUCT_ID, product_type="specification_table",
        name="NE8000 产品规格矩阵", owner="zhangsan", purpose="比较这些型号的能力",
        fields={"model": {"required": True}},
        object_rules={"row_identity_rule": "同一型号和产品版本为同一行"},
        scope_items=[
            ScopeItem("doc_ne8000_hwinfo", HWINFO, ["3"]),
            ScopeItem("doc_ne8000_release_notes", RELEASE_NOTES, ["2.4"]),
            ScopeItem("doc_ne8000_power_guide", POWER_GUIDE, None),
        ],
    )
    await service.replace_draft(PRODUCT_ID, _documents())
    for snapshot in (HWINFO, RELEASE_NOTES, POWER_GUIDE):
        repo.set_snapshot_state(snapshot)
    return service


# ----------------------------------------------------------------- 定义修订


@pytest.mark.asyncio
async def test_updating_the_definition_opens_a_new_revision(drafted) -> None:
    """改定义 = 开新一版，不是原地改。"""
    before = await drafted.current_definition(PRODUCT_ID)
    assert before["definition_revision"] == 1

    after = await drafted.update_definition(
        PRODUCT_ID, editor="zhangsan",
        fields={"model": {"required": True}, "unit": {"required": True}},
    )
    assert after["definition_revision"] == 2
    assert "unit" in after["fields_json"]


@pytest.mark.asyncio
async def test_old_definition_is_kept(drafted, repo) -> None:
    """已发布修订指向旧定义，删了就没法解释「那一版按什么规则做的」。"""
    await drafted.update_definition(PRODUCT_ID, editor="zhangsan", fields={"x": {}})
    old = await repo.get_definition(PRODUCT_ID, 1)
    assert old is not None
    assert "model" in old["fields_json"]


@pytest.mark.asyncio
async def test_unspecified_parts_are_inherited(drafted, repo) -> None:
    """改字段定义不该顺手把资料范围清空。"""
    await drafted.update_definition(PRODUCT_ID, editor="zhangsan", fields={"x": {}})

    scope = await repo.list_scope_items(PRODUCT_ID, 2)
    assert {item["snapshot_id"] for item in scope} == {HWINFO, RELEASE_NOTES, POWER_GUIDE}
    definition = await drafted.current_definition(PRODUCT_ID)
    assert definition["object_rules_json"]["row_identity_rule"]


@pytest.mark.asyncio
async def test_scope_can_be_narrowed_explicitly(drafted, repo) -> None:
    await drafted.update_definition(
        PRODUCT_ID, editor="zhangsan",
        scope_items=[ScopeItem("doc_ne8000_hwinfo", HWINFO, ["3"])],
    )
    scope = await repo.list_scope_items(PRODUCT_ID, 2)
    assert {item["snapshot_id"] for item in scope} == {HWINFO}


@pytest.mark.asyncio
async def test_next_draft_uses_the_new_definition(drafted, repo) -> None:
    """改了定义如果新草稿还按旧定义走，那「改定义」这件事对制作毫无效果。"""
    await drafted.update_definition(PRODUCT_ID, editor="zhangsan", fields={"x": {}})
    result = await drafted.merge_draft(PRODUCT_ID, drafted.parse([_documents()[0]]))

    revision = await repo.get_revision(PRODUCT_ID, result.revision_no)
    assert revision["definition_revision"] == 2


@pytest.mark.asyncio
async def test_updating_an_unknown_product_is_refused(drafted) -> None:
    with pytest.raises(NotFound):
        await drafted.update_definition("no-such-product", editor="zhangsan")


# ----------------------------------------------------------------- 报告问题


@pytest.mark.asyncio
async def test_issue_defaults_to_the_released_revision(drafted, repo) -> None:
    """报告问题的人用的是对外那一份，不是负责人的草稿。"""
    product = await drafted.get_product(PRODUCT_ID)
    await repo.publish_revision(PRODUCT_ID, int(product["current_draft_revision"]))
    # 发布之后负责人又往草稿里写了一版
    await drafted.merge_draft(PRODUCT_ID, drafted.parse([_documents()[0]]))

    issue = await drafted.report_issue(
        PRODUCT_ID, problem="X8 的功耗口径不对", reporter="lisi",
    )
    released = (await drafted.get_product(PRODUCT_ID))["released_revision"]
    assert issue["used_revision"] == released
    assert issue["status"] == "open"


@pytest.mark.asyncio
async def test_issue_can_point_at_a_field(drafted) -> None:
    issue = await drafted.report_issue(
        PRODUCT_ID, problem="单位错了", reporter="lisi",
        object_id=X8_CARD, field_name="key_specification",
        task="给客户做选型对比", correction_basis="功耗指南 2.2 表2-1",
    )
    assert issue["object_id"] == X8_CARD
    assert issue["field_name"] == "key_specification"
    assert issue["correction_basis"] == "功耗指南 2.2 表2-1"


@pytest.mark.asyncio
async def test_issues_are_listed_newest_first(drafted) -> None:
    await drafted.report_issue(PRODUCT_ID, problem="第一个", reporter="a")
    await drafted.report_issue(PRODUCT_ID, problem="第二个", reporter="b")
    issues = await drafted.list_issues(PRODUCT_ID)
    assert [i["problem"] for i in issues] == ["第二个", "第一个"]


@pytest.mark.asyncio
async def test_resolution_records_the_direction_not_just_done(drafted) -> None:
    """同一个问题可能该改资料、该改定义、该改内容，也可能该改 Agent 用法。"""
    issue = await drafted.report_issue(PRODUCT_ID, problem="口径不对", reporter="lisi")
    resolved = await drafted.resolve_issue(
        issue["id"], status="resolved", resolved_by="zhangsan",
        resolution_kind="definition", resolution_note="口径写进对象规则",
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolution_kind"] == "definition"

    assert await drafted.list_issues(PRODUCT_ID, status="open") == []
    assert len(await drafted.list_issues(PRODUCT_ID, status="resolved")) == 1


@pytest.mark.asyncio
async def test_bad_resolution_status_is_refused(drafted) -> None:
    issue = await drafted.report_issue(PRODUCT_ID, problem="x", reporter="a")
    with pytest.raises(ValueError, match="非法的处置状态"):
        await drafted.resolve_issue(issue["id"], status="open", resolved_by="z")


@pytest.mark.asyncio
async def test_resolving_an_unknown_issue_is_refused(drafted) -> None:
    with pytest.raises(NotFound):
        await drafted.resolve_issue("kpis_nope", status="resolved", resolved_by="z")


# ----------------------------------------------------------------- 影响分析


@pytest.mark.asyncio
async def test_healthy_sources_raise_no_alert(drafted) -> None:
    assert await drafted.source_alerts(PRODUCT_ID) == []


@pytest.mark.asyncio
async def test_a_newer_document_version_asks_for_review(drafted, repo) -> None:
    """第一类变化：新资料出现 → 提示复核，是否仍适用由责任人判断。"""
    repo.mark_document_updated("doc_ne8000_power_guide")
    alerts = await drafted.source_alerts(PRODUCT_ID)

    assert len(alerts) == 1
    assert alerts[0].kind == impact.SUPERSEDED
    assert alerts[0].blocking is False
    assert alerts[0].snapshot_id == POWER_GUIDE
    assert alerts[0].fields, "要列出具体是哪些字段引用了它"


@pytest.mark.asyncio
async def test_a_revoked_source_blocks(drafted, repo) -> None:
    """第二类变化：权限收回/明确失效 → 不能只挂待办继续暴露。"""
    repo.set_snapshot_state(HWINFO, lifecycle_status="REVOKED")
    alerts = await drafted.source_alerts(PRODUCT_ID)

    revoked = [a for a in alerts if a.kind == impact.REVOKED_SOURCE]
    assert revoked and revoked[0].blocking is True


@pytest.mark.asyncio
async def test_deprecated_source_warns_without_blocking(drafted, repo) -> None:
    repo.set_snapshot_state(HWINFO, lifecycle_status="DEPRECATED")
    alerts = await drafted.source_alerts(PRODUCT_ID)
    assert alerts[0].kind == impact.DEPRECATED_SOURCE
    assert alerts[0].blocking is False


@pytest.mark.asyncio
async def test_blocking_alerts_sort_first(drafted, repo) -> None:
    """人先看见的应该是最要紧的那条。"""
    repo.set_snapshot_state(HWINFO, lifecycle_status="REVOKED")
    repo.mark_document_updated("doc_ne8000_power_guide")
    alerts = await drafted.source_alerts(PRODUCT_ID)

    assert len(alerts) == 2
    assert alerts[0].blocking and not alerts[1].blocking


@pytest.mark.asyncio
async def test_a_vanished_snapshot_is_worse_than_deprecated(drafted, repo) -> None:
    """快照整个不见了——比废弃更严重：连回源都做不到。"""
    repo._snapshots.pop(HWINFO)
    alerts = await drafted.source_alerts(PRODUCT_ID)
    vanished = [a for a in alerts if a.snapshot_id == HWINFO]
    assert vanished and vanished[0].blocking
    assert "无法回源" in vanished[0].detail


@pytest.mark.asyncio
async def test_alert_payload_caps_the_affected_list(drafted, repo) -> None:
    repo.set_snapshot_state(HWINFO, lifecycle_status="REVOKED")
    payload = (await drafted.source_alerts(PRODUCT_ID))[0].as_dict()
    assert payload["blocking"] is True
    assert payload["affected_count"] >= len(payload["affected"])
    assert len(payload["affected"]) <= 20


# ----------------------------------------------------------------- 分类纯逻辑


def test_revoked_beats_superseded() -> None:
    """一份既被吊销、又有新版本的资料，要紧的是它已经不能用了。"""
    verdict = impact.classify_snapshot({"lifecycle_status": "REVOKED"}, has_newer=True)
    assert verdict[0] == impact.REVOKED_SOURCE


def test_ready_without_newer_is_healthy() -> None:
    assert impact.classify_snapshot({"lifecycle_status": "READY"}, has_newer=False) is None


def test_missing_lifecycle_defaults_to_ready() -> None:
    assert impact.classify_snapshot({}, has_newer=False) is None
