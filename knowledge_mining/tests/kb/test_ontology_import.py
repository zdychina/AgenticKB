"""本体批量导入 service 层测试 —— 真库门控（@pytest.mark.postgres，67号 P2）。

覆盖（§7.2-§7.4）：CSV/XLSX 解析（表头清洗/类型保留/BOM/全空行）；护栏
（文件类型/行数上限）；自动映射预选；commit 全链路（值转换矩阵、
empty_name/duplicate_in_batch/duplicate_existing/invalid_name 四类报告、
整批零写入、token 一次性、绑定失配）；权限（成员拒写）。
本机无 PG 时整个模块在收集期跳过（与 KB_RUN_POSTGRES_ACCEPTANCE 同款门槛）。
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

if os.environ.get("KB_RUN_POSTGRES_ACCEPTANCE") != "1":
    pytest.skip(
        "set KB_RUN_POSTGRES_ACCEPTANCE=1 to run real PostgreSQL tests",
        allow_module_level=True,
    )

from openpyxl import Workbook  # noqa: E402

from knowledge_mining.mining.kb.db import KbDB  # noqa: E402
from knowledge_mining.mining.kb.db_ontology import OntologyDB  # noqa: E402
from knowledge_mining.mining.kb.services.ontology_import_service import (  # noqa: E402
    ImportValidationFailed, OntologyImportError, OntologyImportService,
    _IMPORT_TOKENS,
)
from knowledge_mining.mining.kb.services.ontology_service import (  # noqa: E402
    Forbidden, NotFound, OntologyService,
)

_DOMAIN = "cloud_core_network"


def _svc(pool) -> OntologyService:
    return OntologyService(OntologyDB(pool))


def _imp(pool) -> OntologyImportService:
    return OntologyImportService(OntologyDB(pool))


async def _make_admin(pool) -> dict:
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"ontimp-{uuid.uuid4().hex[:8]}")
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO user_domains (user_id, domain, domain_role)
               VALUES (%s, %s, 'admin')""",
            (user["id"], _DOMAIN),
        )
    return user


async def _make_member(pool) -> dict:
    kbdb = KbDB(pool)
    user = await kbdb.create_user(username=f"ontimp-{uuid.uuid4().hex[:8]}")
    async with pool.connection() as conn:
        await conn.execute(
            """INSERT INTO user_domains (user_id, domain, domain_role)
               VALUES (%s, %s, 'member')""",
            (user["id"], _DOMAIN),
        )
    return user


async def _make_target(svc, admin: dict) -> tuple[str, dict]:
    """建库 + 本体（三个属性），返回 (ontology_id, detail)。"""
    lib = await svc.create_library(
        domain=_DOMAIN, name=f"导入测试库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )
    onto = await svc.create_ontology(
        lib["id"], name=f"本体-{uuid.uuid4().hex[:4]}", description=None,
        attributes=[
            {"name": "厂商"}, {"name": "端口数"}, {"name": "在网状态"},
        ],
        user=admin,
    )
    detail = await svc.get_ontology(onto["id"], user=admin)
    return onto["id"], detail


def _csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _xlsx_bytes(rows: list[list]) -> bytes:
    """内存构造 xlsx：首行表头；值类型原样（number/bool/datetime 保留）。"""
    import io

    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _reset_tokens() -> None:
    _IMPORT_TOKENS.clear()


@pytest.fixture(autouse=True)
def _clean_token_registry():
    _reset_tokens()
    yield
    _reset_tokens()


# ------------------------------------------------------------- Step 1 解析

async def test_upload_parse_csv_with_detected_mapping(async_pool) -> None:
    svc = _svc(async_pool)
    imp = _imp(async_pool)
    admin = await _make_admin(async_pool)
    ontology_id, _ = await _make_target(svc, admin)

    content = _csv_bytes(
        "名称,厂商,备注列\n"
        "AMF,华为,仅备注\n"
        "SMF,中兴, \n"
        "\n"  # 全空行跳过
    )
    result = await imp.parse_upload(
        svc, ontology_id=ontology_id, file_bytes=content,
        file_name="设备.csv", user=admin,
    )

    assert result["row_count"] == 2
    assert [c["name"] for c in result["columns"]] == ["名称", "厂商", "备注列"]
    assert result["columns"][0]["sample"] == "AMF"
    assert len(result["preview_rows"]) == 2
    # 自动映射：名称列同义词命中；厂商列与属性定义名精确匹配
    assert result["detected_mapping"] == {
        "name_column": "名称",
        "attribute_columns": {"厂商": "厂商"},
    }


async def test_upload_parse_csv_bom_and_utf8_guard(async_pool) -> None:
    svc = _svc(async_pool)
    imp = _imp(async_pool)
    admin = await _make_admin(async_pool)
    ontology_id, _ = await _make_target(svc, admin)

    bom = "﻿名称,厂商\nAMF,华为\n".encode("utf-8")
    result = await imp.parse_upload(
        svc, ontology_id=ontology_id, file_bytes=bom,
        file_name="bom.csv", user=admin,
    )
    assert result["detected_mapping"]["name_column"] == "名称"

    with pytest.raises(OntologyImportError) as excinfo:
        await imp.parse_upload(
            svc, ontology_id=ontology_id,
            file_bytes=b"\xff\xfe" + "名称".encode("gbk"),
            file_name="gbk.csv", user=admin,
        )
    assert excinfo.value.code == "csv_must_be_utf8"


async def test_upload_parse_xlsx_keeps_native_types(async_pool) -> None:
    svc = _svc(async_pool)
    imp = _imp(async_pool)
    admin = await _make_admin(async_pool)
    ontology_id, _ = await _make_target(svc, admin)

    content = _xlsx_bytes([
        ["设备名称", "端口数", "在网状态"],
        ["AMF", 100, True],
        ["SMF", 200.5, False],
    ])
    result = await imp.parse_upload(
        svc, ontology_id=ontology_id, file_bytes=content,
        file_name="设备.xlsx", user=admin,
    )
    assert result["row_count"] == 2
    row = result["preview_rows"][0]
    assert row["端口数"] == 100          # 数值格 → number（预览即原生类型）
    assert row["在网状态"] is True       # 布尔格 → boolean


async def test_upload_guards(async_pool) -> None:
    svc = _svc(async_pool)
    imp = _imp(async_pool)
    admin = await _make_admin(async_pool)
    ontology_id, _ = await _make_target(svc, admin)

    with pytest.raises(OntologyImportError) as excinfo:
        await imp.parse_upload(
            svc, ontology_id=ontology_id, file_bytes=b"x", file_name="a.txt",
            user=admin,
        )
    assert excinfo.value.code == "unsupported_file_type"

    with pytest.raises(OntologyImportError) as excinfo:
        await imp.parse_upload(
            svc, ontology_id=ontology_id, file_bytes=b"not a zip",
            file_name="a.xlsx", user=admin,
        )
    assert excinfo.value.code == "invalid_xlsx"

    rows = "\n".join(f"名称,厂商\n网元{i},华为" for i in range(5001))
    with pytest.raises(OntologyImportError) as excinfo:
        await imp.parse_upload(
            svc, ontology_id=ontology_id, file_bytes=rows.encode(),
            file_name="big.csv", user=admin,
        )
    assert excinfo.value.code == "too_many_rows"

    # 普通成员无写权限 → Forbidden（走 get_ontology_for_import 门禁）
    member = await _make_member(async_pool)
    with pytest.raises(Forbidden):
        await imp.parse_upload(
            svc, ontology_id=ontology_id, file_bytes=_csv_bytes("名称\nX\n"),
            file_name="a.csv", user=member,
        )


# ------------------------------------------------------------- Step 2 提交

async def test_commit_import_success_and_value_matrix(async_pool) -> None:
    svc = _svc(async_pool)
    imp = _imp(async_pool)
    admin = await _make_admin(async_pool)
    ontology_id, _ = await _make_target(svc, admin)

    # 列名与属性名故意错开（槽位数→端口数）：证明列→属性映射生效
    content = _xlsx_bytes([
        ["设备名称", "厂商", "槽位数", "在网状态"],
        ["AMF", "华为", 100, True],
        ["SMF", None, "  双栈接入  ", False],
    ])
    upload = await imp.parse_upload(
        svc, ontology_id=ontology_id, file_bytes=content,
        file_name="设备.xlsx", user=admin,
    )
    result = await imp.commit_import(
        svc, ontology_id=ontology_id,
        import_token=upload["import_token"],
        name_column="设备名称",
        attribute_columns={"厂商": "厂商", "槽位数": "端口数", "在网状态": "在网状态"},
        user=admin,
    )
    assert result == {"ontology_id": ontology_id, "imported": 2}

    # 落库校验：source='import'；值转换矩阵（空白不写键/number/bool/strip）
    library_id = (await svc.get_ontology(ontology_id, user=admin))["library_id"]
    objects = await svc.list_objects(
        library_id, ontology_id=ontology_id, q=None,
        limit=50, offset=0, user=admin,
    )
    by_name = {o["name"]: o for o in objects["items"]}
    assert set(by_name) == {"AMF", "SMF"}
    assert all(o["source"] == "import" for o in by_name.values())
    assert by_name["AMF"]["attributes_json"] == {
        "厂商": "华为", "端口数": 100, "在网状态": True,
    }
    # 空白格不写键（厂商缺失）；文本去首尾空白；布尔 false 保留
    assert by_name["SMF"]["attributes_json"] == {
        "端口数": "双栈接入", "在网状态": False,
    }

    # token 一次性：成功后失效
    with pytest.raises(OntologyImportError) as excinfo:
        await imp.commit_import(
            svc, ontology_id=ontology_id, import_token=upload["import_token"],
            name_column="设备名称", attribute_columns=None, user=admin,
        )
    assert excinfo.value.code == "preview_expired"


async def test_commit_validation_report_all_or_nothing(async_pool) -> None:
    svc = _svc(async_pool)
    imp = _imp(async_pool)
    admin = await _make_admin(async_pool)
    ontology_id, _ = await _make_target(svc, admin)

    # 预置一个已有对象 → duplicate_existing
    await svc.create_object(
        ontology_id, name="AMF", attributes={"厂商": "旧数据"},
        user=admin,
    )
    content = _csv_bytes(
        "名称,厂商\n"
        "AMF,重复\n"          # duplicate_existing（行1）
        "SMF,中兴\n"            # OK
        "   ,空名\n"               # empty_name（行3）
        f"{'长' * 201},超长\n"     # invalid_name（行4）
        "SMF,批内重复\n"        # duplicate_in_batch（行5，与行2重名）
    )
    upload = await imp.parse_upload(
        svc, ontology_id=ontology_id, file_bytes=content,
        file_name="脏数据.csv", user=admin,
    )
    with pytest.raises(ImportValidationFailed) as excinfo:
        await imp.commit_import(
            svc, ontology_id=ontology_id,
            import_token=upload["import_token"], name_column="名称",
            attribute_columns={"厂商": "厂商"}, user=admin,
        )
    report = excinfo.value.report
    assert report["total_rows"] == 5
    assert report["error_rows"] == 4
    codes = {(e["row"], e["code"]) for e in report["errors"]}
    assert (1, "duplicate_existing") in codes
    assert (3, "empty_name") in codes
    assert (4, "invalid_name") in codes
    assert (5, "duplicate_in_batch") in codes

    # 整批零写入：库里仍只有预置的 1 个对象
    library_id = (await svc.get_ontology(ontology_id, user=admin))["library_id"]
    objects = await svc.list_objects(
        library_id, ontology_id=ontology_id, q=None,
        limit=50, offset=0, user=admin,
    )
    assert objects["total"] == 1

    # 校验失败 token 保留：同 token 再提交仍走校验（而非 preview_expired）
    with pytest.raises(ImportValidationFailed):
        await imp.commit_import(
            svc, ontology_id=ontology_id, import_token=upload["import_token"],
            name_column="名称", attribute_columns=None, user=admin,
        )


async def test_commit_mapping_and_binding_guards(async_pool) -> None:
    svc = _svc(async_pool)
    imp = _imp(async_pool)
    admin = await _make_admin(async_pool)
    ontology_id, _ = await _make_target(svc, admin)

    upload = await imp.parse_upload(
        svc, ontology_id=ontology_id,
        file_bytes=_csv_bytes("名称,厂商\nAMF,华为\n"),
        file_name="a.csv", user=admin,
    )
    token = upload["import_token"]

    # 名称列不在表头
    with pytest.raises(OntologyImportError) as excinfo:
        await imp.commit_import(
            svc, ontology_id=ontology_id, import_token=token,
            name_column="不存在", attribute_columns=None, user=admin,
        )
    assert excinfo.value.code == "invalid_name_column"

    # 属性目标不在定义集
    with pytest.raises(OntologyImportError) as excinfo:
        await imp.commit_import(
            svc, ontology_id=ontology_id, import_token=token,
            name_column="名称", attribute_columns={"厂商": "无此属性"},
            user=admin,
        )
    assert excinfo.value.code == "unknown_attribute:无此属性"

    # 映射列不在表头
    with pytest.raises(OntologyImportError) as excinfo:
        await imp.commit_import(
            svc, ontology_id=ontology_id, import_token=token,
            name_column="名称",
            attribute_columns={"无此列": "端口数"}, user=admin,
        )
    assert excinfo.value.code == "unknown_column:无此列"

    # 两个列映射到同一属性（独立上传双列文件验证）
    two = await imp.parse_upload(
        svc, ontology_id=ontology_id,
        file_bytes=_csv_bytes("名称,A列,B列\nAMF,x,y\n"),
        file_name="two.csv", user=admin,
    )
    with pytest.raises(OntologyImportError) as excinfo:
        await imp.commit_import(
            svc, ontology_id=ontology_id, import_token=two["import_token"],
            name_column="名称",
            attribute_columns={"A列": "厂商", "B列": "厂商"}, user=admin,
        )
    assert excinfo.value.code == "duplicate_attribute:厂商"

    # token 绑定失配：换另一个本体 → NotFound（不暴露登记）
    other_lib = await svc.create_library(
        domain=_DOMAIN, name=f"他库-{uuid.uuid4().hex[:6]}",
        description=None, user=admin,
    )
    other_onto = await svc.create_ontology(
        other_lib["id"], name="他本体", description=None, attributes=None,
        user=admin,
    )
    with pytest.raises(NotFound):
        await imp.commit_import(
            svc, ontology_id=other_onto["id"], import_token=token,
            name_column="名称", attribute_columns=None, user=admin,
        )

    # 原绑定仍可提交（前面失败未消费 token）
    result = await imp.commit_import(
        svc, ontology_id=ontology_id, import_token=token,
        name_column="名称", attribute_columns={"厂商": "厂商"},
        user=admin,
    )
    assert result["imported"] == 1
