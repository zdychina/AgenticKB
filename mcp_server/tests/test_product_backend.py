"""制品面 HTTP 转发的错误分流（52号 P6）。

这组用例存在的直接原因：``_post_product`` 的 ``ticketed`` 分支曾经因为签名没跟着
函数体一起改而是个 NameError，而当时全部测试照样绿——没有一条用例真的走到这条
路径。转发层的错误措辞是 Agent 能看到的唯一线索，值得被真正执行一遍。
"""
from __future__ import annotations

import json

import pytest

from mcp_server import tools


class _Resp:
    def __init__(self, status: int, body: object = None, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.text = text

    def json(self) -> object:
        if self._body is None:
            raise ValueError("not json")
        return self._body


@pytest.fixture
def captured(monkeypatch):
    """拦住 httpx.post，记下请求并返回预置响应。"""
    calls: list[dict] = []
    box: dict = {"resp": _Resp(200, {"ok": True})}

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return box["resp"]

    monkeypatch.setattr(tools.httpx, "post", fake_post)
    monkeypatch.setattr(tools, "_internal_auth_secret", lambda: "secret")
    return calls, box


# ----------------------------------------------------------------- 请求形状


def test_domain_rides_as_a_query_param(captured) -> None:
    """制品落按域路由的库——每条转发都必须带域，否则打到别的域的库。"""
    calls, _ = captured
    tools.product_catalog("cloud_core_network")
    assert calls[0]["params"] == {"domain": "cloud_core_network"}
    assert calls[0]["headers"]["X-Internal-Auth"] == "secret"


def test_fetch_sends_the_id_batch(captured) -> None:
    calls, _ = captured
    tools.product_fetch(["p@T@a", "p@T@b"], "d")
    assert calls[0]["json"] == {"ids": ["p@T@a", "p@T@b"]}


def test_outline_passes_product_id_as_a_param(captured) -> None:
    calls, _ = captured
    tools.product_outline("spec-ne8000", "d")
    assert calls[0]["params"]["product_id"] == "spec-ne8000"


def test_missing_secret_fails_before_any_request(monkeypatch) -> None:
    monkeypatch.setattr(tools, "_internal_auth_secret", lambda: "")
    with pytest.raises(tools.ToolBackendError, match="内部鉴权"):
        tools.product_catalog("d")


# ----------------------------------------------------------------- 错误分流


def test_typed_error_code_reaches_the_agent(captured) -> None:
    """后端的稳定错误码要原样透出——Agent 靠它自我纠正。"""
    _calls, box = captured
    box["resp"] = _Resp(413, {"detail": {"code": "RESULT_TOO_LARGE", "message": "请分批"}})
    with pytest.raises(tools.ToolBackendError, match=r"\[RESULT_TOO_LARGE\] 请分批"):
        tools.product_fetch(["a"], "d")


def test_consumption_404_does_not_mention_tickets(captured) -> None:
    """消费面没有票据，说「票据被拒」会把 Agent 引到错的方向。"""
    _calls, box = captured
    box["resp"] = _Resp(404, {"detail": "nope"})
    with pytest.raises(tools.ToolBackendError) as exc:
        tools.product_fetch(["a"], "d")
    assert "票据" not in str(exc.value)
    assert "已发布" in str(exc.value)


def test_creation_404_does_mention_tickets(captured) -> None:
    _calls, box = captured
    box["resp"] = _Resp(404, {"detail": "nope"})
    with pytest.raises(tools.ToolBackendError, match="票据"):
        tools.get_creation_context("kpt_x", "d")


def test_creation_403_falls_back_to_ticket_wording(captured) -> None:
    _calls, box = captured
    box["resp"] = _Resp(403, {"detail": "opaque"})
    with pytest.raises(tools.ToolBackendError, match="任务票据被拒绝"):
        tools.get_creation_context("kpt_x", "d")


def test_consumption_403_falls_back_to_access_wording(captured) -> None:
    _calls, box = captured
    box["resp"] = _Resp(403, {"detail": "opaque"})
    with pytest.raises(tools.ToolBackendError) as exc:
        tools.product_search({"terms": ["x"]}, "d")
    assert "无权访问该制品" in str(exc.value)


def test_non_json_error_body_does_not_blow_up(captured) -> None:
    _calls, box = captured
    box["resp"] = _Resp(500, None, text="<html>gateway</html>")
    with pytest.raises(tools.ToolBackendError, match="HTTP 500"):
        tools.product_catalog("d")


def test_unreachable_backend_says_so_plainly(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(tools, "_internal_auth_secret", lambda: "secret")

    def boom(url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(tools.httpx, "post", boom)
    with pytest.raises(tools.ToolBackendError, match="暂不可用"):
        tools.product_catalog("d")


def test_success_body_passes_through(captured) -> None:
    _calls, box = captured
    box["resp"] = _Resp(200, {"products": [{"product_id": "p"}]})
    assert tools.product_catalog("d") == {"products": [{"product_id": "p"}]}


def test_submit_carries_every_field(captured) -> None:
    calls, _ = captured
    tools.submit_creation_result("kpt_x", "sub-1", 3, ["---\nid: a\n---\n"], "d", "prod")
    body = calls[0]["json"]
    assert body["submission_id"] == "sub-1"
    assert body["based_on_draft_revision"] == 3
    assert body["product_id"] == "prod"
    assert json.dumps(body)  # 可序列化
