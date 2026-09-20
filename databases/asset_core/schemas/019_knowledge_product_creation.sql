-- 019 · 知识制品制作面（52号计划 P2，2026-09-20）。
--
-- 018 建的是载体（制品、修订、对象、边、证据）；本迁移建的是**制作过程**：制作
-- 实例、任务票据、提交回执、审核记录。对应 50号 §4.1 八项能力里的中间四项。
--
-- 设计要点（均来自 50号）：
--   · 票据是短期、可撤销、绑定范围与输出结构的凭证，不是管理员密钥；库里只存
--     哈希，明文只在签发那一刻返回一次，页面与日志都脱敏。
--   · 提交幂等靠 (product_id, submission_id) 主键——网络重试不重复写。
--   · 成果只能经 submit_creation_result 进入草稿；聊天回复、容器里的文件都不算。

-- ─────────────────────────────────────────────────────────────
-- 1. 制作实例
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kp_creation_instances (
    id                  TEXT PRIMARY KEY,
    product_id          TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    definition_revision INTEGER NOT NULL,
    base_draft_revision INTEGER NOT NULL,      -- 启动时的草稿修订，用于识别「定义变过了」
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'running', 'paused', 'completed', 'cancelled', 'failed')
    ),
    harness_session_id  TEXT,                  -- DSH 会话映射；平台侧适配器写
    last_event_cursor   TEXT,                  -- 事件补读位置（断线后从这里续）
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

COMMENT ON COLUMN kp_creation_instances.base_draft_revision IS
    '实例启动时的草稿修订。Agent 提交时带的 based_on_draft_revision 要与「当前」比，不是与它比——它只用于追溯这次制作是从哪一版起步的';
COMMENT ON COLUMN kp_creation_instances.last_event_cursor IS
    'DSH 原生事件的补读位置。实时连接断开后用历史接口从此处补齐；不能因为最终回复出现就假定中间过程都已保存（50号 §3.3）';

CREATE INDEX IF NOT EXISTS idx_kp_creation_instances_product
    ON kp_creation_instances(product_id, created_at DESC);

-- ─────────────────────────────────────────────────────────────
-- 2. 任务票据
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kp_task_tickets (
    id          TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES kp_creation_instances(id) ON DELETE CASCADE,
    product_id  TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,          -- sha256(明文)。明文只在签发时返回一次
    status      TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'revoked')
    ),
    issued_at   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT,
    revoked_reason TEXT
);

COMMENT ON TABLE kp_task_tickets IS
    '短期任务票据。库里只存 sha256 哈希——明文泄露即等于越权读资料，所以它不落库、不进日志、不回显（50号 §3.1）';
COMMENT ON COLUMN kp_task_tickets.status IS
    '只有 active/revoked 两态；过期是时间判定（expires_at），不靠后台任务改状态——少一条能失效的路径就少一处漏判';

CREATE INDEX IF NOT EXISTS idx_kp_task_tickets_instance
    ON kp_task_tickets(instance_id, status);

-- ─────────────────────────────────────────────────────────────
-- 3. 提交回执
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kp_submissions (
    product_id               TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    submission_id            TEXT NOT NULL,     -- Agent 生成的稳定 UUID
    instance_id              TEXT NOT NULL REFERENCES kp_creation_instances(id) ON DELETE CASCADE,
    based_on_draft_revision  INTEGER NOT NULL,
    outcome                  TEXT NOT NULL CHECK (
        outcome IN ('pending', 'accepted', 'rejected', 'conflict')
    ),
    written_revision         INTEGER,           -- accepted 时写出的新草稿修订
    accepted_count           INTEGER NOT NULL DEFAULT 0,
    rejected_count           INTEGER NOT NULL DEFAULT 0,
    receipt_json             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at               TEXT NOT NULL,
    PRIMARY KEY (product_id, submission_id)
);

COMMENT ON TABLE kp_submissions IS
    '提交回执。主键即幂等键——同一 submission_id 重复提交返回原回执，不重复写（50号 §5.3）。被拒的提交同样留痕：否则「Agent 说交了但草稿里没有」无从对账';
COMMENT ON COLUMN kp_submissions.outcome IS
    'pending 是认领态：处理前先用 INSERT ... ON CONFLICT DO NOTHING 抢主键，抢到才干活。这样真并发（不只是网络重传）下也只有一方会写草稿，不需要额外的锁';

CREATE INDEX IF NOT EXISTS idx_kp_submissions_instance
    ON kp_submissions(instance_id, created_at);

-- ─────────────────────────────────────────────────────────────
-- 4. 审核记录
-- ─────────────────────────────────────────────────────────────
-- P2 只建表不走流程：人审 API 与「驳回意见发回同一实例续跑」属于 P4。

CREATE TABLE IF NOT EXISTS kp_reviews (
    id              TEXT PRIMARY KEY,
    product_id      TEXT NOT NULL REFERENCES kp_products(id) ON DELETE CASCADE,
    revision_no     INTEGER NOT NULL,
    instance_id     TEXT REFERENCES kp_creation_instances(id) ON DELETE SET NULL,
    reviewer        TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    notes           TEXT,
    per_object_json JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 逐对象意见，驳回续跑的输入
    created_at      TEXT NOT NULL,
    FOREIGN KEY (product_id, revision_no)
        REFERENCES kp_revisions(product_id, revision_no) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kp_reviews_revision
    ON kp_reviews(product_id, revision_no, created_at);
