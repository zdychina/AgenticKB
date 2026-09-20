-- 020 · 知识制品的人审、试用与发布（52号计划 P4，2026-09-20）。
--
-- 018 是载体、019 是制作面，本迁移补最后一段：人怎么改、怎么审、怎么试用、
-- 凭什么放行发布。``kp_reviews`` 已在 019 建好，这里只加两张新表。
--
-- 依据 48号 §六/§七：
--   · 人工修改要留痕——「至少记录谁改了什么、为什么、依据是什么」；
--   · 发布前至少确认：负责人与适用范围明确、关键内容能回源、冲突没有被掩盖、
--     试用记录符合本用途的要求、所引用内容有权发布。

-- ─────────────────────────────────────────────────────────────
-- 1. 人工编辑留痕
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kp_object_edits (
    id           TEXT PRIMARY KEY,
    product_id   TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    revision_no  INTEGER NOT NULL,
    object_id    TEXT NOT NULL,
    editor       TEXT NOT NULL,
    reason       TEXT,                       -- 为什么改
    basis        TEXT,                       -- 依据是什么（专家判断 / 另一处原文 / 口径决定）
    before_md    TEXT,
    after_md     TEXT,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (product_id, revision_no)
        REFERENCES kp_revisions(product_id, revision_no) ON DELETE CASCADE
);

COMMENT ON TABLE kp_object_edits IS
    '人工编辑留痕（48号 §六「人工修改怎样保住」）。Agent 再次整理时要能生成差异让人选择保留哪一版——没有这张表就只剩「谁也说不清这行是谁改的」';

CREATE INDEX IF NOT EXISTS idx_kp_object_edits_object
    ON kp_object_edits(product_id, object_id, created_at DESC);

-- ─────────────────────────────────────────────────────────────
-- 2. 试用记录
-- ─────────────────────────────────────────────────────────────
-- 48号 §七：第一期不需要全功能评测平台。保存问题、使用的资产版本、回答、期望要点、
-- 人工评语和是否通过，就能开始比较新旧结果。

CREATE TABLE IF NOT EXISTS kp_trials (
    id             TEXT PRIMARY KEY,
    product_id     TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    revision_no    INTEGER NOT NULL,          -- 用哪一版试的
    question       TEXT NOT NULL,
    answer         TEXT,
    expected_points TEXT,
    verdict        TEXT NOT NULL CHECK (verdict IN ('passed', 'failed', 'inconclusive')),
    comment        TEXT,
    tried_by       TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (product_id, revision_no)
        REFERENCES kp_revisions(product_id, revision_no) ON DELETE CASCADE
);

COMMENT ON COLUMN kp_trials.revision_no IS
    '试用绑定具体修订——换了修订，旧的试用结论不自动继承，否则「试过了」会变成一句空话';

CREATE INDEX IF NOT EXISTS idx_kp_trials_revision
    ON kp_trials(product_id, revision_no, created_at);
