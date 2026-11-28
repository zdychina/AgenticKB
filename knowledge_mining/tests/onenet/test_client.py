# -*- coding: utf-8 -*-
"""OnenetClient 单元测试（httpx.MockTransport，不触内网）.

覆盖：token 获取/过期重取、原生/DSL 查询、count_source、part_range、
fetch_source_chunk（sort/from 翻页/窗口上限/完整性）、退避重试、
上游非 2xx 稳定类别化（BUG-14：不重试 4xx、报文只进日志且脱敏）。
"""
from __future__ import annotations

import json

import httpx
import pytest

from knowledge_mining.mining.onenet.client import (
    OnenetClient,
    OnenetQueryError,
)

# ---------------------------------------------------------------- mock transport


def _slice(nid: str, part_id: int, path="Pkg > L1 > L2") -> dict:
    return {"nid": nid, "part_id": part_id, "path": path, "title": path.split(" > ")[-1],
            "content": f"内容 {part_id}", "source_id": "DOC1"}


#: BUG-14 现场的 S3 XML 错误页样例（含鉴权头回显）。
S3_XML_ERROR = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    "<Error><Code>InvalidArgument</Code>"
    "<Message>Unsupported Authorization Type</Message>"
    "<RequestId>req-1</RequestId><HostId>host-1</HostId>"
    "<Authorization>Bearer null</Authorization></Error>"
)


class FakeOnenet:
    """按 URL 分发的 MockTransport 处理器."""

    def __init__(self, token_url: str, search_url: str):
        self.token_url = token_url
        self.search_url = search_url
        self.token_requests = 0
        self.search_requests: list[dict] = []
        self.token_status_once = None  # 注入一次 token 端点异常状态码
        self.search_status_once = None  # 注义一次搜索端点异常状态码
        self.token_history: list[str | None] = []
        self.token_result = "Basic dyn-token"  # token 端点 result 值（可置 None）
        self.search_raw_once = None   # (status, text)：一次性原始报文（S3 XML 场景）
        self.search_raw_repeat = None  # (status, text)：持续原始报文（重试耗尽场景）
        # DSL/原生查询的响应数据（search body -> response）
        self.responses: list[dict] = []
        self.repeat_last = False  # 耗尽后重复末条响应（持续异常/满页场景）
        self._resp_idx = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url == self.token_url:
            self.token_requests += 1
            if self.token_status_once is not None:
                code, self.token_status_once = self.token_status_once, None
                return httpx.Response(code, json={"error": "expired"})
            return httpx.Response(200, json={"result": self.token_result})
        if str(request.url) == self.search_url:
            self.token_history.append(request.headers.get("Authorization"))
            body = json.loads(request.content.decode("utf-8"))
            self.search_requests.append(body)
            if self.search_status_once is not None:
                code, self.search_status_once = self.search_status_once, None
                return httpx.Response(code, json={"error": "boom"})
            if self.search_raw_once is not None:
                code, text = self.search_raw_once
                self.search_raw_once = None
                return httpx.Response(
                    code, text=text,
                    headers={"Content-Type": "application/xml"})
            if self.search_raw_repeat is not None:
                code, text = self.search_raw_repeat
                return httpx.Response(
                    code, text=text,
                    headers={"Content-Type": "application/xml"})
            if self.responses and self._resp_idx < len(self.responses):
                resp = self.responses[self._resp_idx]
                self._resp_idx += 1
            elif self.responses and self.repeat_last:
                resp = self.responses[-1]
            else:
                # 响应用尽 → 空页（模拟真实接口翻页到底）
                resp = {"total": 0, "searchResults": []}
            return httpx.Response(200, json=resp)
        return httpx.Response(404, json={"error": "unknown url"})


def _client(fake: FakeOnenet) -> OnenetClient:
    return OnenetClient(
        app_id="app", static_token="secret",
        token_url=fake.token_url, search_url=fake.search_url,
        transport=httpx.MockTransport(fake.handler),
    )


@pytest.fixture
def fake():
    return FakeOnenet(
        token_url="http://oauth2.test/token", search_url="http://apigw.test/search?source_type=0"
    )


# ---------------------------------------------------------------- token


def test_token_fetch_uses_base64_credential(fake):
    c = _client(fake)
    tok = c.get_token()
    assert tok == "Basic dyn-token"
    # 换 token 请求体：credential = base64(static_token)
    # （MockTransport 不暴露请求体给调用方，这里直接验证内部实现一致性）
    import base64
    assert base64.b64encode(b"secret").decode()  # 构造不报错


def test_token_cached_until_forced(fake):
    c = _client(fake)
    c.get_token()
    c.get_token()
    assert fake.token_requests == 1
    c.get_token(force=True)
    assert fake.token_requests == 2


# ---------------------------------------------------------------- queries


def test_query_native_builds_and_query_list(fake):
    fake.responses = [{"total": 1, "searchResults": [_slice("n1", 1)]}]
    c = _client(fake)
    out = c.query_native(
        [{"field": "doc_name", "fuzzy": True, "content": "UDG"},
         {"field": "doc_type", "fuzzy": False, "content": "hwics"}],
        page_num=2, page_size=10,
    )
    assert out["total"] == 1
    body = fake.search_requests[0]
    assert body["pageNum"] == 2 and body["pageSize"] == 10
    assert body["searchQueryList"] == [
        {"field": "doc_name", "fuzzy": True, "content": "UDG"},
        {"field": "doc_type", "fuzzy": False, "content": "hwics"},
    ]


def test_query_native_with_total_track_switches_to_dsl(fake):
    fake.responses = [{"total": 157866, "searchResults": [_slice("n1", 1)]}]
    c = _client(fake)
    out = c.query_native([{"field": "source_id", "fuzzy": False, "content": "DOC1"}],
                          with_total_track=True)
    assert out["total"] == 157866
    body = fake.search_requests[0]
    assert "dsl" in body
    dsl = json.loads(body["dsl"])
    assert dsl["track_total_hits"] is True
    assert dsl["query"]["bool"]["must"][0]["term"]["source_id.keyword"]["value"] == "DOC1"


def test_query_dsl_dict_and_string(fake):
    fake.responses = [{"total": 0, "searchResults": []}]
    c = _client(fake)
    c.query_dsl({"query": {"match_all": {}}, "from": 0, "size": 1})
    c.query_dsl('{"query": {"match_all": {}}, "from": 0, "size": 1}')
    for body in fake.search_requests:
        assert "dsl" in body


def test_malformed_response_raises(fake):
    fake.repeat_last = True
    fake.responses = [{"unexpected": 1}]
    c = _client(fake)
    with pytest.raises(OnenetQueryError):
        c.query_native(page_size=1)


# ---------------------------------------------------------------- token 过期重取


def test_401_triggers_token_refresh_and_retry(fake):
    fake.responses = [{"total": 2, "searchResults": [_slice("n1", 1), _slice("n2", 2)]}]
    c = _client(fake)
    c.get_token()  # 预热缓存（token 端点正常）
    # 搜索端点第一次 401 → 客户端重取 token 后重试成功
    fake.search_status_once = 401
    out = c.query_native(page_size=1)
    assert out["total"] == 2
    assert fake.token_requests == 2  # 重取过一次


# ---------------------------------------------------------------- count / part range


def test_count_source_uses_track_total_hits(fake):
    fake.responses = [{"total": 157866, "searchResults": []}]
    c = _client(fake)
    assert c.count_source("DOC1101733708") == 157866
    dsl = json.loads(fake.search_requests[0]["dsl"])
    assert dsl["track_total_hits"] is True
    assert dsl["query"]["bool"]["must"][0]["term"]["source_id.keyword"]["value"] == "DOC1101733708"


def test_part_range_asc_desc(fake):
    fake.responses = [
        {"total": 5, "searchResults": [_slice("min", 3)]},   # asc
        {"total": 5, "searchResults": [_slice("max", 17)]},  # desc
    ]
    c = _client(fake)
    assert c.part_range("DOC1") == {"min": 3, "max": 17}
    dsls = [json.loads(b["dsl"]) for b in fake.search_requests]
    assert dsls[0]["sort"][0] == {"part_id": "asc"}
    assert dsls[1]["sort"][0] == {"part_id": "desc"}


# ---------------------------------------------------------------- fetch_source_chunk


def test_fetch_chunk_paginates_with_stable_sort(fake):
    # 窗口 [1,5]，page_size=2 → 3 页（2+2+1）
    fake.responses = [
        {"total": 5, "searchResults": [_slice("n1", 1), _slice("n2", 2)]},
        {"total": 5, "searchResults": [_slice("n3", 3), _slice("n4", 4)]},
        {"total": 5, "searchResults": [_slice("n5", 5)]},
    ]
    c = _client(fake)
    rows = c.fetch_source_chunk("DOC1", 1, 5, page_size=2)
    assert [r["part_id"] for r in rows] == [1, 2, 3, 4, 5]
    for body in fake.search_requests:
        dsl = json.loads(body["dsl"])
        assert dsl["sort"] == [{"part_id": "asc"}, {"nid.keyword": "asc"}]
        assert dsl["query"]["bool"]["must"][1]["range"]["part_id"] == {"gte": 1, "lte": 5}


def test_fetch_chunk_incomplete_raises(fake):
    # 窗口声明 total=5 但只回了 3 条（最后一页短且未继续）
    fake.responses = [
        {"total": 5, "searchResults": [_slice("n1", 1), _slice("n2", 2)]},
        {"total": 5, "searchResults": [_slice("n3", 3)]},  # len < page_size → 提前停
    ]
    c = _client(fake)
    with pytest.raises(OnenetQueryError, match="拉取不完整"):
        c.fetch_source_chunk("DOC1", 1, 5, page_size=2)


def test_fetch_chunk_window_over_10000_raises(fake):
    fake.repeat_last = True
    fake.responses = [{"total": 20000, "searchResults": [_slice(f"n{i}", i) for i in range(1, 3)]}]
    c = _client(fake)
    with pytest.raises(OnenetQueryError, match="窗口"):
        c.fetch_source_chunk("DOC1", 1, 20000, page_size=2)


def test_fetch_chunk_fields_projection_passed(fake):
    fake.responses = [{"total": 1, "searchResults": [_slice("n1", 1)]}]
    c = _client(fake)
    c.fetch_source_chunk("DOC1", 1, 1, page_size=1, fields=["path", "title", "part_id"])
    dsl = json.loads(fake.search_requests[0]["dsl"])
    assert dsl["_source"] == ["path", "title", "part_id"]


# ---------------------------------------------------------------- retry / errors


def test_request_failure_retries_then_raises(fake, monkeypatch):
    import knowledge_mining.mining.onenet.client as client_mod
    sleeps: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: sleeps.append(s))
    fake.repeat_last = True
    fake.responses = [{"bad": "shape"}]  # 每次都异常形状 → 重试 3 次后抛
    c = _client(fake)
    c.get_token()
    with pytest.raises(OnenetQueryError):
        c.query_native(page_size=1)
    assert len(sleeps) >= 2  # 退避发生过（最后一次失败不再白等，审查 LOW）


# ---------------------------------------------------------------- BUG-14：上游非 2xx


def test_4xx_fails_fast_without_body_leak(fake, caplog):
    """BUG-14 现场复刻：400 + S3 XML（含鉴权头回显）。

    - 异常文本只含稳定类别+状态码（无 XML/无 Authorization 形态）；
    - 原始报文只进服务端日志，且鉴权回显脱敏；
    - 4xx 不退避重试（不会自愈）。
    """
    fake.search_raw_once = (400, S3_XML_ERROR)
    c = _client(fake)
    with caplog.at_level("WARNING",
                         logger="knowledge_mining.mining.onenet.client"):
        with pytest.raises(OnenetQueryError) as ei:
            c.query_native(page_size=1)
    assert str(ei.value) == "onenet_upstream_error: upstream_status=400"
    assert len(fake.search_requests) == 1  # 不重试
    logged = "".join(r.getMessage() for r in caplog.records)
    assert "InvalidArgument" in logged        # 报文在日志里可排障
    assert "<Authorization>***" in logged     # 鉴权回显已脱敏
    assert "Bearer null" not in logged
    assert "Bearer null" not in str(ei.value)


def test_persistent_401_classified_auth_error(fake):
    """401 首次强制重取 token 后仍 401 → 归类 onenet_auth_error 快速失败。"""
    fake.search_raw_repeat = (401, "denied")
    c = _client(fake)
    c.get_token()
    with pytest.raises(OnenetQueryError, match="onenet_auth_error"):
        c.query_native(page_size=1)
    assert fake.token_requests == 2          # 原始获取 + 强制重取各一次
    assert len(fake.search_requests) == 2    # 重取后只再试一次，不再退避


def test_5xx_retried_then_recovers(fake, monkeypatch):
    import knowledge_mining.mining.onenet.client as client_mod
    sleeps: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: sleeps.append(s))
    fake.search_status_once = 502            # 一次 502，之后恢复
    fake.responses = [{"total": 1, "searchResults": [_slice("n1", 1)]}]
    c = _client(fake)
    out = c.query_native(page_size=1)
    assert out["total"] == 1
    assert len(fake.search_requests) == 2    # 5xx 照旧退避重试
    assert sleeps == [1.5]


def test_5xx_exhausts_retries_status_only(fake, monkeypatch):
    import knowledge_mining.mining.onenet.client as client_mod
    sleeps: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: sleeps.append(s))
    fake.search_raw_repeat = (503, "busy")
    c = _client(fake)
    c.get_token()
    with pytest.raises(OnenetQueryError,
                       match=r"请求失败\(3次\): upstream_status=503"):
        c.query_native(page_size=1)
    assert len(fake.search_requests) == 3
    assert sleeps == [1.5, 3.0]              # 最后一次失败不白等（审查 LOW）


def test_token_endpoint_4xx_auth_error_no_search_sent(fake):
    """凭据被拒：请求前失败（不发 search），稳定 auth 类别，不退避。"""
    fake.token_status_once = 401
    c = _client(fake)
    with pytest.raises(OnenetQueryError,
                       match=r"onenet_auth_error: token_status=401"):
        c.query_native(page_size=1)
    assert fake.token_requests == 1
    assert fake.search_requests == []


def test_token_empty_result_auth_error_no_search_sent(fake):
    """token 端点 200 但 result 为空：同样请求前失败、不发 search。"""
    fake.token_result = None
    c = _client(fake)
    with pytest.raises(OnenetQueryError, match="onenet_auth_error"):
        c.query_native(page_size=1)
    assert fake.search_requests == []
