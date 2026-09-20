"""The seam between the platform and an external Agent harness（52号 P3）。

50号 的解耦定义是「DSH 可以替换，制品定义和资料不丢」。要让这句话成立，平台侧对
harness 的依赖必须收在一个接口里——就是这里。真 DSH 适配器（Cookie 认证、原生
RPC 体、``sessionId``/``requestId``、事件订阅与历史补读）实现它；``FakeHarness``
也实现它，让整条链路在 DSH 就绪之前就能端到端跑。

两条回流不能混为一条（50号 §3.3）：
- **成果**经 MCP 的 ``submit_creation_result`` 回来，是本模块**管不着**的；
- **过程**经本接口的事件回来，只用于页面展示、诊断与审计。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

# 事件种类。够覆盖 50号 §3.3 那张表，不做成开放枚举——种类一多，前端投影就没法写。
MESSAGE = "message"          # 用户消息 / Agent 可见回复
TOOL_CALL = "tool_call"      # 工具开始
TOOL_RESULT = "tool_result"  # 工具结果
ERROR = "error"
STATUS = "status"            # 会话状态、取消、完成

EVENT_KINDS = frozenset({MESSAGE, TOOL_CALL, TOOL_RESULT, ERROR, STATUS})


@dataclass(frozen=True)
class HarnessEvent:
    """一条过程事件。

    ``event_id`` 是**去重键**：重连补读会把同一条事件再送一遍，靠它去重而不是靠
    「看起来一样」。``sequence`` 是 harness 侧的单调位置，用来判断中间有没有漏。
    """

    event_id: str
    sequence: int
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"未知事件种类: {self.kind!r}")
        if self.sequence < 0:
            raise ValueError("事件 sequence 不能为负")


class HarnessPort(Protocol):
    """平台对外部 Agent harness 的全部依赖。

    平台是 harness 的**客户端**：建会话、发消息、取消、跟随事件、按位置补读历史。
    平台不把数据库、对象存储凭据或全库 token 交给它（50号 §4.2）。
    """

    async def create_session(self, *, instance_id: str) -> str:
        """建一个会话，返回 harness 侧的 session id。"""
        ...

    async def send_message(self, session_id: str, text: str, *, request_id: str) -> None:
        """发一条消息。

        **重发同一条消息必须复用同一个 ``request_id``**（50号 §3.1）——否则
        harness 会把重试当成新一轮，同一段指令被执行两次。
        """
        ...

    async def cancel(self, session_id: str) -> None:
        """取消会话。

        调完它**仍必须撤票据**：队列里可能还有迟到的工具调用（50号 §8）。
        """
        ...

    async def fetch_events(
        self, session_id: str, *, after_sequence: int | None = None
    ) -> Sequence[HarnessEvent]:
        """取 ``after_sequence`` 之后的事件（实时流与历史补读共用这一个口）。"""
        ...
