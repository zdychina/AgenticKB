-- 018 · 知识制品载体（52号计划 P1，2026-09-19）。
--
-- 46/48/49/50 号把「知识制品」规划完了但没有表。本迁移建载体侧的 7 张表：制品
-- 身份、定义修订、资料范围、草稿/发布修订、对象、边、证据。制作实例、任务票据、
-- 提交回执、审核记录属于 P2（agent_creation），留给 019。
--
-- 落库位置：**按域路由的 asset_core 库**。制品不跨知识域（52号 D6），所以域由
-- 「这张表在哪个库」表达，表本身不带 domain 列——51号批次3 已定制品身份去 domain，
-- 归属由 owner 表达、边界由资料范围表达。
--
-- 正文不进 PG：对象 md 存对象存储，这里只留索引 + storage_object_id 引用（52号 D2）。

-- ─────────────────────────────────────────────────────────────
-- 0. 放宽 008 的 artifact_class：制品正文是新的一类字节
-- ─────────────────────────────────────────────────────────────
-- 制品 md 不是用户上传的 source，保留策略与配额口径都不同，给它自己的 class。
-- 008 里这个 CHECK 是匿名的（PG 自动命名），用 DO 块按列定位后重建，避免硬编码
-- 自动名；重复执行幂等。
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'asset_storage_objects'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) LIKE '%artifact_class%'
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE asset_storage_objects DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE asset_storage_objects
        ADD CONSTRAINT asset_storage_objects_artifact_class_check
        CHECK (artifact_class IN (
            'source', 'backend_raw', 'parse_ir', 'page_render',
            'binary_asset', 'temporary', 'knowledge_product'
        ));
END
$$;

-- ─────────────────────────────────────────────────────────────
-- 1. 制品身份与定义
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kp_products (
    id                     TEXT PRIMARY KEY,          -- 制品 slug，同时是对象逻辑 ID 的首段
    product_type           TEXT NOT NULL,             -- 业务上这是什么制品（规格矩阵/专题页/规则包…）
    name                   TEXT NOT NULL,
    purpose                TEXT,                      -- 服务什么业务任务或决策（48号登记字段）
    owner                  TEXT NOT NULL,
    lifecycle_status       TEXT NOT NULL DEFAULT 'draft' CHECK (
        lifecycle_status IN ('draft', 'candidate', 'trial', 'published', 'superseded', 'retired')
    ),
    current_draft_revision INTEGER,
    released_revision      INTEGER,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

COMMENT ON COLUMN kp_products.product_type IS
    '制品类型，与对象类型（kp_objects.type / registry）是两个概念——一个制品会同时产出一个 cross scope 身份对象和若干 product scope 对象卡（52号 §5.4 F1）';

CREATE TABLE IF NOT EXISTS kp_product_definitions (
    id                   TEXT PRIMARY KEY,
    product_id           TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    definition_revision  INTEGER NOT NULL CHECK (definition_revision >= 1),
    fields_json          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 字段定义（D5 第二级）
    object_rules_json    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 一行代表什么、去重、单位、冲突处理
    examples_json        JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 人工样例
    trial_questions_json JSONB NOT NULL DEFAULT '[]'::jsonb,  -- 发布前的检验题
    created_at           TEXT NOT NULL,
    created_by           TEXT NOT NULL,
    UNIQUE (product_id, definition_revision)
);

COMMENT ON TABLE kp_product_definitions IS
    '制品定义修订（50号「制品定义修订」）。registry 是第一级（平台定骨架），这里是第二级（制品定业务字段与对象规则）';

-- 资料范围：票据校验的唯一依据。随定义修订走——改了范围就是新的定义修订，旧票据失效。
CREATE TABLE IF NOT EXISTS kp_scope_items (
    id                    TEXT PRIMARY KEY,
    product_id            TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    definition_revision   INTEGER NOT NULL,
    document_id           TEXT NOT NULL,
    snapshot_id           TEXT NOT NULL REFERENCES asset_document_snapshots(id) ON DELETE RESTRICT,
    allowed_sections_json JSONB,                      -- NULL = 整篇可读
    created_at            TEXT NOT NULL,
    UNIQUE (product_id, definition_revision, snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_kp_scope_items_snapshot
    ON kp_scope_items(snapshot_id);

-- ─────────────────────────────────────────────────────────────
-- 2. 修订
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kp_revisions (
    id                  TEXT PRIMARY KEY,
    product_id          TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    revision_no         INTEGER NOT NULL CHECK (revision_no >= 1),
    status              TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'in_review', 'published', 'superseded')
    ),
    definition_revision INTEGER NOT NULL,             -- 本修订按哪一版定义制作
    created_at          TEXT NOT NULL,
    published_at        TEXT,
    UNIQUE (product_id, revision_no)
);

-- 一个制品至多一个已发布修订（照搬 ontology_versions 的 active 唯一约束做法：
-- 不变量交给 DB，不靠服务层自觉）。发布新版时先把旧版 superseded。
CREATE UNIQUE INDEX IF NOT EXISTS uq_kp_revisions_published
    ON kp_revisions(product_id) WHERE status = 'published';

-- ─────────────────────────────────────────────────────────────
-- 3. 对象与边
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kp_objects (
    product_id        TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    object_id         TEXT NOT NULL,                  -- 完整逻辑 ID，版本不进 ID
    revision_no       INTEGER NOT NULL,
    type              TEXT NOT NULL,
    layer             TEXT NOT NULL,
    scope             TEXT NOT NULL CHECK (scope IN ('product', 'cross')),
    name              TEXT,
    frontmatter_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    storage_object_id TEXT REFERENCES asset_storage_objects(id) ON DELETE RESTRICT,
    review_status     TEXT NOT NULL DEFAULT 'agent_submitted' CHECK (
        review_status IN ('agent_submitted', 'human_confirmed', 'unresolved', 'rejected')
    ),
    created_at        TEXT NOT NULL,
    PRIMARY KEY (product_id, object_id, revision_no),
    FOREIGN KEY (product_id, revision_no)
        REFERENCES kp_revisions(product_id, revision_no) ON DELETE CASCADE
);

COMMENT ON COLUMN kp_objects.frontmatter_json IS
    'md 的 frontmatter 全量，含结构化 fields 块。校验与逐字段回源看它，不看正文（52号 §5.4 F2）';
COMMENT ON COLUMN kp_objects.review_status IS
    'unresolved = 有冲突字段，不得进入发布修订';

CREATE INDEX IF NOT EXISTS idx_kp_objects_type
    ON kp_objects(product_id, revision_no, type);

-- 跨制品共享对象按 object_id 反查产出方（cross scope 的对象可被多个制品引用）
CREATE INDEX IF NOT EXISTS idx_kp_objects_object_id
    ON kp_objects(object_id);

CREATE TABLE IF NOT EXISTS kp_edges (
    product_id  TEXT NOT NULL,
    revision_no INTEGER NOT NULL,
    from_id     TEXT NOT NULL,
    relation    TEXT NOT NULL,                        -- 'mentions' = 正文内联、## 边 段未标注
    to_id       TEXT NOT NULL,
    PRIMARY KEY (product_id, revision_no, from_id, relation, to_id),
    FOREIGN KEY (product_id, revision_no)
        REFERENCES kp_revisions(product_id, revision_no) ON DELETE CASCADE,
    CHECK (from_id <> to_id)
);

COMMENT ON TABLE kp_edges IS
    '正向边，由源对象的 md 全文扫 [[ID]] 建出，按 (from, relation, to) 去重。反向边不写回 md——按 to_id 反查本表即可（52号 §6.3 规则 2/3）';

-- 反向边查询的唯一支撑；to_id 允许指向本修订尚不存在的对象（悬挂边，报告不阻断）
CREATE INDEX IF NOT EXISTS idx_kp_edges_to
    ON kp_edges(to_id);

-- ─────────────────────────────────────────────────────────────
-- 4. 证据
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kp_evidence (
    id           TEXT PRIMARY KEY,
    product_id   TEXT NOT NULL,
    revision_no  INTEGER NOT NULL,
    object_id    TEXT NOT NULL,
    field_name   TEXT NOT NULL,
    document_id  TEXT NOT NULL,
    snapshot_id  TEXT NOT NULL REFERENCES asset_document_snapshots(id) ON DELETE RESTRICT,
    segment_id   TEXT NOT NULL REFERENCES asset_raw_segments(id) ON DELETE RESTRICT,
    anchor       TEXT,
    quoted_value TEXT,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (product_id, object_id, revision_no)
        REFERENCES kp_objects(product_id, object_id, revision_no) ON DELETE CASCADE
);

COMMENT ON COLUMN kp_evidence.segment_id IS
    '必填。校验「证据真在票据允许范围内」靠它外键到 asset_raw_segments；anchor 是自由文本、校验不了，只供页面打开正确位置（50号 §2.2 修订，52号 F3）';

-- ⚠️ snapshot_id / segment_id 用 RESTRICT 而非 CASCADE：已发布制品的可回源性优先于
-- 快照回收。代价是 017 的快照 GC 必须先查本表——有证据引用的快照不能物理回收。
-- 这条需要 GC 侧配合，见 52号后续项。

CREATE INDEX IF NOT EXISTS idx_kp_evidence_object
    ON kp_evidence(product_id, revision_no, object_id);

CREATE INDEX IF NOT EXISTS idx_kp_evidence_snapshot
    ON kp_evidence(snapshot_id);
