-- Ontology management (67号): 本体库六表 —— 人工自定义本体的类型层+实例层。
-- 机制权威 = docs/下一阶段/67-本体管理MVP设计-2026-11-17.md §四。
-- domain 必须是 domain_registry.yaml 合法域（应用层校验）；删除保护全强
-- RESTRICT（应用层查计数 → 409，FK 兜底）；全部 idempotent。
CREATE TABLE IF NOT EXISTS ontology_libraries (
    id          TEXT PRIMARY KEY,
    domain      TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    created_by  TEXT NOT NULL REFERENCES kb_users(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (domain, name)                        -- 域内本体库名唯一
);
CREATE INDEX IF NOT EXISTS idx_ontology_libraries_domain ON ontology_libraries(domain);

-- 本体（节点类型）：库内名唯一；删除被"有对象或被关系引用"应用层拦截。
CREATE TABLE IF NOT EXISTS ontologies (
    id          TEXT PRIMARY KEY,
    library_id  TEXT NOT NULL REFERENCES ontology_libraries(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    created_by  TEXT NOT NULL REFERENCES kb_users(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (library_id, name)                    -- 库内本体名唯一
);
CREATE INDEX IF NOT EXISTS idx_ontologies_library ON ontologies(library_id);

-- 属性定义：无类型系统（名称+描述+排序）；改名/删除由应用层同事务
-- 迁移/清理 semantic_objects.attributes_json 的键。
CREATE TABLE IF NOT EXISTS ontology_attributes (
    id          TEXT PRIMARY KEY,
    ontology_id TEXT NOT NULL REFERENCES ontologies(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    UNIQUE (ontology_id, name)                   -- 同一本体内属性名唯一
);
CREATE INDEX IF NOT EXISTS idx_ontology_attributes_ontology ON ontology_attributes(ontology_id);

-- 本体关系（边类型）：命名有向边，绑定源/目标本体；源=目标允许（自环类型）。
-- 本体删除被"被关系引用"应用层拦截，FK 裸 RESTRICT 兜底。
CREATE TABLE IF NOT EXISTS ontology_relations (
    id                  TEXT PRIMARY KEY,
    library_id          TEXT NOT NULL REFERENCES ontology_libraries(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    description         TEXT,
    source_ontology_id  TEXT NOT NULL REFERENCES ontologies(id),
    target_ontology_id  TEXT NOT NULL REFERENCES ontologies(id),
    created_by          TEXT NOT NULL REFERENCES kb_users(id),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (library_id, name)                    -- 库内关系类型名唯一
);
CREATE INDEX IF NOT EXISTS idx_ontology_relations_src ON ontology_relations(source_ontology_id);
CREATE INDEX IF NOT EXISTS idx_ontology_relations_tgt ON ontology_relations(target_ontology_id);

-- 语义对象（图的节点）：attributes_json 键=属性定义名，值=自由标量（无类型
-- 校验）；library_id 冗余列由写入方保证与 ontology 归属一致；source 溯源
-- 实例化通道（手工/导入/AI 抽取确认）。
CREATE TABLE IF NOT EXISTS semantic_objects (
    id              TEXT PRIMARY KEY,
    library_id      TEXT NOT NULL REFERENCES ontology_libraries(id) ON DELETE CASCADE,
    ontology_id     TEXT NOT NULL REFERENCES ontologies(id),
    name            TEXT NOT NULL,
    attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    source          TEXT NOT NULL DEFAULT 'manual'
                    CHECK (source IN ('manual','import','extract')),
    created_by      TEXT NOT NULL REFERENCES kb_users(id),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (ontology_id, name)                   -- 同一本体下对象名唯一
);
CREATE INDEX IF NOT EXISTS idx_semantic_objects_library ON semantic_objects(library_id);
CREATE INDEX IF NOT EXISTS idx_semantic_objects_ontology ON semantic_objects(ontology_id);

-- 语义关系（图的边）：端点本体匹配由应用层强校验（67号 §5.2）；防重边+
-- 禁自指由 DB 约束兜底。
CREATE TABLE IF NOT EXISTS semantic_relations (
    id                    TEXT PRIMARY KEY,
    library_id            TEXT NOT NULL REFERENCES ontology_libraries(id) ON DELETE CASCADE,
    ontology_relation_id  TEXT NOT NULL REFERENCES ontology_relations(id),
    source_object_id      TEXT NOT NULL REFERENCES semantic_objects(id),
    target_object_id      TEXT NOT NULL REFERENCES semantic_objects(id),
    created_by            TEXT NOT NULL REFERENCES kb_users(id),
    created_at            TEXT NOT NULL,
    CONSTRAINT chk_semrel_no_self CHECK (source_object_id <> target_object_id),
    UNIQUE (ontology_relation_id, source_object_id, target_object_id)
);
CREATE INDEX IF NOT EXISTS idx_semantic_relations_src ON semantic_relations(source_object_id);
CREATE INDEX IF NOT EXISTS idx_semantic_relations_tgt ON semantic_relations(target_object_id);
