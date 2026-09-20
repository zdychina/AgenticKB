"""In-process fake harness: 让整条链路在 DSH 就绪之前就能端到端跑（52号 P3）。

它实现 ``HarnessPort``，内部跑一个**脚本化的 Agent**：收到启动指令后，按真实
Agent 该走的路走一遍——先 ``get_creation_context`` 取工作定义，再产出一批对象，
最后 ``submit_creation_result`` 交回去——并把每一步记成事件。

它验证的是**平台侧**：票据是否收窄了范围、上下文够不够 Agent 开工、提交校验与幂等
是否成立、过程事件能不能被投影回页面。它**不**验证 DSH 的原生协议、内网模型是否
真支持工具调用——那两件只有真 DSH 能验（50号 的 T0/T1）。

所以：FakeHarness 跑通 ≠ 可以上线。它是让 P4 人审那一段不必空等 DSH 的脚手架。
"""
from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Sequence

from knowledge_mining.mining.harness_adapter.port import (
    ERROR,
    MESSAGE,
    STATUS,
    TOOL_CALL,
    TOOL_RESULT,
    HarnessEvent,
)

#: 脚本：拿到 creation context，返回这一轮要提交的 md 列表。返回空表示无话可说。
AgentScript = Callable[[dict[str, Any]], Awaitable[Sequence[str]]]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Session:
    session_id: str
    instance_id: str
    counter: itertools.count = field(default_factory=lambda: itertools.count(0))
    events: list[HarnessEvent] = field(default_factory=list)
    seen_request_ids: set[str] = field(default_factory=set)
    cancelled: bool = False
    ticket: str | None = None


class FakeHarness:
    """脚本化 harness。

    ``creation`` 传 ``AgentCreationService``；``script`` 决定这个「Agent」交什么。
    """

    def __init__(
        self,
        creation: Any,
        script: AgentScript,
        *,
        drop_sequences: Sequence[int] = (),
    ) -> None:
        self._creation = creation
        self._script = script
        #: 模拟丢事件——用来验证平台确实会发现缺口并补读
        self._drop = set(drop_sequences)
        self._sessions: dict[str, _Session] = {}

    # ---------------------------------------------------------------- Port

    async def create_session(self, *, instance_id: str) -> str:
        session_id = f"fake-{uuid.uuid4().hex[:12]}"
        self._sessions[session_id] = _Session(session_id, instance_id)
        self._emit(session_id, STATUS, {"status": "created"})
        return session_id

    async def send_message(self, session_id: str, text: str, *, request_id: str) -> None:
        session = self._require(session_id)
        if session.cancelled:
            raise RuntimeError("会话已取消")

        # 重发同一条消息必须复用同一 request_id —— 复用了就不该再跑一轮
        if request_id in session.seen_request_ids:
            return
        session.seen_request_ids.add(request_id)

        self._emit(session_id, MESSAGE, {"role": "user", "text": text})
        ticket = session.ticket or _extract_ticket(text)
        session.ticket = ticket
        if ticket is None:
            self._emit(session_id, ERROR, {"message": "启动指令里没有任务票据"})
            return

        await self._run_agent(session, ticket)

    async def cancel(self, session_id: str) -> None:
        session = self._require(session_id)
        session.cancelled = True
        self._emit(session_id, STATUS, {"status": "cancelled"})

    async def fetch_events(
        self, session_id: str, *, after_sequence: int | None = None
    ) -> Sequence[HarnessEvent]:
        session = self._require(session_id)
        floor = -1 if after_sequence is None else after_sequence
        return [event for event in session.events if event.sequence > floor]

    # ---------------------------------------------------------------- 内部

    def _require(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"未知会话: {session_id!r}")
        return session

    def _emit(self, session_id: str, kind: str, payload: dict[str, Any]) -> None:
        session = self._sessions[session_id]
        sequence = next(session.counter)
        if sequence in self._drop:
            return  # 这一条「丢在路上」，但位置照样被占——平台该发现缺口
        session.events.append(
            HarnessEvent(
                event_id=f"{session_id}:{sequence}",
                sequence=sequence,
                kind=kind,
                payload=payload,
                created_at=_utcnow(),
            )
        )

    async def _run_agent(self, session: _Session, ticket: str) -> None:
        """脚本化 Agent 的一轮：取上下文 → 产出 → 提交。"""
        self._emit(session.session_id, TOOL_CALL, {"tool": "get_creation_context"})
        try:
            context = (await self._creation.get_context(ticket)).as_dict()
        except Exception as exc:  # 票据被拒等——Agent 侧只能看到错误
            self._emit(session.session_id, ERROR, {"tool": "get_creation_context",
                                                   "message": str(exc)})
            return
        self._emit(
            session.session_id, TOOL_RESULT,
            {"tool": "get_creation_context",
             "draft_revision": context["product"].get("draft_revision"),
             "input_count": len(context["inputs"])},
        )

        documents = list(await self._script(context))
        if not documents:
            self._emit(session.session_id, MESSAGE,
                       {"role": "assistant", "text": "本轮没有可提交的内容。"})
            return

        submission_id = str(uuid.uuid4())
        self._emit(
            session.session_id, TOOL_CALL,
            {"tool": "submit_creation_result", "submission_id": submission_id,
             "document_count": len(documents)},
        )
        try:
            receipt = await self._creation.submit(
                ticket,
                submission_id=submission_id,
                based_on_draft_revision=int(context["product"]["draft_revision"] or 0),
                documents=documents,
            )
        except Exception as exc:
            self._emit(session.session_id, ERROR, {"tool": "submit_creation_result",
                                                   "message": str(exc)})
            return

        self._emit(session.session_id, TOOL_RESULT,
                   {"tool": "submit_creation_result", **receipt.as_dict()})
        # Agent 只有拿到回执才能说「成果已交付」
        self._emit(session.session_id, MESSAGE,
                   {"role": "assistant", "text": receipt.message})
        self._emit(session.session_id, STATUS, {"status": "idle"})


_TICKET_MARK = "task_ticket="


def _extract_ticket(text: str) -> str | None:
    """从启动指令里取票据。真 DSH 走的是同样的路子：票据随启动消息附给 Agent。"""
    if _TICKET_MARK not in text:
        return None
    tail = text.split(_TICKET_MARK, 1)[1].strip()
    return tail.split()[0].strip() if tail.split() else None
