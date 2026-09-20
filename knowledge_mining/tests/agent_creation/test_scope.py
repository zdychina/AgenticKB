"""证据范围校验单测（52号 P2，50号 §5.3 第五道）。"""
from __future__ import annotations

import json

import pytest

from knowledge_mining.mining.agent_creation.repository_memory import MemorySegmentLocator
from knowledge_mining.mining.agent_creation.scope import (
    ScopeGuard,
    parse_section_titles,
    section_allowed,
)
from knowledge_mining.mining.knowledge_product.evidence import EvidenceRef


def _ref(segment_id: str, snapshot_id: str = "snap_a") -> EvidenceRef:
    return EvidenceRef(
        object_id="p@DomainFactSet@x",
        field_name="model",
        document_id="doc_a",
        snapshot_id=snapshot_id,
        segment_id=segment_id,
    )


# ----------------------------------------------------------------- section_path


def test_parse_section_titles_from_json_text() -> None:
    """生产真实形状：section_path 是 TEXT 列存 [{level, title}] JSON。"""
    raw = json.dumps([{"level": 1, "title": "3 硬件"}, {"level": 2, "title": "3.1 规格"}])
    assert parse_section_titles(raw) == ("3 硬件", "3.1 规格")


def test_parse_section_titles_tolerates_empty_and_garbage() -> None:
    assert parse_section_titles(None) == ()
    assert parse_section_titles("") == ()
    assert parse_section_titles("not json") == ()
    assert parse_section_titles([{"level": 1}]) == ()


def test_parse_section_titles_accepts_already_parsed_list() -> None:
    assert parse_section_titles([{"level": 1, "title": "A"}]) == ("A",)


# ----------------------------------------------------------------- 章节匹配


def test_unrestricted_scope_allows_everything() -> None:
    assert section_allowed(("任意章节",), None)
    assert section_allowed(("任意章节",), [])


def test_allowed_section_matches_title_prefix() -> None:
    """section_path 存的是标题（「3.1 硬件规格」），不是编号，所以按前缀匹配。"""
    assert section_allowed(("3 硬件", "3.1 硬件规格"), ["3.1"])
    assert section_allowed(("3 硬件", "3.1 硬件规格"), ["3"])


def test_parent_section_allows_child_via_its_own_level() -> None:
    assert section_allowed(("3 硬件", "3.1 硬件规格", "3.1.2 功耗"), ["3"])


def test_unrelated_section_is_not_allowed() -> None:
    assert not section_allowed(("4 运维", "4.2 告警"), ["3", "3.1"])


def test_segment_without_section_path_is_out_of_a_restricted_scope() -> None:
    assert not section_allowed((), ["3"])
    assert section_allowed((), None)


# ----------------------------------------------------------------- ScopeGuard


@pytest.mark.asyncio
async def test_evidence_inside_scope_passes() -> None:
    locator = MemorySegmentLocator()
    locator.add("seg_1", "snap_a", ["3 硬件", "3.1 硬件规格"])
    guard = ScopeGuard([{"snapshot_id": "snap_a", "allowed_sections_json": ["3"]}], locator)

    assert await guard.check([_ref("seg_1")]) == []


@pytest.mark.asyncio
async def test_unknown_segment_cannot_be_traced_back() -> None:
    guard = ScopeGuard([{"snapshot_id": "snap_a", "allowed_sections_json": None}],
                       MemorySegmentLocator())
    violations = await guard.check([_ref("seg_ghost")])
    assert len(violations) == 1
    assert "不存在" in violations[0].reason


@pytest.mark.asyncio
async def test_self_reported_snapshot_is_not_trusted() -> None:
    """Agent 说这段属于 snap_a，平台查出来是 snap_b —— 以平台为准。"""
    locator = MemorySegmentLocator()
    locator.add("seg_1", "snap_b", ["3 硬件"])
    guard = ScopeGuard([{"snapshot_id": "snap_a", "allowed_sections_json": None}], locator)

    violations = await guard.check([_ref("seg_1", snapshot_id="snap_a")])
    assert len(violations) == 1
    assert "snap_b" in violations[0].reason


@pytest.mark.asyncio
async def test_snapshot_outside_the_ticket_scope_is_rejected() -> None:
    locator = MemorySegmentLocator()
    locator.add("seg_1", "snap_other", ["3 硬件"])
    guard = ScopeGuard([{"snapshot_id": "snap_a", "allowed_sections_json": None}], locator)

    violations = await guard.check([_ref("seg_1", snapshot_id="snap_other")])
    assert "不在本次任务允许的资料范围内" in violations[0].reason


@pytest.mark.asyncio
async def test_section_outside_the_ticket_scope_is_rejected() -> None:
    locator = MemorySegmentLocator()
    locator.add("seg_1", "snap_a", ["4 运维", "4.2 告警"])
    guard = ScopeGuard([{"snapshot_id": "snap_a", "allowed_sections_json": ["3"]}], locator)

    violations = await guard.check([_ref("seg_1")])
    assert "章节" in violations[0].reason


@pytest.mark.asyncio
async def test_unrestricted_entry_wins_over_restricted_one_for_same_snapshot() -> None:
    """同一快照既有限章节又有整篇可读的条目时，取整篇。"""
    locator = MemorySegmentLocator()
    locator.add("seg_1", "snap_a", ["9 附录"])
    guard = ScopeGuard(
        [
            {"snapshot_id": "snap_a", "allowed_sections_json": ["3"]},
            {"snapshot_id": "snap_a", "allowed_sections_json": None},
        ],
        locator,
    )
    assert await guard.check([_ref("seg_1")]) == []


@pytest.mark.asyncio
async def test_empty_evidence_needs_no_lookup() -> None:
    guard = ScopeGuard([], MemorySegmentLocator())
    assert await guard.check([]) == []
