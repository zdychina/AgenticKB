"""BUG-03 回归：auth 入口对配置瞬时坏态的韧性。

对照 docs/bugs/BUG-03（P2）登录接口间歇性 500：
- 兜底：login/identify 的未知异常 → 可诊断 503（code=auth_upstream_unavailable），
  不再裸 500；
- 读侧快照回退：registry/pack 瞬时解析失败（带外通道 truncate-write 半截窗口）
  → 降级用上一份好快照，登录入口不中断；
- 合法写盘后缓存即时失效（不吃陈旧配置）；
- 非法 JSON body → 400；
- 可观测：GET /api/v1/admin/auth-stats 的计数与 admin-only 分层。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main_control_service.jwt_util import encode
from main_control_service.main import create_app
from main_control_service.service import YamlConfigService

_AUTH = (
    "enabled: true\njwt_secret: s\ntoken_ttl_seconds: 3600\n"
    "internal_verify_secret: ivs\nbootstrap: {admin_password: x}\n"
)

_VALID_REGISTRY = (
    "domains:\n  d:\n    enabled: true\n"
    "    services:\n      mining_url: http://mining:8901\n"
)

# 半截 YAML：未闭合的 flow 映射，safe_load 必抛 ScannerError。
_TRUNCATED_REGISTRY = "domains:\n  d:\n    enabled: true\n    services: {mining_url: http://min"


def _client(tmp_path: Path, *, registry: str | None = None) -> TestClient:
    d = tmp_path / "system"
    d.mkdir(parents=True, exist_ok=True)
    (d / "auth.yaml").write_text(_AUTH, encoding="utf-8")
    (tmp_path / "domain_registry.yaml").write_text(
        registry if registry is not None else _VALID_REGISTRY,
        encoding="utf-8",
    )
    return TestClient(create_app(config_dir=tmp_path))


def _identify_ok(c: TestClient) -> None:
    with patch("main_control_service.main.identify_via_mining", new_callable=AsyncMock) as m:
        m.return_value = {"mode": "password"}
        r = c.post("/api/v1/auth/identify", json={"username": "root"})
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 兜底：从未成功读过的坏配置 → 可诊断 503（修复前是裸 500）
# ---------------------------------------------------------------------------

def test_identify_cold_corrupt_registry_503_diagnosable(tmp_path):
    with _client(tmp_path, registry=_TRUNCATED_REGISTRY) as c:
        r = c.post("/api/v1/auth/identify", json={"username": "root"})
        assert r.status_code == 503, r.text
        assert r.json()["code"] == "auth_upstream_unavailable"


def test_login_cold_corrupt_registry_503_diagnosable(tmp_path):
    with _client(tmp_path, registry=_TRUNCATED_REGISTRY) as c:
        r = c.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})
        assert r.status_code == 503, r.text
        assert r.json()["code"] == "auth_upstream_unavailable"


def test_identify_cold_non_mapping_registry_503_diagnosable(tmp_path):
    # 根是列表：safe_load 成功但非映射——修复前 payload.get 直接 AttributeError 裸 500。
    with _client(tmp_path, registry="- a\n- b\n") as c:
        r = c.post("/api/v1/auth/identify", json={"username": "root"})
        assert r.status_code == 503, r.text
        assert r.json()["code"] == "auth_upstream_unavailable"


# ---------------------------------------------------------------------------
# 读侧快照回退：先读到过好配置 → 瞬时坏态期间降级用快照，登录入口不中断
# ---------------------------------------------------------------------------

def test_identify_transient_corrupt_registry_serves_snapshot(tmp_path):
    registry = tmp_path / "domain_registry.yaml"
    with _client(tmp_path) as c:
        _identify_ok(c)  # 预热快照
        registry.write_text(_TRUNCATED_REGISTRY, encoding="utf-8")
        _identify_ok(c)  # 坏态期间：降级用快照，不 500 不中断
        registry.write_text(_VALID_REGISTRY, encoding="utf-8")
        _identify_ok(c)  # 恢复后：立即回到读盘


def test_login_transient_corrupt_registry_serves_snapshot(tmp_path):
    registry = tmp_path / "domain_registry.yaml"
    with _client(tmp_path) as c:
        with patch("main_control_service.main.verify_user_via_mining", new_callable=AsyncMock) as m:
            m.return_value = {"ok": True, "user": {
                "username": "alice", "display_name": "Alice", "site_role": "admin",
            }}
            assert c.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
            registry.write_text(_TRUNCATED_REGISTRY, encoding="utf-8")
            r = c.post("/api/v1/auth/login", json={"username": "alice"})
            assert r.status_code == 200, r.text
            assert r.json()["user"]["username"] == "alice"


def test_transient_corrupt_pack_serves_snapshot(tmp_path):
    """scenario pack 半截窗口同样降级：display_name 仍来自上一份好快照。"""
    registry = (
        "domains:\n  d:\n    enabled: true\n"
        "    services:\n      mining_url: http://mining:8901\n"
    )
    pack = tmp_path / "scenario_packs" / "d" / "domain.yaml"
    pack.parent.mkdir(parents=True)
    pack.write_text("display_name: From Pack\n", encoding="utf-8")
    token = encode({"sub": "alice", "role": "member", "name": "Alice"}, "s", ttl=3600)
    with _client(tmp_path, registry=registry) as c:
        with patch(
            "main_control_service.service.YamlConfigService.domain_access_for",
            new_callable=AsyncMock,
        ) as access:
            access.return_value = ({
                "site_role": "member",
                "grants": [{"domain": "d", "domain_role": "member"}],
                "capabilities_by_domain": {},
            }, "")

            def list_names() -> list[str]:
                r = c.get("/api/v1/domains", headers={"Authorization": f"Bearer {token}"})
                assert r.status_code == 200, r.text
                return [item["display_name"] for item in r.json()["items"]]

            assert list_names() == ["From Pack"]
            pack.write_text("display_name: From Pack\nmining: {unclosed", encoding="utf-8")
            assert list_names() == ["From Pack"]  # 快照回退


def test_registry_rewrite_seen_immediately(tmp_path):
    """合法写盘（mtime 变化）后缓存必须失效——不能吃陈旧配置。"""
    registry = tmp_path / "domain_registry.yaml"
    with _client(tmp_path) as c:
        with patch("main_control_service.main.verify_user_via_mining", new_callable=AsyncMock) as m:
            m.return_value = {"ok": True, "user": {
                "username": "alice", "display_name": "Alice", "site_role": "admin",
            }}
            c.post("/api/v1/auth/login", json={"username": "alice"})
            assert m.await_args.args[0] == "http://mining:8901"
            registry.write_text(
                "domains:\n  d:\n    enabled: true\n"
                "    services:\n      mining_url: http://mining-b:8902\n",
                encoding="utf-8",
            )
            c.post("/api/v1/auth/login", json={"username": "alice"})
            assert m.await_args.args[0] == "http://mining-b:8902"


# ---------------------------------------------------------------------------
# 形状守卫：解析成功但结构畸形 → 降级，不 500
# ---------------------------------------------------------------------------

def test_registry_non_mapping_domains_degrades_empty(tmp_path):
    with _client(tmp_path, registry="domains: [a, b]\n") as c:
        r = c.post("/api/v1/auth/identify", json={"username": "root"})
        assert r.status_code == 503, r.text  # 无可用域 → 可诊断 503，而非裸 500
        assert r.json()["detail"] == "mining backend unavailable"


def test_registry_non_dict_entries_skipped(tmp_path):
    (tmp_path / "domain_registry.yaml").write_text(
        'domains:\n  good:\n    enabled: true\n  bad: "oops"\n',
        encoding="utf-8",
    )
    svc = YamlConfigService(config_dir=tmp_path)
    items = svc.list_domains()
    assert [i["domain_id"] for i in items] == ["good"]


# ---------------------------------------------------------------------------
# 请求 body 解析：非法 JSON / 非对象根 → 400（修复前裸 500）
# ---------------------------------------------------------------------------

def test_malformed_json_body_400(tmp_path):
    with _client(tmp_path) as c:
        for path in ("/api/v1/auth/login", "/api/v1/auth/identify"):
            r = c.post(
                path, content=b'{"username": "ro',
                headers={"Content-Type": "application/json"},
            )
            assert r.status_code == 400, (path, r.status_code, r.text)


def test_json_array_body_400(tmp_path):
    with _client(tmp_path) as c:
        for path in ("/api/v1/auth/login", "/api/v1/auth/identify"):
            r = c.post(path, json=[1, 2, 3])
            assert r.status_code == 400, (path, r.status_code, r.text)


# ---------------------------------------------------------------------------
# 可观测：/api/v1/admin/auth-stats（admin-only）计数
# ---------------------------------------------------------------------------

def test_auth_stats_admin_only_and_counters(tmp_path):
    member = encode({"sub": "bob", "role": "member", "name": "Bob"}, "s", ttl=3600)
    admin = encode({"sub": "alice", "role": "admin", "name": "Alice"}, "s", ttl=3600)
    with _client(tmp_path) as c:
        # member → 403（/api/v1/admin/* 分层）
        assert c.get(
            "/api/v1/admin/auth-stats",
            headers={"Authorization": f"Bearer {member}"},
        ).status_code == 403

        _identify_ok(c)
        # 空注册表（合法 YAML）→ 无可用 mining → 可诊断 503，计入 errors_5xx
        (tmp_path / "domain_registry.yaml").write_text("domains: {}\n", encoding="utf-8")
        r = c.post("/api/v1/auth/identify", json={"username": "root"})
        assert r.status_code == 503, r.text

        with patch(
            "main_control_service.service.YamlConfigService.domain_access_for",
            new_callable=AsyncMock,
        ) as access:
            access.return_value = ({"site_role": "admin", "grants": [], "capabilities_by_domain": {}}, "")
            r = c.get("/api/v1/admin/auth-stats",
                      headers={"Authorization": f"Bearer {admin}"})
        assert r.status_code == 200, r.text
        stats = r.json()
        assert stats["identify"]["total"] == 2
        assert stats["identify"]["errors_5xx"] == 1
        assert stats["identify"]["avg_ms"] >= 0
        assert stats["login"]["total"] == 0
