"""Evidence scope enforcement（52号 P2，50号 §5.3 第五道）。

「平台校验这些证据是否真的在本次允许资料范围内，**不能信任 Agent 自报的来源**」。
这条能成立的唯一支点是 ``segment_id``：它外键到 ``asset_raw_segments``，平台据此
查出这一段真属于哪个快照、在哪个章节下。``anchor`` 是自由文本、校验不了。

章节匹配规则：``asset_raw_segments.section_path`` 存的是 ``[{level, title}]`` 标题链
（不是「3.1」这种编号），所以 ``allowed_sections`` 的每一项按**标题前缀**匹配标题链
上的任意一级——配 ``"3"`` 能放行标题为「3 硬件规格」的那一级及其子级。
``allowed_sections`` 为空/缺省 = 整篇可读。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence

from knowledge_mining.mining.knowledge_product.evidence import EvidenceRef


@dataclass(frozen=True)
class SegmentLocation:
    """一个段落的真实归属——由平台查出来的，不是 Agent 说的。"""

    segment_id: str
    snapshot_id: str
    section_titles: tuple[str, ...]


class SegmentLocator(Protocol):
    """按 segment_id 批量查真实归属。"""

    async def locate(self, segment_ids: Sequence[str]) -> dict[str, SegmentLocation]: ...


def parse_section_titles(section_path: Any) -> tuple[str, ...]:
    """``[{level, title}]``（TEXT 存的 JSON，或已解析的 list）→ 标题元组。"""
    if not section_path:
        return ()
    if isinstance(section_path, str):
        try:
            section_path = json.loads(section_path)
        except (TypeError, ValueError):
            return ()
    if not isinstance(section_path, list):
        return ()
    titles = []
    for node in section_path:
        if isinstance(node, dict) and node.get("title"):
            titles.append(str(node["title"]).strip())
        elif isinstance(node, str) and node.strip():
            titles.append(node.strip())
    return tuple(titles)


def section_allowed(
    section_titles: Iterable[str], allowed_sections: Sequence[str] | None
) -> bool:
    """标题链上任意一级命中任一允许项的前缀即放行。"""
    if not allowed_sections:
        return True  # 未限定 = 整篇可读
    wanted = [str(s).strip() for s in allowed_sections if str(s).strip()]
    if not wanted:
        return True
    return any(
        title.startswith(section)
        for title in (t.strip() for t in section_titles)
        for section in wanted
    )


@dataclass(frozen=True)
class ScopeViolation:
    object_id: str
    field_name: str
    segment_id: str
    reason: str


class ScopeGuard:
    """按制品的 ``kp_scope_items`` 判定一批证据是否越权。"""

    def __init__(
        self, scope_items: Sequence[dict[str, Any]], locator: SegmentLocator
    ) -> None:
        # snapshot_id → allowed_sections；同一快照多条时取并集语义（任一放行即放行）
        self._allowed: dict[str, list[str] | None] = {}
        for item in scope_items:
            snapshot_id = item["snapshot_id"]
            sections = item.get("allowed_sections_json") or item.get("allowed_sections")
            if isinstance(sections, str):
                try:
                    sections = json.loads(sections)
                except (TypeError, ValueError):
                    sections = None
            if snapshot_id in self._allowed and self._allowed[snapshot_id] is None:
                continue  # 已有一条不限章节的，整篇可读
            if sections is None:
                self._allowed[snapshot_id] = None
            else:
                self._allowed.setdefault(snapshot_id, [])
                if self._allowed[snapshot_id] is not None:
                    self._allowed[snapshot_id].extend(str(s) for s in sections)
        self._locator = locator

    @property
    def snapshot_ids(self) -> frozenset[str]:
        return frozenset(self._allowed)

    async def check(self, refs: Sequence[EvidenceRef]) -> list[ScopeViolation]:
        """→ 违规清单；空表示全部证据都在允许范围内。"""
        if not refs:
            return []

        located = await self._locator.locate([ref.segment_id for ref in refs])
        violations: list[ScopeViolation] = []

        for ref in refs:
            location = located.get(ref.segment_id)
            if location is None:
                violations.append(
                    ScopeViolation(
                        ref.object_id, ref.field_name, ref.segment_id,
                        "该段落在平台中不存在——证据无法回源",
                    )
                )
                continue

            # Agent 自报的 snapshot_id 不作数，以平台查出来的为准
            if location.snapshot_id != ref.snapshot_id:
                violations.append(
                    ScopeViolation(
                        ref.object_id, ref.field_name, ref.segment_id,
                        f"该段落实属快照 {location.snapshot_id!r}，与自报的 "
                        f"{ref.snapshot_id!r} 不符",
                    )
                )
                continue

            if location.snapshot_id not in self._allowed:
                violations.append(
                    ScopeViolation(
                        ref.object_id, ref.field_name, ref.segment_id,
                        "该快照不在本次任务允许的资料范围内",
                    )
                )
                continue

            if not section_allowed(location.section_titles, self._allowed[location.snapshot_id]):
                violations.append(
                    ScopeViolation(
                        ref.object_id, ref.field_name, ref.segment_id,
                        "该段落所在章节不在本次任务允许的范围内",
                    )
                )

        return violations
