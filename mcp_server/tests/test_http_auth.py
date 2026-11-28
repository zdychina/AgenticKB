"""BUG-13：/mcp 连接期验钥——无效 key initialize 即 401、不建会话。

两层验钥中的 HTTP 层（http_auth.McpKeyTokenVerifier + fastmcp auth 框架）：
- 单元：verify_token 把 verify_key 的结果折叠进 auth 语义（None=401 不抛；
  空开放库照常放行——那是业务状态不是鉴权失败）；
- ASGI 级（http_app + lifespan 启动 session manager）：覆盖 BUG-13 §5 验证
  清单的 1/2/3——无钥/错钥 initialize 401 且无 mcp-session-id、有效钥正常
  握手与清单、轮换后同 session 下次请求 401；外加 /health 豁免锚点
  （auth 只包 /mcp，预签名路由不受影响）。SSE 响应需解析 data: 行。
"""
from __future__ import annotations

import json

import httpx
import pytest

from mcp_server import identity as identity_mod
from mcp_server import server
from mcp_server.http_auth import McpKeyTokenVerifier

_PAYLOAD = {
    "ok": True, "username": "alice", "user_id": "u-1",
    "key_id": "key-9", "key_domain": "odn",
    "open_kb_ids": ["kb-1"],
    "open_kbs": [{"id": "kb-1", "name": "kb-one", "domain": "odn"}],
}


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _stub_verify(monkeypatch, resp) -> None:
    monkeypatch.setenv("MCP_INTERNAL_AUTH_SECRET", "s3cret")
    monkeypatch.setattr(identity_mod.httpx, "post", lambda *a, **k: resp)


# ── 单元：verify_token 折叠 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_token_valid_key_yields_access_token(monkeypatch) -> None:
    _stub_verify(monkeypatch, _Resp(200, _PAYLOAD))
    at = await McpKeyTokenVerifier().verify_token("kbm_good")
    assert at is not None
    assert at.client_id == "alice"
    assert at.token == "kbm_good"
    assert at.claims["key_id"] == "key-9"
    assert at.claims["key_domain"] == "odn"


@pytest.mark.asyncio
async def test_verify_token_empty_open_kbs_still_valid(monkeypatch) -> None:
    """空开放库 ≠ 无效钥（§5.4）：验钥层放行，业务提示留给 tools/call。"""
    _stub_verify(
        monkeypatch, _Resp(200, {**_PAYLOAD, "open_kbs": [], "open_kb_ids": []}))
    assert await McpKeyTokenVerifier().verify_token("kbm_lone") is not None


@pytest.mark.asyncio
async def test_verify_token_invalid_key_folds_to_none(monkeypatch) -> None:
    _stub_verify(monkeypatch, _Resp(401, {"detail": "invalid mcp key"}))
    assert await McpKeyTokenVerifier().verify_token("kbm_bogus") is None


@pytest.mark.asyncio
async def test_verify_token_unreachable_backend_folds_to_none(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise httpx.ConnectError("mining down")

    monkeypatch.setenv("MCP_INTERNAL_AUTH_SECRET", "s3cret")
    monkeypatch.setattr(identity_mod.httpx, "post", boom)
    assert await McpKeyTokenVerifier().verify_token("kbm_any") is None


# ── ASGI 级：/mcp 连接期行为（BUG-13 §5） ───────────────────────────────

_ACCEPT = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


def _sse_data(resp: httpx.Response) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]


def _mutable_stub(monkeypatch) -> dict:
    """可翻转的验钥桩：holder["status"] 改 401 即模拟轮换/吊销。"""
    holder = {"status": 200}
    monkeypatch.setenv("MCP_INTERNAL_AUTH_SECRET", "s3cret")

    def fake_post(*_a, **_k):
        if holder["status"] == 200:
            return _Resp(200, _PAYLOAD)
        return _Resp(401, {"detail": "invalid mcp key"})

    monkeypatch.setattr(identity_mod.httpx, "post", fake_post)
    return holder


@pytest.mark.asyncio
async def test_initialize_without_auth_header_is_401_no_session(
    monkeypatch,
) -> None:
    """§5.1 前半：无 Authorization 头 → 401 空 body + WWW-Authenticate。"""
    _stub_verify(monkeypatch, _Resp(200, _PAYLOAD))  # 钥池有效与否无关：没带头即拒
    app = server.mcp.http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post("/mcp", headers=_ACCEPT, json=_INIT)
    assert resp.status_code == 401
    assert "mcp-session-id" not in resp.headers
    assert resp.headers.get("www-authenticate", "").startswith("Bearer")


@pytest.mark.asyncio
async def test_initialize_with_invalid_key_is_401_no_session(monkeypatch) -> None:
    """§5.1 主体：无效 key → 401 invalid_token、不建会话。"""
    _stub_verify(monkeypatch, _Resp(401, {"detail": "invalid mcp key"}))
    app = server.mcp.http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/mcp", headers={**_ACCEPT, "Authorization": "Bearer kbm_bogus"},
                json=_INIT)
    assert resp.status_code == 401
    assert "mcp-session-id" not in resp.headers
    body = resp.json()
    assert body["error"] == "invalid_token"
    assert 'error="invalid_token"' in resp.headers.get("www-authenticate", "")


@pytest.mark.asyncio
async def test_valid_key_initializes_then_lists_all_three_tools(
    monkeypatch,
) -> None:
    """§5.2：有效 key 握手 200 + session + serverInfo；tools/list 三件套齐全。"""
    _mutable_stub(monkeypatch)
    app = server.mcp.http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.post(
                "/mcp", headers={**_ACCEPT, "Authorization": "Bearer kbm_good"},
                json=_INIT)
            assert resp.status_code == 200
            session_id = resp.headers.get("mcp-session-id")
            assert session_id
            init = _sse_data(resp)
            assert init[0]["result"]["serverInfo"]["name"] == "multi-domain-knowledge"

            resp = await client.post(
                "/mcp",
                headers={**_ACCEPT, "Authorization": "Bearer kbm_good",
                         "mcp-session-id": session_id},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert resp.status_code == 200
            listed = {t["name"] for t in _sse_data(resp)[0]["result"]["tools"]}
            assert listed == {"search_knowledge", "get_knowledge", "manage_files"}


@pytest.mark.asyncio
async def test_rotated_key_rejects_established_session_on_next_call(
    monkeypatch,
) -> None:
    """§5.3：会话建立后密钥被轮换 → 同 session 下次请求 401（无绕过窗口）。"""
    holder = _mutable_stub(monkeypatch)
    app = server.mcp.http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            auth_headers = {**_ACCEPT, "Authorization": "Bearer kbm_good"}
            resp = await client.post("/mcp", headers=auth_headers, json=_INIT)
            assert resp.status_code == 200
            session_id = resp.headers.get("mcp-session-id")

            holder["status"] = 401  # 模拟轮换：好钥从现在起 401
            resp = await client.post(
                "/mcp", headers={**auth_headers, "mcp-session-id": session_id},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert resp.status_code == 401
            assert resp.json()["error"] == "invalid_token"


@pytest.mark.asyncio
async def test_health_route_stays_public_without_auth(monkeypatch) -> None:
    """auth 只包 /mcp：/health 无认证头照常 200（预签名/upload 同理，见
    test_tool_surface 的三个直传用例）。"""
    _stub_verify(monkeypatch, _Resp(200, _PAYLOAD))
    app = server.mcp.http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
