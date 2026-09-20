"""Object-store round-trip for product object bodies（52号 D2）。

正文永远不进 PG 大字段——md 落对象存储，``kp_objects.storage_object_id`` 只留引用。
复用 008 的 ``asset_storage_objects`` 登记表与 ``ObjectStorePort``，走的是
``document_service`` 同一套内容寻址语义：sha256 → object_key → 命中即复用。

制品 md 普遍很小（几 KB），所以这里整包读写，不做流式分块——与 source 上传的
大文件路径刻意分开。
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Protocol

from knowledge_mining.mining.contracts.file_management import StorageObjectRecord
from knowledge_mining.mining.contracts.storage.types import ObjectLocation, PutOptions
from knowledge_mining.mining.infra.object_store.keys import build_object_key

#: 制品正文的 artifact class。与 source 分开：它不是用户上传的原始资料。
ARTIFACT_CLASS = "knowledge_product"
#: bucket 名由 ``bucket_prefix + 类名`` 拼成，而 S3 bucket 名不允许下划线。
BUCKET_SUFFIX = "knowledge-product"
MIME = "text/markdown"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class StorageObjectRegistry(Protocol):
    """``PgStorageObjectRepository`` 的读写子集（测试可换内存实现）。"""

    async def register(self, record: StorageObjectRecord) -> StorageObjectRecord: ...

    async def get(self, storage_object_id: str) -> StorageObjectRecord | None: ...

    async def find_by_location(
        self, bucket: str, object_key: str, object_version_id: str | None
    ) -> StorageObjectRecord | None: ...


def bucket_for(bucket_prefix: str) -> str:
    return f"{bucket_prefix}{BUCKET_SUFFIX}"


class ArtifactContentStore:
    """制品 md 正文的读写。"""

    def __init__(
        self,
        object_store: Any,
        storage_objects: StorageObjectRegistry,
        bucket: str,
    ) -> None:
        self._object_store = object_store
        self._storage_objects = storage_objects
        self._bucket = bucket

    async def put(self, raw_md: str) -> StorageObjectRecord:
        """写入一份正文，返回其登记记录。内容相同则复用已有对象，不重复上传。"""
        payload = raw_md.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        object_key = build_object_key(ARTIFACT_CLASS, digest)

        existing = await self._storage_objects.find_by_location(
            self._bucket, object_key, None
        )
        if existing is not None:
            if existing.sha256 != digest or existing.state != "AVAILABLE":
                raise ValueError("已登记的存储对象与正文内容不符")
            return existing

        async def _chunks() -> AsyncIterator[bytes]:
            yield payload

        put_result = await self._object_store.put_stream(
            ObjectLocation(bucket=self._bucket, object_key=object_key),
            _chunks(),
            PutOptions(
                artifact_class=ARTIFACT_CLASS,
                mime=MIME,
                expected_sha256=digest,
                content_length=len(payload),
            ),
        )
        if put_result.sha256 != digest or put_result.size != len(payload):
            raise ValueError("对象存储校验失败")

        return await self._storage_objects.register(
            StorageObjectRecord(
                id=f"kpo_{uuid.uuid4().hex}",
                provider=getattr(self._object_store, "provider", "unknown"),
                bucket=self._bucket,
                object_key=object_key,
                object_version_id=None,
                sha256=digest,
                size=len(payload),
                mime=MIME,
                etag=put_result.etag,
                artifact_class=ARTIFACT_CLASS,
                state="AVAILABLE",
                created_at=_utcnow(),
                last_verified_at=_utcnow(),
            )
        )

    async def get(self, storage_object_id: str) -> str:
        """按登记 id 读回正文。"""
        record = await self._storage_objects.get(storage_object_id)
        if record is None or record.state != "AVAILABLE":
            raise KeyError(f"存储对象不可用: {storage_object_id!r}")

        buffer = bytearray()
        stream = self._object_store.get_stream(
            ObjectLocation(
                bucket=record.bucket,
                object_key=record.object_key,
                version_id=record.object_version_id,
            )
        )
        async for chunk in stream:
            buffer.extend(chunk)

        if hashlib.sha256(bytes(buffer)).hexdigest() != record.sha256:
            raise ValueError(f"正文哈希与登记不符: {storage_object_id!r}")
        return bytes(buffer).decode("utf-8")
