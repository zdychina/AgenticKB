"""事件账本单测（52号 P3，50号 §3.3）。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.harness_adapter import (
    MESSAGE,
    STATUS,
    TOOL_CALL,
    EventJournal,
    Gap,
    HarnessEvent,
    project,
)


def _event(sequence: int, kind: str = MESSAGE, event_id: str | None = None) -> HarnessEvent:
    return HarnessEvent(
        event_id=event_id or f"e{sequence}",
        sequence=sequence,
        kind=kind,
        payload={"text": f"#{sequence}"},
        created_at="2026-09-20T00:00:00+00:00",
    )


def test_event_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="事件种类"):
        HarnessEvent(event_id="e", sequence=0, kind="whatever")


def test_event_rejects_negative_sequence() -> None:
    with pytest.raises(ValueError, match="sequence"):
        HarnessEvent(event_id="e", sequence=-1, kind=MESSAGE)


def test_contiguous_events_advance_the_cursor() -> None:
    journal = EventJournal()
    assert journal.cursor == -1

    journal.ingest([_event(0), _event(1), _event(2)])
    assert journal.cursor == 2
    assert len(journal) == 3


def test_ingest_returns_only_fresh_events() -> None:
    journal = EventJournal()
    journal.ingest([_event(0), _event(1)])

    fresh = journal.ingest([_event(0), _event(1), _event(2)])
    assert [e.sequence for e in fresh] == [2]


def test_reconnect_replay_does_not_duplicate() -> None:
    """断线重连会把同一批事件再送一遍——靠 event_id 去重，不靠「看起来一样」。"""
    journal = EventJournal()
    batch = [_event(0), _event(1), _event(2)]
    journal.ingest(batch)
    assert journal.ingest(batch) == []
    assert len(journal) == 3


def test_same_sequence_with_a_new_id_is_ignored() -> None:
    """同位置换了 id 视为 harness 侧重排——以先到的为准，页面不出现两种内容。"""
    journal = EventJournal()
    journal.ingest([_event(0, event_id="first")])
    assert journal.ingest([_event(0, event_id="second")]) == []
    assert next(iter(journal)).event_id == "first"


def test_cursor_stops_at_the_gap() -> None:
    """乱序到达的高位事件记下来，但不能把 cursor 拖过缺口——否则永远补不回来。"""
    journal = EventJournal()
    journal.ingest([_event(0), _event(1), _event(5)])

    assert journal.cursor == 1
    assert len(journal) == 3
    assert journal.gaps() == [Gap(2, 4)]
    assert journal.needs_backfill


def test_filling_the_gap_advances_the_cursor_past_it() -> None:
    journal = EventJournal()
    journal.ingest([_event(0), _event(1), _event(5)])
    journal.ingest([_event(2), _event(3), _event(4)])

    assert journal.cursor == 5
    assert journal.gaps() == []
    assert not journal.needs_backfill


def test_a_terminal_status_does_not_prove_the_process_is_complete() -> None:
    """50号 §3.3 那句话的直接断言：收到「完成」不等于中间过程都已保存。"""
    journal = EventJournal()
    journal.ingest([_event(0), _event(4, kind=STATUS)])

    assert journal.events_of(STATUS), "确实收到了终态事件"
    assert journal.needs_backfill, "但中间缺口仍在，必须补读"


def test_multiple_gaps_are_reported_separately() -> None:
    journal = EventJournal()
    journal.ingest([_event(0), _event(3), _event(7)])
    assert journal.gaps() == [Gap(1, 2), Gap(4, 6)]
    assert [g.size for g in journal.gaps()] == [2, 3]


def test_empty_journal_has_no_gaps() -> None:
    journal = EventJournal()
    assert journal.gaps() == []
    assert not journal.needs_backfill
    assert journal.next_sequence == -1


def test_projection_is_ordered_and_flattens_payload() -> None:
    journal = EventJournal()
    journal.ingest([_event(2), _event(0), _event(1, kind=TOOL_CALL)])

    rows = project(list(journal))
    assert [r["sequence"] for r in rows] == [0, 1, 2]
    assert rows[1]["kind"] == TOOL_CALL
    assert rows[0]["text"] == "#0"
