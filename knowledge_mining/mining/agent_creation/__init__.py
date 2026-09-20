"""Agent creation face: 制作实例、任务票据、提交幂等与并发控制（52号 P2）。

载体侧在 ``knowledge_product``；这里管的是「谁、凭什么、在什么范围内、能把成果写
到哪个草稿」。DSH 原生会话适配（``harness_adapter``）与 MCP 工具面在 P3。
"""
from __future__ import annotations

from knowledge_mining.mining.agent_creation.models import (
    ACCEPTED,
    CONFLICT,
    REJECTED,
    CreationContext,
    IssuedTicket,
    RejectedRow,
    SubmissionReceipt,
    TicketClaims,
    TicketRejected,
)
from knowledge_mining.mining.agent_creation.scope import (
    ScopeGuard,
    ScopeViolation,
    SegmentLocation,
    SegmentLocator,
    parse_section_titles,
    section_allowed,
)
from knowledge_mining.mining.agent_creation.service import AgentCreationService
from knowledge_mining.mining.agent_creation.tickets import (
    hash_token,
    mint_token,
    redact,
    verify,
)

__all__ = [
    "ACCEPTED",
    "CONFLICT",
    "REJECTED",
    "AgentCreationService",
    "CreationContext",
    "IssuedTicket",
    "RejectedRow",
    "ScopeGuard",
    "ScopeViolation",
    "SegmentLocation",
    "SegmentLocator",
    "SubmissionReceipt",
    "TicketClaims",
    "TicketRejected",
    "hash_token",
    "mint_token",
    "parse_section_titles",
    "redact",
    "section_allowed",
    "verify",
]
