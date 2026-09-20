"""制品路由的装配与契约（52号）。

这组用例盯的是**路由层本身**，不是业务语义：依赖注入有没有接上、参数契约对不对、
异常有没有被映射成该有的状态码、静态前缀会不会被动态段抢匹配。

存在的直接原因：所有制品仓储都改走了按域路由的池，于是每条路由都多了一个
``domain: str = Query(...)`` 依赖。漏一条，运行时 422，而在此之前**没有任何测试
会发现**——服务层测试绕过 HTTP，仓储测试绕过路由。

用 ``dependency_overrides`` 注假服务走真 HTTP（沿用 tests/kb 的做法），不需要 PG。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge_mining.mining.agent_creation.routes import (
    admin_router as creation_admin_router,
    get_creation_service,
)
from knowledge_mining.mining.kb.auth import current_user
from knowledge_mining.mining.knowledge_product.consume_routes import (
    get_consume_service,
    router as published_router,
)
from knowledge_mining.mining.knowledge_product.consume import ConsumeRejected
from knowledge_mining.mining.knowledge_product.review import ReviewRejected
from knowledge_mining.mining.knowledge_product.routes import (
    get_product_service,
    router as product_router,
)
from knowledge_mining.mining.knowledge_product.service import (
    NotFound,
    ValidationRejected,
)
from knowledge_mining.mining.knowledge_product.validate import Issue

DOMAIN = {"domain": "cloud_core_network"}


class FakeProductService:
    """只实现路由用到的方法；默认成功，按需改成抛异常。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.raises: Exception | None = None

    def _record(self, _name: str, *args, **kwargs):
        self.calls.append((_name, args, kwargs))
        if self.raises is not None:
            raise self.raises

    async def get_product(self, product_id):
        self._record("get_product", product_id)
        return {"id": product_id, "name": "p", "owner": "u"}

    async def list_products(self):
        self._record("list_products")
        return [{"id": "p"}]

    async def create_product(self, **kwargs):
        self._record("create_product", **kwargs)
        return {"id": kwargs["product_id"], "owner": kwargs["owner"]}

    async def replace_draft(self, product_id, documents):
        self._record("replace_draft", product_id, documents)

        class _Result:
            revision_no = 2
            diff = type("D", (), {
                "added": (), "modified": (), "removed": (), "total": 0, "changes": (),
            })()

        return _Result()

    async def list_objects(self, product_id, revision=None):
        self._record("list_objects", product_id, revision)
        return [{"object_id": "p@T@a"}]

    async def get_object_md(self, product_id, object_id, revision=None):
        self._record("get_object_md", product_id, object_id, revision)
        return "---\nid: x\n---\n"

    async def review_objects(self, product_id, decisions, **kwargs):
        self._record("review_objects", product_id, decisions, **kwargs)
        return {"decision": "approved"}

    async def current_definition(self, product_id):
        self._record("current_definition", product_id)
        return {"definition_revision": 1}

    async def update_definition(self, product_id, **kwargs):
        self._record("update_definition", product_id, **kwargs)
        return {"definition_revision": 2}

    async def report_issue(self, product_id, **kwargs):
        self._record("report_issue", product_id, **kwargs)
        return {"id": "kpis_1", "status": "open", **kwargs}

    async def list_issues(self, product_id, status=None):
        self._record("list_issues", product_id, status)
        return []

    async def resolve_issue(self, issue_id, **kwargs):
        self._record("resolve_issue", issue_id, **kwargs)
        return {"id": issue_id, "status": kwargs["status"]}

    async def source_alerts(self, product_id, revision=None):
        self._record("source_alerts", product_id, revision)
        return []

    async def publish_gate(self, product_id, revision=None):
        self._record("publish_gate", product_id, revision)

        class _Gate:
            def as_dict(self):
                return {"passed": True, "checks": []}

        return _Gate()

    async def publish(self, product_id, revision=None, *, force=False):
        self._record("publish", product_id, revision, force=force)
        return {"released_revision": 2, "forced": force}


class FakeCreationService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def invalidate_product_tickets(self, product_id, *, reason):
        self.calls.append(f"revoke:{product_id}")
        return 2


class FakeConsumeService:
    def __init__(self) -> None:
        self.raises: Exception | None = None

    async def catalog(self):
        if self.raises:
            raise self.raises
        return [{"product_id": "p"}]

    async def outline(self, product_id):
        if self.raises:
            raise self.raises
        return {"product_id": product_id, "objects": []}

    async def fetch(self, ids):
        if self.raises:
            raise self.raises
        return {i: {"ok": True, "id": i} for i in ids}

    async def search(self, terms, **kwargs):
        if self.raises:
            raise self.raises

        class _Result:
            def as_dict(self):
                return {"terms": list(terms), "total": 0, "hits": []}

        return _Result()


@pytest.fixture
def services():
    return {
        "product": FakeProductService(),
        "creation": FakeCreationService(),
        "consume": FakeConsumeService(),
    }


@pytest.fixture
def client(services):
    app = FastAPI()
    app.include_router(product_router)
    app.include_router(creation_admin_router)
    app.include_router(published_router)
    app.dependency_overrides[current_user] = lambda: {"username": "alice", "user_id": "u1"}
    app.dependency_overrides[get_product_service] = lambda: services["product"]
    app.dependency_overrides[get_creation_service] = lambda: services["creation"]
    app.dependency_overrides[get_consume_service] = lambda: services["consume"]
    return TestClient(app)


# ----------------------------------------------------------------- 域参数契约


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/knowledge-products"),
        ("get", "/api/knowledge-products/p"),
        ("get", "/api/knowledge-products/p/objects"),
        ("get", "/api/knowledge-products/p/publish-gate"),
        ("get", "/api/knowledge-products/p/issues"),
        ("get", "/api/knowledge-products/p/definition"),
        ("get", "/api/published-products"),
    ],
)
def test_every_product_route_requires_a_domain(method, path) -> None:
    """不注假服务时，缺 domain 必须被 FastAPI 在进依赖之前挡成 422。

    漏一条就意味着那条路由拿不到按域路由的池——真跑起来会连错库或直接 500。
    """
    app = FastAPI()
    app.include_router(product_router)
    app.include_router(creation_admin_router)
    app.include_router(published_router)
    app.dependency_overrides[current_user] = lambda: {"username": "a", "user_id": "u"}

    response = getattr(TestClient(app), method)(path)
    assert response.status_code == 422
    assert any(
        err.get("loc", [])[-1:] == ["domain"] for err in response.json()["detail"]
    ), response.text


# ----------------------------------------------------------------- 路由匹配


def test_static_prefix_is_not_stolen_by_the_dynamic_segment(client, services) -> None:
    """``/backlinks/{id}`` 必须排在 ``/{product_id}`` 之前，否则被当成制品 id。"""
    services["product"].list_backlinks = _async_return([{"from_id": "x"}])
    response = client.get(
        "/api/knowledge-products/backlinks/p@T@a", params=DOMAIN,
    )
    assert response.status_code == 200
    assert response.json() == [{"from_id": "x"}]


def test_published_surface_has_its_own_prefix(client) -> None:
    """消费面独立前缀——挂在 /knowledge-products/published 会被动态段抢匹配。"""
    assert client.get("/api/published-products", params=DOMAIN).status_code == 200


def test_object_id_with_spaces_and_at_signs_routes(client, services) -> None:
    response = client.get(
        "/api/knowledge-products/p/objects/spec%40DomainFactSet%40M8%20V300R022/md",
        params=DOMAIN,
    )
    assert response.status_code == 200
    assert ("get_object_md", ("p", "spec@DomainFactSet@M8 V300R022", None), {}) in [
        (n, a, k) for n, a, k in services["product"].calls
    ]


# ----------------------------------------------------------------- 异常映射


def test_not_found_becomes_404(client, services) -> None:
    services["product"].raises = NotFound("p")
    assert client.get("/api/knowledge-products/p", params=DOMAIN).status_code == 404


def test_validation_rejected_returns_the_issue_list(client, services) -> None:
    """契约是「返回哪些字段不合格」，不是一句话——前端要逐条显示。"""
    services["product"].raises = ValidationRejected(
        [Issue("p@T@a", "missing_frontmatter", "缺必填 frontmatter 'name'", "name")]
    )
    response = client.patch(
        "/api/knowledge-products/p/draft", params=DOMAIN, json={"documents": ["x"]},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "validation_rejected"
    assert detail["issues"][0]["field"] == "name"


def test_review_rejected_becomes_422_with_a_message(client, services) -> None:
    services["product"].raises = ReviewRejected("该对象存在未定论的冲突字段")
    response = client.post(
        "/api/knowledge-products/p/reviews", params=DOMAIN,
        json={"decisions": {"p@T@a": "approve"}},
    )
    assert response.status_code == 422
    assert "冲突" in response.json()["detail"]["message"]


def test_consume_rejection_maps_its_stable_code(client, services) -> None:
    services["consume"].raises = ConsumeRejected("RESULT_TOO_LARGE", "请分批")
    response = client.post(
        "/api/published-products/fetch", params=DOMAIN, json={"ids": ["a"]},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "RESULT_TOO_LARGE"


def test_consume_not_found_maps_to_404(client, services) -> None:
    services["consume"].raises = ConsumeRejected("OBJECT_NOT_FOUND", "没有已发布内容")
    assert client.get(
        "/api/published-products/p", params=DOMAIN,
    ).status_code == 404


# ----------------------------------------------------------------- 请求契约


def test_create_product_takes_owner_from_the_caller(client, services) -> None:
    """负责人是登录身份，不能由请求体指定——否则谁都能把制品挂到别人名下。"""
    response = client.post(
        "/api/knowledge-products", params=DOMAIN,
        json={"product_id": "p", "product_type": "t", "name": "n", "owner": "mallory"},
    )
    assert response.status_code == 201
    assert response.json()["owner"] == "alice"


def test_definition_update_also_revokes_tickets(client, services) -> None:
    """改定义必须连带撤票——旧票据按旧定义继续提交等于绕过这次改动。"""
    response = client.patch(
        "/api/knowledge-products/p/definition", params=DOMAIN,
        json={"fields": {"model": {"required": True}}},
    )
    assert response.status_code == 200
    assert response.json()["revoked_tickets"] == 2
    assert services["creation"].calls == ["revoke:p"]


def test_issue_reporter_comes_from_the_caller(client, services) -> None:
    response = client.post(
        "/api/knowledge-products/p/issues", params=DOMAIN,
        json={"problem": "单位错了"},
    )
    assert response.status_code == 201
    name, _args, kwargs = services["product"].calls[-1]
    assert name == "report_issue" and kwargs["reporter"] == "alice"


@pytest.mark.parametrize("status", ["open", "whatever"])
def test_illegal_resolution_status_is_refused_by_the_schema(client, status) -> None:
    """open 不是处置结论——处置只能是 triaged/resolved/rejected。"""
    response = client.patch(
        "/api/knowledge-products/p/issues/kpis_1", params=DOMAIN,
        json={"status": status},
    )
    assert response.status_code == 422


def test_search_rejects_an_out_of_range_page_size(client) -> None:
    response = client.post(
        "/api/published-products/search", params=DOMAIN,
        json={"terms": ["x"], "size": 500},
    )
    assert response.status_code == 422


def test_publish_passes_the_force_flag_through(client, services) -> None:
    response = client.post(
        "/api/knowledge-products/p/publish", params=DOMAIN, json={"force": True},
    )
    assert response.status_code == 200
    assert response.json()["forced"] is True
    name, _args, kwargs = services["product"].calls[-1]
    assert name == "publish" and kwargs["force"] is True


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn
