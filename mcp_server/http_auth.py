"""HTTP 传输层验钥（BUG-13）：/mcp 端点连接期拒绝无效密钥。

fastmcp 的 auth 框架是"401 + 不建会话"的唯一正确层级——RequireAuthMiddleware
只包 /mcp 路由，短路在 StreamableHTTPSessionManager 之前（无 transport、无
mcp-session-id）；/health 与 /upload/{ticket} 自定义路由在其后追加、天然豁免
（预签名票据模型不依赖 MCP 密钥）。无效/吊销/轮换后旧钥 → 401（RFC 6750：
WWW-Authenticate: Bearer error="invalid_token"），Agent 在连接阶段即见鉴权
失败，不再等到第一次 tools/call。

与 PersonalizationMiddleware 的分工（双验，均为逐请求实时验钥）：
- 本层管"进门"：initialize/tools/list 等一切 /mcp 请求先 401 短路；
- on_call_tool 管"逐调用"：会话建立后密钥被轮换/吊销，下次调用照拒——
  连接期放行的会话不产生绕过窗口。两层各自调一次 mining 验钥（localhost
  开销可忽略，identity.py 模块头有该设计语义的既有说明）。
"""
from __future__ import annotations

import asyncio

from fastmcp.server.auth import AccessToken, TokenVerifier

from mcp_server.identity import IdentityError, verify_key


class McpKeyTokenVerifier(TokenVerifier):
    """/mcp 端点的 Bearer 验钥：verify_key 的结果折叠进 fastmcp auth 语义。

    verify_token 返回 None 即 401（不抛异常——抛非受控异常会穿透成 500）。
    "空开放库"是业务状态不是鉴权失败：verify_key 对 open_kbs 为空的 200
    照常返回 Identity，本层同样放行（业务提示留给 tools/call）。
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            # verify_key 内是同步 httpx（timeout 上限 VERIFY_TIMEOUT），
            # 丢线程池避免阻塞事件循环（比 on_call_tool 的内联同步调用更稳）。
            ident = await asyncio.to_thread(verify_key, token)
        except IdentityError:
            return None
        return AccessToken(
            token=token,
            client_id=ident.username,
            scopes=[],
            claims={
                "key_id": ident.key_id,
                "key_domain": ident.key_domain,
            },
        )
