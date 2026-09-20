"""载体模块单测（52号计划 P1）。

覆盖 fixture 覆盖不到的路径：md_parser 的两处历史坑、edges 的 mentions 分支与
去重、logical_id 的 local 拼接、registry 的 scope/段数一致性、validate 的各类
issue。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.knowledge_product import (
    MENTIONS,
    Registry,
    build_edges,
    build_local,
    build_object,
    classify,
    parse_md,
    segment_count,
    split_id,
    validate_object,
)
from knowledge_mining.mining.knowledge_product.registry import TypeSpec
from knowledge_mining.mining.knowledge_product.validate import (
    EVIDENCE_INCOMPLETE,
    ID_SEGMENTS,
    LOCAL_MISMATCH,
    MISSING_FRONTMATTER,
    MISSING_SECTION,
    PRODUCT_MISMATCH,
    UNKNOWN_TYPE,
    VALUE_MISSING,
)


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load()


# ───────────────────────── md_parser ─────────────────────────


def test_parse_md_splits_frontmatter_body_and_edges() -> None:
    fm, body, edges = parse_md(
        "---\nid: A@b\ntype: X\n---\n正文一段。\n\n## 边\n- 关系: [[C@d]]\n"
    )
    assert fm == {"id": "A@b", "type": "X"}
    assert body.strip() == "正文一段。"
    assert edges.startswith("## 边")


def test_parse_md_without_frontmatter_or_edges() -> None:
    fm, body, edges = parse_md("光板正文，没有任何标记。")
    assert fm == {}
    assert body == "光板正文，没有任何标记。"
    assert edges == ""


def test_parse_md_normalizes_crlf_and_bare_cr() -> None:
    """残留的 \\r 会被渲染器当成额外空行，把表头与分隔符拆开 → GFM 整表判废。"""
    _, body, _ = parse_md("---\r\nid: A@b\r\n---\r\n| a | b |\r\r\n| - | - |\r\n")
    assert "\r" not in body
    assert body.splitlines() == ["| a | b |", "| - | - |"]


def test_parse_md_drops_blank_lines_inside_tables_only() -> None:
    _, body, _ = parse_md(
        "| a | b |\n\n| - | - |\n\n| 1 | 2 |\n\n普通段落一。\n\n普通段落二。\n"
    )
    lines = body.splitlines()
    assert lines[:3] == ["| a | b |", "| - | - |", "| 1 | 2 |"]
    # 段落之间的正常空行必须留着
    assert "" in lines[3:]


# ───────────────────────── logical_id ─────────────────────────


def test_split_id_two_and_three_segments() -> None:
    assert split_id("OntologyModule@site-device") == (None, "OntologyModule", "site-device")
    assert split_id("p@DomainFactSet@M8 V300R022") == ("p", "DomainFactSet", "M8 V300R022")
    assert segment_count("p@T@l") == 3


@pytest.mark.parametrize("bad", ["NoSeparator", "a@b@c@d"])
def test_split_id_rejects_other_shapes(bad: str) -> None:
    with pytest.raises(ValueError):
        split_id(bad)


def test_build_local_joins_fields_in_order() -> None:
    fm = {"fields": {"model": {"value": "M8"}, "product_version": {"value": "V300R022"}}}
    assert build_local(fm, ["model", "product_version"]) == "M8 V300R022"


def test_build_local_rejects_missing_value() -> None:
    with pytest.raises(ValueError, match="product_version"):
        build_local({"fields": {"model": {"value": "M8"}}}, ["model", "product_version"])


# ───────────────────────── edges ─────────────────────────


def test_inline_reference_without_edge_entry_becomes_mentions() -> None:
    edges = build_edges("正文里提到 [[X@y]]。", "## 边\n- 依赖: [[A@b]]\n", "me@T@1")
    assert {(e.relation, e.to) for e in edges} == {("依赖", "A@b"), (MENTIONS, "X@y")}


def test_typed_edge_wins_over_inline_duplicate() -> None:
    """同一目标同时出现在正文与 ## 边 → 只记一条有类型的（52号 F6）。"""
    edges = build_edges("见 [[A@b]]", "## 边\n- 依赖: [[A@b]]\n", "me@T@1")
    assert len(edges) == 1
    assert edges[0].relation == "依赖"


def test_one_line_may_carry_several_targets() -> None:
    edges = build_edges("", "## 边\n- 复用步骤: [[A@b]], [[C@d]]\n", "me@T@1")
    assert [e.to for e in edges] == ["A@b", "C@d"]
    assert {e.relation for e in edges} == {"复用步骤"}


def test_wikilink_alias_resolves_to_target() -> None:
    edges = build_edges("", "## 边\n- 依赖: [[A@b|别名]]\n", "me@T@1")
    assert edges[0].to == "A@b"


# ───────────────────────── registry ─────────────────────────


def test_registry_rejects_segment_count_contradicting_scope() -> None:
    with pytest.raises(ValueError, match="id_segments"):
        TypeSpec.from_dict("Bad", {"layer": "L", "scope": "cross", "id_segments": 3})


def test_registry_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="scope"):
        TypeSpec.from_dict("Bad", {"layer": "L", "scope": "nf"})


def test_registry_override_warns_but_applies(registry: Registry) -> None:
    warnings = registry.merge_overrides(
        {"DataProduct": {"layer": "Data", "scope": "cross", "id_segments": 2}}
    )
    assert warnings == ["对象类型 'DataProduct' 被覆盖"]


def test_registry_require_raises_on_unknown() -> None:
    with pytest.raises(ValueError, match="未知对象类型"):
        Registry.load().require("NoSuchType")


# ───────────────────────── classify ─────────────────────────


def test_classify_uses_path_fields_for_cross_scope() -> None:
    reg = Registry.from_mapping(
        {"Themed": {"layer": "Data", "scope": "cross", "path_fields": ["topic"]}}
    )
    assert classify("Themed@x", reg, {"topic": "power"}) == ("Data/power", "Themed@x.md")


def test_classify_rejects_missing_path_field() -> None:
    reg = Registry.from_mapping(
        {"Themed": {"layer": "Data", "scope": "cross", "path_fields": ["topic"]}}
    )
    with pytest.raises(ValueError, match="topic"):
        classify("Themed@x", reg, {})


# ───────────────────────── validate ─────────────────────────


def _card(**overrides) -> str:
    frontmatter = {
        "id": "p@DomainFactSet@M8 V300R022",
        "type": "DomainFactSet",
        "product": "p",
        "name": "M8",
        "fields": {
            "model": {
                "value": "M8",
                "evidence": [{"document_id": "d", "snapshot_id": "s", "segment_id": "g"}],
            },
            "product_version": {
                "value": "V300R022",
                "evidence": [{"document_id": "d", "snapshot_id": "s", "segment_id": "g"}],
            },
        },
    }
    frontmatter.update(overrides)
    import yaml

    return f"---\n{yaml.safe_dump(frontmatter, allow_unicode=True)}---\n正文\n\n## 边\n- 依赖: [[A@b]]\n"


def _codes(md: str, registry: Registry) -> set[str]:
    return {issue.code for issue in validate_object(build_object(md, registry), registry)}


def test_valid_card_has_no_issues(registry: Registry) -> None:
    assert _codes(_card(), registry) == set()


def test_unknown_type_short_circuits(registry: Registry) -> None:
    assert _codes(_card(type="NoSuchType"), registry) == {UNKNOWN_TYPE}


def test_id_segment_and_product_mismatch(registry: Registry) -> None:
    codes = _codes(_card(id="DomainFactSet@M8 V300R022"), registry)
    assert ID_SEGMENTS in codes and PRODUCT_MISMATCH in codes


def test_local_segment_must_match_local_from(registry: Registry) -> None:
    """Agent 自己拼 local 段就会撞上这条（52号 F4）。"""
    assert LOCAL_MISMATCH in _codes(_card(id="p@DomainFactSet@M8-V300R022"), registry)


def test_missing_required_frontmatter(registry: Registry) -> None:
    md = _card()
    assert MISSING_FRONTMATTER in _codes(md.replace("name: M8\n", ""), registry)


def test_missing_required_section(registry: Registry) -> None:
    md = _card().split("## 边")[0]
    assert MISSING_SECTION in _codes(md, registry)


def test_evidence_without_segment_id_is_rejected(registry: Registry) -> None:
    """F3：没有 segment_id 就没法 FK 校验「来源在允许范围内」。"""
    md = _card().replace("segment_id: g\n      ", "", 1)
    assert EVIDENCE_INCOMPLETE in _codes(md, registry)


def test_null_value_needs_a_registered_conflict(registry: Registry) -> None:
    card = _card()
    assert VALUE_MISSING in _codes(card.replace("value: M8", "value: null", 1), registry)


def test_null_value_with_conflict_is_allowed(registry: Registry) -> None:
    import yaml

    frontmatter = yaml.safe_load(_card().split("---\n")[1])
    frontmatter["fields"]["model"] = {
        "value": None,
        "conflict": {
            "reason": "两份资料口径不同",
            "candidates": [
                {
                    "value": "M8",
                    "evidence": {"document_id": "d", "snapshot_id": "s", "segment_id": "g"},
                }
            ],
        },
    }
    md = f"---\n{yaml.safe_dump(frontmatter, allow_unicode=True)}---\n正文\n\n## 边\n- 依赖: [[A@b]]\n"
    # local_from 取 model.value，冲突时拼不出 local —— 只应报 LOCAL_MISMATCH，不报 VALUE_MISSING
    codes = _codes(md, registry)
    assert VALUE_MISSING not in codes
