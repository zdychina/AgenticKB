"""本体库实例图查询测试 —— 真库门控（68号 P0，GET /libraries/{id}/graph）。

覆盖：空库空图；节点/边字段与方向；ontology_ids 过滤（点集过滤 + 边两端
收敛）；limit 截断与 truncated/total_objects 口径；跨库隔离；读权限
（域成员 200 / 域外 403）。本机无 PG 时整个模块在收集期跳过（与
KB_RUN_POSTGRES_ACCEPTANCE 验收门同款门槛）。
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

if os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1":
    pytest.skip(
        "set KB_RUN_POSTGRES_ACCEPTANCE=1 to run real PostgreSQL tests",
        allow_module_level=True,
    )

from knowledge_mining.mining.kb.db import KbDB  # noqa: E402
from knowledge_mining.mining.kb.db_ontology import OntologyDB  # noqa: E402
from knowledge_mining.mining.kb.services.ontology_service import (  # noqa: E402
    Forbidden, OntologyService,
)

_DOMAIN = "cloud_core_network"
_OTHER_DOMAIN = "civil_engineering"


def _svc(pool) -> OntologyService:
    return OntologyService(OntologyDB(pool))


async def _make_user(pool, *, domain: str | None = None,
                     domain_admin: bool = False) -> dict:
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"graph-{uuid.uuid4().hex[:8]}")
    if domain is not None:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO user_domains (user_id, domain, domain_role)
                   VALUES (%s, %s, %s)""",
                (user["id"], domain, "admin" if domain_admin else "member"),
            )
    return user


async def _make_library(svc, admin: dict) -> dict:
    return await svc.create_library(
        domain=_DOMAIN, name=f"图测试库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )


async def _make_graph_fixture(svc, admin: dict):
    """三对象两本体一边：AMF/SMF（网元）-承载-> 切片1（业务）。"""
    lib = await _make_library(svc, admin)
    onto_a = await svc.create_ontology(
        lib["id"], name=f"网元-{uuid.uuid4().hex[:4]}", description=None,
        attributes=[{"name": "厂商"}], user=admin,
    )
    onto_b = await svc.create_ontology(
        lib["id"], name=f"业务-{uuid.uuid4().hex[:4]}", description=None,
        attributes=[{"name": "备注"}], user=admin,
    )
    rel = await svc.create_ontology_relation(
        lib["id"], name="承载", description=None,
        source_ontology_id=onto_a["id"], target_ontology_id=onto_b["id"],
        user=admin,
    )
    amf = await svc.create_object(
        onto_a["id"], name="AMF", attributes={"厂商": "华为"}, user=admin)
    smf = await svc.create_object(
        onto_a["id"], name="SMF", attributes=None, user=admin)
    slice1 = await svc.create_object(
        onto_b["id"], name="切片1", attributes=None, user=admin)
    edge = await svc.create_semantic_relation(
        ontology_relation_id=rel["id"], source_object_id=amf["id"],
        target_object_id=slice1["id"], user=admin,
    )
    return lib, onto_a, onto_b, rel, (amf, smf, slice1), edge


async def test_graph_empty_library(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib = await _make_library(svc, admin)

    graph = await svc.get_library_graph(
        lib["id"], ontology_ids=None, limit=2000, user=admin)
    assert graph == {
        "nodes": [], "edges": [], "total_objects": 0, "truncated": False,
    }


async def test_graph_returns_nodes_and_edges(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    member = await _make_user(async_pool, domain=_DOMAIN)
    outsider = await _make_user(async_pool, domain=_OTHER_DOMAIN,
                                domain_admin=True)
    lib, onto_a, _onto_b, rel, (amf, _smf, slice1), edge = \
        await _make_graph_fixture(svc, admin)

    graph = await svc.get_library_graph(
        lib["id"], ontology_ids=None, limit=2000, user=member)

    # 读权限：域成员可看，域外用户拒（跨域资源不泄露存在性）
    with pytest.raises(Forbidden):
        await svc.get_library_graph(
            lib["id"], ontology_ids=None, limit=2000, user=outsider)

    assert graph["total_objects"] == 3
    assert graph["truncated"] is False
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert set(nodes) == {amf["id"], _smf["id"], slice1["id"]}
    # 节点不带 attributes_json（图载荷精简），带本体名
    assert nodes[amf["id"]] == {
        "id": amf["id"], "name": "AMF",
        "ontology_id": onto_a["id"], "ontology_name": onto_a["name"],
    }
    assert "attributes_json" not in nodes[amf["id"]]
    # 边字段与方向：source=AMF → target=切片1，带类型名
    assert len(graph["edges"]) == 1
    e = graph["edges"][0]
    assert e == {
        "id": edge["id"], "source": amf["id"], "target": slice1["id"],
        "ontology_relation_id": rel["id"], "relation_name": "承载",
    }


async def test_graph_ontology_filter(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib, onto_a, onto_b, _rel, (_amf, _smf, slice1), _edge = \
        await _make_graph_fixture(svc, admin)

    # 只留业务本体：点集收敛到 切片1；AMF-承载->切片1 边因 source 不在集合内被收敛掉
    graph = await svc.get_library_graph(
        lib["id"], ontology_ids=[onto_b["id"]], limit=2000, user=admin)
    assert [n["id"] for n in graph["nodes"]] == [slice1["id"]]
    assert graph["edges"] == []
    assert graph["total_objects"] == 3  # 过滤前口径不变
    assert graph["truncated"] is False

    # 留两个本体：边保留（两端均在集合）
    graph = await svc.get_library_graph(
        lib["id"], ontology_ids=[onto_a["id"], onto_b["id"]], limit=2000,
        user=admin)
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 1

    # 传不存在的本体 id → 空图（不是报错）
    graph = await svc.get_library_graph(
        lib["id"], ontology_ids=["no-such-ontology"], limit=2000, user=admin)
    assert graph["nodes"] == [] and graph["edges"] == []


async def test_graph_limit_truncation(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib, _onto_a, onto_b, _rel, _objs, _edge = \
        await _make_graph_fixture(svc, admin)
    onto_b_id = onto_b["id"]

    # limit=2：节点截断（created_at,id 序），truncated=True；边按幸存点集收敛
    graph = await svc.get_library_graph(
        lib["id"], ontology_ids=None, limit=2, user=admin)
    assert len(graph["nodes"]) == 2
    assert graph["truncated"] is True
    assert graph["total_objects"] == 3

    # limit 覆盖全部 → truncated=False
    graph = await svc.get_library_graph(
        lib["id"], ontology_ids=None, limit=10, user=admin)
    assert len(graph["nodes"]) == 3
    assert graph["truncated"] is False

    # 过滤收敛到 1 点且 limit=1 → 过滤≠截断，truncated=False
    graph = await svc.get_library_graph(
        lib["id"], ontology_ids=[onto_b_id], limit=1, user=admin)
    assert len(graph["nodes"]) == 1
    assert graph["truncated"] is False


async def test_graph_library_isolation(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domain=_DOMAIN, domain_admin=True)
    lib1, *_fix1, _objs1, _e1 = await _make_graph_fixture(svc, admin)
    lib2, *_fix2, _objs2, _e2 = await _make_graph_fixture(svc, admin)

    g1 = await svc.get_library_graph(
        lib1["id"], ontology_ids=None, limit=2000, user=admin)
    g2 = await svc.get_library_graph(
        lib2["id"], ontology_ids=None, limit=2000, user=admin)
    ids1 = {n["id"] for n in g1["nodes"]}
    ids2 = {n["id"] for n in g2["nodes"]}
    assert len(ids1) == 3 and len(ids2) == 3
    assert ids1.isdisjoint(ids2)
    assert {e["id"] for e in g1["edges"]}.isdisjoint(
        {e["id"] for e in g2["edges"]})
