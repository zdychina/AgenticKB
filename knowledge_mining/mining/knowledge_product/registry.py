"""Object-type registry（移植自 newsfc ``app/registry.py``）。

YAML 驱动、领域无关——这是整份移植里最值钱的一块：它直接就是 48号总纲
``KnowledgeAsset`` 的类型注册表。默认表见 ``default_registry.yaml``；单个知识域
可以覆盖，覆盖冲突以告警形式返回而不是静默生效。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("default_registry.yaml")

SCOPE_PRODUCT = "product"
SCOPE_CROSS = "cross"
_VALID_SCOPES = frozenset({SCOPE_PRODUCT, SCOPE_CROSS})
_SEGMENTS_BY_SCOPE = {SCOPE_PRODUCT: 3, SCOPE_CROSS: 2}


@dataclass(frozen=True)
class TypeSpec:
    """一个对象类型的契约。"""

    name: str
    layer: str
    scope: str
    id_segments: int
    frontmatter_required: tuple[str, ...] = ()
    required_sections: tuple[str, ...] = ()
    path_fields: tuple[str, ...] = ()
    local_from: tuple[str, ...] = ()
    fields_block: dict[str, Any] | None = None

    @property
    def is_product_scoped(self) -> bool:
        return self.scope == SCOPE_PRODUCT

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any]) -> TypeSpec:
        scope = raw.get("scope", SCOPE_CROSS)
        if scope not in _VALID_SCOPES:
            raise ValueError(f"类型 {name!r} 的 scope {scope!r} 非法，应为 product/cross")

        segments = raw.get("id_segments", _SEGMENTS_BY_SCOPE[scope])
        if segments != _SEGMENTS_BY_SCOPE[scope]:
            raise ValueError(
                f"类型 {name!r}: scope={scope} 要求 id_segments="
                f"{_SEGMENTS_BY_SCOPE[scope]}，实为 {segments}"
            )

        return cls(
            name=name,
            layer=raw["layer"],
            scope=scope,
            id_segments=segments,
            frontmatter_required=tuple(raw.get("frontmatter_required", ())),
            required_sections=tuple(raw.get("required_sections", ())),
            path_fields=tuple(raw.get("path_fields", ())),
            local_from=tuple(raw.get("local_from", ())),
            fields_block=raw.get("fields_block"),
        )


class Registry:
    def __init__(self, types: dict[str, TypeSpec]) -> None:
        self._types = types

    @classmethod
    def from_mapping(cls, raw_types: dict[str, dict[str, Any]]) -> Registry:
        return cls({name: TypeSpec.from_dict(name, raw) for name, raw in raw_types.items()})

    @classmethod
    def load(cls, path: Path | None = None) -> Registry:
        path = path or DEFAULT_REGISTRY_PATH
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.from_mapping(data.get("object_types", {}))

    def get(self, type_name: str) -> TypeSpec | None:
        return self._types.get(type_name)

    def require(self, type_name: str) -> TypeSpec:
        spec = self._types.get(type_name)
        if spec is None:
            raise ValueError(f"未知对象类型 {type_name!r}")
        return spec

    def names(self) -> frozenset[str]:
        return frozenset(self._types)

    def merge_overrides(self, overrides: dict[str, dict[str, Any]]) -> list[str]:
        """按知识域覆盖类型定义；返回告警（不抛异常，让调用方决定要不要拦）。"""
        warnings: list[str] = []
        for name, raw in (overrides or {}).items():
            if name in self._types:
                warnings.append(f"对象类型 {name!r} 被覆盖")
            self._types[name] = TypeSpec.from_dict(name, raw)
        return warnings
