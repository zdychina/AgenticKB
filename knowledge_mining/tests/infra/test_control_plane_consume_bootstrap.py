# -*- coding: utf-8 -*-
"""BUG-05：consume_bootstrap_password —— 播种后消费 auth.yaml 的 admin_password。"""
from __future__ import annotations

import httpx

from knowledge_mining.mining.infra import control_plane


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_consume_posts_internal_endpoint_with_headers(monkeypatch) -> None:
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response(200)

    monkeypatch.setattr(control_plane, "control_plane_internal_headers", lambda: {
        "X-Internal-Auth": "ivs-test",
    })
    monkeypatch.setattr(control_plane.httpx, "post", fake_post)

    assert control_plane.consume_bootstrap_password() is True
    assert captured["url"].endswith("/api/v1/system/auth/consume-bootstrap")
    assert captured["headers"] == {"X-Internal-Auth": "ivs-test"}
    assert captured["proxy"] is None and captured["trust_env"] is False


def test_consume_returns_false_on_error_status(monkeypatch) -> None:
    monkeypatch.setattr(control_plane.httpx, "post",
                        lambda url, **kwargs: _Response(500))
    assert control_plane.consume_bootstrap_password() is False
    monkeypatch.setattr(control_plane.httpx, "post",
                        lambda url, **kwargs: _Response(403))
    assert control_plane.consume_bootstrap_password() is False


def test_consume_returns_false_on_transport_error(monkeypatch) -> None:
    def boom(url, **kwargs):
        raise httpx.ConnectError("control plane down")

    monkeypatch.setattr(control_plane.httpx, "post", boom)
    assert control_plane.consume_bootstrap_password() is False
