"""样品制品 fixture 跑通真实载体模块（52号计划 P1 验收）。

fixture 由 ``knowledge_mining.mining.knowledge_product`` 的真实模块解析、建边、
校验——不再有测试内的临时解析函数。fixture 的设计结论见
``tests/fixtures/sample_product/README.md``。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_mining.mining.knowledge_product import (
    MENTIONS,
    ProductObject,
    Registry,
    build_object,
    storage_key,
    validate_batch,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_product"
OBJECTS = FIXTURE / "objects"
MD_FILES = sorted(OBJECTS.glob("*.md"))

# 制作过程中的正常中间态：总览已引用但本制品尚未产出的规则包（52号 A5）
EXPECTED_DANGLING = {"RulePackage@power-budget-check"}

ONTOLOGY = "OntologyModule@device-spec-terms"
OVERVIEW = "DataProduct@spec-ne8000"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load()


@pytest.fixture(scope="module")
def objects(registry: Registry) -> dict[str, ProductObject]:
    loaded = {}
    for path in MD_FILES:
        obj = build_object(path.read_text("utf-8"), registry)
        loaded[obj.id] = obj
    assert loaded, "fixture 里没有对象"
    return loaded


def test_fixture_has_no_structural_issues(objects, registry) -> None:
    issues, dangling = validate_batch(objects.values(), registry)
    assert issues == [], "\n".join(f"{i.object_id}: [{i.code}] {i.detail}" for i in issues)
    assert dangling == EXPECTED_DANGLING


@pytest.mark.parametrize("path", MD_FILES, ids=lambda p: p.stem)
def test_filename_equals_logical_id(path: Path, registry: Registry) -> None:
    """newsfc 铁律：文件名 = 完整逻辑 ID，空格原样保留。"""
    assert build_object(path.read_text("utf-8"), registry).id == path.stem


def test_edge_counts(objects) -> None:
    edges = [edge for obj in objects.values() for edge in obj.edges]
    assert len(edges) == 22, "边按 (from, relation, to) 去重后应为 22 条（52号 F6）"
    # fixture 里每个内联 [[ID]] 都在 ## 边 段有对应关系，有类型的那条胜出
    assert all(edge.is_typed for edge in edges)
    assert not any(edge.relation == MENTIONS for edge in edges)


def test_product_scoped_objects_belong_to_the_product(objects) -> None:
    cards = [obj for obj in objects.values() if obj.scope == "product"]
    assert len(cards) == 5
    assert {card.product for card in cards} == {"spec-ne8000"}


def test_conflicted_object_is_not_publishable(objects) -> None:
    """有冲突字段的对象卡必须标成 unresolved，不能混进可发布集合。"""
    conflicted = [
        obj for obj in objects.values()
        if any(f.get("conflict") for f in obj.fields.values())
    ]
    assert len(conflicted) == 1
    assert conflicted[0].frontmatter["review_status"] == "unresolved"


def test_cross_scope_object_is_not_backfilled_per_reference(objects) -> None:
    """F5：跨制品共享对象**不**逐条反向回填——反向边由 ``kp_edges`` 反查提供。

    newsfc 的「被引用对象在 ## 边 反向回填」只在单一资产库、全量重建索引时成立。
    这里本体模块被 5 张对象卡引用；逐条回填会让它的 ## 边 随引用方增长，而引用方
    属于别的制品、别的修订，对它并无写权限。
    """
    referrers = {
        obj.id for obj in objects.values()
        if any(edge.to == ONTOLOGY for edge in obj.edges)
    }
    assert len(referrers) >= 5, "fixture 应有多个引用方才能验证这条"

    backfilled = {edge.to for edge in objects[ONTOLOGY].edges}
    assert backfilled == {OVERVIEW}, "跨制品共享对象只回指制品身份对象，不逐条回填引用方"


def test_storage_keys_separate_product_from_cross(objects, registry) -> None:
    """product scope 的对象归到制品 slug 下，cross scope 的不归任何制品。"""
    card = objects["spec-ne8000@DomainFactSet@NE8000-M8 V300R022"]
    assert storage_key(card.id, registry, card.frontmatter) == (
        "spec-ne8000/Fact/spec-ne8000@DomainFactSet@NE8000-M8 V300R022.md"
    )

    overview = objects[OVERVIEW]
    assert storage_key(overview.id, registry, overview.frontmatter) == (
        "Data/DataProduct@spec-ne8000.md"
    )
