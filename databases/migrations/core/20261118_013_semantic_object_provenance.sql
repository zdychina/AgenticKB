-- 69号批次0 · 抽取溯源（online_expand，与启动链 kb/schemas/023 同文；fresh
-- install 库只跑 schemas/ 目录，两份漂移由 tests/maintenance/test_schema_mirror_sync.py 钉死）。
-- semantic_objects.source_document_id 可空列：AI 抽取确认落库时记来源文档，
-- 记录事实而非约束（不加 FK：purge 对 asset_documents 有物理 DELETE，加了会被
-- 存量引用卡死清库；读取侧对软删/物理删/查无统一降级 name/kb_id → null）。
ALTER TABLE semantic_objects
    ADD COLUMN IF NOT EXISTS source_document_id TEXT;
CREATE INDEX IF NOT EXISTS idx_semantic_objects_source_doc
    ON semantic_objects(source_document_id)
    WHERE source_document_id IS NOT NULL;
