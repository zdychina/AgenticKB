"""69号 P0：MCP 本体图查询内部端点真库测试。

覆盖（§3.3/§五）：三端点正路 200（域成员+本域钥匙，graph 带 ontologies 摘要、
neighbors 带对象卡+出入边）；ontology_names 过滤生效、未知名 422 附合法名；
他域钥匙访问他域库 → 404（钥匙域收口，不泄露存在性）；钥匙不存在/非本人/
已吊销 → 401 invalid mcp key；有钥匙但域成员身份被移除 → 403。
ASGITransport + 真池（骨架照抄 test_mcp_keys_routes）；本机无 PG 时整个
模块在收集期跳过。
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

if os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1":
    pytest.skip(
        "set KB_RUN_POSTGRES_ACCEPTANCE=1 to run real PostgreSQL tests",
        allow_module_level=True,
    )

from knowledge_mining.mining.kb.db import KbDB  # noqa: E402
from knowledge_mining.mining.kb.db_ontology import OntologyDB  # noqa: E402
from knowledge_mining.mining.kb.routes.mcp_keys import (  # noqa: E402
    router as mcp_keys_router,
)
from knowledge_mining.mining.kb.routes.mcp_ontology import (  # noqa: E402
    router as mcp_ontology_router,
)
from knowledge_mining.mining.kb.services.ontology_service import (  # noqa: E402
    OntologyService,
)
from knowledge_mining.tests.conftest import kb_headers  # noqa: E402

_DOMAIN = "cloud_core_network"
_OTHER_DOMAIN = "civil_engineering"
_KEYS = "/api/kb/users/me/mcp-keys"
_INTERNAL = {"X-Internal-Auth": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs")}


async def _client(async_pool) -> AsyncClient:
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig

    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()
    app.include_router(mcp_keys_router)
    app.include_router(mcp_ontology_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _mk_user(async_pool, *, domains: list[str],
                  role: str = "member") -> dict:
    db = KbDB(async_pool)
    user = await db.create_user(username=f"ontmcp-{uuid.uuid4().hex[:8]}")
    async with async_pool.connection() as conn:
        for domain in domains:
            await conn.execute(
                """INSERT INTO user_domains (user_id, domain, domain_role)
                   VALUES (%s, %s, %s)""",
                (user["id"], domain, role),
            )
    return user


async def _mk_key(c: AsyncClient, user: dict, domain: str) -> dict:
    r = await c.post(
        _KEYS, json={"name": f"图查询钥匙-{uuid.uuid4().hex[:6]}", "domain": domain},
        headers=kb_headers(user["username"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _mk_fixture(async_pool, admin: dict) -> tuple[dict, dict, str, str]:
    """库 + 网元/业务双本体 + AMF/SMF + AMF-承载->SMF + 业务对象一个。

    返回 (library, 网元本体, amf_id, 业务对象 id)——双本体供过滤断言。
    """
    svc = OntologyService(OntologyDB(async_pool))
    library = await svc.create_library(
        domain=_DOMAIN, name=f"图查询测试库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )
    device = await svc.create_ontology(
        library["id"], name="网元", description=None, attributes=None, user=admin,
    )
    service = await svc.create_ontology(
        library["id"], name="业务", description=None, attributes=None, user=admin,
    )
    carry = await svc.create_ontology_relation(
        library["id"], name="承载",
        source_ontology_id=device["id"], target_ontology_id=device["id"],
        description=None, user=admin,
    )
    devices = await svc.instantiate_batch(
        library["id"], ontology_id=device["id"], source="manual",
        objects=[{"name": "AMF"}, {"name": "SMF"}],
        relations=[{"ontology_relation_id": carry["id"],
                    "source_index": 0, "target_index": 1}],
        user=admin,
    )
    services = await svc.instantiate_batch(
        library["id"], ontology_id=service["id"], source="manual",
        objects=[{"name": "网络切片"}], relations=[], user=admin,
    )
    return library, device, devices["object_ids"][0], services["object_ids"][0]


# ------------------------------------------------------------ 三端点正路

async def test_three_endpoints_happy_path(async_pool) -> None:
    admin = await _mk_user(async_pool, domains=[_DOMAIN], role="admin")
    library, device, amf_id, slice_id = await _mk_fixture(async_pool, admin)
    async with await _client(async_pool) as c:
        key = await _mk_key(c, admin, _DOMAIN)
        scope = {"username": admin["username"], "key_id": key["id"]}

        r = await c.post("/api/kb/mcp-tools/ontology/list-libraries",
                         json=scope, headers=_INTERNAL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["domain"] == _DOMAIN
        mine = [l for l in body["libraries"] if l["id"] == library["id"]]
        assert mine and mine[0]["ontology_count"] == 2
        assert mine[0]["object_count"] == 3 and mine[0]["relation_count"] == 1

        r = await c.post("/api/kb/mcp-tools/ontology/library-graph",
                         json={**scope, "library_id": library["id"]},
                         headers=_INTERNAL)
        assert r.status_code == 200, r.text
        graph = r.json()
        # 3 节点 + 1 边 + ontologies 摘要（id/name 对应表）
        assert len(graph["nodes"]) == 3
        assert len(graph["edges"]) == 1
        assert graph["edges"][0]["relation_name"] == "承载"
        assert {o["name"] for o in graph["ontologies"]} == {"网元", "业务"}
        assert graph["total_objects"] == 3 and graph["truncated"] is False

        r = await c.post("/api/kb/mcp-tools/ontology/object-neighbors",
                         json={**scope, "object_id": amf_id}, headers=_INTERNAL)
        assert r.status_code == 200, r.text
        nb = r.json()
        assert nb["object"]["name"] == "AMF"
        assert nb["object"]["ontology_name"] == "网元"
        assert nb["object"]["outgoing_count"] == 1
        assert len(nb["outgoing"]) == 1
        assert nb["outgoing"][0]["relation_name"] == "承载"
        assert nb["outgoing"][0]["other_name"] == "SMF"
        assert nb["incoming"] == []


# ------------------------------------------------------------ 名称过滤

async def test_ontology_names_filter_and_unknown_422(async_pool) -> None:
    admin = await _mk_user(async_pool, domains=[_DOMAIN], role="admin")
    library, device, amf_id, slice_id = await _mk_fixture(async_pool, admin)
    async with await _client(async_pool) as c:
        key = await _mk_key(c, admin, _DOMAIN)
        scope = {"username": admin["username"], "key_id": key["id"]}

        r = await c.post(
            "/api/kb/mcp-tools/ontology/library-graph",
            json={**scope, "library_id": library["id"],
                  "ontology_names": ["网元"]},
            headers=_INTERNAL,
        )
        assert r.status_code == 200, r.text
        graph = r.json()
        node_ids = {n["id"] for n in graph["nodes"]}
        assert slice_id not in node_ids            # 业务本体被过滤掉
        assert len(node_ids) == 2                  # AMF + SMF（都是网元）
        assert all(n["ontology_id"] == device["id"] for n in graph["nodes"])
        assert len(graph["edges"]) == 1            # AMF→SMF 两端均在点集，边保留
        assert graph["edges"][0]["relation_name"] == "承载"

        r = await c.post(
            "/api/kb/mcp-tools/ontology/library-graph",
            json={**scope, "library_id": library["id"],
                  "ontology_names": ["网元", "不存在的本体"]},
            headers=_INTERNAL,
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["unknown_names"] == ["不存在的本体"]
        assert set(detail["valid_names"]) == {"网元", "业务"}


# ------------------------------------------------------------ 钥匙域收口

async def test_other_domain_key_404_and_scoped_listing(async_pool) -> None:
    owner = await _mk_user(async_pool, domains=[_DOMAIN], role="admin")
    library, _device, amf_id, _slice_id = await _mk_fixture(async_pool, owner)
    # 双域成员拿他域钥匙：authz 过、钥匙域不覆盖库域 → 404 不泄露存在性
    dual = await _mk_user(async_pool, domains=[_DOMAIN, _OTHER_DOMAIN])
    async with await _client(async_pool) as c:
        other_key = await _mk_key(c, dual, _OTHER_DOMAIN)
        scope = {"username": dual["username"], "key_id": other_key["id"]}

        r = await c.post("/api/kb/mcp-tools/ontology/library-graph",
                         json={**scope, "library_id": library["id"]},
                         headers=_INTERNAL)
        assert r.status_code == 404, r.text

        r = await c.post("/api/kb/mcp-tools/ontology/object-neighbors",
                         json={**scope, "object_id": amf_id}, headers=_INTERNAL)
        assert r.status_code == 404, r.text

        # 清单按钥匙域收口：他域库不出现在结果里
        r = await c.post("/api/kb/mcp-tools/ontology/list-libraries",
                         json=scope, headers=_INTERNAL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["domain"] == _OTHER_DOMAIN
        assert all(l["id"] != library["id"] for l in body["libraries"])


# ------------------------------------------------------------ 钥匙校验

async def test_key_scope_401(async_pool) -> None:
    member = await _mk_user(async_pool, domains=[_DOMAIN])
    stranger = await _mk_user(async_pool, domains=[_DOMAIN])
    async with await _client(async_pool) as c:
        key = await _mk_key(c, member, _DOMAIN)

        r = await c.post("/api/kb/mcp-tools/ontology/list-libraries",
                         json={"username": member["username"], "key_id": "nope"},
                         headers=_INTERNAL)
        assert r.status_code == 401 and "invalid mcp key" in r.text

        # 非本人钥匙
        r = await c.post("/api/kb/mcp-tools/ontology/list-libraries",
                         json={"username": stranger["username"], "key_id": key["id"]},
                         headers=_INTERNAL)
        assert r.status_code == 401 and "invalid mcp key" in r.text

        # 吊销后失效
        r = await c.post(f"{_KEYS}/{key['id']}/revoke",
                         headers=kb_headers(member["username"]))
        assert r.status_code == 204, r.text
        r = await c.post("/api/kb/mcp-tools/ontology/list-libraries",
                         json={"username": member["username"], "key_id": key["id"]},
                         headers=_INTERNAL)
        assert r.status_code == 401 and "invalid mcp key" in r.text


async def test_valid_key_but_not_domain_member_403(async_pool) -> None:
    member = await _mk_user(async_pool, domains=[_DOMAIN])
    async with await _client(async_pool) as c:
        key = await _mk_key(c, member, _DOMAIN)

    # 域成员身份被移除（钥匙仍 active）→ 授权模型另一半失败 → 403
    # （set_user_domains 拒绝空集，直删行模拟管理员解绑）
    async with async_pool.connection() as conn:
        await conn.execute(
            "DELETE FROM user_domains WHERE user_id = %s", (member["id"],),
        )
    async with await _client(async_pool) as c:
        r = await c.post("/api/kb/mcp-tools/ontology/list-libraries",
                         json={"username": member["username"], "key_id": key["id"]},
                         headers=_INTERNAL)
        assert r.status_code == 403, r.text
