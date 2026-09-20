"""Read service for the published surface（52号 P6）。

与 ``KnowledgeProductService`` 分开：那个管制作与治理（草稿、人审、发布），这个
只管**已发布内容怎么被读到**。分开的理由不是文件大小——是权限与可见性不同：
制作面认负责人身份、能看草稿；消费面谁都能读已发布的那一份，且**看不到草稿**。
"""
from __future__ import annotations

from typing import Any, Sequence

from knowledge_mining.mining.knowledge_product import consume
from knowledge_mining.mining.knowledge_product.content_store import ArtifactContentStore
from knowledge_mining.mining.knowledge_product.consume import (
    ConsumeRejected,
    SearchResult,
)


class ProductConsumeService:
    def __init__(self, repo: Any, content: ArtifactContentStore) -> None:
        self._repo = repo
        self._content = content

    # ---------------------------------------------------------------- 入口

    async def catalog(self) -> list[dict[str, Any]]:
        """已发布制品清单——不知道任何 ID 时的入口。

        只列已发布的：草稿是负责人的在制品，不是对外内容。
        """
        return [
            {
                "product_id": row["id"],
                "name": row["name"],
                "type": row["product_type"],
                "purpose": row.get("purpose"),
                "owner": row["owner"],
                "released_revision": row["released_revision"],
                "object_count": row.get("object_count", 0),
                "updated_at": row.get("updated_at"),
            }
            for row in await self._repo.list_published_products()
        ]

    async def outline(self, product_id: str) -> dict[str, Any]:
        """一个制品的对象清单——比 catalog 深一层，比全量取正文轻得多。"""
        rows = await self._repo.list_published_objects(product_id=product_id)
        if not rows:
            raise ConsumeRejected(
                consume.OBJECT_NOT_FOUND,
                f"制品 {product_id!r} 没有已发布内容（可能还在草稿）",
            )
        return {
            "product_id": product_id,
            "product_name": rows[0].get("product_name") or product_id,
            "revision": rows[0].get("revision_no"),
            "objects": [
                {
                    "id": row["object_id"],
                    "type": row["type"],
                    "layer": row["layer"],
                    "name": row.get("name"),
                }
                for row in rows
            ],
        }

    # ---------------------------------------------------------------- 取原文

    async def fetch(self, object_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """批量取对象 md。**这是权威原文的唯一来源。**

        每个 ID 恰好一个条目：单项失败不阻断整批——一个 ID 不存在不该让另外九个
        也白取。
        """
        wanted = consume.normalize_ids(object_ids)
        rows = {r["object_id"]: r for r in await self._repo.get_published_objects(wanted)}

        out: dict[str, dict[str, Any]] = {}
        for object_id in wanted:
            row = rows.get(object_id)
            if row is None:
                out[object_id] = {
                    "ok": False,
                    "id": object_id,
                    "error_code": consume.OBJECT_NOT_FOUND,
                    "error": "对象不存在，或它所属的制品尚未发布",
                }
                continue
            try:
                raw_md = await self._content.get(row["storage_object_id"])
            except (KeyError, ValueError) as exc:
                out[object_id] = {
                    "ok": False,
                    "id": object_id,
                    "error_code": consume.OBJECT_NOT_FOUND,
                    "error": f"正文不可读：{exc}",
                }
                continue

            out[object_id] = {
                "ok": True,
                "id": object_id,
                "product_id": row["product_id"],
                "product_name": row.get("product_name") or row["product_id"],
                "type": row["type"],
                "layer": row["layer"],
                "name": row.get("name"),
                "revision": row.get("revision_no"),
                "md": raw_md,
                # 正文里的 [[ID]]，直接给出下钻入口——不用再搜一次
                "references": consume.references_of(raw_md),
            }

        consume.guard_response_size(out)
        return out

    # ---------------------------------------------------------------- 搜索

    async def search(
        self,
        terms: Sequence[str],
        *,
        match: str = consume.MATCH_ANY,
        product_id: str | None = None,
        type_name: str | None = None,
        page: int = 1,
        size: int = consume.DEFAULT_PAGE_SIZE,
    ) -> SearchResult:
        """不知道 ID 时定位候选。

        ⚠️ 命中里的 ``snippets`` **不是权威依据**——选定候选后必须 ``fetch`` 取
        完整正文再引用。
        """
        if match not in (consume.MATCH_ANY, consume.MATCH_ALL):
            raise ConsumeRejected(
                consume.INVALID_ARGUMENT, f"match 只能是 any 或 all，收到 {match!r}"
            )
        if page < 1:
            raise ConsumeRejected(consume.INVALID_ARGUMENT, "page 从 1 开始")
        if not 1 <= size <= consume.MAX_PAGE_SIZE:
            raise ConsumeRejected(
                consume.INVALID_ARGUMENT, f"size 取 1~{consume.MAX_PAGE_SIZE}"
            )

        normalized = consume.normalize_terms(terms)
        rows = await self._repo.list_published_objects(
            product_id=product_id, type_name=type_name
        )

        hits = consume.rank_hits(rows, normalized, match)
        # 每词命中数按 any 口径单独算——用于零结果时判断是哪个词没救
        term_counts = {
            term: len(consume.rank_hits(rows, [term], consume.MATCH_ANY))
            for term in normalized
        }

        start = (page - 1) * size
        return SearchResult(
            terms=normalized,
            match=match,
            total=len(hits),
            page=page,
            size=size,
            hits=hits[start:start + size],
            facets=consume.build_facets(hits),
            term_counts=term_counts,
            recovery=(
                consume.recovery_codes(
                    normalized, match, term_counts,
                    filtered=bool(product_id or type_name),
                )
                if not hits else []
            ),
        )
