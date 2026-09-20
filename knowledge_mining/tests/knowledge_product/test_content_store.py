"""制品正文的对象存储往返（52号 D2）。

用 FakeObjectStore + MemoryStorageObjectRepository——与 file_management 的服务层
测试同一套 Protocol 契约，不需要 MinIO。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.file_management.repositories_memory import (
    MemoryStorageObjectRepository,
)
from knowledge_mining.mining.infra.object_store.fake import FakeObjectStore
from knowledge_mining.mining.knowledge_product.content_store import (
    ARTIFACT_CLASS,
    ArtifactContentStore,
    bucket_for,
)

MD = """---
id: DataProduct@sample
type: DataProduct
---
# 样品

## 边
- 依赖本体: [[OntologyModule@terms]]
"""


@pytest.fixture
def store(tmp_path) -> ArtifactContentStore:
    return ArtifactContentStore(
        FakeObjectStore(root_path=str(tmp_path / "objects")),
        MemoryStorageObjectRepository(),
        bucket_for("agentickb-test-"),
    )


@pytest.mark.asyncio
async def test_put_then_get_round_trips_utf8(store: ArtifactContentStore) -> None:
    record = await store.put(MD)
    assert record.artifact_class == ARTIFACT_CLASS
    assert record.mime == "text/markdown"
    assert record.size == len(MD.encode("utf-8"))
    assert await store.get(record.id) == MD


@pytest.mark.asyncio
async def test_identical_content_is_stored_once(store: ArtifactContentStore) -> None:
    """内容寻址：同一份正文重复提交复用同一个对象，不重复上传。"""
    first = await store.put(MD)
    second = await store.put(MD)
    assert first.id == second.id
    assert first.object_key == second.object_key


@pytest.mark.asyncio
async def test_different_content_gets_different_objects(store: ArtifactContentStore) -> None:
    first = await store.put(MD)
    second = await store.put(MD.replace("样品", "样品二"))
    assert first.id != second.id
    assert first.sha256 != second.sha256


@pytest.mark.asyncio
async def test_object_key_is_content_addressed(store: ArtifactContentStore) -> None:
    record = await store.put(MD)
    # SRS §8.1 布局：{prefix}/{ab}/{cd}/{sha256}
    assert record.object_key.endswith(record.sha256)
    assert f"/{record.sha256[:2]}/{record.sha256[2:4]}/" in record.object_key


@pytest.mark.asyncio
async def test_get_unknown_id_raises(store: ArtifactContentStore) -> None:
    with pytest.raises(KeyError):
        await store.get("kpo_nope")


@pytest.mark.asyncio
async def test_bucket_name_has_no_underscore() -> None:
    """artifact_class 带下划线，但 S3 bucket 名不许——两者刻意不同名。"""
    assert "_" not in bucket_for("agentickb-dev-")
