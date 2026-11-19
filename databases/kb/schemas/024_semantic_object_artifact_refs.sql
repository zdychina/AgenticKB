-- 70号：对象↔制品 refs 软引用表。对象侧属表，跟随 current + 读侧降级
-- （不存 revision、不存标题快照）；形态学仿 kb_artifact_sources（跨族身份
-- TEXT 无 FK + 制品侧反查索引）。族内 FK 两条：object_id ON DELETE CASCADE
-- （对象删=引用随删）、created_by（kb_users）。无 updated_at：refs 只有建/删。
CREATE TABLE IF NOT EXISTS semantic_object_artifact_refs (
    id          TEXT PRIMARY KEY,             -- sor_<16hex>，应用生成
    object_id   TEXT NOT NULL REFERENCES semantic_objects(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL,                -- kb_artifacts.artifact_id，跨族软引用不建 FK
    target_key  TEXT NOT NULL DEFAULT '',     -- ''=制品整体；wiki=page_key；table=row_key（跨 revision 稳定）
    note        TEXT NOT NULL DEFAULT '',     -- 关联理由（可选；应用层限 500 字）
    ref_source  TEXT NOT NULL DEFAULT 'manual'
                CONSTRAINT ck_semobj_refs_source
                CHECK (ref_source IN ('manual','auto_compile','auto_import')),
    created_by  TEXT NOT NULL REFERENCES kb_users(id),
    created_at  TEXT NOT NULL                 -- TEXT ISO（ontology 族口径，同 022）
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_semobj_artifact_refs
    ON semantic_object_artifact_refs(object_id, artifact_id, target_key);
CREATE INDEX IF NOT EXISTS idx_semobj_refs_artifact
    ON semantic_object_artifact_refs(artifact_id);
