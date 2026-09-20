"""Platform-side orchestration of one creation run（52号 P3）。

50号 §3.1 的那串动作，落成代码：

    创建制品草稿和输入快照 → 创建 creation_instance → 签发短期 task_ticket
    → 调 harness 建会话 → 发启动指令和 task_ticket → 跟随事件并保存可见过程

这一层**不依赖 DSH**：它只依赖 ``HarnessPort``。换适配器不改本文件，这正是 50号
所说「DSH 可以替换」的落点。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from knowledge_mining.mining.harness_adapter.journal import EventJournal, project
from knowledge_mining.mining.harness_adapter.port import HarnessPort

#: 启动指令。只告诉 Agent「去哪取定义、守什么规矩、成果怎么交」——业务定义不进
#: 提示词，它在 get_creation_context 里（50号 §3.1）。
START_PROMPT = """\
你正在制作知识制品 {product_name}。
先调用 get_creation_context，取得本次工作定义和资料清单：task_ticket={ticket}
只使用当前任务票据允许的材料；遇到关键歧义列入 unresolved_items。
不要把聊天回复作为最终成果；完成一批内容后调用 submit_creation_result。
"""


@dataclass
class CreationRun:
    """一次制作的平台侧句柄。"""

    instance_id: str
    product_id: str
    session_id: str
    journal: EventJournal = field(default_factory=EventJournal)
    #: 发过的消息 → request_id。重发必须复用，否则 harness 会当成新一轮。
    request_ids: dict[str, str] = field(default_factory=dict)

    def visible_process(self) -> list[dict[str, Any]]:
        return project(list(self.journal))


class CreationDriver:
    def __init__(self, creation: Any, harness: HarnessPort) -> None:
        self._creation = creation
        self._harness = harness

    async def start(
        self, product_id: str, *, created_by: str, product_name: str | None = None
    ) -> CreationRun:
        """起实例 → 签票 → 建会话 → 发启动指令。

        票据明文只在这里流转一次：平台 → harness 会话。它不回给浏览器、不进日志。
        """
        instance, ticket = await self._creation.start_instance(
            product_id, created_by=created_by
        )
        session_id = await self._harness.create_session(instance_id=instance["id"])

        bind = getattr(self._creation._repo, "bind_harness_session", None)
        if bind is not None:
            await bind(instance["id"], session_id)

        run = CreationRun(
            instance_id=instance["id"], product_id=product_id, session_id=session_id
        )
        await self.send(
            run,
            START_PROMPT.format(product_name=product_name or product_id, ticket=ticket.token),
            key="start",
        )
        return run

    async def send(self, run: CreationRun, text: str, *, key: str) -> None:
        """发一条消息。

        ``key`` 标识「这是哪一条消息」——同一个 key 重发会复用同一个
        ``request_id``，因为 50号 明确要求重发不能被 harness 当成新一轮。
        """
        request_id = run.request_ids.setdefault(key, str(uuid.uuid4()))
        await self._harness.send_message(run.session_id, text, request_id=request_id)

    async def follow(self, run: CreationRun) -> list[dict[str, Any]]:
        """从上次的补读位置继续取事件，返回**本次新增**的投影。

        断线重连只管再调一次：``EventJournal`` 按 event_id 去重，不会重复显示。
        """
        events = await self._harness.fetch_events(
            run.session_id, after_sequence=run.journal.next_sequence
        )
        return project(run.journal.ingest(events))

    async def backfill(self, run: CreationRun) -> list[dict[str, Any]]:
        """有缺口就从头全量补一次。

        「不能因为最终回复出现，就假定前面的工具过程都已保存」——所以判据是
        ``journal.needs_backfill``，不是「有没有收到完成事件」。
        """
        if not run.journal.needs_backfill:
            return []
        events = await self._harness.fetch_events(run.session_id, after_sequence=None)
        return project(run.journal.ingest(events))

    async def cancel(self, run: CreationRun, *, reason: str = "任务已取消") -> None:
        """取消会话并撤票。

        顺序照 50号 §8：先让 harness 停，**再撤票据**——队列里迟到的工具调用只有
        票据失效了才写不回来。
        """
        await self._harness.cancel(run.session_id)
        await self._creation.cancel_instance(run.instance_id, reason=reason)
