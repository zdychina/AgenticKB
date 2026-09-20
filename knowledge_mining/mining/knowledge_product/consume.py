"""Consumption surface: 读**已发布**制品（52号 P6）。

48号 §七：制品有三个消费入口——人读网页、业务 Agent 经 MCP、其他制品引用它。
三者**使用同一份发布内容**，遵守同样的权限规则。所以这一层只认已发布修订：

- 草稿失败或新版本未发布时，旧的可用版本继续服务；
- 发布产生明确版本，网页和 MCP 必须读到同一个发布结果。

调用纪律照搬 newsfc（已在 2 万+ 对象上跑过）：

1. **搜索结果的 snippet 不是权威依据**——选定候选后必须 ``fetch`` 取原文。摘要
   是为了让 Agent 挑对对象，不是为了让它直接拿去回答。
2. 批量取有上限，**单项失败不阻断整批**：一个 ID 不存在不该让另外九个也白取。
3. 响应总量有上限，超限整单失败并指引分批——宁可让 Agent 多跑一轮，也不要塞爆
   它的上下文。
4. 零结果时给**恢复码**，而不是一个空数组让 Agent 干瞪眼。
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Sequence

# ---- 护栏 -------------------------------------------------------------------
MAX_FETCH_IDS = 50
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TERMS = 10
MAX_TERM_LEN = 80
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20

# ---- 稳定错误码（对外契约，别改名也别复用旧义）------------------------------
INVALID_ARGUMENT = "INVALID_ARGUMENT"
OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
RESULT_TOO_LARGE = "RESULT_TOO_LARGE"

# ---- 零结果恢复码 -----------------------------------------------------------
USE_MATCH_ANY = "USE_MATCH_ANY"
REMOVE_OR_REPHRASE_TERM = "REMOVE_OR_REPHRASE_TERM"
RELAX_FILTERS = "RELAX_FILTERS"

MATCH_ANY = "any"
MATCH_ALL = "all"

_WIKILINK = re.compile(r"\[\[([^\]\|]+?)(?:\|[^\]]*)?\]\]")


class ConsumeRejected(Exception):
    """入参不合法。``code`` 是上面的稳定错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_term(term: str) -> str:
    """NFKC + casefold——中英混排的关键词要能匹配上全角/半角、大小写差异。"""
    return unicodedata.normalize("NFKC", term).casefold().strip()


def normalize_terms(terms: Sequence[str]) -> list[str]:
    if not terms:
        raise ConsumeRejected(INVALID_ARGUMENT, "terms 至少要一个关键词")
    if len(terms) > MAX_TERMS:
        raise ConsumeRejected(INVALID_ARGUMENT, f"terms 最多 {MAX_TERMS} 个")

    out: list[str] = []
    for raw in terms:
        term = normalize_term(str(raw))
        if not term:
            raise ConsumeRejected(INVALID_ARGUMENT, "关键词不能为空")
        if len(term) > MAX_TERM_LEN:
            raise ConsumeRejected(INVALID_ARGUMENT, f"关键词最长 {MAX_TERM_LEN} 字符")
        if term not in out:
            out.append(term)
    return out


def normalize_ids(ids: Sequence[str]) -> list[str]:
    """去重保序。重复 ID 不该重复计入上限，也不该在返回里出现两次。"""
    if not ids:
        raise ConsumeRejected(INVALID_ARGUMENT, "ids 至少要一个对象 ID")
    out: list[str] = []
    for raw in ids:
        object_id = str(raw).strip()
        if object_id and object_id not in out:
            out.append(object_id)
    if not out:
        raise ConsumeRejected(INVALID_ARGUMENT, "ids 里没有有效的对象 ID")
    if len(out) > MAX_FETCH_IDS:
        raise ConsumeRejected(
            INVALID_ARGUMENT,
            f"一次最多取 {MAX_FETCH_IDS} 个对象（去重后 {len(out)} 个），请分批",
        )
    return out


@dataclass(frozen=True)
class Hit:
    """一条搜索命中。``snippets`` 只用于挑对象，不是引用依据。"""

    object_id: str
    product_id: str
    product_name: str
    type: str
    layer: str
    name: str | None
    revision_no: int
    matched_terms: tuple[str, ...]
    matched_in: tuple[str, ...]
    snippets: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.object_id,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "type": self.type,
            "layer": self.layer,
            "name": self.name,
            "revision": self.revision_no,
            "matched_terms": list(self.matched_terms),
            "matched_in": list(self.matched_in),
            "snippets": list(self.snippets),
        }


def _searchable(row: dict[str, Any]) -> dict[str, str]:
    """一个对象里可被搜到的几个面。

    ⚠️ **不含正文散文**：正文在对象存储里，搜它要另建全文索引。frontmatter 里的
    结构化 `fields` 覆盖了事实型制品真正的数据值（值、单位、出处摘录），所以对
    规格表/事实集这类制品，这里的覆盖面已经够用；对 Wiki/专题页这类正文承载内容
    的制品**不够**——这条写进工具描述，别让 Agent 以为搜不到就是没有。
    """
    frontmatter = row.get("frontmatter_json") or {}
    return {
        "id": normalize_term(row.get("object_id") or ""),
        "name": normalize_term(row.get("name") or ""),
        "fields": normalize_term(
            json.dumps(frontmatter, ensure_ascii=False, separators=(",", ":"))
        ),
    }


def _snippet(text: str, term: str, width: int = 60) -> str:
    index = text.find(term)
    if index < 0:
        return ""
    start = max(0, index - width // 2)
    end = min(len(text), index + len(term) + width // 2)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def rank_hits(
    rows: Sequence[dict[str, Any]], terms: Sequence[str], match: str,
) -> list[Hit]:
    """按命中词数 ↓ → 元数据等级 ↓ → (type, id) ↑ 排。

    元数据等级：ID 全等 > 名称全等 > ID/名称前缀 > 仅字段值包含。排序稳定且不含
    随机因素——同一次查询两次调用必须同序，否则 Agent 的分页会错乱。
    """
    hits: list[tuple[tuple[int, int, str, str], Hit]] = []

    for row in rows:
        faces = _searchable(row)
        matched: list[str] = []
        matched_in: list[str] = []
        snippets: list[str] = []
        grade = 0

        for term in terms:
            where = [face for face, text in faces.items() if term and term in text]
            if not where:
                continue
            matched.append(term)
            for face in where:
                if face not in matched_in:
                    matched_in.append(face)
            if faces["id"] == term:
                grade = max(grade, 4)
            elif faces["name"] == term:
                grade = max(grade, 3)
            elif faces["id"].startswith(term) or faces["name"].startswith(term):
                grade = max(grade, 2)
            else:
                grade = max(grade, 1)
            if len(snippets) < 3:
                text = next((faces[f] for f in where if faces[f]), "")
                piece = _snippet(text, term)
                if piece:
                    snippets.append(piece)

        if not matched:
            continue
        if match == MATCH_ALL and len(matched) != len(terms):
            continue

        hits.append((
            (-len(matched), -grade, row.get("type") or "", row.get("object_id") or ""),
            Hit(
                object_id=row["object_id"],
                product_id=row["product_id"],
                product_name=row.get("product_name") or row["product_id"],
                type=row.get("type") or "",
                layer=row.get("layer") or "",
                name=row.get("name"),
                revision_no=int(row.get("revision_no") or 0),
                matched_terms=tuple(matched),
                matched_in=tuple(matched_in),
                snippets=tuple(snippets),
            ),
        ))

    hits.sort(key=lambda pair: pair[0])
    return [hit for _key, hit in hits]


def recovery_codes(
    terms: Sequence[str], match: str, term_counts: dict[str, int], filtered: bool,
) -> list[str]:
    """零结果时告诉 Agent 下一步怎么救，而不是丢一个空数组。"""
    codes: list[str] = []
    if match == MATCH_ALL and any(term_counts.get(t) for t in terms):
        codes.append(USE_MATCH_ANY)
    if any(not term_counts.get(t) for t in terms):
        codes.append(REMOVE_OR_REPHRASE_TERM)
    if filtered:
        codes.append(RELAX_FILTERS)
    return codes


@dataclass
class SearchResult:
    terms: list[str]
    match: str
    total: int
    page: int
    size: int
    hits: list[Hit]
    facets: dict[str, dict[str, int]] = field(default_factory=dict)
    term_counts: dict[str, int] = field(default_factory=dict)
    recovery: list[str] = field(default_factory=list)

    @property
    def has_more(self) -> bool:
        return self.page * self.size < self.total

    def as_dict(self) -> dict[str, Any]:
        return {
            "terms": self.terms,
            "match": self.match,
            "total": self.total,
            "page": self.page,
            "size": self.size,
            "has_more": self.has_more,
            "hits": [hit.as_dict() for hit in self.hits],
            "facets": self.facets,
            "diagnostics": {"term_counts": self.term_counts, "recovery": self.recovery},
            "note": "snippets 只用于挑对象；要引用必须先用 get_product 取完整正文。",
        }


def build_facets(hits: Sequence[Hit]) -> dict[str, dict[str, int]]:
    """当前结果的构成（分页前精确计数）——Agent 据此决定下一步收窄哪一维。"""
    facets: dict[str, dict[str, int]] = {"type": {}, "product": {}}
    for hit in hits:
        facets["type"][hit.type] = facets["type"].get(hit.type, 0) + 1
        facets["product"][hit.product_id] = facets["product"].get(hit.product_id, 0) + 1
    return facets


def references_of(raw_md: str) -> list[str]:
    """正文里的全部 ``[[ID]]``（去重保序）——直接给出下钻入口。"""
    return list(dict.fromkeys(target.strip() for target in _WIKILINK.findall(raw_md)))


def guard_response_size(payload: Any) -> None:
    """超限整单失败并指引分批——宁可多跑一轮，也别塞爆 Agent 的上下文。"""
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    if size > MAX_RESPONSE_BYTES:
        raise ConsumeRejected(
            RESULT_TOO_LARGE,
            f"响应 {size} 字节超过上限 {MAX_RESPONSE_BYTES}，请减少 ids 分批取",
        )
