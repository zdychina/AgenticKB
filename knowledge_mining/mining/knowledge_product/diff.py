"""Minimal diff between two product revisions（52号 D9 加项）。

D4（一个制品一批 md 对象）与 D9（首期不做完整入图闸门）叠加会让人审失去抓手：
一次提交动辄几十个对象，没有 diff 等于让人肉眼比对几十篇 md。这里取 newsfc
``pipeline/gate.py`` 的 diff 部分——**只做 diff**，沙箱构建、覆盖/只新增/撤销三选、
按任务回退都不做。

口径沿用 newsfc：文本按行计 ±，改动按对象归类。
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Mapping

ADDED = "added"
MODIFIED = "modified"
REMOVED = "removed"


@dataclass(frozen=True)
class ObjectChange:
    object_id: str
    change: str
    added_lines: int = 0
    removed_lines: int = 0


@dataclass(frozen=True)
class RevisionDiff:
    """两个修订之间的对象级变更清单。"""

    added: tuple[str, ...]
    modified: tuple[str, ...]
    removed: tuple[str, ...]
    changes: tuple[ObjectChange, ...]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.removed)

    @property
    def total(self) -> int:
        return len(self.added) + len(self.modified) + len(self.removed)


def _count_line_delta(before: str, after: str) -> tuple[int, int]:
    """→ ``(added_lines, removed_lines)``。"""
    added = removed = 0
    matcher = difflib.SequenceMatcher(
        None, before.splitlines(), after.splitlines(), autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def diff_revisions(
    before: Mapping[str, str], after: Mapping[str, str]
) -> RevisionDiff:
    """对比两个修订的 ``{object_id: raw_md}``。

    内容逐字节相同的对象不进任何清单——制作过程会反复重提同一批对象，只报真变的。
    """
    before_ids, after_ids = set(before), set(after)

    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)
    modified = sorted(
        oid for oid in before_ids & after_ids if before[oid] != after[oid]
    )

    changes: list[ObjectChange] = []
    for oid in added:
        changes.append(
            ObjectChange(oid, ADDED, added_lines=len(after[oid].splitlines()))
        )
    for oid in modified:
        gained, lost = _count_line_delta(before[oid], after[oid])
        changes.append(ObjectChange(oid, MODIFIED, added_lines=gained, removed_lines=lost))
    for oid in removed:
        changes.append(
            ObjectChange(oid, REMOVED, removed_lines=len(before[oid].splitlines()))
        )

    return RevisionDiff(
        added=tuple(added),
        modified=tuple(modified),
        removed=tuple(removed),
        changes=tuple(changes),
    )


def diff_object(before: str | None, after: str | None, *, object_id: str = "") -> str:
    """单对象前后对比，unified diff 文本。缺一侧时按空内容对比。"""
    return "".join(
        difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            (after or "").splitlines(keepends=True),
            fromfile=f"{object_id}@before" if object_id else "before",
            tofile=f"{object_id}@after" if object_id else "after",
        )
    )
