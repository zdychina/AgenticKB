"""Event journal: 去重键 + 补读位置 + 缺口检测（50号 §3.3）。

> 「平台必须保存事件位置、去重键和最后一次成功补读位置。实时 WebSocket 断开后，
> 用历史接口补齐；**不能因为最终聊天回复出现，就假定前面的工具过程和结果都已
> 保存**。」

最后那句是本模块存在的理由：事件按 ``sequence`` 判连续，只要中间缺号就是缺口，
哪怕已经收到了状态为「完成」的那条。``cursor`` 只推进到**连续前缀**的末尾——
乱序到达的高位事件会被记下，但不会把 cursor 拖过缺口，否则补读就永远补不回来了。

本模块与 DSH 无关，纯内存 + 纯逻辑，真适配器与 ``FakeHarness`` 共用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from knowledge_mining.mining.harness_adapter.port import HarnessEvent


@dataclass(frozen=True)
class Gap:
    """一段没收到的事件位置（闭区间）。"""

    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1


class EventJournal:
    """一个会话的过程事件账本。"""

    def __init__(self, *, start_sequence: int = 0) -> None:
        #: 连续前缀的末位。补读从这里之后继续。
        self._cursor = start_sequence - 1
        self._start = start_sequence
        self._by_id: dict[str, HarnessEvent] = {}
        self._by_sequence: dict[int, HarnessEvent] = {}

    # ---------------------------------------------------------------- 状态

    @property
    def cursor(self) -> int:
        """最后一次**连续**收到的位置。缺口之后的事件不会推进它。"""
        return self._cursor

    @property
    def next_sequence(self) -> int:
        """下次补读应当从哪个位置之后要。"""
        return self._cursor

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[HarnessEvent]:
        """按 sequence 升序遍历已收事件（含缺口之后的）。"""
        return iter(sorted(self._by_sequence.values(), key=lambda e: e.sequence))

    def has(self, event_id: str) -> bool:
        return event_id in self._by_id

    # ---------------------------------------------------------------- 写入

    def ingest(self, events: Iterable[HarnessEvent]) -> list[HarnessEvent]:
        """收一批事件，返回其中**真正新增**的那些（按 ``event_id`` 去重）。

        同一 ``event_id`` 重复到达直接丢弃；同一 ``sequence`` 换了 ``event_id``
        视为 harness 侧重排，以先到的为准——宁可漏记一条，也不让页面上同一位置
        出现两种内容。
        """
        fresh: list[HarnessEvent] = []
        for event in events:
            if event.event_id in self._by_id:
                continue
            if event.sequence in self._by_sequence:
                continue
            self._by_id[event.event_id] = event
            self._by_sequence[event.sequence] = event
            fresh.append(event)

        self._advance()
        return fresh

    def _advance(self) -> None:
        nxt = self._cursor + 1
        while nxt in self._by_sequence:
            self._cursor = nxt
            nxt += 1

    # ---------------------------------------------------------------- 缺口

    def gaps(self) -> list[Gap]:
        """→ 已知范围内还没收到的位置段。空表示过程是完整的。"""
        if not self._by_sequence:
            return []
        highest = max(self._by_sequence)
        out: list[Gap] = []
        missing_start: int | None = None
        for seq in range(self._start, highest + 1):
            if seq in self._by_sequence:
                if missing_start is not None:
                    out.append(Gap(missing_start, seq - 1))
                    missing_start = None
            elif missing_start is None:
                missing_start = seq
        if missing_start is not None:  # pragma: no cover - highest 必然在册
            out.append(Gap(missing_start, highest))
        return out

    @property
    def needs_backfill(self) -> bool:
        """有缺口就得补读——**哪怕已经收到了「完成」那条**。"""
        return bool(self.gaps())

    def events_of(self, kind: str) -> list[HarnessEvent]:
        return [event for event in self if event.kind == kind]


def project(events: Sequence[HarnessEvent]) -> list[dict]:
    """把事件投影成页面要的形状——人不该为了看过程去翻 harness 后台（50号 §3.3）。"""
    return [
        {
            "sequence": event.sequence,
            "kind": event.kind,
            "created_at": event.created_at,
            **event.payload,
        }
        for event in sorted(events, key=lambda e: e.sequence)
    ]
