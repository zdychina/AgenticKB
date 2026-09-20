-- 021 · 知识制品运营：报告问题（52号 P7，2026-09-20）。
--
-- 48号 §八「资料变了、用错了，能继续更新」。首期只需要「提醒 + 人工触发更新 +
-- 差异确认」，不建全自动知识演进系统。
--
-- 影响分析**不需要新表**：文档→制品的直接关系已经在 kp_evidence 里（每个字段
-- 都记了 snapshot_id），制品→制品的引用在 kp_edges 里。021 只补反馈这一张。

CREATE TABLE IF NOT EXISTS kp_issues (
    id                TEXT PRIMARY KEY,
    product_id        TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    -- 用哪一版时发现的。**不挂外键到 kp_revisions**：修订可能因为制品重建而消失，
    -- 而问题记录本身要留着——报告过的问题不该因为版本被清理就凭空没了。
    used_revision     INTEGER,
    object_id         TEXT,                      -- 可选：具体是哪个对象
    field_name        TEXT,                      -- 可选：具体是哪个字段
    task              TEXT,                      -- 当时在做什么任务
    problem           TEXT NOT NULL,             -- 哪里不对
    correction_basis  TEXT,                      -- 修正依据
    reporter          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'open' CHECK (
        status IN ('open', 'triaged', 'resolved', 'rejected')
    ),
    -- 知识负责人的处置：修资料 / 改定义 / 改内容 / 改 Agent 用法（48号 §八）
    resolution_kind   TEXT CHECK (
        resolution_kind IN ('source', 'definition', 'content', 'agent_usage', 'none')
    ),
    resolution_note   TEXT,
    resolved_by       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

COMMENT ON TABLE kp_issues IS
    '报告问题（48号 §八）。「调用次数只能说明用过，不能说明有用」——所以从试点起就要记下人工补查与修正，这张表是其中一半';
COMMENT ON COLUMN kp_issues.resolution_kind IS
    '处置方向而不是「已修复」一个布尔：同一个问题可能该改资料、该改定义、该改内容，也可能该改 Agent 的用法——区分开才知道下次该防哪一类';

CREATE INDEX IF NOT EXISTS idx_kp_issues_product
    ON kp_issues(product_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_kp_issues_object
    ON kp_issues(product_id, object_id);
