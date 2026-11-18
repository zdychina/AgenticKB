-- 69号批次0 · 抽取溯源：semantic_objects.source_document_id 可空列。
-- 机制权威 = docs/下一阶段/69-本体图谱消费设计-2026-11-18.md §二。
-- 记录事实而非约束：不加 FK——purge 对 asset_documents 有物理 DELETE，加了会被
-- 存量引用卡死清库；读取侧对软删/物理删/查无统一降级（LEFT JOIN 滤
-- deleted_at，name/kb_id → null，id 仍在）。仅 source='extract' 通道写入。
ALTER TABLE semantic_objects
    ADD COLUMN IF NOT EXISTS source_document_id TEXT;
CREATE INDEX IF NOT EXISTS idx_semantic_objects_source_doc
    ON semantic_objects(source_document_id)
    WHERE source_document_id IS NOT NULL;
