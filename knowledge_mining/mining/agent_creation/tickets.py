"""Task-ticket minting and verification（52号 P2，50号 §3.1）。

票据是短期、可撤销、绑定制作实例与资料范围的凭证——**不是管理员密钥**。所以：

- 明文只在签发那一刻返回一次，库里只存 ``sha256``；
- 过期是时间判定（``expires_at``），不靠后台任务改状态——少一条能失效的路径就少
  一处漏判；
- 撤销立即生效：任务取消、资料范围收缩、权限撤销、草稿定义不兼容变更都要撤票。

本模块是纯函数 + 无状态校验，落库在 ``repository``。
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from knowledge_mining.mining.agent_creation.models import (
    ACCEPTS_SUBMISSION,
    INSTANCE_NOT_ACCEPTING,
    TICKET_EXPIRED,
    TICKET_INVALID,
    TICKET_PRODUCT_MISMATCH,
    TICKET_REVOKED,
    TicketClaims,
    TicketRejected,
)

#: 默认有效期。够一轮制作用，短到泄露了也很快失效。
DEFAULT_TTL = timedelta(hours=2)
TOKEN_BYTES = 32
TOKEN_PREFIX = "kpt_"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mint_token() -> tuple[str, str]:
    """→ ``(明文, sha256 哈希)``。明文之后不再可得。"""
    token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_ticket_id() -> str:
    return f"kpt_{uuid.uuid4().hex}"


def expiry_from(ttl: timedelta | None = None, *, now: datetime | None = None) -> str:
    return ((now or _utcnow()) + (ttl or DEFAULT_TTL)).isoformat()


def redact(token: str) -> str:
    """日志/页面用的脱敏形式。票据明文任何时候都不该原样出现。"""
    if not token:
        return "<empty>"
    return f"{token[:8]}…{token[-4:]}" if len(token) > 16 else "…"


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def verify(
    ticket_row: dict[str, Any] | None,
    instance_row: dict[str, Any] | None,
    *,
    expected_product_id: str | None = None,
    now: datetime | None = None,
) -> TicketClaims:
    """校验票据 + 实例，返回 claims；任一条不过即抛 :class:`TicketRejected`。

    对应 50号 §5.3 的第一道：票据仍有效、实例仍在运行、提交目标匹配。
    """
    if ticket_row is None:
        raise TicketRejected(TICKET_INVALID, "任务票据无效：未知或已被清除。")

    if ticket_row.get("status") == "revoked":
        reason = ticket_row.get("revoked_reason") or "票据已被撤销"
        raise TicketRejected(TICKET_REVOKED, f"任务票据已失效：{reason}。请向平台重新取票。")

    now = now or _utcnow()
    if _parse_iso(str(ticket_row["expires_at"])) <= now:
        raise TicketRejected(
            TICKET_EXPIRED, "任务票据已过期。请向平台重新取票后再继续。"
        )

    if instance_row is None:
        raise TicketRejected(TICKET_INVALID, "任务票据对应的制作实例不存在。")

    status = instance_row.get("status")
    if status not in ACCEPTS_SUBMISSION:
        raise TicketRejected(
            INSTANCE_NOT_ACCEPTING,
            f"制作实例当前状态为 {status!r}，不再接收提交。",
        )

    if expected_product_id is not None and ticket_row["product_id"] != expected_product_id:
        raise TicketRejected(
            TICKET_PRODUCT_MISMATCH,
            "任务票据绑定的制品与本次提交目标不一致。",
        )

    return TicketClaims(
        ticket_id=ticket_row["id"],
        instance_id=ticket_row["instance_id"],
        product_id=ticket_row["product_id"],
        definition_revision=int(instance_row["definition_revision"]),
        expires_at=str(ticket_row["expires_at"]),
    )
