"""本体 AI 抽取 + 批量实例化 service 层测试 —— 真库门控（@pytest.mark.postgres，67号 P3）。

覆盖（§6.8/§8.1-§8.6）：抽取发起/轮询（token 登记、绑定失配、过期、
每发起人上限）；源文本装配（text 超限、invalid_source、KB 文档域校验/
未挖掘/分段拼接/截断）；prompt 组装（版本冻结常量、§8.4 渲染顺序）；
候选组装（conflict 标记、关系引用解析丢弃 + warning、端点预检、模型
透传）；instantiate（新↔新/新↔已有任意组合、source='extract'、四类
对象校验 + 六类关系校验聚合报告、整批零写入、存量重边预检）。

LLM 经注入的假 GenerationClient（记录 messages/kwargs），KB 文档取文
本经假 kb_db；本机无 PG 时整个模块在收集期跳过。
"""
from __future__ import annotations

import os
import time
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
from knowledge_mining.mining.kb.services.ontology_extract_prompts import (  # noqa: E402
    EXTRACT_SYSTEM_PROMPT, EXTRACT_SYSTEM_PROMPT_WITH_RELATIONS,
)
from knowledge_mining.mining.kb.services.ontology_extract_service import (  # noqa: E402
    EXTRACT_TTL_SECONDS, OntologyExtractError, OntologyExtractService,
    _EXTRACT_TOKENS,
)
from knowledge_mining.mining.kb.services.ontology_service import (  # noqa: E402
    BatchInstantiateFailed, Forbidden, InvalidName, NotFound, OntologyService,
)

_DOMAIN = "cloud_core_network"


def _svc(pool) -> OntologyService:
    return OntologyService(OntologyDB(pool))


class _FakeGen:
    """假 GenerationClient：记录调用、返回预置 payload。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def execute(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return self.payload


class _FakeKbDb:
    """假 KbDB：仅实现抽取用到的三个只读方法。"""

    def __init__(self, kb=None, document=None, knowledge=None) -> None:
        self.kb = kb
        self.document = document
        self.knowledge = knowledge

    async def get_kb(self, kb_id: str):
        return self.kb if self.kb and kb_id == self.kb["id"] else None

    async def get_document_identity(self, document_id: str):
        return self.document if self.document and document_id == self.document["id"] else None

    async def get_document_knowledge(self, kb_id: str, document_id: str):
        return self.knowledge


def _ext(pool, gen: _FakeGen, kb_db=None) -> OntologyExtractService:
    return OntologyExtractService(
        OntologyDB(pool), kb_db or KbDB(pool), gen,
    )


async def _make_admin(pool) -> dict:
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"ontext-{uuid.uuid4().hex[:8]}")
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO user_domains (user_id, domain, domain_role)
               VALUES (%s, %s, 'admin')""",
            (user["id"], _DOMAIN),
        )
    return user


async def _make_member(pool) -> dict:
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"ontext-{uuid.uuid4().hex[:8]}")
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO user_domains (user_id, domain, domain_role)
               VALUES (%s, %s, 'member')""",
            (user["id"], _DOMAIN),
        )
    return user


async def _make_library(svc, admin: dict) -> dict:
    return await svc.create_library(
        domain=_DOMAIN, name=f"抽取测试库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )


async def _await_all_tasks() -> None:
    for record in list(_EXTRACT_TOKENS.values()):
        task = record.get("task")
        if task is not None:
            await task


@pytest.fixture(autouse=True)
def _clean_token_registry():
    _EXTRACT_TOKENS.clear()
    yield
    _EXTRACT_TOKENS.clear()


# ------------------------------------------------------------- 抽取主链路

async def test_extract_text_flow_with_conflict_marking(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library = await _make_library(svc, admin)
    onto = await svc.create_ontology(
        library["id"], name="药品", description="药品本体",
        attributes=[{"name": "适应症"}, {"name": "剂量"}], user=admin,
    )
    # 库内已有同名对象 → 候选 conflict 标记
    await svc.create_object(
        onto["id"], name="阿司匹林", attributes=None, user=admin,
    )
    gen = _FakeGen({
        "objects": [
            {"name": "阿司匹林", "attributes": {"适应症": "发热"}},
            {"name": "布洛芬", "attributes": {}},
        ],
    })
    ext = _ext(async_pool, gen)
    text = "阿司匹林用于发热。布洛芬用于止痛。"

    result = await ext.start_extract(
        svc, library_id=library["id"], ontology_id=onto["id"],
        source={"kind": "text", "text": text},
        suggest_relations=False, model="test-model", user=admin,
    )
    token = result["extract_token"]
    await _EXTRACT_TOKENS[token]["task"]

    body = await ext.get_extract(
        library_id=library["id"], extract_token=token, user=admin,
    )
    assert body["status"] == "done"
    candidates = body["candidates"]
    assert candidates["input_chars"] == len(text)
    assert candidates["truncated"] is False
    assert candidates["warnings"] == []
    by_name = {o["name"]: o for o in candidates["objects"]}
    assert by_name["阿司匹林"]["conflict"] is True
    assert by_name["阿司匹林"]["conflict_with"] == "阿司匹林"
    assert by_name["布洛芬"]["conflict"] is False

    # prompt 断言：版本冻结常量 + §8.4 渲染顺序 + 模型/输出类型透传
    assert len(gen.calls) == 1
    call = gen.calls[0]
    assert call["messages"][0]["content"] == EXTRACT_SYSTEM_PROMPT
    user_prompt = call["messages"][1]["content"]
    assert user_prompt.index("【本体定义】") < user_prompt.index("【源文本】")
    assert "本体名称：药品" in user_prompt
    assert "- 适应症" in user_prompt
    assert text in user_prompt
    assert call["kwargs"]["expected_output_type"] == "json_object"
    assert call["kwargs"]["knowledge_domain"] == _DOMAIN
    assert call["kwargs"]["model"] == "test-model"


async def test_extract_relations_resolution_and_endpoint_precheck(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library = await _make_library(svc, admin)
    drug = await svc.create_ontology(
        library["id"], name="药品", description=None,
        attributes=[{"name": "适应症"}], user=admin,
    )
    disease = await svc.create_ontology(
        library["id"], name="疾病", description=None, attributes=None, user=admin,
    )
    treats = await svc.create_ontology_relation(
        library["id"], name="治疗",
        source_ontology_id=drug["id"], target_ontology_id=disease["id"],
        description=None, user=admin,
    )
    existing = await svc.create_object(
        disease["id"], name="感冒", attributes=None, user=admin,
    )
    gen = _FakeGen({
        "objects": [{"name": "阿司匹林", "attributes": {}}],
        "relations": [
            {"source": "阿司匹林", "relation": "治疗", "target": "感冒",
             "target_existing": True},        # 新→已有，端点匹配
            {"source": "阿司匹林", "relation": "治疗", "target": "发烧",
             "target_existing": True},        # 目标解析失败 → 丢弃
            {"source": "阿司匹林", "relation": "无此关系", "target": "感冒",
             "target_existing": True},        # 关系类型不在库 → endpoint_ok=False
            {"source": "布洛芬", "relation": "治疗", "target": "感冒",
             "target_existing": True},        # 源不在候选 → 丢弃
            {"source": "阿司匹林", "relation": "治疗", "target": "阿司匹林",
             "target_existing": False},       # 新→新（端点不匹配：药品→药品）
        ],
    })
    ext = _ext(async_pool, gen)
    result = await ext.start_extract(
        svc, library_id=library["id"], ontology_id=drug["id"],
        source={"kind": "text", "text": "阿司匹林治疗感冒。"},
        suggest_relations=True, model=None, user=admin,
    )
    await _EXTRACT_TOKENS[result["extract_token"]]["task"]

    body = await ext.get_extract(
        library_id=library["id"], extract_token=result["extract_token"],
        user=admin,
    )
    assert body["status"] == "done"
    candidates = body["candidates"]
    relations = candidates["relations"]
    assert len(relations) == 3

    treats_row = next(r for r in relations if r["relation"] == "治疗"
                      and r["target"] == "感冒")
    assert treats_row["target_existing"] is True
    assert treats_row["target_object_id"] == existing["id"]
    assert treats_row["endpoint_ok"] is True

    unknown_rel = next(r for r in relations if r["relation"] == "无此关系")
    assert unknown_rel["endpoint_ok"] is False

    new_new = next(r for r in relations if r["target"] == "阿司匹林")
    assert new_new["target_existing"] is False
    assert new_new["endpoint_ok"] is False  # 治疗要求 药品→疾病

    warnings_text = "\n".join(candidates["warnings"])
    assert "发烧" in warnings_text          # 目标无法解析
    assert "布洛芬" in warnings_text        # 源不在候选
    assert "无此关系" in warnings_text       # 关系类型未定义

    # suggest_relations=True：系统 prompt 带关系规则 + 已有对象清单入上下文
    call = gen.calls[0]
    assert call["messages"][0]["content"] == EXTRACT_SYSTEM_PROMPT_WITH_RELATIONS
    assert "【关系类型】" in call["messages"][1]["content"]
    assert "疾病/感冒" in call["messages"][1]["content"]


# ------------------------------------------------------------- 护栏与轮询

async def test_extract_guards_and_poll_binding(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library = await _make_library(svc, admin)
    onto = await svc.create_ontology(
        library["id"], name="药品", description=None, attributes=None, user=admin,
    )
    gen = _FakeGen({"objects": []})
    ext = _ext(async_pool, gen)

    # 普通成员无写权限 → Forbidden（get_library_for_extract 门禁）
    member = await _make_member(async_pool)
    with pytest.raises(Forbidden):
        await ext.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "text", "text": "x"},
            suggest_relations=False, model=None, user=member,
        )

    # 粘贴文本超限 → text_too_long；kind 非法 → invalid_source
    with pytest.raises(OntologyExtractError) as excinfo:
        await ext.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "text", "text": "长" * 20001},
            suggest_relations=False, model=None, user=admin,
        )
    assert excinfo.value.code == "text_too_long"
    with pytest.raises(OntologyExtractError) as excinfo:
        await ext.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "url", "text": "x"},
            suggest_relations=False, model=None, user=admin,
        )
    assert excinfo.value.code == "invalid_source"

    # 未知 KB（真 KbDB 查不到）→ NotFound（不泄露存在性）
    with pytest.raises(NotFound):
        await ext.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "kb_document", "kb_id": uuid.uuid4().hex,
                    "document_id": uuid.uuid4().hex},
            suggest_relations=False, model=None, user=admin,
        )

    # 每发起人活跃上限：5 个在飞后第 6 个 → extract_limit（清最旧）
    tokens = []
    tasks = []
    for i in range(5):
        started = await ext.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "text", "text": f"文本{i}"},
            suggest_relations=False, model=None, user=admin,
        )
        tokens.append(started["extract_token"])
        tasks.append(_EXTRACT_TOKENS[started["extract_token"]]["task"])
    with pytest.raises(OntologyExtractError) as excinfo:
        await ext.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "text", "text": "第6个"},
            suggest_relations=False, model=None, user=admin,
        )
    assert excinfo.value.code == "extract_limit"
    # 被驱逐记录的 task 也一并等完（登记已摘除，任务本身可能仍在飞）
    for task in tasks:
        await task
    await _await_all_tasks()

    # 轮询绑定：他人 token / 他库 → NotFound
    first = tokens[1]
    with pytest.raises(NotFound):
        await ext.get_extract(
            library_id=library["id"], extract_token=first, user=member,
        )
    other_library = await _make_library(svc, admin)
    with pytest.raises(NotFound):
        await ext.get_extract(
            library_id=other_library["id"], extract_token=first, user=admin,
        )
    # 过期 → NotFound（404，§8.6）
    record = _EXTRACT_TOKENS[first]
    record["created_at"] = time.monotonic() - EXTRACT_TTL_SECONDS - 1
    with pytest.raises(NotFound):
        await ext.get_extract(
            library_id=library["id"], extract_token=first, user=admin,
        )


async def test_extract_kb_document_source_paths(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library = await _make_library(svc, admin)
    onto = await svc.create_ontology(
        library["id"], name="药品", description=None, attributes=None, user=admin,
    )
    gen = _FakeGen({"objects": []})

    kb = {"id": "kb-1", "domain": _DOMAIN, "status": "active"}
    document = {"id": "doc-1", "kb_id": "kb-1"}
    mined = {
        "mined": True,
        "segments": [
            {"normalized_text": "阿司匹林用于发热。", "raw_text": "忽略"},
            {"normalized_text": None, "raw_text": "布洛芬用于止痛。"},
            {"normalized_text": "   ", "raw_text": None},
        ],
    }
    happy = _ext(async_pool, gen, _FakeKbDb(kb=kb, document=document, knowledge=mined))
    result = await happy.start_extract(
        svc, library_id=library["id"], ontology_id=onto["id"],
        source={"kind": "kb_document", "kb_id": "kb-1", "document_id": "doc-1"},
        suggest_relations=False, model=None, user=admin,
    )
    await _EXTRACT_TOKENS[result["extract_token"]]["task"]
    body = await happy.get_extract(
        library_id=library["id"], extract_token=result["extract_token"],
        user=admin,
    )
    assert body["status"] == "done"
    # 分段按序拼接：normalized 优先、缺则 raw、全空白段跳过
    user_prompt = gen.calls[0]["messages"][1]["content"]
    assert "阿司匹林用于发热。\n布洛芬用于止痛。" in user_prompt

    # KB 跨域 → NotFound；未挖掘 → document_not_mined；文档挂他库 → NotFound
    cross = _ext(async_pool, gen, _FakeKbDb(kb={"id": "kb-2", "domain": "other_domain", "status": "active"}))
    with pytest.raises(NotFound):
        await cross.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "kb_document", "kb_id": "kb-2", "document_id": "doc-1"},
            suggest_relations=False, model=None, user=admin,
        )
    unmined = _ext(async_pool, gen, _FakeKbDb(kb=kb, document=document, knowledge={"mined": False}))
    with pytest.raises(OntologyExtractError) as excinfo:
        await unmined.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "kb_document", "kb_id": "kb-1", "document_id": "doc-1"},
            suggest_relations=False, model=None, user=admin,
        )
    assert excinfo.value.code == "document_not_mined"
    mismatch = _ext(async_pool, gen, _FakeKbDb(
        kb=kb, document={"id": "doc-1", "kb_id": "kb-other"},
        knowledge={"mined": True, "segments": []},
    ))
    with pytest.raises(NotFound):
        await mismatch.start_extract(
            svc, library_id=library["id"], ontology_id=onto["id"],
            source={"kind": "kb_document", "kb_id": "kb-1", "document_id": "doc-1"},
            suggest_relations=False, model=None, user=admin,
        )

    # KB 文档超限自动截断（§8.2：truncated=true，不报 400）
    long_kb = _ext(async_pool, gen, _FakeKbDb(
        kb=kb, document=document,
        knowledge={"mined": True, "segments": [
            {"normalized_text": "字" * 25000, "raw_text": None},
        ]},
    ))
    truncated_result = await long_kb.start_extract(
        svc, library_id=library["id"], ontology_id=onto["id"],
        source={"kind": "kb_document", "kb_id": "kb-1", "document_id": "doc-1"},
        suggest_relations=False, model=None, user=admin,
    )
    await _EXTRACT_TOKENS[truncated_result["extract_token"]]["task"]
    body = await long_kb.get_extract(
        library_id=library["id"],
        extract_token=truncated_result["extract_token"], user=admin,
    )
    assert body["candidates"]["truncated"] is True
    assert body["candidates"]["input_chars"] == 20000


async def test_extract_llm_failure_marks_task_failed(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library = await _make_library(svc, admin)
    onto = await svc.create_ontology(
        library["id"], name="药品", description=None, attributes=None, user=admin,
    )

    class _BoomGen:
        def execute(self, messages, **kwargs):
            raise RuntimeError("llm_service down")

    ext = OntologyExtractService(
        OntologyDB(async_pool), KbDB(async_pool), _BoomGen(),
    )
    result = await ext.start_extract(
        svc, library_id=library["id"], ontology_id=onto["id"],
        source={"kind": "text", "text": "文本"},
        suggest_relations=False, model=None, user=admin,
    )
    await _EXTRACT_TOKENS[result["extract_token"]]["task"]
    body = await ext.get_extract(
        library_id=library["id"], extract_token=result["extract_token"],
        user=admin,
    )
    assert body["status"] == "failed"
    assert "llm_service down" in body["error"]


# ------------------------------------------------------------- 批量实例化

async def test_instantiate_success_new_new_and_new_existing(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library = await _make_library(svc, admin)
    drug = await svc.create_ontology(
        library["id"], name="药品", description=None,
        attributes=[{"name": "适应症"}], user=admin,
    )
    disease = await svc.create_ontology(
        library["id"], name="疾病", description=None, attributes=None, user=admin,
    )
    treats = await svc.create_ontology_relation(
        library["id"], name="治疗",
        source_ontology_id=drug["id"], target_ontology_id=disease["id"],
        description=None, user=admin,
    )
    synergy = await svc.create_ontology_relation(
        library["id"], name="协同",
        source_ontology_id=drug["id"], target_ontology_id=drug["id"],
        description=None, user=admin,
    )
    existing = await svc.create_object(
        disease["id"], name="感冒", attributes=None, user=admin,
    )

    result = await svc.instantiate_batch(
        library["id"], ontology_id=drug["id"], source="extract",
        objects=[
            {"name": "阿司匹林", "attributes": {"适应症": "发热"}},
            {"name": "布洛芬", "attributes": {}},
        ],
        relations=[
            {"ontology_relation_id": synergy["id"],
             "source_index": 0, "target_index": 1},          # 新→新（药品→药品）
            {"ontology_relation_id": treats["id"],
             "source_index": 0, "target_object_id": existing["id"]},  # 新→已有（药品→疾病）
        ],
        user=admin,
    )
    assert len(result["object_ids"]) == 2
    assert len(result["relation_ids"]) == 2

    objects = await svc.list_objects(
        library["id"], ontology_id=drug["id"], q=None,
        limit=50, offset=0, user=admin,
    )
    by_name = {o["name"]: o for o in objects["items"]}
    assert set(by_name) == {"阿司匹林", "布洛芬"}
    assert all(o["source"] == "extract" for o in by_name.values())
    assert by_name["阿司匹林"]["attributes_json"] == {"适应症": "发热"}

    relations = await svc.list_semantic_relations(
        library["id"], source_object_id=None, target_object_id=None,
        ontology_relation_id=None, limit=50, offset=0, user=admin,
    )
    assert relations["total"] == 2
    pairs = {
        (r["relation_name"], r["source_object_name"], r["target_object_name"])
        for r in relations["items"]
    }
    assert ("协同", "阿司匹林", "布洛芬") in pairs
    assert ("治疗", "阿司匹林", "感冒") in pairs


async def test_instantiate_validation_report_all_or_nothing(async_pool) -> None:
    svc = _svc(async_pool)
    admin = await _make_admin(async_pool)
    library = await _make_library(svc, admin)
    drug = await svc.create_ontology(
        library["id"], name="药品", description=None,
        attributes=[{"name": "适应症"}], user=admin,
    )
    disease = await svc.create_ontology(
        library["id"], name="疾病", description=None, attributes=None, user=admin,
    )
    treats = await svc.create_ontology_relation(
        library["id"], name="治疗",
        source_ontology_id=drug["id"], target_ontology_id=disease["id"],
        description=None, user=admin,
    )
    synergy = await svc.create_ontology_relation(
        library["id"], name="协同",
        source_ontology_id=drug["id"], target_ontology_id=drug["id"],
        description=None, user=admin,
    )
    # 库内存量：阿司匹林（重名靶）+ 感冒 + 存量边 治疗(阿司匹林→感冒)
    aspirin = await svc.create_object(
        drug["id"], name="阿司匹林", attributes=None, user=admin,
    )
    cold = await svc.create_object(
        disease["id"], name="感冒", attributes=None, user=admin,
    )
    await svc.create_semantic_relation(
        ontology_relation_id=treats["id"],
        source_object_id=aspirin["id"], target_object_id=cold["id"],
        user=admin,
    )

    # source 白名单：import 不允许走本端点
    with pytest.raises(InvalidName):
        await svc.instantiate_batch(
            library["id"], ontology_id=drug["id"], source="import",
            objects=[{"name": "X"}], relations=[], user=admin,
        )

    with pytest.raises(BatchInstantiateFailed) as excinfo:
        await svc.instantiate_batch(
            library["id"], ontology_id=drug["id"], source="extract",
            objects=[
                {"name": "布洛芬", "attributes": {"适应症": "止痛"}},   # OK
                {"name": "阿司匹林", "attributes": None},              # duplicate_existing（行1）
                {"name": "   ", "attributes": None},                  # empty_name（行2）
                {"name": "布洛芬", "attributes": None},                # duplicate_in_batch（行3，与行0）
                {"name": "新药", "attributes": {"无此属性": "x"}},      # unknown_attribute（行4）
            ],
            relations=[
                {"ontology_relation_id": treats["id"],
                 "source_index": 99, "target_index": 0},              # invalid_source_ref（行0：下标越界）
                {"ontology_relation_id": uuid.uuid4().hex,
                 "source_index": 0, "target_index": 0},               # unknown_relation（行1）
                {"ontology_relation_id": treats["id"],
                 "source_index": 0, "target_object_id": cold["id"]},  # 行2：端点实际匹配（药品→疾病）→ 不报错
                {"ontology_relation_id": synergy["id"],
                 "source_object_id": cold["id"], "target_index": 0},  # endpoint_mismatch（行3：协同要药品→药品）
                {"ontology_relation_id": treats["id"],
                 "source_object_id": aspirin["id"],
                 "target_object_id": cold["id"]},                     # duplicate_edge（行4：存量边预检）
                {"ontology_relation_id": synergy["id"],
                 "source_index": 0, "target_index": 0},               # self_edge（行5：同下标同对象）
                {"ontology_relation_id": synergy["id"],
                 "source_index": 0, "target_index": 1},               # 行6：合法（供行7 批内重复）
                {"ontology_relation_id": synergy["id"],
                 "source_index": 0, "target_index": 1},               # duplicate_edge（行7：批内重复）
            ],
            user=admin,
        )
    report = excinfo.value.report
    assert report["total_objects"] == 5
    assert report["total_relations"] == 8
    codes = {(e["field"], e["code"].split(":")[0]) for e in report["errors"]}
    assert ("objects[1]", "duplicate_existing") in codes
    assert ("objects[2]", "empty_name") in codes
    assert ("objects[3]", "duplicate_in_batch") in codes
    assert ("objects[4]", "unknown_attribute") in codes
    assert ("relations[0]", "invalid_source_ref") in codes
    assert ("relations[1]", "unknown_relation") in codes
    assert ("relations[3]", "endpoint_mismatch") in codes
    assert ("relations[4]", "duplicate_edge") in codes
    assert ("relations[5]", "self_edge") in codes
    assert ("relations[7]", "duplicate_edge") in codes
    # 行2 端点实际匹配（药品→疾病）→ 不在错误集
    assert ("relations[2]", "endpoint_mismatch") not in codes

    # 整批零写入：库内仍只有预置 2 对象 + 1 边
    objects = await svc.list_objects(
        library["id"], ontology_id=None, q=None, limit=50, offset=0,
        user=admin,
    )
    assert objects["total"] == 2
    relations = await svc.list_semantic_relations(
        library["id"], source_object_id=None, target_object_id=None,
        ontology_relation_id=None, limit=50, offset=0, user=admin,
    )
    assert relations["total"] == 1

    # 空批 → empty_batch
    with pytest.raises(BatchInstantiateFailed) as excinfo:
        await svc.instantiate_batch(
            library["id"], ontology_id=drug["id"], source="manual",
            objects=[], relations=[], user=admin,
        )
    assert ("objects", "empty_batch") in {
        (e["field"], e["code"]) for e in excinfo.value.report["errors"]
    }
