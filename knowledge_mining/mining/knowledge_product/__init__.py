"""Knowledge-product carrier: md + frontmatter + ``[[ID]]`` edges.

52号计划 P1。移植自 newsfc ``graph-asset-platform``，改动见各模块 docstring 与
``docs/下一阶段/52-newsfc制品能力移植-知识制品制作闭环-分析与实施计划-2026-09-19.md``。

本包只管**载体**——解析、归类、建边、结构校验。持久化、制作实例、票据、DSH 适配
分别在 ``kp_*`` 表与后续 ``agent_creation`` / ``harness_adapter`` / ``creation_mcp``。
"""
from __future__ import annotations

from knowledge_mining.mining.knowledge_product.classify import classify, storage_key
from knowledge_mining.mining.knowledge_product.diff import (
    ObjectChange,
    RevisionDiff,
    diff_object,
    diff_revisions,
)
from knowledge_mining.mining.knowledge_product.edges import build_edges, iter_wikilinks
from knowledge_mining.mining.knowledge_product.evidence import (
    EvidenceRef,
    extract_batch,
    extract_evidence,
)
from knowledge_mining.mining.knowledge_product.loader import build_object
from knowledge_mining.mining.knowledge_product.logical_id import (
    build_local,
    is_product_scoped,
    segment_count,
    split_id,
)
from knowledge_mining.mining.knowledge_product.md_parser import parse_md
from knowledge_mining.mining.knowledge_product.models import MENTIONS, Edge, ProductObject
from knowledge_mining.mining.knowledge_product.registry import (
    SCOPE_CROSS,
    SCOPE_PRODUCT,
    Registry,
    TypeSpec,
)
from knowledge_mining.mining.knowledge_product.validate import (
    Issue,
    validate_batch,
    validate_object,
)

__all__ = [
    "MENTIONS",
    "SCOPE_CROSS",
    "SCOPE_PRODUCT",
    "Edge",
    "EvidenceRef",
    "Issue",
    "ObjectChange",
    "ProductObject",
    "Registry",
    "RevisionDiff",
    "TypeSpec",
    "build_edges",
    "build_local",
    "build_object",
    "classify",
    "diff_object",
    "diff_revisions",
    "extract_batch",
    "extract_evidence",
    "is_product_scoped",
    "iter_wikilinks",
    "parse_md",
    "segment_count",
    "split_id",
    "storage_key",
    "validate_batch",
    "validate_object",
]
