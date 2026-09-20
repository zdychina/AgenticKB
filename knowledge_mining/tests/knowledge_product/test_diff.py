"""最小 diff 单测（52号 D9 加项）。"""
from __future__ import annotations

from knowledge_mining.mining.knowledge_product.diff import (
    ADDED,
    MODIFIED,
    REMOVED,
    diff_object,
    diff_revisions,
)


def test_empty_diff_between_identical_revisions() -> None:
    snapshot = {"a": "x\ny\n", "b": "z\n"}
    result = diff_revisions(snapshot, dict(snapshot))
    assert result.is_empty
    assert result.total == 0


def test_unchanged_objects_are_not_reported() -> None:
    """制作过程会反复重提同一批对象，只报真变的。"""
    result = diff_revisions({"a": "same\n", "b": "old\n"}, {"a": "same\n", "b": "new\n"})
    assert result.modified == ("b",)
    assert result.added == () and result.removed == ()


def test_added_modified_removed_are_classified() -> None:
    result = diff_revisions(
        {"keep": "1\n", "drop": "2\n", "edit": "3\n"},
        {"keep": "1\n", "edit": "3 changed\n", "new": "4\n"},
    )
    assert result.added == ("new",)
    assert result.modified == ("edit",)
    assert result.removed == ("drop",)
    assert result.total == 3
    assert {c.change for c in result.changes} == {ADDED, MODIFIED, REMOVED}


def test_line_deltas_are_counted_per_object() -> None:
    result = diff_revisions({"a": "l1\nl2\nl3\n"}, {"a": "l1\nl2 changed\nl3\nl4\n"})
    change = next(c for c in result.changes if c.object_id == "a")
    assert change.change == MODIFIED
    assert change.added_lines == 2  # 改掉的一行 + 新增的一行
    assert change.removed_lines == 1


def test_added_object_counts_all_its_lines() -> None:
    result = diff_revisions({}, {"a": "l1\nl2\n"})
    change = result.changes[0]
    assert change.change == ADDED
    assert change.added_lines == 2 and change.removed_lines == 0


def test_first_draft_diffs_against_nothing() -> None:
    result = diff_revisions({}, {"a": "x\n", "b": "y\n"})
    assert result.added == ("a", "b")
    assert result.modified == () and result.removed == ()


def test_diff_object_produces_unified_text() -> None:
    text = diff_object("before\n", "after\n", object_id="obj@1")
    assert "obj@1@before" in text and "obj@1@after" in text
    assert "-before" in text and "+after" in text


def test_diff_object_tolerates_missing_side() -> None:
    assert "+new\n" in diff_object(None, "new\n")
    assert "-gone\n" in diff_object("gone\n", None)
