"""70号 · 对象↔制品 refs 软引用真库测试。

覆盖（70号 §三/§五/§七）：三种形态 CRUD（整体/wiki 页/表格行）与列表解析
（wiki→页 title、table→row_key、整体→永不 degraded）；建引用校验（制品
404 反探测/跨域 422/目标不在 current revision 422 附样例/重复 409/note
500 上限）；权限矩阵（非域管理员写 403；refs 列表按当前用户制品可见性
整行过滤；制品侧反查对撤域残留成员行 404）；降级矩阵（页在新版移除 →
target_missing、deprecated 徽标、制品删除同事务清 refs、对象删除
CASCADE）；MCP neighbors refs 摘要按钥匙 open_artifacts 子集过滤。
本机无 PG 时整个模块在收集期跳过（与 KB_RUN_POSTGRES_ACCEPTANCE 同款门槛）。
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
from knowledge_mining.mining.kb.db_artifacts import ArtifactDB  # noqa: E402
from knowledge_mining.mining.kb.db_ontology import OntologyDB  # noqa: E402
from knowledge_mining.mining.kb.services.ontology_service import (  # noqa: E402
    CrossDomainRef, Duplicate, Forbidden, InvalidName, InvalidRefTarget,
    NotFound, OntologyService,
)
from knowledge_mining.tests.conftest import kb_headers  # noqa: E402

_DOMAIN = "cloud_core_network"
_OTHER_DOMAIN = "civil_engineering"


def _svc(pool) -> OntologyService:
    return OntologyService(OntologyDB(pool))


async def _make_user(pool, *, domains: dict[str, str] | None = None) -> dict:
    """domains: {domain: role}；缺省无域绑定。"""
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"ontrefs-{uuid.uuid4().hex[:8]}")
    for domain, role in (domains or {}).items():
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO user_domains (user_id, domain, domain_role)
                   VALUES (%s, %s, %s)""",
                (user["id"], domain, role),
            )
    return user


async def _make_fixture(svc, admin: dict) -> tuple[str, str, str]:
    """库 + 网元本体 + AMF 对象，返回 (library_id, ontology_id, object_id)。"""
    lib = await svc.create_library(
        domain=_DOMAIN, name=f"引用测试库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )
    onto = await svc.create_ontology(
        lib["id"], name="网元", description=None, attributes=None, user=admin,
    )
    batch = await svc.instantiate_batch(
        lib["id"], ontology_id=onto["id"], source="manual",
        objects=[{"name": "AMF"}], relations=[], user=admin,
    )
    return lib["id"], onto["id"], batch["object_ids"][0]


async def _mk_table_artifact(
    pool, admin: dict, *, domain: str = _DOMAIN,
    visibility: str = "private", status: str = "draft",
) -> str:
    art = f"art-{uuid.uuid4().hex[:12]}"
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO kb_artifacts
                   (artifact_id, domain_id, kind, title, owner_user_id,
                    visibility, status, current_revision)
               VALUES (%s, %s, 'table', %s, %s, %s, %s, 0)""",
            (art, domain, f"命令表-{art[:8]}", admin["id"], visibility, status),
        )
        await conn.execute(
            """INSERT INTO kb_table_rows (artifact_id, revision, row_key, data)
               VALUES (%s, 0, %s, %s)""",
            (art, "cmd-amf-001", '{"命令": "ADD POOL"}'),
        )
    return art


async def _mk_wiki_artifact(
    pool, admin: dict, *, pages: list[tuple[str, str]],
    visibility: str = "private", status: str = "draft",
    revision: int = 0,
) -> str:
    """pages = [(page_key, title)]，落在 revision（current_revision 同值）。"""
    art = f"wiki-{uuid.uuid4().hex[:12]}"
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO kb_artifacts
                   (artifact_id, domain_id, kind, title, owner_user_id,
                    visibility, status, current_revision)
               VALUES (%s, %s, 'wiki', %s, %s, %s, %s, %s)""",
            (art, _DOMAIN, f"开局Wiki-{art[:8]}", admin["id"],
             visibility, status, revision),
        )
        for page_key, title in pages:
            await conn.execute(
                """INSERT INTO kb_wiki_pages
                       (artifact_id, revision, page_key, title)
                   VALUES (%s, %s, %s, %s)""",
                (art, revision, page_key, title),
            )
    return art


async def _count_refs(pool, *, artifact_id: str | None = None,
                      object_id: str | None = None) -> int:
    async with pool.connection() as conn:
        if artifact_id is not None:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM semantic_object_artifact_refs"
                " WHERE artifact_id = %s", (artifact_id,))
        else:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM semantic_object_artifact_refs"
                " WHERE object_id = %s", (object_id,))
        return int((await cur.fetchone())["n"])


# ------------------------------------------------------------ CRUD 三形态

async def test_ref_crud_three_forms(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    table_art = await _mk_table_artifact(async_pool, admin)
    wiki_art = await _mk_wiki_artifact(
        async_pool, admin, pages=[("amf", "AMF 开局指导")],
    )

    whole = await svc.create_object_ref(
        amf, artifact_id=table_art, target_key=None, note="整表相关",
        user=admin)
    page = await svc.create_object_ref(
        amf, artifact_id=wiki_art, target_key="amf", note=None, user=admin)
    row = await svc.create_object_ref(
        amf, artifact_id=table_art, target_key="cmd-amf-001", note=None,
        user=admin)
    assert whole["target_key"] == "" and whole["ref_source"] == "manual"
    assert whole["target_title"] is None and whole["degraded"] is None
    assert page["target_title"] == "AMF 开局指导"
    assert row["target_title"] == "cmd-amf-001"   # 行业务主键即展示名

    listing = await svc.list_object_refs(amf, limit=100, user=admin)
    assert len(listing["items"]) == 3 and listing["truncated"] is False
    by_art = {(i["artifact_id"], i["target_key"]): i for i in listing["items"]}
    assert by_art[(table_art, "")]["artifact_title"].startswith("命令表-")
    assert by_art[(table_art, "")]["kind"] == "table"
    assert by_art[(wiki_art, "amf")]["artifact_status"] == "draft"

    # 整体级与页/行级可并存于同一 (object, artifact)（评审⑦）——不算重复；
    # 同键重复才 409
    with pytest.raises(Duplicate):
        await svc.create_object_ref(
            amf, artifact_id=table_art, target_key="cmd-amf-001",
            note=None, user=admin)

    await svc.delete_object_ref(amf, row["id"], user=admin)
    listing = await svc.list_object_refs(amf, limit=100, user=admin)
    assert len(listing["items"]) == 2

    # note 500 字上限（评审⑦）
    with pytest.raises(InvalidName):
        await svc.create_object_ref(
            amf, artifact_id=wiki_art, target_key="amf",
            note="长" * 501, user=admin)


# ------------------------------------------------------------ 建引用校验

async def test_ref_create_validation(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    other_art = await _mk_table_artifact(
        async_pool, admin, domain=_OTHER_DOMAIN)
    table_art = await _mk_table_artifact(async_pool, admin)

    # 制品不存在 → 404（不泄露存在性）
    with pytest.raises(NotFound):
        await svc.create_object_ref(
            amf, artifact_id="art-nope", target_key=None,
            note=None, user=admin)

    # 跨域 → 422 cross_domain_forbidden（§3.2 一期限同域）
    with pytest.raises(CrossDomainRef):
        await svc.create_object_ref(
            amf, artifact_id=other_art, target_key=None, note=None,
            user=admin)

    # 目标不在 current revision → 422 附 valid_keys 样例（前 20）
    with pytest.raises(InvalidRefTarget) as exc_info:
        await svc.create_object_ref(
            amf, artifact_id=table_art, target_key="no-such-row",
            note=None, user=admin)
    assert exc_info.value.valid_keys == ["cmd-amf-001"]
    assert await _count_refs(async_pool, object_id=amf) == 0   # 零写入


# ------------------------------------------------------------ 写权限

async def test_ref_write_requires_domain_admin(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    member = await _make_user(async_pool, domains={_DOMAIN: "member"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    table_art = await _mk_table_artifact(async_pool, admin)
    ref = await svc.create_object_ref(
        amf, artifact_id=table_art, target_key=None, note=None, user=admin)

    with pytest.raises(Forbidden):
        await svc.create_object_ref(
            amf, artifact_id=table_art, target_key="cmd-amf-001",
            note=None, user=member)
    with pytest.raises(Forbidden):
        await svc.delete_object_ref(amf, ref["id"], user=member)
    assert await _count_refs(async_pool, object_id=amf) == 1


# ------------------------------------------------------------ 列表可见性过滤

async def test_ref_list_visibility_filter(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    member = await _make_user(async_pool, domains={_DOMAIN: "member"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    private_art = await _mk_table_artifact(async_pool, admin)          # 他人私有
    public_art = await _mk_wiki_artifact(
        async_pool, admin, pages=[("amf", "AMF 公开页")],
        visibility="public",
    )
    await svc.create_object_ref(
        amf, artifact_id=private_art, target_key=None, note=None,
        user=admin)
    await svc.create_object_ref(
        amf, artifact_id=public_art, target_key="amf", note=None,
        user=admin)

    # 域成员：私有制品引用整行不出现（不显示"有 N 条你看不见"）
    mine = await svc.list_object_refs(amf, limit=100, user=member)
    assert [i["artifact_id"] for i in mine["items"]] == [public_art]
    assert mine["items"][0]["target_title"] == "AMF 公开页"

    # 域管理员：全通（对同域制品全权，§3.2 坍缩）
    all_refs = await svc.list_object_refs(amf, limit=100, user=admin)
    assert len(all_refs["items"]) == 2

    # 制品成员行补上 → 私有引用对该成员出现
    async with async_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO kb_artifact_members (artifact_id, user_id, role)"
            " VALUES (%s, %s, 'viewer')",
            (private_art, member["id"]),
        )
    mine = await svc.list_object_refs(amf, limit=100, user=member)
    assert {i["artifact_id"] for i in mine["items"]} == {private_art, public_art}

    # limit 截断 + truncated（评审⑤）
    tight = await svc.list_object_refs(amf, limit=1, user=member)
    assert len(tight["items"]) == 1 and tight["truncated"] is True


# ------------------------------------------------------------ 降级矩阵

async def test_degrade_target_missing_on_page_removed(async_pool) -> None:
    """wiki 页在新 revision 被移除 → degraded=target_missing，保留可删。"""
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    wiki_art = await _mk_wiki_artifact(
        async_pool, admin, pages=[("amf", "AMF 开局指导")])
    ref = await svc.create_object_ref(
        amf, artifact_id=wiki_art, target_key="amf", note=None, user=admin)
    assert ref["degraded"] is None

    # 发布新版：rev1 不含 amf 页（跟随 current → 读侧降级）
    async with async_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO kb_wiki_pages (artifact_id, revision, page_key, title)"
            " VALUES (%s, 1, 'smf', 'SMF 开局指导')", (wiki_art,))
        await conn.execute(
            "UPDATE kb_artifacts SET current_revision = 1"
            " WHERE artifact_id = %s", (wiki_art,))

    listing = await svc.list_object_refs(amf, limit=100, user=admin)
    item = next(i for i in listing["items"] if i["id"] == ref["id"])
    assert item["degraded"] == "target_missing"
    assert item["target_title"] is None
    assert item["artifact_title"].startswith("开局Wiki-")   # 制品信息仍在

    # 降级条目保留可删
    await svc.delete_object_ref(amf, ref["id"], user=admin)
    assert await _count_refs(async_pool, object_id=amf) == 0


async def test_deprecated_artifact_badge_only(async_pool) -> None:
    """制品 deprecated → 正常返回 + status 徽标（不算降级）。"""
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    art = await _mk_table_artifact(async_pool, admin, status="deprecated")
    await svc.create_object_ref(
        amf, artifact_id=art, target_key=None, note=None, user=admin)
    listing = await svc.list_object_refs(amf, limit=100, user=admin)
    assert listing["items"][0]["artifact_status"] == "deprecated"
    assert listing["items"][0]["degraded"] is None


async def test_artifact_delete_cleans_refs(async_pool) -> None:
    """制品真删（显式级联事务）→ 引用行同事务清理（§五）。"""
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    art = await _mk_table_artifact(async_pool, admin)
    await svc.create_object_ref(
        amf, artifact_id=art, target_key=None, note=None, user=admin)
    await svc.create_object_ref(
        amf, artifact_id=art, target_key="cmd-amf-001", note=None, user=admin)
    assert await _count_refs(async_pool, artifact_id=art) == 2

    await ArtifactDB(async_pool).delete_artifact(art)
    assert await _count_refs(async_pool, artifact_id=art) == 0


async def test_object_delete_cascades_refs(async_pool) -> None:
    """对象删除 → refs 族内 CASCADE 随删（无悬挂）。"""
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    art = await _mk_table_artifact(async_pool, admin)
    await svc.create_object_ref(
        amf, artifact_id=art, target_key=None, note=None, user=admin)
    await svc.delete_object(amf, user=admin)     # 无语义关系，删得动
    assert await _count_refs(async_pool, object_id=amf) == 0


# ------------------------------------------------------------ 制品侧反查

async def test_artifact_refs_reverse_query(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    member = await _make_user(async_pool, domains={_DOMAIN: "member"})
    unbound = await _make_user(async_pool)                # 有成员行、无域绑定
    _lib, _onto, amf = await _make_fixture(svc, admin)
    table_art = await _mk_table_artifact(async_pool, admin)

    ref_whole = await svc.create_object_ref(
        amf, artifact_id=table_art, target_key=None, note="整表", user=admin)
    ref_row = await svc.create_object_ref(
        amf, artifact_id=table_art, target_key="cmd-amf-001", note=None,
        user=admin)

    # 省略 target_key = 全部引用（含整体级，评审①）
    all_refs = await svc.list_artifact_refs(
        table_art, target_key=None, limit=100, user=admin)
    assert {i["object_name"] for i in all_refs["items"]} == {"AMF"}
    assert all_refs["items"][0]["ontology_name"] == "网元"
    assert all_refs["items"][0]["library_id"] == _lib
    assert all_refs["truncated"] is False

    # 行级过滤
    rows_only = await svc.list_artifact_refs(
        table_art, target_key="cmd-amf-001", limit=100, user=admin)
    assert [i["id"] for i in rows_only["items"]] == [ref_row["id"]]
    assert rows_only["items"][0]["degraded"] is None

    # 普通域成员 + 制品成员 → 双重口径通过，对象信息可见（同域坍缩）
    async with async_pool.connection() as conn:
        await conn.execute(
            "INSERT INTO kb_artifact_members (artifact_id, user_id, role)"
            " VALUES (%s, %s, 'viewer')", (table_art, member["id"]))
        await conn.execute(
            "INSERT INTO kb_artifact_members (artifact_id, user_id, role)"
            " VALUES (%s, %s, 'viewer')", (table_art, unbound["id"]))
    as_member = await svc.list_artifact_refs(
        table_art, target_key=None, limit=100, user=member)
    assert as_member["items"][0]["object_name"] == "AMF"

    # 撤域残留成员行 → 404 反探测（对齐 artifact_service._assert_role）
    with pytest.raises(NotFound):
        await svc.list_artifact_refs(
            table_art, target_key=None, limit=100, user=unbound)

    # 私有制品 + 非成员域成员 → 404
    stranger = await _make_user(async_pool, domains={_DOMAIN: "member"})
    with pytest.raises(NotFound):
        await svc.list_artifact_refs(
            table_art, target_key=None, limit=100, user=stranger)

    # 防御分支：直插跨域对象的引用行（绕过 service 同域约束），
    # 制品读者非对象域成员 → object_invisible 占位（§3.3 第 2 行）
    other_admin = await _make_user(async_pool, domains={_OTHER_DOMAIN: "admin"})
    other_lib = await svc.create_library(
        domain=_OTHER_DOMAIN, name=f"他域库-{uuid.uuid4().hex[:6]}",
        description=None, user=other_admin)
    other_onto = await svc.create_ontology(
        other_lib["id"], name="他域网元", description=None, attributes=None,
        user=other_admin)
    other_obj = await svc.instantiate_batch(
        other_lib["id"], ontology_id=other_onto["id"], source="manual",
        objects=[{"name": "他域AMF"}], relations=[], user=other_admin)
    defensive_id = f"sor-{uuid.uuid4().hex[:12]}"
    async with async_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO semantic_object_artifact_refs
                   (id, object_id, artifact_id, target_key, note,
                    ref_source, created_by, created_at)
               VALUES (%s, %s, %s, '', '', 'manual', %s, %s)""",
            (defensive_id, other_obj["object_ids"][0], table_art,
             admin["id"], "2026-11-19T00:00:00+00:00"),
        )
    mixed = await svc.list_artifact_refs(
        table_art, target_key=None, limit=100, user=member)
    assert len(mixed["items"]) == 3          # 2 条真实 + 1 条防御行
    degraded_row = next(i for i in mixed["items"] if i["id"] == defensive_id)
    assert degraded_row["object_name"] is None
    assert degraded_row["ontology_name"] is None
    assert degraded_row["degraded"] == "object_invisible"
    visible = [i for i in mixed["items"] if i["id"] != defensive_id]
    assert {i["object_name"] for i in visible} == {"AMF"}
    assert all(i["degraded"] is None for i in visible)


# ------------------------------------------------------------ neighbors 摘要

async def test_summarize_refs_user_visibility(async_pool) -> None:
    """UI neighbors 摘要：按当前用户制品可见性（与 refs 列表同一套）。"""
    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    member = await _make_user(async_pool, domains={_DOMAIN: "member"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    private_art = await _mk_table_artifact(async_pool, admin)
    public_art = await _mk_wiki_artifact(
        async_pool, admin, pages=[("amf", "AMF 公开页")], visibility="public")
    await svc.create_object_ref(
        amf, artifact_id=private_art, target_key=None, note=None, user=admin)
    await svc.create_object_ref(
        amf, artifact_id=public_art, target_key="amf", note=None, user=admin)

    # neighbors 本体不掺 refs（路由层合并），摘要独立成方法
    neighbors = await svc.get_object_neighbors(amf, user=admin)
    assert "artifact_refs" not in neighbors

    as_member = await svc.summarize_object_refs(amf, user=member)
    assert [r["artifact_id"] for r in as_member["artifact_refs"]] == [public_art]
    assert as_member["artifact_refs_truncated"] is False
    entry = as_member["artifact_refs"][0]
    assert entry["target_title"] == "AMF 公开页"
    assert entry["artifact_status"] == "draft" and entry["degraded"] is None

    as_admin = await svc.summarize_object_refs(amf, user=admin)
    assert len(as_admin["artifact_refs"]) == 2


# ------------------------------------------------------------ MCP 开放子集

async def test_mcp_neighbors_refs_open_subset(async_pool) -> None:
    """MCP 摘要按钥匙 open_artifacts 过滤：未开放的制品引用不出现（§3.3
    第 3 行）；degraded 条目 target_title=null（评审⑦）。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    from knowledge_mining.mining.kb.routes.mcp_keys import (
        router as mcp_keys_router,
    )
    from knowledge_mining.mining.kb.routes.mcp_ontology import (
        router as mcp_ontology_router,
    )

    svc = _svc(async_pool)
    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    _lib, _onto, amf = await _make_fixture(svc, admin)
    open_art = await _mk_wiki_artifact(
        async_pool, admin, pages=[("amf", "AMF 开局指导")])
    closed_art = await _mk_table_artifact(async_pool, admin)
    await svc.create_object_ref(
        amf, artifact_id=open_art, target_key="amf", note=None, user=admin)
    await svc.create_object_ref(
        amf, artifact_id=closed_art, target_key=None, note=None, user=admin)

    app = FastAPI()
    app.state.pg_pool = async_pool
    app.state.db_config = MiningDbConfig()
    app.include_router(mcp_keys_router)
    app.include_router(mcp_ontology_router)
    internal = {"X-Internal-Auth": os.environ.get("KB_TEST_INTERNAL_AUTH", "test-ivs")}

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        r = await c.post(
            "/api/kb/users/me/mcp-keys",
            json={"name": f"引用钥匙-{uuid.uuid4().hex[:6]}", "domain": _DOMAIN},
            headers=kb_headers(admin["username"]),
        )
        assert r.status_code == 201, r.text
        key = r.json()
        scope = {"username": admin["username"], "key_id": key["id"]}

        # 未勾选任何制品 → refs 摘要为空（public ≠ 自动暴露，008 口径）
        r = await c.post("/api/kb/mcp-tools/ontology/object-neighbors",
                         json={**scope, "object_id": amf}, headers=internal)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["artifact_refs"] == []
        assert body["artifact_refs_truncated"] is False

        # 显式开放 open_art → 只有它出现，closed_art 不出现
        async with async_pool.connection() as conn:
            await conn.execute(
                "INSERT INTO mcp_key_open_artifacts (key_id, artifact_id)"
                " VALUES (%s, %s)", (key["id"], open_art))
        r = await c.post("/api/kb/mcp-tools/ontology/object-neighbors",
                         json={**scope, "object_id": amf}, headers=internal)
        assert r.status_code == 200, r.text
        body = r.json()
        assert [x["artifact_id"] for x in body["artifact_refs"]] == [open_art]
        assert body["artifact_refs"][0]["target_title"] == "AMF 开局指导"

        # 目标页在新版移除 → degraded 条目 target_title=null（评审⑦）
        async with async_pool.connection() as conn:
            await conn.execute(
                "UPDATE kb_artifacts SET current_revision = 1"
                " WHERE artifact_id = %s", (open_art,))
        r = await c.post("/api/kb/mcp-tools/ontology/object-neighbors",
                         json={**scope, "object_id": amf}, headers=internal)
        assert r.status_code == 200, r.text
        entry = r.json()["artifact_refs"][0]
        assert entry["degraded"] == "target_missing"
        assert entry["target_title"] is None

        # 钥匙吊销 → 401（钥匙校验先于一切）
        r = await c.post(f"/api/kb/users/me/mcp-keys/{key['id']}/revoke",
                         headers=kb_headers(admin["username"]))
        assert r.status_code == 204, r.text
        r = await c.post("/api/kb/mcp-tools/ontology/object-neighbors",
                         json=scope, headers=internal)
        assert r.status_code == 401, r.text


# ------------------------------------------------------------ 行选择器数据源（评审⑦）

async def test_row_selector_q_filter_and_revision_caliber(async_pool) -> None:
    """评审⑦：行选择器复用 rows 读端点，但必须带过滤 + 显式 revision——
    q 按 row_key/列值包含匹配（万行级不翻页选取）；revision=current_revision
    与建引用校验同口径（含从未发布制品的 rev0；运行态 draft=False 读不到
    rev0 行，选择器须走显式 revision=0）。"""
    from knowledge_mining.mining.kb.services.artifact_service import ArtifactService

    admin = await _make_user(async_pool, domains={_DOMAIN: "admin"})
    art = await _mk_table_artifact(async_pool, admin)  # current_revision=0
    async with async_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO kb_table_rows (artifact_id, revision, row_key, data)
               VALUES (%s, 0, %s, %s)""",
            (art, "cmd-smf-002", '{"命令": "DEL POOL"}'),
        )

    svc = ArtifactService(ArtifactDB(async_pool))

    # 运行态缺省读：从未发布 → 空集（viewer 不可见 rev0）
    runtime = await svc.list_rows(user=admin, artifact_id=art)
    assert runtime["rows"] == [] and runtime["total"] == 0

    # 显式 revision=0（域管理员=设计态角色）→ 选择器口径取到 rev0 行
    rev0 = await svc.list_rows(user=admin, artifact_id=art, revision=0)
    assert {r["row_key"] for r in rev0["rows"]} == {"cmd-amf-001", "cmd-smf-002"}
    assert rev0["total"] == 2

    # q=row_key 包含
    by_key = await svc.list_rows(user=admin, artifact_id=art, revision=0, q="smf")
    assert [r["row_key"] for r in by_key["rows"]] == ["cmd-smf-002"]
    assert by_key["total"] == 1

    # q=列值包含（data::text 匹配）
    by_value = await svc.list_rows(
        user=admin, artifact_id=art, revision=0, q="ADD POOL")
    assert [r["row_key"] for r in by_value["rows"]] == ["cmd-amf-001"]

    # %/_ 按字面匹配：查询里的 _ 不当「任意单字符」通配（否则 x_001 会
    # 误中 xA001 这样的邻居行）
    async with async_pool.connection() as conn:
        for row_key, command in (("cmd-x_001", "LITERAL"), ("cmd-xA001", "NEIGHBOR")):
            await conn.execute(
                """INSERT INTO kb_table_rows (artifact_id, revision, row_key, data)
                   VALUES (%s, 0, %s, %s)""",
                (art, row_key, f'{{"命令": "{command}"}}'),
            )
    literal = await svc.list_rows(user=admin, artifact_id=art, revision=0, q="cmd-x_001")
    assert [r["row_key"] for r in literal["rows"]] == ["cmd-x_001"]
