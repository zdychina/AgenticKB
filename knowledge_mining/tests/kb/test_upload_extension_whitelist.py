"""BUG-02：上传入口扩展名白名单（intake_upload 前置 + 归档成员整包预检）。

口径（2026-11-26）：单文件 = _EXTENSION_MIME（17 个文档/归档格式）；
归档成员 = 文档格式 ∪ 资产格式——批次2b 决策"全部入库，含图片等资产"
继续成立（真实 hdx/chm 包内嵌图片/索引部件），可执行/任意二进制整包拒。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.kb.services.document_service import (
    DocumentService,
    _validate_extension,
)


class _Db:
    def __init__(self):
        self.documents: list[dict] = []
        self.folders: dict[str, dict] = {}

    async def get_kb(self, kb_id):
        return {"id": kb_id, "domain": "generic"}

    async def is_visible(self, *, kb_id, user_id):
        return True

    async def can_write(self, *, kb_id, user_id):
        return True

    async def find_folder_by_path(self, kb_id, path):
        return self.folders.get(path)

    async def insert_folder(self, **values):
        folder = {"id": f"folder-{len(self.folders) + 1}", **values}
        self.folders[folder["path"]] = folder
        return folder

    async def find_document_by_location(self, kb_id, directory_path, document_name, *, include_deleted=False):
        return None

    async def find_document_by_key(self, kb_id, document_key, *, include_deleted=False):
        return None

    async def revive_document_from_storage(self, document_id, **kw):
        return None

    async def insert_document_from_storage(self, **values):
        doc = {"id": f"doc-{len(self.documents) + 1}", "status": "uploaded",
               "content_revision": 1, **values}
        self.documents.append(doc)
        return doc


def _svc(tmp_path) -> tuple[DocumentService, _Db]:
    db = _Db()
    svc = DocumentService(
        db,  # type: ignore[arg-type]
        object_store=FakeObjectStore(root_path=str(tmp_path / "objects")),
        storage_objects=MemoryStorageObjectRepository(),
        source_bucket="kbs-source",
    )
    return svc, db


def _zip(tmp_path: Path, name: str, members: dict[str, str]) -> Path:
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as zf:
        for rel, content in members.items():
            zf.writestr(rel, content)
    return zp


async def _stream_of(payload: bytes):
    yield payload


# ------------------------------------------------------------ 纯函数

def test_validate_extension_rejects_exe_and_lists_supported() -> None:
    with pytest.raises(ValueError, match="不支持的文件类型 \\.exe"):
        _validate_extension("virus.exe")


def test_validate_extension_rejects_missing_extension() -> None:
    with pytest.raises(ValueError, match="无扩展名"):
        _validate_extension("unnamed")


def test_validate_extension_accepts_supported_and_case_insensitive() -> None:
    _validate_extension("手册.md")
    _validate_extension("report.PDF")
    _validate_extension("pack.zip")
    _validate_extension("help.chm")


# ------------------------------------------------------------ intake_upload 前置

@pytest.mark.asyncio
async def test_intake_upload_rejects_exe_before_any_write(tmp_path):
    """virus.exe 在触碰存储/DB 之前被拒（BUG-02 主复现路径）。"""
    svc, db = _svc(tmp_path)
    with pytest.raises(ValueError, match="不支持的文件类型"):
        await svc.intake_upload(
            kb_id="kb-1", owner_id="alice", filename="virus.exe",
            stream=_stream_of(b"MZ fake exe"),
        )
    assert db.documents == [] and db.folders == {}


@pytest.mark.asyncio
async def test_intake_upload_supported_file_still_passes(tmp_path):
    svc, db = _svc(tmp_path)
    result = await svc.intake_upload(
        kb_id="kb-1", owner_id="alice", filename="手册.md",
        stream=_stream_of(b"# hi"),
    )
    assert result["kind"] == "file"
    assert len(db.documents) == 1


# ------------------------------------------------------------ 归档成员整包预检

@pytest.mark.asyncio
async def test_archive_member_exe_rejected_whole_package(tmp_path):
    """合法 zip 夹带 .exe → 整包拒绝，无部分入库残留（不建目录不入库）。"""
    svc, db = _svc(tmp_path)
    zp = _zip(tmp_path, "pack.zip", {"a.txt": "aaa", "malware.exe": "MZ"})
    with pytest.raises(ValueError, match="malware\\.exe"):
        await svc.upload_archive_path(
            kb_id="kb-1", owner_id="alice", archive_path=zp, archive_name="pack.zip",
        )
    assert db.documents == [] and db.folders == {}


@pytest.mark.asyncio
async def test_archive_member_exe_rejected_via_intake(tmp_path):
    """同上但走 intake_upload（网页/MCP 同源入口）——异常向上冒泡为
    ValueError（路由映射 400）。"""
    svc, db = _svc(tmp_path)
    zp = _zip(tmp_path, "pack.zip", {"sub/SETUP.EXE": "MZ"})
    with pytest.raises(ValueError, match="归档包含不支持的文件类型"):
        await svc.intake_upload(
            kb_id="kb-1", owner_id="alice", filename="pack.zip",
            stream=_stream_of(zp.read_bytes()),
        )
    assert db.documents == [] and db.folders == {}


@pytest.mark.asyncio
async def test_archive_image_and_chm_part_members_still_ingested(tmp_path):
    """批次2b 决策回归（2026-11-26 口径）：图片/CHM 部件是资产格式，
    成员照常入库——不得因白名单误伤真实 hdx/chm 包。"""
    svc, db = _svc(tmp_path)
    hdx = _zip(tmp_path, "产品文档.hdx", {
        "resources/topics/intro.htm": "<html>intro</html>",
        "resources/images/logo.png": "\x89PNG fake",
        "resources/index.hhc": "<html>toc</html>",
    })
    docs = await svc.upload_archive_path(
        kb_id="kb-1", owner_id="alice", archive_path=hdx, archive_name="产品文档.hdx",
    )
    assert len(docs) == 3
    assert {d["document_name"] for d in db.documents} == {
        "intro.htm", "logo.png", "index.hhc"}
