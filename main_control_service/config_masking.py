"""系统配置敏感值掩码与写路径合并（BUG-05）。

浏览器（非内部调用）读 /api/v1/system/* 等配置接口时掩码敏感值；PUT 回传的
掩码按路径配对恢复磁盘原值——绝不把掩码串写进配置文件。

实现基于 PyYAML compose 节点的 start/end_mark 列区间做文本拼接：列区间就是
标量本身（不含引号外的任何东西），天然保留注释/引号/缩进；恢复按"路径"而非
"行序"配对（llm_service.yaml 有 11 个 api_key，增删重排后行序必然错位）。

fail-closed 约定：
- 结构无法安全处理（重复键、别名/锚点、多行/块标量带掩码）→ MaskMergeError，
  handler 层转 400；掩码侧则跳过该键并记录，宁可漏掩不可破坏文件。
- 一切失败路径都不会把 MASK_PREFIX 写盘。
"""
from __future__ import annotations

import copy
import json
from typing import Any

import yaml

MASK_PREFIX = "••••••"  # 6 × U+2022：js-yaml / CodeMirror / PyYAML 均为合法 plain scalar

#: 归一化（lower + 连字符转下划线）后子串匹配；子串表必须写下划线形态
#: （"x-hw-" 永远匹配不上归一化后的 "x_hw_appkey"）。
_SENSITIVE_SUBSTR = (
    "secret", "password", "passwd", "token", "api_key", "apikey",
    "appkey", "credential", "private_key", "access_key", "authorization",
    "x_hw_",
)

#: 归一化后全等豁免：含敏感子串但非凭据的公开端点键。
_NON_SENSITIVE_KEYS = frozenset({"token_url"})

_STR_TAG = "tag:yaml.org,2002:str"

_MISSING = object()


class MaskMergeError(Exception):
    """掩码回传无法按磁盘原值恢复（fail-closed，handler 层转 400）。"""

    def __init__(self, paths: list[str], reason: str) -> None:
        super().__init__(f"{reason}: {', '.join(paths) if paths else '(root)'}")
        self.paths = paths
        self.reason = reason


def _normalize_key(key: object) -> str:
    return str(key).lower().replace("-", "_")


def is_sensitive_key(key: object) -> bool:
    kl = _normalize_key(key)
    if kl in _NON_SENSITIVE_KEYS:
        return False
    return any(s in kl for s in _SENSITIVE_SUBSTR)


def should_mask_value(value: object) -> bool:
    """仅掩码"长度 ≥ 8 的字符串"：int（token_ttl_seconds）、短串/空串不掩。"""
    return isinstance(value, str) and len(value) >= 8


def mask_value(value: str) -> str:
    """${VAR} 引用形态不泄末 4 位；其余保留末 4 位便于人工比对。"""
    if "${" in value:
        return MASK_PREFIX
    return MASK_PREFIX + value[-4:]


# ---------------------------------------------------------------------------
# 结构化（已解析对象）掩码
# ---------------------------------------------------------------------------

def mask_structured(obj: Any) -> tuple[Any, list[str]]:
    """返回（深拷贝后掩码的对象, 被掩码的点线路径列表）。

    必须深拷贝：service 层读走 _load_yaml_cached 的缓存对象，不可原地改。
    """
    masked = copy.deepcopy(obj)
    paths: list[str] = []
    _mask_obj_inplace(masked, [], paths)
    return masked, paths


def _mask_obj_inplace(node: Any, path: list[str], paths: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if is_sensitive_key(k) and should_mask_value(v):
                node[k] = mask_value(v)
                paths.append(".".join(path + [str(k)]))
            else:
                _mask_obj_inplace(v, path + [str(k)], paths)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _mask_obj_inplace(v, path + [str(i)], paths)


# ---------------------------------------------------------------------------
# compose 节点遍历（marks + 路径）
# ---------------------------------------------------------------------------

def _collect_sensitive_scalars(
    root: yaml.Node,
    *,
    restore: bool,
) -> list[tuple[list[str], int, int, int, str]]:
    """收集需要文本区间编辑的敏感标量。

    restore=False（掩码）：敏感键 + 单行 str 标量 + 值规则命中（len≥8）。
    restore=True（恢复）：敏感键 + 单行 str 标量 + 值以 MASK_PREFIX 开头。
    返回 [(path, line, col_start, col_end, value)]；重复键/别名抛 MaskMergeError；
    掩码侧遇多行/块标量保守跳过，恢复侧则抛（无法安全写回）。
    """
    edits: list[tuple[list[str], int, int, int, str]] = []
    seen_ids: set[int] = set()

    def visit(node: Any, path: list[str]) -> None:
        if id(node) in seen_ids:
            raise MaskMergeError(
                [".".join(path)], "config uses YAML anchors/aliases, unsupported")
        seen_ids.add(id(node))
        if isinstance(node, yaml.MappingNode):
            keys_seen: set[str] = set()
            for key_node, value_node in node.value:
                if not isinstance(key_node, yaml.ScalarNode):
                    continue  # 复杂键不参与路径/掩码判断
                key = key_node.value
                if key in keys_seen:
                    raise MaskMergeError(
                        [".".join(path + [str(key)])],
                        "duplicate mapping key, path resolution ambiguous")
                keys_seen.add(key)
                child_path = path + [str(key)]
                if (isinstance(value_node, yaml.ScalarNode)
                        and value_node.tag == _STR_TAG
                        and is_sensitive_key(key)):
                    v = value_node.value
                    hit = v.startswith(MASK_PREFIX) if restore else should_mask_value(v)
                    if hit:
                        sm, em = value_node.start_mark, value_node.end_mark
                        if sm.line != em.line:
                            if restore:
                                raise MaskMergeError(
                                    [".".join(child_path)],
                                    "masked value spans multiple lines, unsupported")
                            continue  # 掩码侧：多行/块标量保守跳过
                        edits.append((child_path, sm.line, sm.column, em.column, v))
                        continue
                visit(value_node, child_path)
        elif isinstance(node, yaml.SequenceNode):
            for i, item in enumerate(node.value):
                visit(item, path + [str(i)])

    visit(root, [])
    return edits


def _apply_edits(text: str, edits: list[tuple[int, int, int, str]]) -> str:
    """按 (line, col_start, col_end, repl) 做行内区间替换（同行多处按列倒序）。"""
    if not edits:
        return text
    lines = text.split("\n")
    by_line: dict[int, list[tuple[int, int, str]]] = {}
    for line_no, col_start, col_end, repl in edits:
        by_line.setdefault(line_no, []).append((col_start, col_end, repl))
    for line_no, spans in by_line.items():
        line = lines[line_no]
        for col_start, col_end, repl in sorted(spans, reverse=True):
            line = line[:col_start] + repl + line[col_end:]
        lines[line_no] = line
    return "\n".join(lines)


def mask_yaml_text(text: str) -> tuple[str, list[str]]:
    """掩码 YAML 文本中的敏感标量，保留注释/引号/缩进/行内尾注释。

    返回 (掩码后文本, 被掩码路径列表)。文本不可解析时抛 yaml.YAMLError
    （handler 层转 503：宁可拒读不可漏掩）。
    """
    root = yaml.compose(text)
    if root is None:
        return text, []
    collected = _collect_sensitive_scalars(root, restore=False)
    edits = [
        (line, cs, ce, json.dumps(mask_value(v), ensure_ascii=False))
        for _path, line, cs, ce, v in collected
    ]
    masked = _apply_edits(text, edits)
    return masked, [".".join(p) for p, *_ in collected]


def _resolve_disk_value(disk_root: Any, path: list[str]) -> Any:
    """在 safe_load 解析结果里按路径取值；缺失返回 _MISSING。"""
    node: Any = disk_root
    for seg in path:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif isinstance(node, list):
            try:
                node = node[int(seg)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return node


def merge_masked_yaml(incoming: str, disk: str) -> tuple[str, list[str]]:
    """把 incoming 中回传的掩码值按路径恢复为 disk 原值。

    - incoming 中"敏感键 + 值以 MASK_PREFIX 开头"→ disk 同路径必须存在 str 值，
      以 json.dumps 双引号形态拼回该标量列区间；
    - disk 缺路径/非 str/结构不支持 → MaskMergeError（handler 转 400）；
    - 非掩码的新真实值 = 轮换，原样放行。
    返回 (合并后文本, 恢复了原值的路径列表)。
    """
    incoming_root = yaml.compose(incoming)
    if incoming_root is None:
        return incoming, []
    disk_root: Any
    try:
        disk_root = yaml.safe_load(disk)
    except yaml.YAMLError:
        raise MaskMergeError([], "disk config is not parseable YAML") from None

    collected = _collect_sensitive_scalars(incoming_root, restore=True)
    replacements: list[tuple[int, int, int, str]] = []
    restored: list[str] = []
    problems: list[str] = []
    for path, line, col_start, col_end, _v in collected:
        disk_value = _resolve_disk_value(disk_root, path)
        if not isinstance(disk_value, str):
            problems.append(".".join(path))
            continue
        replacements.append((line, col_start, col_end,
                             json.dumps(disk_value, ensure_ascii=False)))
        restored.append(".".join(path))
    if problems:
        raise MaskMergeError(
            problems,
            "masked values have no matching string on disk "
            "(new/renamed keys must use real values, not masks)")
    merged = _apply_edits(incoming, replacements)
    return merged, restored


def consume_bootstrap_line(text: str) -> tuple[str, bool]:
    """删除 auth.yaml 中 bootstrap.admin_password 所在行（保留其余注释/结构）。

    返回 (处理后文本, 是否删除了行)。
    """
    root = yaml.compose(text)
    target_lines: set[int] = set()
    if isinstance(root, yaml.MappingNode):
        for key_node, value_node in root.value:
            if (isinstance(key_node, yaml.ScalarNode)
                    and key_node.value == "bootstrap"
                    and isinstance(value_node, yaml.MappingNode)):
                for k2, _v2 in value_node.value:
                    if isinstance(k2, yaml.ScalarNode) and k2.value == "admin_password":
                        target_lines.add(k2.start_mark.line)
    if not target_lines:
        return text, False
    lines = text.split("\n")
    out = [ln for i, ln in enumerate(lines) if i not in target_lines]
    return "\n".join(out), True
