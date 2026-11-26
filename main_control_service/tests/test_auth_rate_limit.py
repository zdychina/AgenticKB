"""BUG-04 回归：auth 入口限流（登录失败锁定 + identify 防枚举）。

对照 docs/bugs/BUG-04 §5 验证项：
1. 连错 5 次后第 6 次返回 429 且带 Retry-After；锁定窗口过后恢复；
2. 正确密码在未触发锁定前始终 200（成功清零计数，不误伤）；
3. 锁键含 username+IP：不同用户名/不同 IP 互不影响；
4. identify 同 IP 每分钟 30 次后 429；正常登录流程（1 identify + 1 login）不受影响；
5. throttled_429 计入 GET /api/v1/admin/auth-stats。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main_control_service.auth_rate_limit import FixedWindowRateLimiter, LoginThrottle
from main_control_service.jwt_util import encode
from main_control_service.main import create_app

_AUTH = (
    "enabled: true\njwt_secret: s\ntoken_ttl_seconds: 3600\n"
    "internal_verify_secret: ivs\nbootstrap: {admin_password: x}\n"
)

_VALID_REGISTRY = (
    "domains:\n  d:\n    enabled: true\n"
    "    services:\n      mining_url: http://mining:8901\n"
)


def _app(tmp_path: Path):
    d = tmp_path / "system"
    d.mkdir(parents=True, exist_ok=True)
    (d / "auth.yaml").write_text(_AUTH, encoding="utf-8")
    (tmp_path / "domain_registry.yaml").write_text(_VALID_REGISTRY, encoding="utf-8")
    return create_app(config_dir=tmp_path)


class _SourceIPOverride:
    """测试专用 ASGI 包装：带 X-Test-Source-IP 头的请求改写 scope["client"]，
    在真实 ASGI 层模拟另一来源 IP（本仓 starlette 0.37 的 TestClient 尚无
    client= 参数）。"""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            ip = headers.get("x-test-source-ip")
            if ip:
                scope["client"] = (ip, 40000)
        await self._app(scope, receive, send)


def _client_from_ip(app, ip: str) -> TestClient:
    """以指定来源 IP 驱动同一 app（全部请求经 _SourceIPOverride 改写）。"""
    return TestClient(
        _SourceIPOverride(app), headers={"X-Test-Source-IP": ip},
    )


def _login_fail(c: TestClient, username: str = "alice", password: str = "bad"):
    with patch(
        "main_control_service.main.verify_user_via_mining", new_callable=AsyncMock,
    ) as m:
        m.return_value = None  # mining verify：凭证不符
        return c.post(
            "/api/v1/auth/login", json={"username": username, "password": password},
        )


# ---------------------------------------------------------------------------
# 单元级：假时钟直接驱动限流器（窗口滑动 / 锁过期 / 成功清零）
# ---------------------------------------------------------------------------

def test_login_throttle_sliding_window_and_lock():
    t = 1000.0
    th = LoginThrottle(fail_limit=5, fail_window=300.0, lock_seconds=300.0,
                       clock=lambda: t)
    key = ("alice", "1.2.3.4")
    for _ in range(4):
        assert th.record_failure(key) == 0.0
    t += 301.0  # 旧失败滑出窗口
    for _ in range(4):
        assert th.record_failure(key) == 0.0  # 窗口内累计 1..4，未达 5
    assert th.record_failure(key) == 300.0  # 窗口内第 5 次 → 触发锁定
    assert 0.0 < th.locked_for(key) <= 300.0
    t += 300.0
    assert th.locked_for(key) == 0.0  # 锁定窗口过后恢复


def test_login_throttle_success_resets_counter():
    th = LoginThrottle(clock=lambda: 1000.0)
    key = ("alice", "1.2.3.4")
    for _ in range(4):
        th.record_failure(key)
    th.record_success(key)
    for _ in range(4):
        assert th.record_failure(key) == 0.0  # 计数已清零，再失败 4 次不触发


def test_fixed_window_limiter_allows_then_blocks_then_resets():
    t = 1000.0
    rl = FixedWindowRateLimiter(limit=3, window=60.0, clock=lambda: t)
    assert all(rl.allow("1.2.3.4") for _ in range(3))
    assert not rl.allow("1.2.3.4")  # 窗口内第 4 次 → 拒
    assert rl.allow("5.6.7.8")  # 其它键不受影响
    assert 0.0 < rl.retry_after("1.2.3.4") <= 60.0
    t += 60.0
    assert rl.allow("1.2.3.4")  # 窗口重置


# ---------------------------------------------------------------------------
# 路由级：§5 验证项
# ---------------------------------------------------------------------------

def test_login_lockout_after_five_failures(tmp_path):
    """§5.1：连错 5 次全 401，第 6 次 429 + Retry-After；锁定期不回源 mining。"""
    with TestClient(_app(tmp_path)) as c:
        for _ in range(5):
            assert _login_fail(c).status_code == 401
        r = _login_fail(c)
        assert r.status_code == 429, r.text
        assert int(r.headers["Retry-After"]) >= 1
        assert r.json()["code"] == "auth_rate_limited"
        # 锁定期间连正确密码也 429，且在调 mining 之前就被拦（不泄露窗口状态）
        with patch(
            "main_control_service.main.verify_user_via_mining", new_callable=AsyncMock,
        ) as m:
            m.return_value = {"ok": True, "user": {
                "username": "alice", "site_role": "admin",
            }}
            r = c.post("/api/v1/auth/login",
                       json={"username": "alice", "password": "right"})
            assert r.status_code == 429
            m.assert_not_awaited()


def test_login_lock_expires_after_window(tmp_path):
    """§5.1 后半：等待锁定窗口后恢复正常（等价地把锁定起点拨回过去）。"""
    app = _app(tmp_path)
    with TestClient(app) as c:
        for _ in range(5):
            _login_fail(c)
        assert _login_fail(c).status_code == 429
        throttle = app.state.login_throttle
        for key in list(throttle._locked_until):
            throttle._locked_until[key] -= 301.0  # 快进 301 秒
        assert _login_fail(c).status_code == 401  # 恢复（重新计数）


def test_login_success_resets_counter_no_false_positive(tmp_path):
    """§5.2：正确密码在未触发锁定前始终 200；成功清零，不误伤。"""
    with TestClient(_app(tmp_path)) as c:
        for _ in range(4):
            assert _login_fail(c).status_code == 401
        with patch(
            "main_control_service.main.verify_user_via_mining", new_callable=AsyncMock,
        ) as m:
            m.return_value = {"ok": True, "user": {
                "username": "alice", "display_name": "Alice", "site_role": "admin",
            }}
            r = c.post("/api/v1/auth/login",
                       json={"username": "alice", "password": "right"})
            assert r.status_code == 200, r.text
            assert r.json()["user"]["username"] == "alice"
        # 成功清零后再连错 4 次仍 401（未达 5 次），第 5 次触发、第 6 次 429
        for _ in range(4):
            assert _login_fail(c).status_code == 401
        assert _login_fail(c).status_code == 401
        assert _login_fail(c).status_code == 429


def test_lockout_scoped_to_username_and_ip(tmp_path):
    """§5.3：锁键含 username+IP，不同用户名/不同 IP 互不影响。"""
    app = _app(tmp_path)
    with TestClient(app) as c:
        for _ in range(5):
            assert _login_fail(c).status_code == 401
        assert _login_fail(c).status_code == 429
        assert _login_fail(c, username="bob").status_code == 401  # 同 IP 不同用户名
    # 同用户名不同 IP：不受影响
    assert _login_fail(_client_from_ip(app, "203.0.113.9")).status_code == 401


def test_identify_rate_limited_per_ip(tmp_path):
    """§5.4：identify 同 IP 30 次/分钟，第 31 次 429；其它 IP 不受影响。"""
    app = _app(tmp_path)
    with TestClient(app) as c:
        with patch(
            "main_control_service.main.identify_via_mining", new_callable=AsyncMock,
        ) as m:
            m.return_value = {"mode": "password"}
            for _ in range(30):
                r = c.post("/api/v1/auth/identify", json={"username": "root"})
                assert r.status_code == 200, r.text
            r = c.post("/api/v1/auth/identify", json={"username": "root"})
            assert r.status_code == 429, r.text
            assert int(r.headers["Retry-After"]) >= 1
            awaited = m.await_count  # 超频请求不再回源 mining
            r = c.post("/api/v1/auth/identify", json={"username": "root"})
            assert r.status_code == 429
            assert m.await_count == awaited
    c2 = _client_from_ip(app, "203.0.113.9")
    with patch(
        "main_control_service.main.identify_via_mining", new_callable=AsyncMock,
    ) as m:
        m.return_value = {"mode": "password"}
        assert c2.post("/api/v1/auth/identify",
                       json={"username": "root"}).status_code == 200


def test_normal_login_flow_unaffected(tmp_path):
    """§5.4 后半：正常登录流程（1 identify + 1 login）不受限流影响。"""
    with TestClient(_app(tmp_path)) as c:
        with patch(
            "main_control_service.main.identify_via_mining", new_callable=AsyncMock,
        ) as m:
            m.return_value = {"mode": "password"}
            r = c.post("/api/v1/auth/identify", json={"username": "alice"})
            assert r.status_code == 200, r.text
        with patch(
            "main_control_service.main.verify_user_via_mining", new_callable=AsyncMock,
        ) as m:
            m.return_value = {"ok": True, "user": {
                "username": "alice", "display_name": "Alice", "site_role": "admin",
            }}
            r = c.post("/api/v1/auth/login",
                       json={"username": "alice", "password": "pw"})
            assert r.status_code == 200, r.text


def test_auth_stats_counts_throttled(tmp_path):
    """§4.3 可观测：throttled_429 计入 /api/v1/admin/auth-stats。"""
    admin = encode({"sub": "alice", "role": "admin", "name": "Alice"}, "s", ttl=3600)
    with TestClient(_app(tmp_path)) as c:
        for _ in range(5):
            _login_fail(c)
        assert _login_fail(c).status_code == 429
        with patch(
            "main_control_service.service.YamlConfigService.domain_access_for",
            new_callable=AsyncMock,
        ) as access:
            access.return_value = ({
                "site_role": "admin", "grants": [], "capabilities_by_domain": {},
            }, "")
            r = c.get("/api/v1/admin/auth-stats",
                      headers={"Authorization": f"Bearer {admin}"})
        assert r.status_code == 200, r.text
        stats = r.json()
        assert stats["login"]["total"] == 5  # 429 的那次不计 total
        assert stats["login"]["throttled_429"] == 1
