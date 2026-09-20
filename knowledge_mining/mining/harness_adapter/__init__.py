"""Harness adapter: 平台与外部 Agent harness 之间的那道缝（52号 P3）。

``port.HarnessPort`` 是平台对 harness 的全部依赖——真 DSH 适配器和 ``FakeHarness``
都实现它，``driver.CreationDriver`` 只认接口。这就是 50号 说的「DSH 可以替换，
制品定义和资料不丢」。

当前状态：``FakeHarness`` 可用，真 DSH 适配器**尚未实现**（等 T0/T1 环境就绪）。
"""
from __future__ import annotations

from knowledge_mining.mining.harness_adapter.driver import (
    START_PROMPT,
    CreationDriver,
    CreationRun,
)
from knowledge_mining.mining.harness_adapter.fake import AgentScript, FakeHarness
from knowledge_mining.mining.harness_adapter.journal import EventJournal, Gap, project
from knowledge_mining.mining.harness_adapter.port import (
    ERROR,
    EVENT_KINDS,
    MESSAGE,
    STATUS,
    TOOL_CALL,
    TOOL_RESULT,
    HarnessEvent,
    HarnessPort,
)

__all__ = [
    "ERROR",
    "EVENT_KINDS",
    "MESSAGE",
    "START_PROMPT",
    "STATUS",
    "TOOL_CALL",
    "TOOL_RESULT",
    "AgentScript",
    "CreationDriver",
    "CreationRun",
    "EventJournal",
    "FakeHarness",
    "Gap",
    "HarnessEvent",
    "HarnessPort",
    "project",
]
