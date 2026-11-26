# -*- coding: utf-8 -*-
"""BUG-05 端到端：读掩码 / 写路径合并 / consume-bootstrap / 审计落盘。

浏览器（无内部头）读 → 掩码；内部调用（X-Internal-Auth）→ 原值；
PUT 回传掩码 → 服务端按磁盘原值恢复（结构性保证，验证项 3）。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from fastapi.testclient import TestClient

from main_control_service import audit
from main_control_service.config_masking import MASK_PREFIX, mask_value
from main_control_service.jwt_util import encode
from main_control_service.logs import list_logs
from main_control_service.main import create_app

_JWT = "abcdef1234567890abcdef1234567890"
_IVS = "ivs-0123456789abcdef"
_ADMIN_PW = "0123456789abcdef0123456789abcdef01234567"

_AUTH_YAML = (
    "# comment line\n"
    "enabled: false\n"
    f"jwt_secret: {_JWT}\n"
    "token_ttl_seconds: 43200\n"
    f"internal_verify_secret: {_IVS}\n"
    "bootstrap:\n"
    "  initialize_on_deploy: false\n"
    f"  admin_password: \"{_ADMIN_PW}\"\n"
)

_INTERNAL = {"X-Internal-Auth": _IVS}


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "system").mkdir(exist_ok=True)
    (tmp_path / "system" / "auth.yaml").write_text(_AUTH_YAML, encoding="utf-8")
    (tmp_path / "domain_registry.yaml").write_text("domains: {}\n", encoding="utf-8")
    return TestClient(create_app(config_dir=tmp_path))


# ---------------------------------------------------------------- 读掩码


def test_browser_get_masks_auth_secrets(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        structured = c.get("/api/v1/system/auth").json()
        assert structured["jwt_secret"] == mask_value(_JWT)
        assert structured["internal_verify_secret"] == mask_value(_IVS)
        assert structured["bootstrap"]["admin_password"] == mask_value(_ADMIN_PW)
        assert structured["token_ttl_seconds"] == 43200  # int 不掩

        raw = c.get("/api/v1/system/auth/raw").text
        assert _JWT not in raw and _IVS not in raw and _ADMIN_PW not in raw
        assert "# comment line" in raw  # 注释保留
        parsed = yaml.safe_load(raw)
        assert parsed["jwt_secret"] == mask_value(_JWT)
        assert parsed["token_ttl_seconds"] == 43200


def test_internal_get_returns_plaintext(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        structured = c.get("/api/v1/system/auth", headers=_INTERNAL).json()
        assert structured["jwt_secret"] == _JWT
        assert structured["bootstrap"]["admin_password"] == _ADMIN_PW
        raw = c.get("/api/v1/system/auth/raw", headers=_INTERNAL).text
        assert _JWT in raw and _ADMIN_PW in raw


# ---------------------------------------------------------------- 写合并（验证项 3/6/7）


def test_put_roundtrip_preserves_secrets(tmp_path: Path) -> None:
    """浏览器改 token_ttl_seconds 保存 → 磁盘密钥原值不动（结构性保证）。"""
    with _client(tmp_path) as c:
        masked = c.get("/api/v1/system/auth/raw").text
        edited = masked.replace("43200", "3600")
        r = c.put("/api/v1/system/auth/raw", content=edited,
                  headers={"Content-Type": "text/yaml"})
        assert r.status_code == 200, r.text
        assert set(r.json()["preserved"]) == {
            "jwt_secret", "internal_verify_secret", "bootstrap.admin_password"}

        disk = (tmp_path / "system" / "auth.yaml").read_text(encoding="utf-8")
        assert _JWT in disk and _IVS in disk and _ADMIN_PW in disk
        assert MASK_PREFIX not in disk  # 掩码绝不落盘
        assert yaml.safe_load(disk)["token_ttl_seconds"] == 3600
        assert "# comment line" in disk  # 注释保留

        raw_int = c.get("/api/v1/system/auth/raw", headers=_INTERNAL).text
        assert "3600" in raw_int and _JWT in raw_int


def test_put_mask_literal_on_new_key_400(tmp_path: Path) -> None:
    """验证项 6：新键填掩码字面量 → 400 且磁盘不变。"""
    with _client(tmp_path) as c:
        before = (tmp_path / "system" / "auth.yaml").read_text(encoding="utf-8")
        body = _AUTH_YAML + f'new_password: "{MASK_PREFIX}abcd"\n'
        r = c.put("/api/v1/system/auth/raw", content=body,
                  headers={"Content-Type": "text/yaml"})
        assert r.status_code == 400
        assert "new_password" in r.json()["detail"]
        after = (tmp_path / "system" / "auth.yaml").read_text(encoding="utf-8")
        assert after == before


def test_put_structure_change_with_mask_400(tmp_path: Path) -> None:
    """验证项 7：结构变化后掩码无原值可配对 → 明确 400，不 panic。"""
    with _client(tmp_path) as c:
        masked = c.get("/api/v1/system/auth/raw").text
        # 把 bootstrap 从 mapping 改成 list，掩码值落在 list 项里 → 路径失配
        broken = masked.replace(
            'bootstrap:\n  initialize_on_deploy: false\n'
            f'  admin_password: "{mask_value(_ADMIN_PW)}"\n',
            'bootstrap:\n  - initialize_on_deploy: false\n'
            f'  - admin_password: "{mask_value(_ADMIN_PW)}"\n')
        assert broken != masked  # 替换确实生效
        r = c.put("/api/v1/system/auth/raw", content=broken,
                  headers={"Content-Type": "text/yaml"})
        assert r.status_code == 400
        assert "bootstrap" in r.json()["detail"]


def test_put_rotation_with_real_value_passes(tmp_path: Path) -> None:
    """轮换：输入新真实密钥 → 原样落盘。"""
    with _client(tmp_path) as c:
        masked = c.get("/api/v1/system/auth/raw").text
        rotated = masked.replace(mask_value(_JWT), "ffff567890aaaaffff567890aaaaffff")
        r = c.put("/api/v1/system/auth/raw", content=rotated,
                  headers={"Content-Type": "text/yaml"})
        assert r.status_code == 200, r.text
        disk = (tmp_path / "system" / "auth.yaml").read_text(encoding="utf-8")
        assert "ffff567890aaaaffff567890aaaaffff" in disk
        assert _IVS in disk and _ADMIN_PW in disk  # 其余密钥保留


# ---------------------------------------------------------------- consume-bootstrap


def test_consume_bootstrap_internal_only_and_idempotent(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        # 无内部头（admin JWT 也一样，见下）→ 403
        r0 = c.post("/api/v1/system/auth/consume-bootstrap")
        assert r0.status_code == 403

        r1 = c.post("/api/v1/system/auth/consume-bootstrap", headers=_INTERNAL)
        assert r1.status_code == 200, r1.text
        assert r1.json() == {"ok": True, "removed": True}
        disk = (tmp_path / "system" / "auth.yaml").read_text(encoding="utf-8")
        assert "admin_password" not in disk
        assert _JWT in disk and "# comment line" in disk
        assert yaml.safe_load(disk)["bootstrap"] == {"initialize_on_deploy": False}

        r2 = c.post("/api/v1/system/auth/consume-bootstrap", headers=_INTERNAL)
        assert r2.json() == {"ok": True, "removed": False}  # 幂等


def test_consume_bootstrap_admin_jwt_still_403(tmp_path: Path) -> None:
    """真 app + enabled 鉴权：admin JWT 能过中间件，但 handler 仍只认内部调用。"""
    (tmp_path / "system").mkdir(exist_ok=True)
    (tmp_path / "system" / "auth.yaml").write_text(
        _AUTH_YAML.replace("enabled: false", "enabled: true"), encoding="utf-8")
    (tmp_path / "domain_registry.yaml").write_text("domains: {}\n", encoding="utf-8")
    token = encode({"sub": "admin", "role": "admin", "name": "A"}, _JWT, ttl=3600)
    with patch("main_control_service.service.YamlConfigService.domain_access_for",
               new_callable=AsyncMock,
               return_value=({"site_role": "admin", "grants": []}, "")):
        with TestClient(create_app(config_dir=tmp_path)) as c:
            r = c.post("/api/v1/system/auth/consume-bootstrap",
                       headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403
            assert r.json()["detail"] == "internal_only"
            ok = c.post("/api/v1/system/auth/consume-bootstrap", headers=_INTERNAL)
            assert ok.status_code == 200


# ---------------------------------------------------------------- 审计（4.4：GET 也要记）


def test_audit_logs_read_and_write_without_values(
    tmp_path: Path, monkeypatch,
) -> None:
    log_dir = tmp_path / "audit-out"
    monkeypatch.setenv("CMKB_LOG_DIR", str(log_dir))
    audit._reset_for_tests()
    try:
        with _client(tmp_path) as c:
            c.get("/api/v1/system/auth")                    # 浏览器读（掩码）
            c.get("/api/v1/system/auth", headers=_INTERNAL)  # 内部读（不记）
            masked = c.get("/api/v1/system/auth/raw").text
            c.put("/api/v1/system/auth/raw", content=masked,
                  headers={"Content-Type": "text/yaml"})     # 掩码回传保存

            lines = (log_dir / "audit.log").read_text(encoding="utf-8").splitlines()
            entries = [json.loads(ln) for ln in lines]
            reads = [e for e in entries if e["event"] == "config_read"]
            writes = [e for e in entries if e["event"] == "config_write"]
            # 浏览器读两条（结构化+raw）；内部读不产生审计
            assert len(reads) == 2
            assert all(e["path"].startswith("/api/v1/system/auth") for e in reads)
            assert {k for e in reads for k in e["keys"]} >= {
                "jwt_secret", "internal_verify_secret", "bootstrap.admin_password"}
            assert len(writes) == 1
            assert set(writes[0]["keys"]) == {
                "jwt_secret", "internal_verify_secret", "bootstrap.admin_password"}
            # 键名可以有，值绝不进审计
            audit_text = "\n".join(lines)
            assert _JWT not in audit_text and _IVS not in audit_text
            assert _ADMIN_PW not in audit_text
    finally:
        audit._reset_for_tests()


def test_audit_log_hidden_from_logs_api(tmp_path: Path, monkeypatch) -> None:
    log_dir = tmp_path / "audit-out2"
    monkeypatch.setenv("CMKB_LOG_DIR", str(log_dir))
    audit._reset_for_tests()
    try:
        with _client(tmp_path) as c:
            c.get("/api/v1/system/auth")
            assert (log_dir / "audit.log").exists()
            names = [entry.name for entry in list_logs()]
            assert "audit.log" not in names  # 审计与被审计对象不同窗
    finally:
        audit._reset_for_tests()
