"""本体库六表仓储（67号）—— 异步仓储 + 模块级纯函数。

风格契约与 mining/kb/db_wiki.py 一致：TEXT id、参数化 SQL、dict_row、
多语句显式 ``conn.transaction()``、每方法自开连接（一个事务）。

机制权威 = docs/下一阶段/67-本体管理MVP设计-2026-11-17.md §四/§五：
- 删除保护全强 RESTRICT：本层只提供计数查询，拦截判断在 service 层
  （InUse → 409 带计数），FK 裸约束兜底；
- 属性定义改名/删除 → 同事务迁移/清理 semantic_objects.attributes_json 键；
- semantic_objects.library_id 冗余列由写入方（本层方法内部查本体归属）
  保证与 ontology_id 一致，调用方无需双传；
- UniqueViolation 原样上抛，由 service 层翻译为 Duplicate（区分场景文案）。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(obj: Any) -> str:
    if obj is None:
        return "{}"
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class OntologyDB:
    """本体库六表 + 域权限查询（can_manage_domain/is_domain_member）。"""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # ------------------------------------------------------------- 域权限

    async def can_manage_domain(self, *, user_id: str, domain: str) -> bool:
        """Site admin 全域可管；domain admin 管辖本域（同 kb/db.py 语义）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM kb_users u
                   WHERE u.id = %(uid)s AND u.status = 'active'
                     AND (u.site_role = 'admin'
                          OR EXISTS (SELECT 1 FROM user_domains ud
                                     WHERE ud.user_id = u.id
                                       AND ud.domain = %(domain)s
                                       AND ud.domain_role = 'admin'))""",
                {"uid": user_id, "domain": domain},
            )
            return await cur.fetchone() is not None

    async def is_domain_member(self, *, user_id: str, domain: str) -> bool:
        """读门槛：site admin 或 user_domains 绑定该域（任意角色）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM kb_users u
                   WHERE u.id = %(uid)s AND u.status = 'active'
                     AND (u.site_role = 'admin'
                          OR EXISTS (SELECT 1 FROM user_domains ud
                                     WHERE ud.user_id = u.id
                                       AND ud.domain = %(domain)s))""",
                {"uid": user_id, "domain": domain},
            )
            return await cur.fetchone() is not None

    # ------------------------------------------------------------- 本体库

    async def create_library(
        self, *, domain: str, name: str, description: str | None,
        created_by: str,
    ) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO ontology_libraries
                       (id, domain, name, description, created_by,
                        created_at, updated_at)
                   VALUES (%(id)s, %(domain)s, %(name)s, %(description)s,
                           %(created_by)s, %(now)s, %(now)s)
                   RETURNING *""",
                {"id": _new_id(), "domain": domain, "name": name,
                 "description": description, "created_by": created_by,
                 "now": _utcnow()},
            )
            return dict(await cur.fetchone())

    async def list_libraries(
        self, *, domain: str, q: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """SELECT l.*,
                      (SELECT count(*) FROM ontologies o
                        WHERE o.library_id = l.id) AS ontology_count,
                      (SELECT count(*) FROM semantic_objects so
                        WHERE so.library_id = l.id) AS object_count,
                      (SELECT count(*) FROM semantic_relations sr
                        WHERE sr.library_id = l.id) AS relation_count
                 FROM ontology_libraries l
                 WHERE l.domain = %(domain)s"""
        params: dict[str, Any] = {"domain": domain}
        if q:
            sql += " AND l.name ILIKE %(q)s"
            params["q"] = f"%{_escape_like(q)}%"
        sql += " ORDER BY l.created_at DESC"
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return [dict(r) for r in await cur.fetchall()]

    async def get_library(self, library_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT l.*,
                          (SELECT count(*) FROM ontologies o
                            WHERE o.library_id = l.id) AS ontology_count,
                          (SELECT count(*) FROM semantic_objects so
                            WHERE so.library_id = l.id) AS object_count,
                          (SELECT count(*) FROM semantic_relations sr
                            WHERE sr.library_id = l.id) AS relation_count
                   FROM ontology_libraries l WHERE l.id = %s""",
                (library_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_library(
        self, library_id: str, *, name: str | None, description: str | None,
    ) -> dict[str, Any] | None:
        sets = ["updated_at = %(now)s"]
        params: dict[str, Any] = {"id": library_id, "now": _utcnow()}
        if name is not None:
            sets.append("name = %(name)s")
            params["name"] = name
        if description is not None:
            sets.append("description = %(description)s")
            params["description"] = description
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE ontology_libraries SET {', '.join(sets)} "
                "WHERE id = %(id)s RETURNING *",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_library(self, library_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM ontology_libraries WHERE id = %s", (library_id,),
            )
            return cur.rowcount > 0

    async def count_ontologies(self, library_id: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM ontologies WHERE library_id = %s",
                (library_id,),
            )
            return int((await cur.fetchone())["n"])

    # ------------------------------------------------------------- 本体

    async def create_ontology(
        self, *, library_id: str, name: str, description: str | None,
        created_by: str, attributes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """本体 + 初始属性列表同事务创建（attributes 可为空）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """INSERT INTO ontologies
                           (id, library_id, name, description, created_by,
                            created_at, updated_at)
                       VALUES (%(id)s, %(library_id)s, %(name)s,
                               %(description)s, %(created_by)s, %(now)s, %(now)s)
                       RETURNING *""",
                    {"id": _new_id(), "library_id": library_id, "name": name,
                     "description": description, "created_by": created_by,
                     "now": _utcnow()},
                )
                row = dict(await cur.fetchone())
                if attributes:
                    await _insert_attributes(conn, row["id"], attributes)
                return row

    async def list_ontologies(self, library_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT o.*,
                          (SELECT count(*) FROM ontology_attributes a
                            WHERE a.ontology_id = o.id) AS attribute_count,
                          (SELECT count(*) FROM semantic_objects so
                            WHERE so.ontology_id = o.id) AS object_count,
                          (SELECT count(*) FROM ontology_relations r
                            WHERE r.source_ontology_id = o.id
                                OR r.target_ontology_id = o.id) AS relation_ref_count
                   FROM ontologies o
                   WHERE o.library_id = %s
                   ORDER BY o.created_at""",
                (library_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_ontology(self, ontology_id: str) -> dict[str, Any] | None:
        """本体详情：本体 + 属性定义（sort_order 序）+ 参与的关系类型。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM ontologies WHERE id = %s", (ontology_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            result = dict(row)
            cur = await conn.execute(
                """SELECT id, name, description, sort_order, created_at
                   FROM ontology_attributes
                   WHERE ontology_id = %s
                   ORDER BY sort_order, created_at""",
                (ontology_id,),
            )
            result["attributes"] = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                """SELECT id, name, description, source_ontology_id,
                          target_ontology_id, created_at
                   FROM ontology_relations
                   WHERE source_ontology_id = %s OR target_ontology_id = %s
                   ORDER BY created_at""",
                (ontology_id, ontology_id),
            )
            result["ontology_relations"] = [dict(r) for r in await cur.fetchall()]
            return result

    async def get_ontology_brief(self, ontology_id: str) -> dict[str, Any] | None:
        """仅本体行（权限/归属判断用轻量版）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM ontologies WHERE id = %s", (ontology_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_ontology(
        self, ontology_id: str, *, name: str | None, description: str | None,
    ) -> dict[str, Any] | None:
        sets = ["updated_at = %(now)s"]
        params: dict[str, Any] = {"id": ontology_id, "now": _utcnow()}
        if name is not None:
            sets.append("name = %(name)s")
            params["name"] = name
        if description is not None:
            sets.append("description = %(description)s")
            params["description"] = description
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE ontologies SET {', '.join(sets)} "
                "WHERE id = %(id)s RETURNING *",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_ontology(self, ontology_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM ontologies WHERE id = %s", (ontology_id,),
            )
            return cur.rowcount > 0

    async def count_objects_of_ontology(self, ontology_id: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM semantic_objects WHERE ontology_id = %s",
                (ontology_id,),
            )
            return int((await cur.fetchone())["n"])

    async def count_relations_referencing(
        self, ontology_id: str,
    ) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT count(*) AS n FROM ontology_relations
                   WHERE source_ontology_id = %s OR target_ontology_id = %s""",
                (ontology_id, ontology_id),
            )
            return int((await cur.fetchone())["n"])

    # ------------------------------------------------------------- 属性

    async def add_attribute(
        self, *, ontology_id: str, name: str, description: str | None,
        sort_order: int | None,
    ) -> dict[str, Any]:
        if sort_order is None:
            sort_order = await self._next_sort_order(ontology_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO ontology_attributes
                       (id, ontology_id, name, description, sort_order, created_at)
                   VALUES (%(id)s, %(ontology_id)s, %(name)s, %(description)s,
                           %(sort_order)s, %(now)s)
                   RETURNING *""",
                {"id": _new_id(), "ontology_id": ontology_id, "name": name,
                 "description": description, "sort_order": sort_order,
                 "now": _utcnow()},
            )
            return dict(await cur.fetchone())

    async def _next_sort_order(self, ontology_id: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT coalesce(max(sort_order), -1) + 1 AS next
                   FROM ontology_attributes WHERE ontology_id = %s""",
                (ontology_id,),
            )
            return int((await cur.fetchone())["next"])

    async def get_attribute(self, attribute_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM ontology_attributes WHERE id = %s", (attribute_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_attribute(
        self, attribute_id: str, *, name: str | None, description: str | None,
        sort_order: int | None,
    ) -> dict[str, Any] | None:
        """改名时同事务迁移该本体全部对象的属性键（保留值，67号 §4.9）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT * FROM ontology_attributes WHERE id = %s",
                    (attribute_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                old = dict(row)
                sets: list[str] = []
                params: dict[str, Any] = {"id": attribute_id}
                if name is not None and name != old["name"]:
                    sets.append("name = %(name)s")
                    params["name"] = name
                if description is not None:
                    sets.append("description = %(description)s")
                    params["description"] = description
                if sort_order is not None:
                    sets.append("sort_order = %(sort_order)s")
                    params["sort_order"] = sort_order
                if sets:
                    await conn.execute(
                        f"UPDATE ontology_attributes SET {', '.join(sets)} "
                        "WHERE id = %(id)s",
                        params,
                    )
                if name is not None and name != old["name"]:
                    await conn.execute(
                        """UPDATE semantic_objects
                           SET attributes_json =
                                   attributes_json - %(old)s::text
                               || jsonb_build_object(
                                       %(new)s::text,
                                       attributes_json -> %(old)s::text),
                               updated_at = %(now)s
                           WHERE ontology_id = %(oid)s
                             AND attributes_json ? %(old)s::text""",
                        {"old": old["name"], "new": name,
                         "oid": old["ontology_id"], "now": _utcnow()},
                    )
                cur = await conn.execute(
                    "SELECT * FROM ontology_attributes WHERE id = %s",
                    (attribute_id,),
                )
                return dict(await cur.fetchone())

    async def delete_attribute(self, attribute_id: str) -> dict[str, Any] | None:
        """删除属性定义 + 同事务清理该本体全部对象上的对应键（67号 §4.8）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT * FROM ontology_attributes WHERE id = %s",
                    (attribute_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                attr = dict(row)
                await conn.execute(
                    "DELETE FROM ontology_attributes WHERE id = %s",
                    (attribute_id,),
                )
                await conn.execute(
                    """UPDATE semantic_objects
                       SET attributes_json = attributes_json - %(name)s::text,
                           updated_at = %(now)s
                       WHERE ontology_id = %(oid)s
                         AND attributes_json ? %(name)s::text""",
                    {"name": attr["name"], "oid": attr["ontology_id"],
                     "now": _utcnow()},
                )
                return attr

    # ------------------------------------------------------------- 本体关系

    async def create_ontology_relation(
        self, *, library_id: str, name: str, description: str | None,
        source_ontology_id: str, target_ontology_id: str, created_by: str,
    ) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO ontology_relations
                       (id, library_id, name, description,
                        source_ontology_id, target_ontology_id,
                        created_by, created_at, updated_at)
                   VALUES (%(id)s, %(library_id)s, %(name)s, %(description)s,
                           %(src)s, %(tgt)s, %(created_by)s, %(now)s, %(now)s)
                   RETURNING *""",
                {"id": _new_id(), "library_id": library_id, "name": name,
                 "description": description, "src": source_ontology_id,
                 "tgt": target_ontology_id, "created_by": created_by,
                 "now": _utcnow()},
            )
            return dict(await cur.fetchone())

    async def list_ontology_relations(
        self, library_id: str,
    ) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT r.*, so.name AS source_ontology_name,
                          to_n.name AS target_ontology_name,
                          (SELECT count(*) FROM semantic_relations sr
                            WHERE sr.ontology_relation_id = r.id) AS instance_count
                   FROM ontology_relations r
                   JOIN ontologies so ON so.id = r.source_ontology_id
                   JOIN ontologies to_n ON to_n.id = r.target_ontology_id
                   WHERE r.library_id = %s
                   ORDER BY r.created_at""",
                (library_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_ontology_relation(
        self, relation_id: str,
    ) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM ontology_relations WHERE id = %s", (relation_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_ontology_relation(
        self, relation_id: str, *, name: str | None, description: str | None,
        source_ontology_id: str | None, target_ontology_id: str | None,
    ) -> dict[str, Any] | None:
        sets = ["updated_at = %(now)s"]
        params: dict[str, Any] = {"id": relation_id, "now": _utcnow()}
        if name is not None:
            sets.append("name = %(name)s")
            params["name"] = name
        if description is not None:
            sets.append("description = %(description)s")
            params["description"] = description
        if source_ontology_id is not None:
            sets.append("source_ontology_id = %(src)s")
            params["src"] = source_ontology_id
        if target_ontology_id is not None:
            sets.append("target_ontology_id = %(tgt)s")
            params["tgt"] = target_ontology_id
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE ontology_relations SET {', '.join(sets)} "
                "WHERE id = %(id)s RETURNING *",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_ontology_relation(self, relation_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM ontology_relations WHERE id = %s", (relation_id,),
            )
            return cur.rowcount > 0

    async def count_semantic_relations_of(
        self, ontology_relation_id: str,
    ) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM semantic_relations "
                "WHERE ontology_relation_id = %s",
                (ontology_relation_id,),
            )
            return int((await cur.fetchone())["n"])

    # ------------------------------------------------------------- 语义对象

    async def create_object(
        self, *, ontology_id: str, name: str,
        attributes: dict[str, Any], created_by: str, source: str = "manual",
    ) -> dict[str, Any]:
        """library_id 由本体归属查得（写入方保证一致性）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT library_id FROM ontologies WHERE id = %s",
                    (ontology_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return {}  # 调用方（service）已先行校验存在性
                cur = await conn.execute(
                    """INSERT INTO semantic_objects
                           (id, library_id, ontology_id, name,
                            attributes_json, source, created_by,
                            created_at, updated_at)
                       VALUES (%(id)s, %(library_id)s, %(ontology_id)s,
                               %(name)s, %(attrs)s::jsonb, %(source)s,
                               %(created_by)s, %(now)s, %(now)s)
                       RETURNING *""",
                    {"id": _new_id(), "library_id": row["library_id"],
                     "ontology_id": ontology_id, "name": name,
                     "attrs": _json(attributes), "source": source,
                     "created_by": created_by, "now": _utcnow()},
                )
                return dict(await cur.fetchone())

    async def list_objects(
        self, *, library_id: str, ontology_id: str | None, q: str | None,
        limit: int, offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        where = ["so.library_id = %(library_id)s"]
        params: dict[str, Any] = {
            "library_id": library_id, "limit": limit, "offset": offset,
        }
        if ontology_id:
            where.append("so.ontology_id = %(ontology_id)s")
            params["ontology_id"] = ontology_id
        if q:
            where.append("so.name ILIKE %(q)s")
            params["q"] = f"%{_escape_like(q)}%"
        where_sql = " AND ".join(where)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT so.*, o.name AS ontology_name
                    FROM semantic_objects so
                    JOIN ontologies o ON o.id = so.ontology_id
                    WHERE {where_sql}
                    ORDER BY so.created_at DESC, so.id
                    LIMIT %(limit)s OFFSET %(offset)s""",
                params,
            )
            items = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                f"""SELECT count(*) AS n FROM semantic_objects so
                    WHERE {where_sql}""",
                {k: v for k, v in params.items()
                 if k not in ("limit", "offset")},
            )
            total = int((await cur.fetchone())["n"])
            return items, total

    async def get_object(self, object_id: str) -> dict[str, Any] | None:
        """对象详情：本体信息 + 属性定义对齐 + 邻接边计数。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT so.*, o.name AS ontology_name, o.library_id
                   FROM semantic_objects so
                   JOIN ontologies o ON o.id = so.ontology_id
                   WHERE so.id = %s""",
                (object_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            result = dict(row)
            cur = await conn.execute(
                """SELECT id, name, description, sort_order FROM ontology_attributes
                   WHERE ontology_id = %s ORDER BY sort_order, created_at""",
                (result["ontology_id"],),
            )
            result["attribute_defs"] = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                """SELECT
                     (SELECT count(*) FROM semantic_relations
                       WHERE source_object_id = %s) AS outgoing_count,
                     (SELECT count(*) FROM semantic_relations
                       WHERE target_object_id = %s) AS incoming_count""",
                (object_id, object_id),
            )
            counts = dict(await cur.fetchone())
            result.update(counts)
            return result

    async def get_object_brief(self, object_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM semantic_objects WHERE id = %s", (object_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_object(
        self, object_id: str, *, name: str | None,
        attributes: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        sets = ["updated_at = %(now)s"]
        params: dict[str, Any] = {"id": object_id, "now": _utcnow()}
        if name is not None:
            sets.append("name = %(name)s")
            params["name"] = name
        if attributes is not None:
            sets.append("attributes_json = %(attrs)s::jsonb")
            params["attrs"] = _json(attributes)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE semantic_objects SET {', '.join(sets)} "
                "WHERE id = %(id)s RETURNING *",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_object(self, object_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM semantic_objects WHERE id = %s", (object_id,),
            )
            return cur.rowcount > 0

    async def count_relations_for_object(self, object_id: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT count(*) AS n FROM semantic_relations
                   WHERE source_object_id = %s OR target_object_id = %s""",
                (object_id, object_id),
            )
            return int((await cur.fetchone())["n"])

    async def list_object_names(
        self, ontology_id: str, names: list[str],
    ) -> set[str]:
        """批量导入 duplicate_existing 预检：返回 names 中已存在于该本体的
        对象名集合（单查询，ANY 数组）。"""
        if not names:
            return set()
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT name FROM semantic_objects
                   WHERE ontology_id = %s AND name = ANY(%s)""",
                (ontology_id, sorted(set(names))),
            )
            return {str(r["name"]) for r in await cur.fetchall()}

    async def batch_insert_objects(
        self, *, ontology_id: str,
        rows: list[tuple[str, dict[str, Any]]],
        created_by: str, source: str = "import",
    ) -> int:
        """批量导入落库（67号 §7.2 Step 2）：executemany 单事务全有或全无，
        library_id 由本体归属查得（与 create_object 同口径）。rows =
        [(name, attributes), ...]；校验全部在 service 层完成，本层只写。"""
        if not rows:
            return 0
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT library_id FROM ontologies WHERE id = %s",
                    (ontology_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return 0  # 调用方（service）已先行校验存在性
                now = _utcnow()
                payload = [
                    (_new_id(), row["library_id"], ontology_id, name,
                     _json(attributes), source, created_by, now, now)
                    for name, attributes in rows
                ]
                await conn.cursor().executemany(
                    """INSERT INTO semantic_objects
                           (id, library_id, ontology_id, name,
                            attributes_json, source, created_by,
                            created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)""",
                    payload,
                )
                return len(payload)

    # ------------------------------------------------------------- 语义关系

    async def create_semantic_relation(
        self, *, library_id: str, ontology_relation_id: str,
        source_object_id: str, target_object_id: str, created_by: str,
    ) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO semantic_relations
                       (id, library_id, ontology_relation_id,
                        source_object_id, target_object_id, created_by,
                        created_at)
                   VALUES (%(id)s, %(library_id)s, %(orel)s, %(src)s, %(tgt)s,
                           %(created_by)s, %(now)s)
                   RETURNING *""",
                {"id": _new_id(), "library_id": library_id,
                 "orel": ontology_relation_id, "src": source_object_id,
                 "tgt": target_object_id, "created_by": created_by,
                 "now": _utcnow()},
            )
            return dict(await cur.fetchone())

    async def list_semantic_relations(
        self, *, library_id: str, source_object_id: str | None,
        target_object_id: str | None, ontology_relation_id: str | None,
        limit: int, offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        where = ["sr.library_id = %(library_id)s"]
        params: dict[str, Any] = {
            "library_id": library_id, "limit": limit, "offset": offset,
        }
        if source_object_id:
            where.append("sr.source_object_id = %(src)s")
            params["src"] = source_object_id
        if target_object_id:
            where.append("sr.target_object_id = %(tgt)s")
            params["tgt"] = target_object_id
        if ontology_relation_id:
            where.append("sr.ontology_relation_id = %(orel)s")
            params["orel"] = ontology_relation_id
        where_sql = " AND ".join(where)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT sr.*, r.name AS relation_name,
                           so.name AS source_object_name,
                           tso.name AS target_object_name,
                           so.ontology_id AS source_ontology_id,
                           tso.ontology_id AS target_ontology_id
                    FROM semantic_relations sr
                    JOIN ontology_relations r ON r.id = sr.ontology_relation_id
                    JOIN semantic_objects so ON so.id = sr.source_object_id
                    JOIN semantic_objects tso ON tso.id = sr.target_object_id
                    WHERE {where_sql}
                    ORDER BY sr.created_at DESC, sr.id
                    LIMIT %(limit)s OFFSET %(offset)s""",
                params,
            )
            items = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                f"""SELECT count(*) AS n FROM semantic_relations sr
                    WHERE {where_sql}""",
                {k: v for k, v in params.items()
                 if k not in ("limit", "offset")},
            )
            total = int((await cur.fetchone())["n"])
            return items, total

    async def get_semantic_relation(
        self, relation_id: str,
    ) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM semantic_relations WHERE id = %s", (relation_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_semantic_relation(self, relation_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM semantic_relations WHERE id = %s", (relation_id,),
            )
            return cur.rowcount > 0

    # ------------------------------------------- 批量实例化（67号 §6.8/§八）

    async def get_objects_by_ids(self, object_ids: list[str]) -> dict[str, dict]:
        """instantiate 端点解析「已有对象」引用：id → {library_id,
        ontology_id, name}（单查询；不存在/软约束外的 id 直接缺席）。"""
        if not object_ids:
            return {}
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, library_id, ontology_id, name
                   FROM semantic_objects WHERE id = ANY(%s)""",
                (sorted(set(object_ids)),),
            )
            return {str(r["id"]): dict(r) for r in await cur.fetchall()}

    async def edge_exists_batch(
        self, library_id: str,
        combos: list[tuple[str, str, str]],
    ) -> set[tuple[str, str, str]]:
        """存量重边预检：combos = [(ontology_relation_id, source_id,
        target_id)]（两端均已存在的组合；含新端的组合不可能与存量重边）。
        命中唯一索引列组，OR 展开单查询。"""
        if not combos:
            return set()
        clauses = " OR ".join(
            "(ontology_relation_id = %s AND source_object_id = %s"
            " AND target_object_id = %s)" for _ in combos)
        params = [library_id]
        for combo in combos:
            params.extend(combo)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT ontology_relation_id, source_object_id, target_object_id
                    FROM semantic_relations
                    WHERE library_id = %s AND ({clauses})""",
                params,
            )
            return {
                (str(r["ontology_relation_id"]), str(r["source_object_id"]),
                 str(r["target_object_id"]))
                for r in await cur.fetchall()
            }

    async def instantiate_batch(
        self, *, ontology_id: str,
        objects: list[tuple[str, dict[str, Any]]],
        relations: list[tuple[str, Any, Any]],
        created_by: str, source: str = "extract",
    ) -> dict[str, Any]:
        """批量实例化落库（§6.8）：一批对象 + 一批语义关系单事务全有或全无。

        relations = (ontology_relation_id, source_ref, target_ref)，ref 为
        int（本批下标，service 层校验过界）或 str（库内已有对象 id）；对象
        id 在本层生成（executemany 无 RETURNING，先发 id 供关系引用）。
        校验全部在 service 层完成，本层只写。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT library_id FROM ontologies WHERE id = %s",
                    (ontology_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return {"object_ids": [], "relation_ids": []}
                library_id = row["library_id"]
                now = _utcnow()
                object_ids = [_new_id() for _ in objects]

                def _ref_id(ref: Any) -> str:
                    return object_ids[ref] if isinstance(ref, int) else ref

                if objects:
                    await conn.cursor().executemany(
                        """INSERT INTO semantic_objects
                               (id, library_id, ontology_id, name,
                                attributes_json, source, created_by,
                                created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)""",
                        [
                            (oid, library_id, ontology_id, name,
                             _json(attributes), source, created_by, now, now)
                            for oid, (name, attributes) in zip(object_ids, objects)
                        ],
                    )
                relation_ids = [_new_id() for _ in relations]
                if relations:
                    await conn.cursor().executemany(
                        """INSERT INTO semantic_relations
                               (id, library_id, ontology_relation_id,
                                source_object_id, target_object_id, created_by,
                                created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        [
                            (rid, library_id, orel,
                             _ref_id(src), _ref_id(tgt), created_by, now)
                            for rid, (orel, src, tgt)
                            in zip(relation_ids, relations)
                        ],
                    )
                return {
                    "object_ids": object_ids,
                    "relation_ids": relation_ids,
                }

    # ------------------------------------------------------------- 图查询

    async def get_library_schema(self, library_id: str) -> dict[str, Any]:
        """schema 全量：本体（含属性）+ 本体关系（两端本体名）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT o.*,
                          (SELECT count(*) FROM semantic_objects so
                            WHERE so.ontology_id = o.id) AS object_count
                   FROM ontologies o WHERE o.library_id = %s
                   ORDER BY o.created_at""",
                (library_id,),
            )
            ontologies = [dict(r) for r in await cur.fetchall()]
            for onto in ontologies:
                cur = await conn.execute(
                    """SELECT id, name, description, sort_order
                       FROM ontology_attributes
                       WHERE ontology_id = %s ORDER BY sort_order, created_at""",
                    (onto["id"],),
                )
                onto["attributes"] = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                """SELECT r.id, r.name, r.description,
                          r.source_ontology_id, r.target_ontology_id,
                          so.name AS source_ontology_name,
                          to_n.name AS target_ontology_name
                   FROM ontology_relations r
                   JOIN ontologies so ON so.id = r.source_ontology_id
                   JOIN ontologies to_n ON to_n.id = r.target_ontology_id
                   WHERE r.library_id = %s ORDER BY r.created_at""",
                (library_id,),
            )
            relations = [dict(r) for r in await cur.fetchall()]
            return {"ontologies": ontologies, "relations": relations}

    async def get_library_graph(
        self, library_id: str, *, ontology_ids: list[str] | None, limit: int,
    ) -> dict[str, Any]:
        """库内实例图全量（68号 §2.2）：对象节点 + 关系边。

        节点护栏：limit 截断时 truncated=true；total_objects 是过滤前的
        库内对象总数（前端横幅提示用）。ontology_ids 过滤在 SQL 内完成，
        边按两端均在节点集内收敛（图上不出现悬空半边）。
        """
        params: dict[str, Any] = {"library_id": library_id}
        onto_filter = ""
        if ontology_ids:
            onto_filter = " AND so.ontology_id = ANY(%(ontology_ids)s)"
            params["ontology_ids"] = ontology_ids
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM semantic_objects"
                " WHERE library_id = %(library_id)s",
                {"library_id": library_id},
            )
            total_objects = int((await cur.fetchone())["n"])
            cur = await conn.execute(
                f"""SELECT count(*) AS n FROM semantic_objects so
                    WHERE so.library_id = %(library_id)s{onto_filter}""",
                params,
            )
            matching = int((await cur.fetchone())["n"])
            cur = await conn.execute(
                f"""SELECT so.id, so.name, so.ontology_id,
                           o.name AS ontology_name
                    FROM semantic_objects so
                    JOIN ontologies o ON o.id = so.ontology_id
                    WHERE so.library_id = %(library_id)s{onto_filter}
                    ORDER BY so.created_at, so.id
                    LIMIT %(limit)s""",
                {**params, "limit": limit},
            )
            nodes = [dict(r) for r in await cur.fetchall()]
            if not nodes:
                return {
                    "nodes": [], "edges": [],
                    "total_objects": total_objects, "truncated": False,
                }
            cur = await conn.execute(
                """SELECT sr.id, sr.source_object_id AS source,
                          sr.target_object_id AS target,
                          sr.ontology_relation_id,
                          r.name AS relation_name
                   FROM semantic_relations sr
                   JOIN ontology_relations r ON r.id = sr.ontology_relation_id
                   WHERE sr.library_id = %(library_id)s""",
                {"library_id": library_id},
            )
            all_edges = [dict(r) for r in await cur.fetchall()]
            node_ids = {n["id"] for n in nodes}
            edges = [e for e in all_edges
                     if e["source"] in node_ids and e["target"] in node_ids]
            return {
                "nodes": nodes, "edges": edges,
                "total_objects": total_objects,
                "truncated": matching > len(nodes),
            }

    async def get_object_neighbors(self, object_id: str) -> dict[str, Any] | None:
        """一度邻接：出边/入边各带关系类型名与对端对象摘要。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id FROM semantic_objects WHERE id = %s", (object_id,),
            )
            if await cur.fetchone() is None:
                return None
            cur = await conn.execute(
                """SELECT sr.id, sr.ontology_relation_id, r.name AS relation_name,
                          tso.id AS other_id, tso.name AS other_name,
                          tso.ontology_id AS other_ontology_id,
                          o.name AS other_ontology_name
                   FROM semantic_relations sr
                   JOIN ontology_relations r ON r.id = sr.ontology_relation_id
                   JOIN semantic_objects tso ON tso.id = sr.target_object_id
                   JOIN ontologies o ON o.id = tso.ontology_id
                   WHERE sr.source_object_id = %s
                   ORDER BY sr.created_at""",
                (object_id,),
            )
            outgoing = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                """SELECT sr.id, sr.ontology_relation_id, r.name AS relation_name,
                          sso.id AS other_id, sso.name AS other_name,
                          sso.ontology_id AS other_ontology_id,
                          o.name AS other_ontology_name
                   FROM semantic_relations sr
                   JOIN ontology_relations r ON r.id = sr.ontology_relation_id
                   JOIN semantic_objects sso ON sso.id = sr.source_object_id
                   JOIN ontologies o ON o.id = sso.ontology_id
                   WHERE sr.target_object_id = %s
                   ORDER BY sr.created_at""",
                (object_id,),
            )
            incoming = [dict(r) for r in await cur.fetchall()]
            return {"outgoing": outgoing, "incoming": incoming}


# ------------------------------------------------------------- 内部 helper

async def _insert_attributes(conn: Any, ontology_id: str,
                             attributes: list[dict[str, Any]]) -> None:
    """批量插入属性定义（建本体时初始列表；sort_order 缺省按下标）。

    路由层 pydantic 会 dump 出显式 ``sort_order=None``（键存在值为
    None）——``.get(key, default)`` 取不到默认值，须按 None 兜底，否则
    NOT NULL 列插空（1.1.30 生产冒烟暴露）。
    """
    rows = [
        (_new_id(), ontology_id, a["name"], a.get("description"),
         a["sort_order"] if a.get("sort_order") is not None else idx,
         _utcnow())
        for idx, a in enumerate(attributes)
    ]
    await conn.cursor().executemany(
        """INSERT INTO ontology_attributes
               (id, ontology_id, name, description, sort_order, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        rows,
    )


def _escape_like(value: str) -> str:
    """Escape PostgreSQL LIKE metacharacters for a literal user search term."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
