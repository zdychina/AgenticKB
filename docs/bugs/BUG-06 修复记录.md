# BUG-06 修复记录：死信任务与失败文档缺少重放入口

对应分析：[BUG-06（P2）死信任务与失败文档缺少重放入口，修复后无法批量恢复.md](./BUG-06%EF%BC%88P2%EF%BC%89%E6%AD%BB%E4%BF%A1%E4%BB%BB%E5%8A%A1%E4%B8%8E%E5%A4%B1%E8%B4%A5%E6%96%87%E6%A1%A3%E7%BC%BA%E5%B0%91%E9%87%8D%E6%94%BE%E5%85%A5%E5%8F%A3%EF%BC%8C%E4%BF%AE%E5%A4%8D%E5%90%8E%E6%97%A0%E6%B3%95%E6%89%B9%E9%87%8F%E6%81%A2%E5%A4%8D.md)（已勘误）。**按勘误后范围实施：只修 LLM 侧**（KB 侧维持现状——`document_ids` 选择性挖掘 + 前端"筛选失败 → 多选 → 挖掘选中"已可用，一键路由属便利性改进，未做）。

## 修复内容

### 1. 后端批量重放端点 `POST /api/v1/tasks/retry-batch`（`llm_service/api/tasks.py`）

- **两种圈定**：`task_ids`（UI 多选/本页，1–256 个，对齐 `BatchStatusRequest` 上限）或
  筛选模式 `status` / `task_type` / `created_after` + `limit`（1–1000，默认 500，
  `ORDER BY created_at`）。
- **`dry_run` 默认 True**（对齐 `CleanupRequest` 的安全默认）：漏传字段只回
  `matched` + `task_ids` 预览，不动库。
- **重排语义与单任务 `retry_task` 完全一致**（勘误指出的真实复用点——路由内 SQL
  UPDATE，非不存在的 `_mgr.retry()`）：`status='queued'`、`attempt_count=0`、
  清 `started_at`/`finished_at`/`lease_expires_at`、`available_at=now`。
- **状态守卫**：matched SELECT 与重排 UPDATE 均带 `status = ANY(可重试集)`——
  圈定与执行之间漂移到 running 的任务计入 `skipped`，不会把运行中/成功任务拉回队列。
- 逐条 `_bus.emit(task_id, "submitted", "task retried (batch)")`（与单任务重试的
  事件类型一致，message 注明批量来源）。
- status 白名单与 `retry_task` 同集（failed / dead_letter / cancelled），非法值、
  非法 `created_after` 时间戳 → 400；请求模型 `RetryBatchRequest` 放
  `llm_service/models.py`。

### 2. 前端 API 封装（`kb-ui/src/api/llm.ts`）

- `retryTask(taskId)`：单任务重试（详情页用）。
- `retryTasksBatch(payload)`：批量，返回 `{ dry_run, matched, requeued, skipped, task_ids }`。

### 3. 任务详情页重试按钮（`LlmTaskDetailView.vue`）

- header 操作区补"重试任务"（`failed`/`dead_letter`/`cancelled` 时可见，与"取消任务"
  互斥出现），成功后刷新详情并**恢复轮询**（终态曾 `stopPolling`，重排后任务回到
  queued 需要继续跟踪）。

### 4. 任务列表批量重试（`LlmView.vue`）

- 表格加 selection 列；**勾选框点击不触发 row-click 跳详情**
  （`closest('.el-checkbox')` 守卫）。
- **"重试选中（n）"**：多选圈定，计数只含可重试状态。
- **"重试本页全部（n）"**：状态筛选为 failed / dead_letter / cancelled 时出现。
- 两者均走 `ElMessageBox.confirm`（明示"重试次数将清零"）→ `dry_run: false` 执行 →
  提示 `requeued` / `skipped`（状态漂移）→ 刷新任务列表与统计。

## 分析文档验证项对账（§5）

| 验证项 | 结果 | 测试 |
|---|---|---|
| 1 `dry_run` 数量预览；重放后死信数下降、任务重新排队 | ✅ 契约由单测锁定（预览不动库、重排语义=单任务 retry、事件补发）；线上死信下降需部署后实操 | test_retry_batch.py 全套 |
| 2 库侧"仅重试失败项"只重跑失败文档 | 不适用——勘误后 KB 侧维持现状，现有选择性挖掘即可达成 | — |
| 3 重复点击不重复入队 | ✅（返回形态差异：第二次 matched=0 而非 skipped——matched SELECT 已排除重新 queued 的任务；UI 侧按钮计数同步归零消失） | test_dry_run_previews_matched_without_touching_db、test_ids_mode_requeues_only_retryable |

## 修改文件清单

- 新建：`llm_service/tests/test_retry_batch.py`（8 例）、本记录
- 修改：`llm_service/models.py`（`RetryBatchRequest`）、`llm_service/api/tasks.py`
  （retry-batch 端点）、`kb-ui/src/api/llm.ts`（`retryTask` / `retryTasksBatch`）、
  `kb-ui/src/views/llm/LlmTaskDetailView.vue`（重试按钮）、
  `kb-ui/src/views/LlmView.vue`（selection 列 + 批量重试按钮与逻辑）

## 测试结论

- `python -m pytest llm_service/tests -q`：**153 过 + 5 既有 skip**（含新增 8 例：
  dry_run 预览不动库、ids 圈定只重排可重试并清零重试次数、圈定后状态漂移计入
  skipped、筛选模式 task_type/时间窗/limit、无匹配不动库、非法 status 400、
  非法 created_after 400、模型约束与默认值）。
- `cd kb-ui && npm run test`：**958/958 过**（128 文件）；`npm run build`
  （vue-tsc + vite）：**通过**。

## 接受的风险与遗留事项

1. KB 侧 `retry-failed` 一键路由未实现（勘误裁定为便利性改进；如后续要做：后端按
   `status='failed'` 收集 ids 走同一 mine 提交路径即可）。
2. 前端 Llm 视图（LlmView / LlmTaskDetailView）**本无任何 spec 覆盖**，本轮未新增
   前端用例——以 vue-tsc + build + 后端契约测试为门。
3. 生产部署后建议按验证项 1 实操一轮（2026-11-26 快照：死信 11、失败 4）。
