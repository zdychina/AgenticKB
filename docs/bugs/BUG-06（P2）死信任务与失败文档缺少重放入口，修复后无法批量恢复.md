# BUG-06（P2）死信任务与失败文档缺少重放入口，修复后无法批量恢复

> 级别：P2（可运维性；阻塞 P1 修复后的数据恢复）
> 环境：AgenticKB v1.1.41
> 发现：2026-11-19
> 勘误：2026-11-26（按代码与生产库核查，共修正三处与事实不符：KB 侧已有"筛选失败→多选→挖掘选中"替代路径且默认增量不重跑成功文档；`_mgr.retry()` 不存在；运行时数字为过期快照。LLM 侧核心主张——无 UI 重试按钮、无批量重放接口——属实，bug 范围收窄为 LLM 侧重放的人机工程）

## 1 现象

- LLM 任务中心显示死信 110 条、失败 2 条（2026-11-19 时点快照）。**勘误**：2026-11-26 核查生产（39.96.13.112，各服务实连库 `coremasterkb_v2`，已经 `pg_stat_activity` 确认）为死信 11、失败 4、succeeded 16。数字随运维操作变动，修复前应以当时查询为准。
- 知识库侧：原文称"9k+ 篇文档处于挖掘失败，等待重试"。**勘误**：当前所有库 `asset_documents` 合计仅 25 条，`mining_run_documents` 失败 6 条（共 18 行）——"9k+"在任何库均不存在，11-19 快照已不可复现。
- LLM 服务**已有**单任务重试接口：`POST /api/v1/tasks/{task_id}/retry`（`llm_service/api/tasks.py:152`，允许 failed/dead_letter/cancelled 重新入队）。✅ 核查属实
- 但：
  1. UI 任务详情页**没有重试按钮**（只有"取消"，`kb-ui/src/views/llm/LlmTaskDetailView.vue:22`，`:602` 仅 `cancelTask`）；✅ 属实
  2. 没有**批量**重放接口（按类型/时间窗/状态）——后端有 `batch-cancel`（`tasks.py:184`）但无任何 batch-retry；✅ 属实
  3. 知识库侧缺"按库重挖失败项"的**一键**入口。**勘误**：原称"只能手动全库重挖，会重跑已成功文档"不成立：
     - 选择性挖掘 API 已存在：`POST /api/kb/{kb_id}/mine` 的 `MineKbBody.document_ids`（`mining.py:43-48`）；
     - 前端已接线：文件列表有 `status=failed` 筛选（"挖失败清单"管理场景，`KbFileManager.vue:401`、注释 `:413`）+ 多选 + "挖掘选中/强制重挖选中"按钮（`KbFileManager.vue:80`，`:647` 调 `kbApi.mineKb(kbId, ids, forceRedo)`）；
     - 默认整库增量也**不会**重跑未变化的成功文档：lifecycle SKIP（`run.py:1219-1258`，`skip_reason="unchanged"`，零算子执行），仅 `force_redo=true` 才无视哈希去重重跑（`mining.py:45`）。
     - 即"仅重试失败项"今天已可达成（筛选失败 → 全选 → 挖掘选中），缺的只是一键便捷性。

结果：LLM 侧死信/失败任务只能逐条调 API（无 UI、无批量），恢复成本高且易遗漏；KB 侧已有替代路径，一键入口属便利性改进而非能力缺口。

## 2 复现

1. 打开 `/llm?tab=tasks`，筛选"死信" → 进入任一任务详情 → 操作区只有"取消/返回"，无重试。✅
2. `grep -r "tasks/.*retry" kb-ui/src` → 无命中，确认前端未接线（`api/llm.ts` 仅 `cancelTask`，`:46`）。✅
3. 知识库详情"挖掘"tab → 只有整库"挖掘全部/强制重挖全部"（`KbMiningPanel.vue:35`）。**勘误**：挖掘 tab 确实无一键"仅失败项"，但文件列表侧已支持 `status=failed` 筛选 + 多选批量挖掘（见 §1 第 3 条），并非只能全库重挖。

## 3 根因

- 后端能力已具备但**未暴露**：`llm_service/api/tasks.py:152` 的 `retry_task` 只支持单 id（实现为路由内直接 SQL UPDATE，`:168-176`）。✅ 属实
- 前端未实现对应按钮（`kb-ui/src/api/llm.ts` 没有 `retryTask`；原文写"llm_task API 封装"，实际文件名为 `llm.ts`）。✅ 属实
- 挖掘侧：`knowledge_mining/mining/jobs/run.py:285` 的 `_is_retry_rejected_row`——status=='failed' 或 processing 且 `metadata.retry_required` → 忽略可复用节点事件重执行；capability tag 名即 `retry_rejected`（`run.py:1316`）。**勘误**：原文据此得出"失败文档单独重试没有面向用户的路由"不成立——`document_ids` 选择性挖掘路由已存在（`mining.py:43-48`）且 UI 已接线（§1 第 3 条），缺的只是"按状态一键收集 ids"的便捷封装。

## 4 修复建议

1. **LLM 侧批量重放**（推荐最小实现）：

```
POST /api/v1/tasks/retry-batch
body: {"status": ["dead_letter","failed"], "task_type": "embedding",
       "created_after": "2026-11-11T00:00:00Z", "limit": 500, "dry_run": true}
→ {"matched": 15, "requeued": 15, "skipped": 0, "task_ids": [...]}
```

- `dry_run=true` 先给数量预览（防误操作）。
- **勘误**：原文"复用 `retry_task` 的单任务逻辑，逐条 `_mgr.retry()`"——`_mgr.retry()` **不存在**：`task_manager.py` 只有 submit/insert_task_row/claim/complete/fail/cancel，无 retry 方法。单任务重试的实际落点是路由内 SQL UPDATE（`tasks.py:168-176`）；批量版应复用该 UPDATE 语义（按筛选条件批量置 queued、attempt_count=0，并逐条 `_bus.emit`），或先把这段逻辑下沉为 task_manager 方法再复用。

2. **UI 接线**（原文不变）：

- 任务列表支持多选 + "重试选中"；筛选"死信"后提供"重试本页全部"。
- 任务详情页补"重试"按钮（`dead_letter`/`failed` 时可用）。

3. **知识库侧"仅重试失败项"**：

```
POST /api/kb/{kb_id}/documents/retry-failed
→ 202 {"queued": n}
```

**勘误（价值重估）**：现有 `document_ids` 选择性挖掘 + 前端筛选已能达成同一效果，此路由的增量价值只是"免手动筛选"一步。若仍做：后端按 `status='failed'` 收集本库 ids 后走同一 mine 提交路径即可，不必新开执行逻辑；不触碰 `mined` 文档由 lifecycle SKIP 天然保证。

4. **幂等与防抖**：同库已有 open run 时现状是 **409 `kb_mining_busy` 拒绝**（`mining.py:113-115`），并非"直接复用"；保持该约束即可。

## 5 验证

1. 修复前：`dry_run` 返回当时快照数（2026-11-26 生产为死信 11 + 失败 4 = 15；原文 110 为 11-19 时点）；修复后重放 → 死信数下降，任务重新排队并成功（在 embedding 正常的前提下）。
2. 库侧：一个有 5 篇失败 / 5 篇成功的库执行"仅重试失败项"（或筛选失败 → 挖掘选中，现有功能即可验证）→ 只产生 5 篇的解析，成功文档的 `content_revision` 不变。
3. 重复点击 → 第二次返回 `skipped` 而非重复入队。

## 6 相关文件

| 文件 | 位置 | 核查 |
|---|---|---|
| `llm_service/api/tasks.py:152` | `retry_task`（单任务重试，已存在；SQL UPDATE 在 `:168-176`，batch-cancel 在 `:184`） | ✅ |
| `llm_service/runtime/task_manager.py` | dead_letter 状态机在 `fail()`（`:126-153`）；**勘误：无 `retry()` 方法** | ⚠️ 原文有误 |
| `knowledge_mining/mining/jobs/run.py:285` | `_is_retry_rejected_row`（增量重挖标记） | ✅ |
| `knowledge_mining/mining/kb/routes/mining.py:73` | 整库挖掘入口；**:43-48 `MineKbBody.document_ids` 选择性挖掘已存在（原文遗漏）** | ⚠️ 补充 |
| `kb-ui/src/components/kb/KbFileManager.vue:401/:647` | `status=failed` 筛选 + 多选"挖掘选中"（**原文遗漏：KB 侧已有替代路径**） | ⚠️ 补充 |
| `kb-ui/src/views/llm/LlmTaskDetailView.vue`、`kb-ui/src/views/LlmView.vue` | 需补 UI（详情页仅"取消任务"，`:22`/`:602`） | ✅ |
