"""OntologyExtractService — AI 辅助实例化（67号 §八）。

start_extract（202）：同步护栏（库级写权限 + 源文本装配，KB 文档只读
单向不回写）→ 内存任务登记（token → {status, result?, error?}，1h 过期
惰性清理、每发起人活跃 ≤5 超限清最旧 + extract_limit 429）→
asyncio.create_task 后台执行并立即返回 token。

后台链路（§8.3，对齐 wiki_compile 的 GenerationClient 模式）：组装
prompt（§8.4 版本冻结常量）→ generation_client.execute(
expected_output_type="json_object")（同步阻塞，anyio.to_thread 卸载）
→ 结构校验 → 候选组装（重名检测 / 关系端点预检 / 候选 ≤100 截断）→
写回 status=done。LLM 失败/解析失败 → status=failed + 原因，不自动重试
（llm_service 侧已有任务级重试，execute 单次；重新提交由用户决定）。

get_extract：前端 2s 轮询。token 过期/不存在/绑定失配（他库或他人发起）
统一 NotFound（404，不泄露登记内容）。

落库不走本服务：候选确认页统一提交 OntologyService.instantiate_batch
（§6.8 单事务全有或全无强校验）。

源文本长度口径（§8.2/§8.6）：粘贴/上传文本 >20000 → 400 text_too_long；
KB 文档拼接超限自动截断并在响应带 truncated=true 提示。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import anyio

from knowledge_mining.mining.kb.db_ontology import OntologyDB
from knowledge_mining.mining.kb.services.ontology_extract_prompts import (
    EXTRACT_SYSTEM_PROMPT, EXTRACT_SYSTEM_PROMPT_WITH_RELATIONS,
)
from knowledge_mining.mining.kb.services.ontology_service import (
    NotFound, OntologyService,
)

MAX_TEXT_CHARS = 20_000                  # 源文本 ≤ 20000 字符（§8.6）
MAX_CANDIDATE_OBJECTS = 100              # 候选对象单次 ≤ 100（超出截断 + warning）
MAX_EXISTING_CONTEXT = 300               # 已有对象给 LLM 的上下文 ≤ 300
EXTRACT_TTL_SECONDS = 3600.0             # 任务登记 1h 过期
MAX_ACTIVE_EXTRACTS_PER_INITIATOR = 5    # 每发起人活跃任务上限（LRU 清最旧）

#: 进程内抽取任务登记（模块级——deps 每请求新建服务实例，登记必须跨请求
#: 存活；单进程单 worker 部署成立，与制品导入 preview token 同一既定约束）。
_EXTRACT_TOKENS: dict[str, dict[str, Any]] = {}

#: 后台任务强引用集（事件循环对 Task 只持弱引用——对齐 wiki_compile 的
#: _COMPILE_TASKS 做法，防任务被 GC 静默丢失）。
_EXTRACT_TASKS: set["asyncio.Task[None]"] = set()


class OntologyExtractError(Exception):
    """向导源/登记级错误。route 翻译：text_too_long→400、
    extract_limit→429、其余→422；KB/文档/登记类问题走 NotFound(404)。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _cleanup_expired() -> None:
    now = time.monotonic()
    expired = [
        token for token, record in _EXTRACT_TOKENS.items()
        if now - record["created_at"] > EXTRACT_TTL_SECONDS
    ]
    for token in expired:
        _EXTRACT_TOKENS.pop(token, None)


def _enforce_extract_cap(initiator: str) -> None:
    """每发起人活跃登记 ≤5：超限清最旧再报 extract_limit（429）。"""
    active = [
        (token, record) for token, record in _EXTRACT_TOKENS.items()
        if record.get("uploader") == initiator
    ]
    if len(active) < MAX_ACTIVE_EXTRACTS_PER_INITIATOR:
        return
    oldest_token, _ = min(active, key=lambda item: item[1]["created_at"])
    _EXTRACT_TOKENS.pop(oldest_token, None)
    raise OntologyExtractError("extract_limit")


class OntologyExtractService:
    """AI 抽取编排。写路径权限门禁经 OntologyService
    （get_library_for_extract：域管理员写 + schema 全量）。"""

    def __init__(
        self, db: OntologyDB, kb_db: Any, generation_client: Any,
    ) -> None:
        self._db = db
        self._kb_db = kb_db        # KbDB：KB 文档只读取文本（§8.2）
        self._generation_client = generation_client  # GenerationClient 协议

    # ------------------------------------------------------------ 发起

    async def start_extract(
        self, service: OntologyService, *,
        library_id: str, ontology_id: str, source: dict[str, Any],
        suggest_relations: bool, model: str | None, user: dict[str, Any],
    ) -> dict[str, Any]:
        """发起抽取（202）：同步护栏 + 登记 + 后台任务，立即返回 token。"""
        _cleanup_expired()
        _enforce_extract_cap(str(user["id"]))
        context = await service.get_library_for_extract(library_id, user=user)
        library = context["library"]
        schema = context["schema"]
        ontology = next(
            (o for o in schema["ontologies"] if o["id"] == ontology_id), None,
        )
        if ontology is None:
            raise NotFound(ontology_id)

        text, truncated = await self._assemble_text(
            source, domain=library["domain"],
        )
        if not text:
            raise OntologyExtractError("empty_text")

        existing_objects: list[dict[str, Any]] = []
        existing_total = 0
        if suggest_relations:
            existing_objects, existing_total = await self._db.list_objects(
                library_id=library_id, ontology_id=None, q=None,
                limit=MAX_EXISTING_CONTEXT, offset=0,
            )

        token = uuid.uuid4().hex
        record: dict[str, Any] = {
            "status": "pending",
            "library_id": library_id,
            "uploader": str(user["id"]),
            "created_at": time.monotonic(),
            "result": None,
            "error": None,
            "task": None,
        }
        _EXTRACT_TOKENS[token] = record
        task = asyncio.create_task(self._run_extract(
            token, library=library, ontology=ontology,
            relation_types=schema["relations"],
            existing_objects=existing_objects,
            existing_total=existing_total,
            suggest_relations=suggest_relations, model=model,
            text=text, truncated=truncated,
        ))
        _EXTRACT_TASKS.add(task)
        task.add_done_callback(_EXTRACT_TASKS.discard)
        record["task"] = task
        return {"extract_token": token}

    # ------------------------------------------------------------ 轮询

    async def get_extract(
        self, *, library_id: str, extract_token: str, user: dict[str, Any],
    ) -> dict[str, Any]:
        """轮询抽取结果：pending / done（candidates）/ failed（error）。
        过期、不存在、他库或他人发起 → NotFound（404，不泄露登记）。"""
        _cleanup_expired()
        record = _EXTRACT_TOKENS.get(extract_token)
        if record is None:
            raise NotFound(extract_token)
        if record["library_id"] != library_id \
                or record["uploader"] != str(user["id"]):
            raise NotFound(extract_token)
        body: dict[str, Any] = {"status": record["status"]}
        if record["status"] == "failed":
            body["error"] = record["error"]
        elif record["status"] == "done":
            body["candidates"] = record["result"]
        return body

    # -------------------------------------------------------- 源文本装配

    async def _assemble_text(
        self, source: dict[str, Any], *, domain: str,
    ) -> tuple[str, bool]:
        """源文本装配：text（粘贴/前端 FileReader 读的 txt/md）超限即拒；
        kb_document 取当前版本解析分段按序拼接，超限截断 + truncated。"""
        kind = source.get("kind")
        if kind == "text":
            text = str(source.get("text") or "")
            if len(text) > MAX_TEXT_CHARS:
                raise OntologyExtractError("text_too_long")
            return text.strip(), False
        if kind == "kb_document":
            return await self._kb_document_text(source, domain=domain)
        raise OntologyExtractError("invalid_source")

    async def _kb_document_text(
        self, source: dict[str, Any], *, domain: str,
    ) -> tuple[str, bool]:
        """KB 文档只读单向（§8.2）：KB 须属于本库所在域且 active；文档须
        未软删且挂该 KB；文本 = current_serving 分段 normalized_text（缺则
        raw_text）按序拼接。跨域/不存在统一 NotFound（不泄露存在性）。"""
        kb_id = source.get("kb_id")
        document_id = source.get("document_id")
        if not kb_id or not document_id:
            raise OntologyExtractError("invalid_source")
        kb = await self._kb_db.get_kb(kb_id)
        if kb is None or kb.get("domain") != domain or kb.get("status") != "active":
            raise NotFound(kb_id)
        document = await self._kb_db.get_document_identity(document_id)
        if document is None or document.get("kb_id") != kb_id:
            raise NotFound(document_id)
        knowledge = await self._kb_db.get_document_knowledge(kb_id, document_id)
        if not knowledge.get("mined"):
            raise OntologyExtractError("document_not_mined")
        parts = [
            str(segment.get("normalized_text")
                or segment.get("raw_text") or "").strip()
            for segment in knowledge.get("segments") or []
        ]
        text = "\n".join(part for part in parts if part)
        if len(text) > MAX_TEXT_CHARS:
            return text[:MAX_TEXT_CHARS], True
        return text, False

    # ------------------------------------------------------------ 后台执行

    async def _run_extract(
        self, token: str, *, library: dict[str, Any], ontology: dict[str, Any],
        relation_types: list[dict[str, Any]],
        existing_objects: list[dict[str, Any]], existing_total: int,
        suggest_relations: bool, model: str | None,
        text: str, truncated: bool,
    ) -> None:
        """后台链路（§8.3）：prompt → LLM → 解析 → 候选组装 → 写回登记。"""
        record = _EXTRACT_TOKENS.get(token)
        if record is None:  # pragma: no cover - 发起与执行同帧注册
            return
        warnings: list[str] = []
        try:
            if suggest_relations and existing_total > len(existing_objects):
                warnings.append(
                    f"已有对象清单超限（共 {existing_total} 个），仅提供前 "
                    f"{MAX_EXISTING_CONTEXT} 个；新对象与清单外对象的关系"
                    "建议可能缺失"
                )
            system = (
                EXTRACT_SYSTEM_PROMPT_WITH_RELATIONS if suggest_relations
                else EXTRACT_SYSTEM_PROMPT
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": _build_user_prompt(
                    ontology, relation_types, existing_objects,
                    suggest_relations, text,
                )},
            ]
            raw = await anyio.to_thread.run_sync(lambda: (
                self._generation_client.execute(
                    messages,
                    expected_output_type="json_object",
                    pipeline_stage="ontology_extract",
                    knowledge_domain=library["domain"],
                    model=model,
                )
            ))
            objects_raw, relations_raw = _validate_llm_output(raw)
            result = await self._assemble_candidates(
                ontology_id=ontology["id"],
                relation_types=relation_types,
                existing_objects=existing_objects,
                objects_raw=objects_raw, relations_raw=relations_raw,
                warnings=warnings, text=text, truncated=truncated,
            )
            record["status"] = "done"
            record["result"] = result
        except Exception as exc:  # noqa: BLE001 - 失败原因原样带回给用户
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"[:500]

    async def _assemble_candidates(
        self, *, ontology_id: str, relation_types: list[dict[str, Any]],
        existing_objects: list[dict[str, Any]],
        objects_raw: list[dict[str, Any]], relations_raw: list[dict[str, Any]],
        warnings: list[str], text: str, truncated: bool,
    ) -> dict[str, Any]:
        """候选组装（§8.5）：重名检测（conflict）+ 关系引用解析（先本批后
        库内，解析失败丢弃 + warning）+ 端点预检（endpoint_ok 仅影响 UI
        提示）+ 候选 ≤100 截断。"""
        objects = objects_raw
        if len(objects) > MAX_CANDIDATE_OBJECTS:
            warnings.append(
                f"候选对象超限（{len(objects)} 个），仅保留前 "
                f"{MAX_CANDIDATE_OBJECTS} 个"
            )
            objects = objects[:MAX_CANDIDATE_OBJECTS]

        existing_names = await self._db.list_object_names(
            ontology_id, [o["name"] for o in objects],
        )
        for item in objects:
            conflict = item["name"] in existing_names
            item["conflict"] = conflict
            item["conflict_with"] = item["name"] if conflict else None

        relation_by_name = {r["name"]: r for r in relation_types}
        existing_by_name: dict[str, dict[str, Any]] = {}
        for obj in existing_objects:
            existing_by_name.setdefault(str(obj["name"]), obj)
        candidate_names = {o["name"] for o in objects}

        relations: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        for raw in relations_raw:
            source = raw["source"]
            relation_name = raw["relation"]
            target = raw["target"]
            if source not in candidate_names:
                warnings.append(
                    f"关系「{source}-[{relation_name}]->{target}」的源对象"
                    "不在候选中，已丢弃"
                )
                continue
            if target in candidate_names:
                target_existing = False
                target_object_id: str | None = None
                target_ontology = ontology_id
            elif target in existing_by_name:
                existing = existing_by_name[target]
                target_existing = True
                target_object_id = str(existing["id"])
                target_ontology = str(existing["ontology_id"])
            else:
                warnings.append(
                    f"关系「{source}-[{relation_name}]->{target}」的目标对象"
                    "无法解析，已丢弃"
                )
                continue
            relation = relation_by_name.get(relation_name)
            if relation is None:
                warnings.append(f"关系类型「{relation_name}」不在库内定义中")
            edge_key = (source, relation_name, target)
            if edge_key in seen_edges:
                continue  # LLM 重复建议静默去重
            seen_edges.add(edge_key)
            endpoint_ok = relation is not None and (
                relation["source_ontology_id"] == ontology_id
                and relation["target_ontology_id"] == target_ontology
            )
            relations.append({
                "source": source, "relation": relation_name, "target": target,
                "target_existing": target_existing,
                "target_object_id": target_object_id,
                "endpoint_ok": endpoint_ok,
            })

        return {
            "objects": objects,
            "relations": relations,
            "warnings": warnings,
            "input_chars": len(text),
            "truncated": truncated,
        }


# ---------------------------------------------------------------- helpers

def _build_user_prompt(
    ontology: dict[str, Any], relation_types: list[dict[str, Any]],
    existing_objects: list[dict[str, Any]],
    suggest_relations: bool, text: str,
) -> str:
    """USER_PROMPT 渲染顺序（§8.4）：本体定义 → 关系类型 → 已有对象清单
    → 源文本。"""
    lines = ["【本体定义】", f"本体名称：{ontology['name']}"]
    if ontology.get("description"):
        lines.append(f"描述：{ontology['description']}")
    attributes = ontology.get("attributes") or []
    if attributes:
        lines.append("属性：")
        lines.extend(
            f"- {a['name']}：{a.get('description') or '（无描述）'}"
            for a in attributes
        )
    else:
        lines.append("属性：（该本体未定义属性）")
    if suggest_relations:
        lines.append("")
        lines.append("【关系类型】")
        if relation_types:
            lines.extend(
                f"- {r.get('source_ontology_name')} -[{r['name']}]-> "
                f"{r.get('target_ontology_name')}"
                + (f"：{r['description']}" if r.get("description") else "")
                for r in relation_types
            )
        else:
            lines.append("（库内未定义关系类型）")
        if existing_objects:
            lines.append("")
            lines.append("【已有对象清单】")
            lines.extend(
                f"- {o.get('ontology_name')}/{o['name']}"
                for o in existing_objects
            )
    lines.append("")
    lines.append("【源文本】")
    lines.append(text)
    return "\n".join(lines)


def _validate_llm_output(raw: Any) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]],
]:
    """LLM 输出结构校验（§8.5 输出契约）：json_object 客户端已解析为 dict
    （含栅栏正则兜底）；此处清洗名称/属性形态，丢弃畸形条目（名称截断至
    200 与对象名上限同口径）。"""
    if not isinstance(raw, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    raw_objects = raw.get("objects")
    if not isinstance(raw_objects, list):
        raise ValueError("LLM 输出缺少 objects 数组")
    objects: list[dict[str, Any]] = []
    for item in raw_objects:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        objects.append({
            "name": name[:200],
            "attributes": {
                str(key): value for key, value in attributes.items()
                if value is not None
            },
        })
    raw_relations = raw.get("relations") or []
    if not isinstance(raw_relations, list):
        raw_relations = []
    relations: list[dict[str, Any]] = []
    for item in raw_relations:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        relation = str(item.get("relation") or "").strip()
        target = str(item.get("target") or "").strip()
        if source and relation and target:
            relations.append({
                "source": source, "relation": relation, "target": target,
                "target_existing": bool(item.get("target_existing")),
            })
    return objects, relations
