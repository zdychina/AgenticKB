"""OntologyService — 本体库业务逻辑（67号）。

职责：domain 合法性 + 读/写权限 enforcement（域管理员写 / 域成员读）+
业务规则（属性键校验、语义关系端点匹配、删除保护全强 RESTRICT）。
Route 层捕获 OntologyError 子类翻译成 HTTP 状态（见 routes/ontology.py）。

机制权威 = docs/下一阶段/67-本体管理MVP设计-2026-11-17.md §五/§六/§九：
- 写 = can_manage_domain（site admin OR domain admin）；读 = 域成员；
- 跨域资源统一 NotFound（不泄露存在性）；
- 删除保护：InUse 带计数；属性删除例外（总是允许，同事务清存量键）。
"""
from __future__ import annotations

from typing import Any

from psycopg.errors import UniqueViolation

from knowledge_mining.mining.infra.domain_pack import resolve_domain
from knowledge_mining.mining.kb.db_ontology import OntologyDB


# ----------------------------------------------------------------- errors

class OntologyError(Exception):
    """Base for ontology business errors."""


class NotFound(OntologyError):
    pass


class Forbidden(OntologyError):
    pass


class Duplicate(OntologyError):
    pass


class InUse(OntologyError):
    """删除/改端点保护：资源仍被下游引用（detail 带计数）。"""


class InvalidDomain(OntologyError):
    pass


class InvalidName(OntologyError):
    pass


class UnknownAttribute(OntologyError):
    """对象属性键不在本体属性定义集合内（67号 §5.1）。"""


class InvalidAttributeValue(OntologyError):
    """属性值非标量（string/number/boolean 之外，67号 §5.1）。"""


class EndpointMismatch(OntologyError):
    """语义关系两端对象的本体与关系类型端点不匹配（67号 §5.2）。"""


class CrossDomainRef(OntologyError):
    """引用目标制品与对象库不同域（70号 §3.2 一期限同域）。"""


class InvalidRefTarget(OntologyError):
    """target_key 在制品 current revision 内不存在（422 附 valid_keys 样例）。"""

    def __init__(self, message: str, valid_keys: list[str]) -> None:
        super().__init__(message)
        self.valid_keys = valid_keys



class BatchInstantiateFailed(OntologyError):
    """批量实例化校验失败（67号 §6.8）：整批未落库。route 翻 422，
    detail={message, report}（report.errors 带 objects[i]/relations[j]
    定位，供前端逐项回显）。"""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__(report.get("message", "batch validation failed"))
        self.report = report


# ----------------------------------------------------------------- limits

_MAX_NAME_LENGTH = 80       # 库/本体/关系类型/属性名
_MAX_OBJECT_NAME_LENGTH = 200
_MAX_DESCRIPTION_LENGTH = 2000
_MAX_ATTRIBUTE_KEYS = 100   # 单本体属性定义数上限（防误操作炸表）
_MAX_REF_NOTE_LENGTH = 500  # refs 关联理由上限（70号 评审⑦）
_REF_SUMMARY_LIMIT = 20     # neighbors 对象卡 refs 摘要上限（§四）



class OntologyService:
    def __init__(self, db: OntologyDB) -> None:
        self._db = db

    # --------------------------------------------------------- 本体库

    async def create_library(
        self, *, domain: str, name: str, description: str | None,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        domain = _validate_domain(domain)
        name = _normalize_name(name, _MAX_NAME_LENGTH, "本体库名称")
        description = _validate_description(description)
        await self._require_write(user=user, domain=domain)
        try:
            return await self._db.create_library(
                domain=domain, name=name, description=description,
                created_by=user["id"],
            )
        except UniqueViolation as exc:
            raise Duplicate(f"本体库「{name}」在该域下已存在") from exc

    async def list_libraries(
        self, *, domain: str, q: str | None, user: dict[str, Any],
    ) -> list[dict[str, Any]]:
        domain = _validate_domain(domain)
        await self._require_read(user=user, domain=domain)
        return await self._db.list_libraries(domain=domain, q=q)

    async def search_objects(
        self, *, domain: str, q: str, limit: int, user: dict[str, Any],
    ) -> dict[str, Any]:
        """域级对象名搜索（69号 §4.1）：权限同 list_libraries（域成员读）。"""
        domain = _validate_domain(domain)
        await self._require_read(user=user, domain=domain)
        return await self._db.search_objects(domain=domain, q=q, limit=limit)

    async def get_library(
        self, library_id: str, *, user: dict[str, Any],
    ) -> dict[str, Any]:
        library = await self._get_library_checked(library_id, user)
        return library

    async def update_library(
        self, library_id: str, *, name: str | None, description: str | None,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        library = await self._get_library_checked(
            library_id, user, require_write=True,
        )
        if name is not None:
            name = _normalize_name(name, _MAX_NAME_LENGTH, "本体库名称")
        description = _validate_description(description)
        try:
            updated = await self._db.update_library(
                library_id, name=name, description=description,
            )
        except UniqueViolation as exc:
            raise Duplicate(f"本体库「{name}」在该域下已存在") from exc
        if updated is None:
            raise NotFound(library_id)
        return updated

    async def delete_library(
        self, library_id: str, *, user: dict[str, Any],
    ) -> None:
        await self._get_library_checked(library_id, user, require_write=True)
        ontology_count = await self._db.count_ontologies(library_id)
        if ontology_count > 0:
            raise InUse(
                f"本体库内仍有 {ontology_count} 个本体，须先清空本体才能删除"
            )
        if not await self._db.delete_library(library_id):
            raise NotFound(library_id)

    # --------------------------------------------------------- 本体与属性

    async def create_ontology(
        self, library_id: str, *, name: str, description: str | None,
        attributes: list[dict[str, Any]] | None,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        await self._get_library_checked(library_id, user, require_write=True)
        name = _normalize_name(name, _MAX_NAME_LENGTH, "本体名称")
        description = _validate_description(description)
        attrs = _validate_attribute_defs(attributes or [])
        try:
            onto = await self._db.create_ontology(
                library_id=library_id, name=name, description=description,
                created_by=user["id"], attributes=attrs,
            )
        except UniqueViolation as exc:
            raise Duplicate(f"本体「{name}」在该库内已存在") from exc
        return onto

    async def list_ontologies(
        self, library_id: str, *, user: dict[str, Any],
    ) -> list[dict[str, Any]]:
        await self._get_library_checked(library_id, user)
        return await self._db.list_ontologies(library_id)

    async def get_ontology(
        self, ontology_id: str, *, user: dict[str, Any],
    ) -> dict[str, Any]:
        onto = await self._get_ontology_checked(ontology_id, user)
        detail = await self._db.get_ontology(ontology_id)
        return detail if detail is not None else onto

    async def get_ontology_for_import(
        self, ontology_id: str, *, user: dict[str, Any],
    ) -> dict[str, Any]:
        """批量导入向导前置（67号 §七）：写权限门禁 + 本体详情（含属性
        定义，供列映射/校验用）。导入服务 upload/commit 两次都走这里。"""
        await self._get_ontology_checked(ontology_id, user, require_write=True)
        detail = await self._db.get_ontology(ontology_id)
        if detail is None:  # pragma: no cover - 上行已保证存在
            raise NotFound(ontology_id)
        return detail

    async def get_library_for_extract(
        self, library_id: str, *, user: dict[str, Any],
    ) -> dict[str, Any]:
        """AI 抽取上下文入口（67号 §八）：库级写权限 + schema 全量
        （本体含属性 / 关系类型含端点名），供 prompt 组装与端点预检。"""
        library = await self._get_library_checked(
            library_id, user, require_write=True,
        )
        schema = await self._db.get_library_schema(library_id)
        return {"library": library, "schema": schema}

    async def instantiate_batch(
        self, library_id: str, *, ontology_id: str, source: str,
        objects: list[dict[str, Any]], relations: list[dict[str, Any]],
        user: dict[str, Any], source_document_id: str | None = None,
    ) -> dict[str, Any]:
        """批量实例化（§6.8）：一批对象 + 一批关系单事务全有或全无。

        relations 两端各用 ``{end}_index``（本批新对象下标）或
        ``{end}_object_id``（库内已有对象）二选一引用——「新/已有」任意
        组合；全部校验聚合进一份报告（objects[i]/relations[j] 定位）后
        整批拒绝。source: manual|extract（import 由导入端点自落库）。
        source_document_id 仅 extract 通道可带（69号溯源，不重验 KB 归属
        ——越域伪造最多写一个无效引用，读取侧降级兜底）。"""
        await self._get_library_checked(library_id, user, require_write=True)
        await self._get_ontology_in_library(ontology_id, library_id)
        if source not in ("manual", "extract"):
            raise InvalidName("source 仅允许 manual/extract")
        if source_document_id is not None and source != "extract":
            raise InvalidName("source_document_id 仅允许 source='extract'")
        detail = await self._db.get_ontology(ontology_id)
        defined = {a["name"] for a in (detail or {}).get("attributes", [])}

        errors: list[dict[str, str]] = []
        if not objects:
            errors.append({
                "field": "objects", "code": "empty_batch",
                "message": "objects 至少包含 1 个对象",
            })

        # ── 对象校验（码表与批量导入 §7.4 对齐）──
        names: list[str | None] = []
        for index, item in enumerate(objects):
            field = f"objects[{index}]"
            raw_name = item.get("name")
            name = str(raw_name).strip() if raw_name is not None else ""
            if not name:
                errors.append({
                    "field": field, "code": "empty_name", "message": "名称为空",
                })
                names.append(None)
                continue
            if len(name) > _MAX_OBJECT_NAME_LENGTH:
                errors.append({
                    "field": field, "code": "invalid_name",
                    "message": f"名称超过 {_MAX_OBJECT_NAME_LENGTH} 字符",
                })
                names.append(None)
                continue
            names.append(name)
            for key, value in (item.get("attributes") or {}).items():
                if key not in defined:
                    errors.append({
                        "field": field, "code": f"unknown_attribute:{key}",
                        "message": f"属性「{key}」不在本体定义内",
                    })
                elif value is not None and not isinstance(
                        value, (str, int, float, bool)):
                    errors.append({
                        "field": field, "code": f"invalid_attribute_value:{key}",
                        "message": f"属性「{key}」的值必须是标量",
                    })
        candidate_names = [n for n in names if n]
        existing_names = (
            await self._db.list_object_names(ontology_id, candidate_names)
            if candidate_names else set()
        )
        seen: dict[str, int] = {}
        for index, name in enumerate(names):
            if name is None:
                continue
            field = f"objects[{index}]"
            if name in existing_names:
                errors.append({
                    "field": field, "code": "duplicate_existing",
                    "message": f"对象「{name}」已存在",
                })
            elif name in seen:
                errors.append({
                    "field": field, "code": "duplicate_in_batch",
                    "message": f"与 objects[{seen[name]}] 重名",
                })
            else:
                seen[name] = index

        # ── 关系校验（§5.2 端点规则的批量版）──
        relation_defs: dict[str, dict[str, Any] | None] = {}
        for rid in {r.get("ontology_relation_id") for r in relations
                    if r.get("ontology_relation_id")}:
            relation_defs[rid] = await self._db.get_ontology_relation(rid)
        referenced_ids = sorted({
            r.get(key) for r in relations
            for key in ("source_object_id", "target_object_id")
            if r.get(key)
        })
        existing_objs = (
            await self._db.get_objects_by_ids(referenced_ids)
            if referenced_ids else {}
        )

        def _resolve_end(
            item: dict[str, Any], end: str, field: str,
        ) -> tuple[str, Any] | None:
            """端点引用解析：返回 (kind, ref)（new=批内下标 int /
            existing=对象 id str）；非法时记错误并返回 None。"""
            index = item.get(f"{end}_index")
            object_id = item.get(f"{end}_object_id")
            if (index is None) == (object_id is None):
                errors.append({
                    "field": field, "code": f"invalid_{end}_ref",
                    "message": f"{end} 必须且只能提供 index 或 object_id 之一",
                })
                return None
            if index is not None:
                if not isinstance(index, int) or not 0 <= index < len(objects) \
                        or names[index] is None:
                    errors.append({
                        "field": field, "code": f"invalid_{end}_ref",
                        "message": f"{end}_index 越界或指向无效对象",
                    })
                    return None
                return ("new", index)
            obj = existing_objs.get(object_id)
            if obj is None or obj["library_id"] != library_id:
                errors.append({
                    "field": field, "code": f"unknown_{end}_object",
                    "message": f"{end}_object_id 不在本库内",
                })
                return None
            return ("existing", object_id)

        validated: list[tuple[str, tuple[str, Any], tuple[str, Any]]] = []
        seen_edges: set[tuple[Any, ...]] = set()
        db_check_combos: list[tuple[str, str, str]] = []
        for index, item in enumerate(relations):
            field = f"relations[{index}]"
            rid = item.get("ontology_relation_id")
            relation = relation_defs.get(rid)
            if not rid or relation is None or relation["library_id"] != library_id:
                errors.append({
                    "field": field, "code": "unknown_relation",
                    "message": "关系类型不存在或不属于本库",
                })
                continue
            source_end = _resolve_end(item, "source", field)
            target_end = _resolve_end(item, "target", field)
            if source_end is None or target_end is None:
                continue
            source_ontology = (
                ontology_id if source_end[0] == "new"
                else existing_objs[source_end[1]]["ontology_id"]
            )
            target_ontology = (
                ontology_id if target_end[0] == "new"
                else existing_objs[target_end[1]]["ontology_id"]
            )
            if source_ontology != relation["source_ontology_id"] \
                    or target_ontology != relation["target_ontology_id"]:
                errors.append({
                    "field": field, "code": "endpoint_mismatch",
                    "message": (
                        f"关系「{relation['name']}」要求端点本体不匹配"
                        f"（实际 {source_ontology} → {target_ontology}）"
                    ),
                })
                continue
            if source_end == target_end:
                errors.append({
                    "field": field, "code": "self_edge",
                    "message": "关系两端不能是同一个对象",
                })
                continue
            edge_key = (rid, source_end, target_end)
            if edge_key in seen_edges:
                errors.append({
                    "field": field, "code": "duplicate_edge",
                    "message": "与批内其它关系重复（同类型同两端）",
                })
                continue
            seen_edges.add(edge_key)
            if source_end[0] == "existing" and target_end[0] == "existing":
                db_check_combos.append(
                    (rid, source_end[1], target_end[1]))
            validated.append((rid, source_end, target_end))

        if db_check_combos:
            for combo in await self._db.edge_exists_batch(
                    library_id, db_check_combos):
                for index, item in enumerate(relations):
                    rid = item.get("ontology_relation_id")
                    if rid != combo[0]:
                        continue
                    if item.get("source_object_id") == combo[1] \
                            and item.get("target_object_id") == combo[2]:
                        errors.append({
                            "field": f"relations[{index}]",
                            "code": "duplicate_edge",
                            "message": "该语义关系（同类型同两端）已存在",
                        })

        if errors:
            raise BatchInstantiateFailed({
                "message": f"{len(errors)} 项校验失败，整批未导入",
                "total_objects": len(objects),
                "total_relations": len(relations),
                "errors": errors,
            })

        try:
            return await self._db.instantiate_batch(
                ontology_id=ontology_id,
                objects=[(n, item.get("attributes") or {})
                         for item, n in zip(objects, names)],
                relations=[
                    (rid, source_end[1], target_end[1])
                    for rid, source_end, target_end in validated
                ],
                created_by=user["id"], source=source,
                source_document_id=source_document_id,
            )
        except UniqueViolation as exc:
            # 并发窗口竞态（校验后到插入前他人先写）——整事务已回滚
            raise Duplicate("并发写入冲突（对象重名或重复关系），请重试") from exc

    async def update_ontology(
        self, ontology_id: str, *, name: str | None, description: str | None,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        onto = await self._get_ontology_checked(
            ontology_id, user, require_write=True,
        )
        if name is not None:
            name = _normalize_name(name, _MAX_NAME_LENGTH, "本体名称")
        description = _validate_description(description)
        try:
            updated = await self._db.update_ontology(
                ontology_id, name=name, description=description,
            )
        except UniqueViolation as exc:
            raise Duplicate(f"本体「{name}」在该库内已存在") from exc
        if updated is None:
            raise NotFound(ontology_id)
        return updated

    async def delete_ontology(
        self, ontology_id: str, *, user: dict[str, Any],
    ) -> None:
        onto = await self._get_ontology_checked(
            ontology_id, user, require_write=True,
        )
        object_count = await self._db.count_objects_of_ontology(ontology_id)
        if object_count > 0:
            raise InUse(f"本体「{onto['name']}」下仍有 {object_count} 个语义对象")
        relation_refs = await self._db.count_relations_referencing(ontology_id)
        if relation_refs > 0:
            raise InUse(
                f"本体「{onto['name']}」仍被 {relation_refs} 个本体关系引用"
                "（作源或目标），须先删除这些关系类型"
            )
        if not await self._db.delete_ontology(ontology_id):
            raise NotFound(ontology_id)

    async def add_attribute(
        self, ontology_id: str, *, name: str, description: str | None,
        sort_order: int | None, user: dict[str, Any],
    ) -> dict[str, Any]:
        onto = await self._get_ontology_checked(
            ontology_id, user, require_write=True,
        )
        name = _normalize_name(name, _MAX_NAME_LENGTH, "属性名称")
        description = _validate_description(description)
        existing = await self._db.get_ontology(ontology_id)
        if existing is not None and len(existing["attributes"]) >= _MAX_ATTRIBUTE_KEYS:
            raise InvalidName(
                f"本体属性数量已达上限 {_MAX_ATTRIBUTE_KEYS}"
            )
        try:
            return await self._db.add_attribute(
                ontology_id=ontology_id, name=name, description=description,
                sort_order=sort_order,
            )
        except UniqueViolation as exc:
            raise Duplicate(
                f"属性「{name}」在本体「{onto['name']}」中已存在"
            ) from exc

    async def update_attribute(
        self, attribute_id: str, *, name: str | None, description: str | None,
        sort_order: int | None, user: dict[str, Any],
    ) -> dict[str, Any]:
        attr = await self._db.get_attribute(attribute_id)
        if attr is None:
            raise NotFound(attribute_id)
        await self._get_ontology_checked(
            attr["ontology_id"], user, require_write=True,
        )
        if name is not None:
            name = _normalize_name(name, _MAX_NAME_LENGTH, "属性名称")
        description = _validate_description(description)
        try:
            updated = await self._db.update_attribute(
                attribute_id, name=name, description=description,
                sort_order=sort_order,
            )
        except UniqueViolation as exc:
            raise Duplicate(f"属性「{name}」在本体中已存在") from exc
        if updated is None:
            raise NotFound(attribute_id)
        return updated

    async def delete_attribute(
        self, attribute_id: str, *, user: dict[str, Any],
    ) -> None:
        attr = await self._db.get_attribute(attribute_id)
        if attr is None:
            raise NotFound(attribute_id)
        await self._get_ontology_checked(
            attr["ontology_id"], user, require_write=True,
        )
        # 属性删除总是允许：定义删除 + 同事务清理对象存量键（db 层保证）。
        await self._db.delete_attribute(attribute_id)

    # --------------------------------------------------------- 本体关系

    async def create_ontology_relation(
        self, library_id: str, *, name: str, description: str | None,
        source_ontology_id: str, target_ontology_id: str,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        await self._get_library_checked(library_id, user, require_write=True)
        name = _normalize_name(name, _MAX_NAME_LENGTH, "本体关系名称")
        description = _validate_description(description)
        source = await self._get_ontology_in_library(source_ontology_id, library_id)
        target = await self._get_ontology_in_library(target_ontology_id, library_id)
        try:
            return await self._db.create_ontology_relation(
                library_id=library_id, name=name, description=description,
                source_ontology_id=source["id"], target_ontology_id=target["id"],
                created_by=user["id"],
            )
        except UniqueViolation as exc:
            raise Duplicate(f"本体关系「{name}」在该库内已存在") from exc

    async def list_ontology_relations(
        self, library_id: str, *, user: dict[str, Any],
    ) -> list[dict[str, Any]]:
        await self._get_library_checked(library_id, user)
        return await self._db.list_ontology_relations(library_id)

    async def update_ontology_relation(
        self, relation_id: str, *, name: str | None, description: str | None,
        source_ontology_id: str | None, target_ontology_id: str | None,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        relation = await self._db.get_ontology_relation(relation_id)
        if relation is None:
            raise NotFound(relation_id)
        await self._get_library_checked(
            relation["library_id"], user, require_write=True,
        )
        if name is not None:
            name = _normalize_name(name, _MAX_NAME_LENGTH, "本体关系名称")
        description = _validate_description(description)
        endpoint_changed = (
            (source_ontology_id is not None
             and source_ontology_id != relation["source_ontology_id"])
            or (target_ontology_id is not None
                and target_ontology_id != relation["target_ontology_id"])
        )
        if endpoint_changed:
            instance_count = await self._db.count_semantic_relations_of(
                relation_id,
            )
            if instance_count > 0:
                raise InUse(
                    f"本体关系「{relation['name']}」已有 {instance_count} 个"
                    "语义关系实例，不能修改端点（只能删除重建）"
                )
            if source_ontology_id is not None:
                await self._get_ontology_in_library(
                    source_ontology_id, relation["library_id"],
                )
            if target_ontology_id is not None:
                await self._get_ontology_in_library(
                    target_ontology_id, relation["library_id"],
                )
        try:
            updated = await self._db.update_ontology_relation(
                relation_id, name=name, description=description,
                source_ontology_id=source_ontology_id,
                target_ontology_id=target_ontology_id,
            )
        except UniqueViolation as exc:
            raise Duplicate(f"本体关系「{name}」在该库内已存在") from exc
        if updated is None:
            raise NotFound(relation_id)
        return updated

    async def delete_ontology_relation(
        self, relation_id: str, *, user: dict[str, Any],
    ) -> None:
        relation = await self._db.get_ontology_relation(relation_id)
        if relation is None:
            raise NotFound(relation_id)
        await self._get_library_checked(
            relation["library_id"], user, require_write=True,
        )
        instance_count = await self._db.count_semantic_relations_of(relation_id)
        if instance_count > 0:
            raise InUse(
                f"本体关系「{relation['name']}」仍有 {instance_count} 个"
                "语义关系实例，须先删除这些实例"
            )
        if not await self._db.delete_ontology_relation(relation_id):
            raise NotFound(relation_id)

    # --------------------------------------------------------- 语义对象

    async def create_object(
        self, ontology_id: str, *, name: str,
        attributes: dict[str, Any] | None, user: dict[str, Any],
        source: str = "manual",
    ) -> dict[str, Any]:
        onto = await self._get_ontology_checked(
            ontology_id, user, require_write=True,
        )
        name = _normalize_name(name, _MAX_OBJECT_NAME_LENGTH, "对象名称")
        await _validate_attribute_values(
            ontology_id=ontology_id, attributes=attributes, db=self._db,
        )
        try:
            return await self._db.create_object(
                ontology_id=ontology_id, name=name,
                attributes=attributes or {}, created_by=user["id"],
                source=source,
            )
        except UniqueViolation as exc:
            raise Duplicate(
                f"对象「{name}」在本体「{onto['name']}」下已存在"
            ) from exc

    async def list_objects(
        self, library_id: str, *, ontology_id: str | None, q: str | None,
        limit: int, offset: int, user: dict[str, Any],
    ) -> dict[str, Any]:
        await self._get_library_checked(library_id, user)
        if ontology_id:
            await self._get_ontology_in_library(ontology_id, library_id)
        items, total = await self._db.list_objects(
            library_id=library_id, ontology_id=ontology_id, q=q,
            limit=limit, offset=offset,
        )
        return {"items": items, "total": total}

    async def get_object(
        self, object_id: str, *, user: dict[str, Any],
    ) -> dict[str, Any]:
        obj = await self._get_object_checked(object_id, user)
        detail = await self._db.get_object(object_id)
        return detail if detail is not None else obj

    async def update_object(
        self, object_id: str, *, name: str | None,
        attributes: dict[str, Any] | None, user: dict[str, Any],
    ) -> dict[str, Any]:
        obj = await self._get_object_checked(object_id, user, require_write=True)
        if name is not None:
            name = _normalize_name(name, _MAX_OBJECT_NAME_LENGTH, "对象名称")
        if attributes is not None:
            await _validate_attribute_values(
                ontology_id=obj["ontology_id"], attributes=attributes,
                db=self._db,
            )
        try:
            updated = await self._db.update_object(
                object_id, name=name, attributes=attributes,
            )
        except UniqueViolation as exc:
            raise Duplicate(f"对象「{name}」在本体下已存在") from exc
        if updated is None:
            raise NotFound(object_id)
        return updated

    async def delete_object(
        self, object_id: str, *, user: dict[str, Any],
    ) -> None:
        obj = await self._get_object_checked(object_id, user, require_write=True)
        relation_count = await self._db.count_relations_for_object(object_id)
        if relation_count > 0:
            raise InUse(
                f"对象「{obj['name']}」参与 {relation_count} 个语义关系，"
                "须先删除这些关系"
            )
        if not await self._db.delete_object(object_id):
            raise NotFound(object_id)

    # --------------------------------------------------------- 语义关系

    async def create_semantic_relation(
        self, *, ontology_relation_id: str, source_object_id: str,
        target_object_id: str, user: dict[str, Any],
    ) -> dict[str, Any]:
        relation = await self._db.get_ontology_relation(ontology_relation_id)
        if relation is None:
            raise NotFound(ontology_relation_id)
        await self._get_library_checked(
            relation["library_id"], user, require_write=True,
        )
        source = await self._get_object_in_library(
            source_object_id, relation["library_id"],
        )
        target = await self._get_object_in_library(
            target_object_id, relation["library_id"],
        )
        if source["ontology_id"] != relation["source_ontology_id"] \
                or target["ontology_id"] != relation["target_ontology_id"]:
            raise EndpointMismatch(
                f"关系「{relation['name']}」要求 "
                f"{relation['source_ontology_id']} → "
                f"{relation['target_ontology_id']}，"
                f"实际两端对象的本体不匹配"
            )
        if source_object_id == target_object_id:
            raise EndpointMismatch("语义关系两端不能是同一个对象")
        try:
            return await self._db.create_semantic_relation(
                library_id=relation["library_id"],
                ontology_relation_id=ontology_relation_id,
                source_object_id=source_object_id,
                target_object_id=target_object_id,
                created_by=user["id"],
            )
        except UniqueViolation as exc:
            raise Duplicate("该语义关系（同类型同两端）已存在") from exc

    async def list_semantic_relations(
        self, library_id: str, *, source_object_id: str | None,
        target_object_id: str | None, ontology_relation_id: str | None,
        limit: int, offset: int, user: dict[str, Any],
    ) -> dict[str, Any]:
        await self._get_library_checked(library_id, user)
        items, total = await self._db.list_semantic_relations(
            library_id=library_id, source_object_id=source_object_id,
            target_object_id=target_object_id,
            ontology_relation_id=ontology_relation_id,
            limit=limit, offset=offset,
        )
        return {"items": items, "total": total}

    async def delete_semantic_relation(
        self, relation_id: str, *, user: dict[str, Any],
    ) -> None:
        relation = await self._db.get_semantic_relation(relation_id)
        if relation is None:
            raise NotFound(relation_id)
        await self._get_library_checked(
            relation["library_id"], user, require_write=True,
        )
        if not await self._db.delete_semantic_relation(relation_id):
            raise NotFound(relation_id)

    # --------------------------------------------------------- 图查询

    async def get_library_schema(
        self, library_id: str, *, user: dict[str, Any],
    ) -> dict[str, Any]:
        await self._get_library_checked(library_id, user)
        return await self._db.get_library_schema(library_id)

    async def get_library_graph(
        self, library_id: str, *, ontology_ids: list[str] | None, limit: int,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """实例图全量（68号 §2.1）：读权限对齐 schema——域成员可看。"""
        await self._get_library_checked(library_id, user)
        return await self._db.get_library_graph(
            library_id, ontology_ids=ontology_ids, limit=limit,
        )

    async def get_object_neighbors(
        self, object_id: str, *, user: dict[str, Any],
    ) -> dict[str, Any]:
        await self._get_object_checked(object_id, user)
        neighbors = await self._db.get_object_neighbors(object_id)
        if neighbors is None:  # pragma: no cover - _checked 已保证存在
            raise NotFound(object_id)
        return neighbors

    # --------------------------------------------------------- 对象↔制品 refs（70号）

    async def create_object_ref(
        self, object_id: str, *, artifact_id: str, target_key: str | None,
        note: str | None, user: dict[str, Any],
    ) -> dict[str, Any]:
        """建引用（§三/§四）：写=对象库域管理员；制品须存在且 kind 合法
        （404 不泄露他域）；同域约束（422 cross_domain_forbidden）；
        target_key 须存在于制品 current revision（422 附样例）；重复 409。"""
        obj = await self._get_object_checked(object_id, user, require_write=True)
        library = await self._db.get_library(obj["library_id"])
        if library is None:  # pragma: no cover - _checked 已保证存在
            raise NotFound(object_id)
        artifact_id = str(artifact_id or "").strip()
        artifact = await self._db.get_ref_artifact(artifact_id)
        if artifact is None or artifact["kind"] not in ("table", "wiki"):
            raise NotFound(artifact_id)
        if artifact["domain_id"] != library["domain"]:
            raise CrossDomainRef(
                "cross_domain_forbidden：引用目标制品须与对象库同域（70号一期约束）"
            )
        target_key = str(target_key or "").strip()
        note = str(note or "").strip()
        if len(note) > _MAX_REF_NOTE_LENGTH:
            raise InvalidName(f"关联理由不能超过 {_MAX_REF_NOTE_LENGTH} 字")
        if target_key and not await self._db.artifact_target_exists(
            artifact_id, revision=artifact["current_revision"],
            kind=artifact["kind"], target_key=target_key,
        ):
            valid = await self._db.sample_artifact_target_keys(
                artifact_id, revision=artifact["current_revision"],
                kind=artifact["kind"],
            )
            raise InvalidRefTarget(
                f"目标 {target_key} 不在制品「{artifact['title']}」当前版本内"
                "（跟随 current，目标被移除后不可新建引用）",
                valid,
            )
        try:
            ref = await self._db.create_object_ref(
                object_id=object_id, artifact_id=artifact_id,
                target_key=target_key, note=note, created_by=user["id"],
            )
        except UniqueViolation as exc:
            raise Duplicate("该引用（同对象同制品同目标）已存在") from exc
        item = {**ref,
                "artifact_title": artifact["title"], "kind": artifact["kind"],
                "artifact_status": artifact["status"],
                "artifact_domain_id": artifact["domain_id"],
                "current_revision": artifact["current_revision"]}
        return (await self._decorate_refs([item]))[0]

    async def list_object_refs(
        self, object_id: str, *, limit: int, user: dict[str, Any],
    ) -> dict[str, Any]:
        """refs 列表（§3.3 第 1 行）：读=域成员；返回当前用户可见的制品，
        不可见的引用整行不出现；truncated=过滤后总数超 limit（评审⑤）。"""
        obj = await self._get_object_checked(object_id, user)
        library = await self._db.get_library(obj["library_id"])
        if library is None:  # pragma: no cover - _checked 已保证存在
            raise NotFound(object_id)
        privileged = await self._artifact_privileged(user, library["domain"])
        items, total = await self._db.list_object_refs(
            object_id, user_id=user["id"], privileged=privileged, limit=limit,
        )
        items = await self._decorate_refs(items)
        return {"items": items, "truncated": total > len(items)}

    async def delete_object_ref(
        self, object_id: str, ref_id: str, *, user: dict[str, Any],
    ) -> None:
        """删单条引用：对象库域管理员；ref 不属于该对象 → 404。"""
        await self._get_object_checked(object_id, user, require_write=True)
        ref = await self._db.get_object_ref(ref_id)
        if ref is None or ref["object_id"] != object_id:
            raise NotFound(ref_id)
        if not await self._db.delete_object_ref(ref_id):
            raise NotFound(ref_id)

    async def list_artifact_refs(
        self, artifact_id: str, *, target_key: str | None, limit: int,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """制品侧反查（§3.3 第 2 行，评审①）：制品读（不可见 404 反探测）+
        对象域成员双重口径——非对象域成员该 ref 降级为对象不可见占位
        （object_name/ontology_name 置空 + degraded=object_invisible）。"""
        artifact = await self._db.get_ref_artifact(str(artifact_id or "").strip())
        if artifact is None or artifact["kind"] not in ("table", "wiki"):
            raise NotFound(artifact_id)
        privileged = await self._artifact_privileged(user, artifact["domain_id"])
        if not privileged:
            # 制品读口径对齐 artifact_service._assert_role：非特权读者先要求
            # 当前绑定制品域（撤域残留成员行不放行），再 public/owner/成员，
            # 不满足一律 404 反探测。
            if not await self._db.is_domain_member(
                user_id=user["id"], domain=artifact["domain_id"],
            ):
                raise NotFound(artifact_id)
            member_ids = await self._db.user_artifact_member_ids(
                user["id"], [artifact["artifact_id"]],
            )
            if (artifact["visibility"] != "public"
                    and artifact["owner_user_id"] != user["id"]
                    and artifact["artifact_id"] not in member_ids):
                raise NotFound(artifact_id)
        items, total = await self._db.list_artifact_refs(
            artifact["artifact_id"], target_key=target_key, limit=limit,
        )
        # 对象信息降级（同域坍缩下域管理员/站点管理员恒为域成员，仅防御）
        member_of: dict[str, bool] = {}
        for domain in sorted({str(i["library_domain"]) for i in items}):
            member_of[domain] = await self._db.is_domain_member(
                user_id=user["id"], domain=domain,
            )
        for item in items:
            if member_of.get(str(item["library_domain"])):
                item["degraded"] = None
            else:
                item["object_name"] = None
                item["ontology_name"] = None
                item["degraded"] = "object_invisible"
        return {"items": items, "truncated": total > len(items)}

    async def summarize_object_refs(
        self, object_id: str, *, user: dict[str, Any], key_id: str | None = None,
        limit: int = _REF_SUMMARY_LIMIT,
    ) -> dict[str, Any]:
        """neighbors 对象卡的 refs 摘要（§四）：key_id 给定走 MCP 钥匙
        open_artifacts 开放子集（§3.3 第 3 行），否则按当前用户制品可见性
        （第 1 行，与 refs 列表同一套）。degraded 条目 target_title=None
        （评审⑦）。读权限由调用方（neighbors 链路）先行。"""
        if key_id is not None:
            rows, total = await self._db.list_object_refs_for_key(
                object_id, key_id=key_id, limit=limit,
            )
        else:
            obj = await self._db.get_object_brief(object_id)
            if obj is None:
                raise NotFound(object_id)
            library = await self._db.get_library(obj["library_id"])
            if library is None:  # pragma: no cover - 对象在则库在
                raise NotFound(object_id)
            privileged = await self._artifact_privileged(user, library["domain"])
            rows, total = await self._db.list_object_refs(
                object_id, user_id=user["id"], privileged=privileged,
                limit=limit,
            )
        rows = await self._decorate_refs(rows)
        refs = [
            {
                "artifact_id": r["artifact_id"],
                "artifact_title": r["artifact_title"],
                "kind": r["kind"],
                "target_key": r["target_key"],
                "target_title": r["target_title"],
                "artifact_status": r["artifact_status"],
                "degraded": r["degraded"],
            }
            for r in rows
        ]
        return {"artifact_refs": refs, "artifact_refs_truncated": total > len(refs)}

    async def _decorate_refs(
        self, items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """读侧降级装饰（§五矩阵）：整体级永不 degraded；页/行级按 current
        revision 现解析，缺席 → degraded=target_missing（可删保留）。"""
        if not items:
            return items
        titles = await self._db.resolve_ref_targets(items)
        for item in items:
            item["target_title"] = None
            item["degraded"] = None
            if item["target_key"]:
                hit = titles.get((item["artifact_id"], item["target_key"]))
                if hit is None:
                    item["degraded"] = "target_missing"
                else:
                    item["target_title"] = hit
        return items

    async def _artifact_privileged(
        self, user: dict[str, Any], domain: str,
    ) -> bool:
        """制品特权视角：site admin 或该域 domain admin（对同域制品全通）。"""
        if user.get("site_role") == "admin":
            return True
        return await self._db.can_manage_domain(user_id=user["id"], domain=domain)

    # --------------------------------------------------------- 权限/校验

    async def _require_write(self, *, user: dict[str, Any], domain: str) -> None:
        if user.get("site_role") != "admin" and not await self._db.can_manage_domain(
            user_id=user["id"], domain=domain,
        ):
            raise Forbidden("domain_admin_required")

    async def _require_read(self, *, user: dict[str, Any], domain: str) -> None:
        if user.get("site_role") != "admin" \
                and not await self._db.is_domain_member(
                    user_id=user["id"], domain=domain,
                ):
            raise Forbidden("domain_member_required")

    async def _get_library_checked(
        self, library_id: str, user: dict[str, Any],
        require_write: bool = False,
    ) -> dict[str, Any]:
        library = await self._db.get_library(library_id)
        if library is None:
            raise NotFound(library_id)
        if require_write:
            await self._require_write(user=user, domain=library["domain"])
        else:
            await self._require_read(user=user, domain=library["domain"])
        return library

    async def _get_ontology_checked(
        self, ontology_id: str, user: dict[str, Any],
        require_write: bool = False,
    ) -> dict[str, Any]:
        onto = await self._db.get_ontology_brief(ontology_id)
        if onto is None:
            raise NotFound(ontology_id)
        await self._get_library_checked(
            onto["library_id"], user, require_write=require_write,
        )
        return onto

    async def _get_object_checked(
        self, object_id: str, user: dict[str, Any],
        require_write: bool = False,
    ) -> dict[str, Any]:
        obj = await self._db.get_object_brief(object_id)
        if obj is None:
            raise NotFound(object_id)
        await self._get_library_checked(
            obj["library_id"], user, require_write=require_write,
        )
        return obj

    async def _get_ontology_in_library(
        self, ontology_id: str, library_id: str,
    ) -> dict[str, Any]:
        """归属校验（不带权限——调用方已过库级权限门）。"""
        onto = await self._db.get_ontology_brief(ontology_id)
        if onto is None or onto["library_id"] != library_id:
            raise NotFound(ontology_id)
        return onto

    async def _get_object_in_library(
        self, object_id: str, library_id: str,
    ) -> dict[str, Any]:
        obj = await self._db.get_object_brief(object_id)
        if obj is None or obj["library_id"] != library_id:
            raise NotFound(object_id)
        return obj


# ----------------------------------------------------------------- helpers

def _validate_domain(domain: str) -> str:
    """domain 必须非空且在 domain_registry.yaml 合法域内。"""
    if not domain or not domain.strip():
        raise InvalidDomain(domain)
    cleaned = domain.strip()
    try:
        resolve_domain(cleaned)
    except Exception as exc:
        raise InvalidDomain(cleaned) from exc
    return cleaned


def _normalize_name(value: str, max_length: int, label: str) -> str:
    if not isinstance(value, str):
        raise InvalidName(f"{label}必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise InvalidName(f"{label}不能为空")
    if len(normalized) > max_length:
        raise InvalidName(f"{label}长度不能超过 {max_length} 个字符")
    return normalized


def _validate_description(description: str | None) -> str | None:
    if description is None:
        return None
    description = description.strip()
    if not description:
        return None
    if len(description) > _MAX_DESCRIPTION_LENGTH:
        raise InvalidName("描述长度不能超过 2000 个字符")
    return description


def _validate_attribute_defs(
    attributes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """建本体时的初始属性列表：每项 {name, description?, sort_order?}。"""
    if len(attributes) > _MAX_ATTRIBUTE_KEYS:
        raise InvalidName(f"属性数量超过上限 {_MAX_ATTRIBUTE_KEYS}")
    names: set[str] = set()
    for attr in attributes:
        name = _normalize_name(attr.get("name", ""), _MAX_NAME_LENGTH, "属性名称")
        if name in names:
            raise Duplicate(f"初始属性列表中「{name}」重复")
        names.add(name)
    return attributes


async def _validate_attribute_values(
    *, ontology_id: str, attributes: dict[str, Any] | None, db: OntologyDB,
) -> None:
    """对象属性值校验：键 ⊆ 本体属性定义集合；值必须是标量（67号 §5.1）。"""
    if not attributes:
        return
    if not isinstance(attributes, dict):
        raise InvalidAttributeValue("attributes 必须是对象（属性名 → 值）")
    detail = await db.get_ontology(ontology_id)
    defined = (
        {a["name"] for a in detail["attributes"]}
        if detail is not None else set()
    )
    unknown = sorted(set(attributes) - defined)
    if unknown:
        raise UnknownAttribute(
            f"未知属性：{', '.join(unknown)}（须先在本体上定义这些属性）"
        )
    for key, value in attributes.items():
        if not isinstance(value, (str, int, float, bool)):
            raise InvalidAttributeValue(
                f"属性「{key}」的值必须是标量（字符串/数字/布尔），"
                f"得到 {type(value).__name__}"
            )
