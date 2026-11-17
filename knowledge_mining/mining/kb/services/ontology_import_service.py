"""OntologyImportService — 语义对象批量导入两步向导（67号 §七）。

Step 1 parse_upload：护栏（.csv/.xlsx、≤10MB、≤5000 数据行）→ 解析
（xlsx 走 openpyxl read_only、csv 走 csv.DictReader utf-8-sig 容忍 BOM）
→ 进程内登记（token → parsed，TTL 1h 惰性清理，每 uploader 活跃 ≤5
超限清最旧 + preview_limit）→ 返回列清单/前 20 行预览/自动映射预选。

Step 2 commit_import：映射校验 → 全量逐行校验（empty_name /
duplicate_existing / duplicate_in_batch / invalid_name，§7.4）→ 任一行
错误整批拒绝（ImportValidationFailed → 422 报告，token 保留可改映射重提）
→ 全部通过 executemany 单事务落库（source='import'），token 消费失效。

口径对齐 artifact_import_service（错误码→状态映射同款）：file_too_large→
413、preview_expired→410、preview_limit→429、其余解析错误→422、登记绑定
失配→404、校验报告→422。openpyxl 解析经 anyio.to_thread 移出事件循环
（本服务内部完成，route 无需包裹）。

公式注入：不做（制品/用户导入查公式是因为产物会再导出 Excel；语义对象
落 JSONB 且无表格导出通道，§7 未要求）。
"""
from __future__ import annotations

import csv
import io
import time
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import anyio
from openpyxl import load_workbook

from knowledge_mining.mining.kb.db_ontology import OntologyDB
from knowledge_mining.mining.kb.services.ontology_service import (
    NotFound, OntologyService,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024        # 文件 ≤ 10MB
MAX_ROWS = 5_000                           # 数据行 ≤ 5000
PREVIEW_ROWS = 20                          # 预览样例行 ≤ 20
IMPORT_TTL_SECONDS = 3600.0                # token 1h
#: 单 uploader 活跃登记上限（同 artifact_import_service 口径）。
MAX_ACTIVE_IMPORTS_PER_UPLOADER = 5

#: xlsx 解压护栏（同 user_import_service 口径）。
MAX_ZIP_MEMBERS = 1_000
MAX_EXPANDED_BYTES = 50 * 1024 * 1024

#: 对象名列自动识别同义词（列名精确命中即预选，UI 可改）。
_NAME_COLUMN_SYNONYMS = {"名称", "对象名称", "对象名", "name"}

_MAX_OBJECT_NAME_LENGTH = 200              # 与 ontology_service 同口径

#: 进程内导入登记（模块级——deps 每请求新建服务实例，登记必须跨请求存活；
#: 单进程单 worker 部署成立，与 artifact preview token 同一既定约束）。
_IMPORT_TOKENS: dict[str, dict[str, Any]] = {}


class OntologyImportError(Exception):
    """向导文件/登记级错误。route 翻译：file_too_large→413、
    preview_expired→410、preview_limit→429、其余→422。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ImportValidationFailed(Exception):
    """逐行校验失败（§7.4）：整批未导入。route 翻 422，
    detail={message, report}（report.errors 供前端表格+CSV 导出）。"""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__(report.get("message", "validation failed"))
        self.report = report


# ---------------------------------------------------------------- helpers

def _dedupe_headers(header_values: Any) -> list[str]:
    """表头清洗：空白→列序号占位名；重名→追加序号后缀（同 artifact 口径）。"""
    names: list[str] = []
    seen: dict[str, int] = {}
    for position, raw in enumerate(header_values or [], start=1):
        text = str(raw).strip() if raw is not None and str(raw).strip() else f"列{position}"
        count = seen.get(text, 0)
        seen[text] = count + 1
        names.append(text if count == 0 else f"{text}_{count + 1}")
    return names


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _cell_to_scalar(value: Any) -> Any:
    """单元格 → 标量（§7.3 值转换矩阵）。空/纯空白 → None（= 不写键）；
    xlsx 数值格→number、布尔格→boolean、datetime→ISO 字符串（jsonb 可序
    列化）；其余→string 去首尾空白。CSV 解析出的全是字符串，此函数统一
    收口空白语义。"""
    if _is_blank(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _check_zip_bomb(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            expanded = sum(m.file_size for m in members)
            compressed = sum(max(m.compress_size, 1) for m in members)
            if (
                len(members) > MAX_ZIP_MEMBERS
                or expanded > MAX_EXPANDED_BYTES
                or expanded > compressed * 100
            ):
                raise OntologyImportError("xlsx_archive_limits_exceeded")
    except OntologyImportError:
        raise
    except zipfile.BadZipFile as exc:
        raise OntologyImportError("invalid_xlsx") from exc


def _parse_xlsx(content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """首个 sheet：表头行 + 数据行 dict 列表（值保持原生类型，转换在
    commit 侧 _cell_to_scalar 统一做）。"""
    _check_zip_bomb(content)
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True)
    except Exception as exc:  # openpyxl 暴露多种格式私有异常，统一收敛
        raise OntologyImportError("invalid_xlsx") from exc
    try:
        sheet = workbook[workbook.sheetnames[0]]
        iterator = sheet.iter_rows(values_only=True)
        header_row = next(iterator, None)
        if header_row is None:
            raise OntologyImportError("empty_sheet")
        headers = _dedupe_headers(header_row)
        rows: list[dict[str, Any]] = []
        for raw in iterator:
            if all(_is_blank(value) for value in raw):
                continue  # 全空行跳过（Excel 常见尾随空行）
            rows.append(dict(zip(headers, raw)))
            if len(rows) > MAX_ROWS:
                raise OntologyImportError("too_many_rows")
        return headers, rows
    finally:
        workbook.close()


def _parse_csv(content: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """utf-8(-sig) CSV：DictReader；多余列落 restkey 丢弃，短行缺列补 None。"""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OntologyImportError("csv_must_be_utf8") from exc
    reader = csv.reader(io.StringIO(text))
    header_row = next(reader, None)
    if header_row is None:
        raise OntologyImportError("empty_sheet")
    headers = _dedupe_headers(header_row)
    rows: list[dict[str, Any]] = []
    for raw in reader:
        if not raw or all(_is_blank(value) for value in raw):
            continue
        padded = list(raw) + [None] * (len(headers) - len(raw))
        rows.append(dict(zip(headers, padded)))
        if len(rows) > MAX_ROWS:
            raise OntologyImportError("too_many_rows")
    return headers, rows


def _cleanup_expired() -> None:
    now = time.monotonic()
    expired = [
        token for token, record in _IMPORT_TOKENS.items()
        if now - record["parsed_at"] > IMPORT_TTL_SECONDS
    ]
    for token in expired:
        _IMPORT_TOKENS.pop(token, None)


def _enforce_import_cap(uploader: str | None) -> None:
    """每 uploader 活跃登记 ≤5：超限清最旧再报 preview_limit（429）。
    未绑定 uploader 的登记（直调测试形态）不计。"""
    if uploader is None:
        return
    active = [
        (token, record) for token, record in _IMPORT_TOKENS.items()
        if record.get("uploader") == uploader
    ]
    if len(active) < MAX_ACTIVE_IMPORTS_PER_UPLOADER:
        return
    oldest_token, _ = min(active, key=lambda item: item[1]["parsed_at"])
    _IMPORT_TOKENS.pop(oldest_token, None)
    raise OntologyImportError("preview_limit")


def _require_import(
    import_token: str, *, ontology_id: str, uploader: str,
) -> dict[str, Any]:
    """登记查找 + 绑定校验：token 不存在/过期 → preview_expired(410)；
    绑定本体或上传者失配 → NotFound(404)——token 属于他人/他本体，不暴露
    登记内容。"""
    _cleanup_expired()
    record = _IMPORT_TOKENS.get(import_token)
    if record is None:
        raise OntologyImportError("preview_expired")
    if record.get("ontology_id") != ontology_id \
            or record.get("uploader") != uploader:
        raise NotFound("import_token_mismatch")
    return record


def _detect_mapping(
    columns: list[str], attribute_names: set[str],
) -> dict[str, Any]:
    """自动映射预选：属性列 = 列名与属性定义名精确匹配；对象名列 =
    同义词表精确命中（name 大小写不敏感；无命中留空由用户选）。"""
    attribute_columns = {
        column: column for column in columns if column in attribute_names
    }
    name_column = next(
        (column for column in columns if column in _NAME_COLUMN_SYNONYMS),
        next(
            (column for column in columns if column.lower() == "name"), "",
        ),
    )
    return {"name_column": name_column, "attribute_columns": attribute_columns}


# ----------------------------------------------------------------- service

class OntologyImportService:
    """两步导入编排。写路径的权限门禁经 OntologyService
    （get_ontology_for_import：域管理员写 + 属性定义）。"""

    def __init__(self, db: OntologyDB) -> None:
        self._db = db

    # ------------------------------------------------------------ Step 1

    async def parse_upload(
        self, service: OntologyService, *,
        ontology_id: str, file_bytes: bytes, file_name: str,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """上传解析（202）。openpyxl/CSV 解析经 anyio.to_thread 移出事件
        循环；权限与属性定义经 service.get_ontology_for_import。"""
        _cleanup_expired()
        _enforce_import_cap(str(user["id"]))
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise OntologyImportError("file_too_large")
        suffix = Path(file_name).suffix.lower()
        if suffix not in (".csv", ".xlsx"):
            raise OntologyImportError("unsupported_file_type")
        detail = await service.get_ontology_for_import(ontology_id, user=user)
        attribute_names = {a["name"] for a in detail["attributes"]}
        headers, rows = await anyio.to_thread.run_sync(
            lambda: (
                _parse_xlsx(file_bytes) if suffix == ".xlsx"
                else _parse_csv(file_bytes)
            ),
        )
        token = uuid.uuid4().hex
        _IMPORT_TOKENS[token] = {
            "ontology_id": ontology_id,
            "uploader": str(user["id"]),
            "file_name": file_name,
            "columns": headers,
            "rows": rows,
            "parsed_at": time.monotonic(),
        }
        columns_payload = [
            {"name": column, "sample": _sample_of(rows, column)}
            for column in headers
        ]
        return {
            "import_token": token,
            "row_count": len(rows),
            "columns": columns_payload,
            "preview_rows": rows[:PREVIEW_ROWS],
            "detected_mapping": _detect_mapping(headers, attribute_names),
        }

    # ------------------------------------------------------------ Step 2

    async def commit_import(
        self, service: OntologyService, *,
        ontology_id: str, import_token: str,
        name_column: str | None, attribute_columns: dict[str, str] | None,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """提交导入。映射校验 → 逐行校验（任一错误整批拒绝，token 保留）
        → executemany 单事务落库 → token 消费失效。"""
        record = _require_import(
            import_token, ontology_id=ontology_id, uploader=str(user["id"]),
        )
        detail = await service.get_ontology_for_import(ontology_id, user=user)
        defined = {a["name"] for a in detail["attributes"]}
        columns = list(record["columns"])
        column_set = set(columns)

        name_column = (name_column or "").strip()
        if not name_column or name_column not in column_set:
            raise OntologyImportError("invalid_name_column")
        mapping = dict(attribute_columns or {})
        targets: dict[str, str] = {}
        for column, attribute in mapping.items():
            column = str(column or "").strip()
            attribute = str(attribute or "").strip()
            if not column or column not in column_set:
                raise OntologyImportError(f"unknown_column:{column}")
            if attribute not in defined:
                raise OntologyImportError(f"unknown_attribute:{attribute}")
            if attribute in targets:
                raise OntologyImportError(f"duplicate_attribute:{attribute}")
            targets[attribute] = column

        # 逐行校验（§7.4）：报告按 1 起数据行号；四类错误聚合在同一份报告
        # （先单查询取库内存量重名集，再单趟校验，避免 duplicate_existing
        # 只在其它错误为零时才被发现）
        candidate_names = []
        for row in record["rows"]:
            raw_name = row.get(name_column)
            name = str(raw_name).strip() if raw_name is not None else ""
            if name:
                candidate_names.append(name)
        existing_names = await self._db.list_object_names(
            ontology_id, candidate_names,
        )
        errors: list[dict[str, Any]] = []
        seen_names: dict[str, int] = {}
        prepared: list[tuple[str, dict[str, Any]]] = []
        for row_number, row in enumerate(record["rows"], start=1):
            raw_name = row.get(name_column)
            name = str(raw_name).strip() if raw_name is not None else ""
            if not name:
                errors.append({
                    "row": row_number, "code": "empty_name",
                    "message": "名称列为空",
                })
                continue
            if len(name) > _MAX_OBJECT_NAME_LENGTH:
                errors.append({
                    "row": row_number, "code": "invalid_name",
                    "message": f"名称超过 {_MAX_OBJECT_NAME_LENGTH} 字符",
                })
                continue
            if name in existing_names:
                errors.append({
                    "row": row_number, "code": "duplicate_existing",
                    "message": f"对象「{name}」已存在",
                })
                continue
            if name in seen_names:
                errors.append({
                    "row": row_number, "code": "duplicate_in_batch",
                    "message": f"与第 {seen_names[name]} 行重名",
                })
                continue
            attributes: dict[str, Any] = {}
            for attribute, column in targets.items():
                value = _cell_to_scalar(row.get(column))
                if value is not None:
                    attributes[attribute] = value
            prepared.append((name, attributes))
            seen_names[name] = row_number
        if errors:
            raise ImportValidationFailed({
                "message": f"{len(errors)} 行校验失败，整批未导入",
                "total_rows": len(record["rows"]),
                "valid_rows": len(record["rows"]) - len(errors),
                "error_rows": len(errors),
                "errors": errors,
            })

        inserted = await self._db.batch_insert_objects(
            ontology_id=ontology_id, rows=prepared,
            created_by=str(user["id"]), source="import",
        )
        _IMPORT_TOKENS.pop(import_token, None)  # 一次性消费
        return {"ontology_id": ontology_id, "imported": inserted}


def _sample_of(rows: list[dict[str, Any]], column: str) -> Any:
    """列样例值：首个非空格（空列返回 None）。"""
    for row in rows:
        value = _cell_to_scalar(row.get(column))
        if value is not None:
            return value
    return None
