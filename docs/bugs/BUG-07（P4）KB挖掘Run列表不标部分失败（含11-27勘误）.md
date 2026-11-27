# BUG-07（P4，原报 P3）KB 挖掘 Run 列表不标部分失败：`completed` 与全成功不可区分

> 级别：P4（信息透传不完整——仅列表层缺标记；失败原因本身可追溯）
> 环境：AgenticKB v1.1.41 发现；2026-11-27 在 master（1.1.54）逐条复核修订
> 发现：2026-11-19；勘误：2026-11-27（见 §7，原报标题"挖掘失败原因不可追溯"不成立）

## 1 现象（核对后）

一个含 2 篇文档的库（1 篇 `.md` 正常、1 篇 `.exe` 解析失败）触发自动挖掘后，`GET /api/kb/{kb_id}/runs` 返回：

```json
{"id":"adcdb629…","status":"completed","current_stage":"done",
 "total_documents":2,"new_count":0,"failed_count":1,
 "committed_count":1,"error_summary":null}
```

- ✅ 属实：`status` 是 `completed`，且该列表端点**不返回 `metadata_json`**（`mining/kb/db.py:2055-2070` 的 SELECT 无此列），`has_failures`/`partial_success` 均不可见——仅看列表无法与全成功区分，只能靠 `failed_count>0` 推断。
- ❌ 原报失实："没有任何路由暴露 `error_message`"。`GET /api/runs/{run_id}/documents?status=failed&domain=…`（`mining/api/routes/runs.py:404-448`，自仓库 init 0dd66ca 起即有）逐文档返回 `error_message`，带分页与 `status`/`action`/`has_error` 过滤、`require_run_read` 权限。
- ❌ 原报失实："UI 点不进原因"。挖掘面板 run 行可点击（`kb-ui/src/components/kb/KbMiningPanel.vue:61` 的 `@row-click` → 285-286 跳 `kb-run-detail`），详情页以红字展示每篇失败文档的 `error_message`（`kb-ui/src/views/kb/KbRunDetailView.vue:199`），并有"任务部分完成"横幅（58-65 行）与含 failed 的文档过滤 tab（36号 §九，73b00b2 2026-09-05 专做）。

对照：全失败场景走 `run_status='failed'` 分支并写 `error_summary`（master `run.py:2892-2908`），所以只有"部分失败"在**列表层**无摘要。

## 2 复现（修订）

1. 建库并绑定任意范式。
2. 上传 1 篇正常 `.md` + 1 个 `.exe`（见 BUG-02）。
3. `GET /api/kb/{kb_id}/runs` → 上述响应；**列表页**只显示"完成 · 失败 1"，不点进去不知道失败原因。
4. 点击该 run 行 → 详情页可见失败文档与红字原因——原报"点不进原因"不成立，实际缺口止步于列表层。

## 3 根因（核对后）

"部分成功 = completed + metadata-only 标记"是 36号 §六/§九 的既定设计，信息链本身完整，缺口只在 KB 列表端点：

- `knowledge_mining/mining/jobs/run.py`：`failed_count>0` 且有产出 → `run_status` 保持 completed，`has_failures`/`partial_success`/`failed_count` 只写进 `metadata_json`（master 1.1.54：document_index 分支 2836-2864，`elif failed_count > 0` 兜底 2881-2890；v1.1.41 为 ~2821-2829）；
- `tracker.complete_run`（`mining/runtime/__init__.py:32-43`）**没有 `error_summary` 参数**——摘要只在 `fail_run` 路径写；
- `GET /api/kb/{kb_id}/runs` 的列表查询（`mining/kb/db.py:2055-2070`，原报误写"约 1677 行"）SELECT 无 `metadata_json` 列 → 列表响应看不到部分失败标记。
- 但 run 详情端点 `GET /api/runs/{run_id}`（`mining/api/routes/runs.py:316-338`，327 行含 `metadata_json`）已返回该标记，UI"任务部分完成"横幅（`KbRunDetailView.vue:254` 的 `partialStats`）正取自于此——原报"前端与 API 使用者都看不到 `has_failures`"不成立。

**结论：唯一真实缺口是 KB runs 列表端点不透传 `has_failures`/`partial_success`，属 cosmetic 级信息补齐，不是"不可追溯"。**

## 4 修复建议（修订）

1. **（保留，推荐）列表暴露部分失败**：`list_kb_runs` SELECT 增加 `metadata_json`（或仅取 `has_failures`/`partial_success` 键），`GET /api/kb/{kb_id}/runs` 响应透传；前端列表状态标签改为"完成（N 篇失败）"并标黄。改动面小：一处 SQL + 序列化 + 一个标签。
2. **（可选）部分失败也写摘要**：`failed_count>0` 时写 `error_summary = _partition_failure_summary(...)`（`run.py:2955-2966`）。语义上"completed 但有摘要"可接受，不破坏全失败路径。与 1 二选一即可，不必都做。
3. ~~补逐文档失败查询~~ **作废**：`GET /api/runs/{run_id}/documents?status=failed` 已存在（含权限、分页、过滤），无需 KB 侧重复建设。
4. ~~UI 弹层~~ **作废**：run 详情页已有失败文档展示与过滤（`KbRunDetailView.vue:199`）。

## 5 验证（按建议 1）

1. 构造 1 成功 + 1 失败 → `GET /api/kb/{kb_id}/runs` 返回 `has_failures: true` / `partial_success: true`。
2. 全成功库 → `has_failures: false`，UI 标签不变。
3. 全失败库 → 仍走 `status='failed'` + 摘要（回归 `run.py:2892` 分支）。
4. （既有能力回归）`GET /api/runs/{run_id}/documents?status=failed` 返回失败文档与原因；详情页红字可见。

## 6 相关文件（行号按 master 1.1.54）

| 文件 | 位置 | 说明 |
|---|---|---|
| `knowledge_mining/mining/jobs/run.py:2836-2864, 2881-2890` | 状态判定：部分失败 → completed + metadata | |
| `knowledge_mining/mining/jobs/run.py:2892-2908` | 全失败写 `error_summary` | |
| `knowledge_mining/mining/jobs/run.py:2955-2966` | `_partition_failure_summary` | |
| `knowledge_mining/mining/runtime/__init__.py:32-43` | `complete_run`（无 `error_summary` 参数） | |
| `knowledge_mining/mining/runtime/__init__.py:177-180` | `fail_document`（写 `error_message`） | |
| `knowledge_mining/mining/kb/routes/kbs.py:319` | `GET /{kb_id}/runs` | |
| `knowledge_mining/mining/kb/db.py:2055-2070` | runs 列表查询（无 `metadata_json` 列，本 BUG 唯一改动点） | |
| `knowledge_mining/mining/api/routes/runs.py:404-448` | 既有 `GET /{run_id}/documents`（已暴露 `error_message`） | |
| `knowledge_mining/mining/api/routes/runs.py:316-338` | 既有 run 详情（返回 `metadata_json`） | |
| `kb-ui/src/components/kb/KbMiningPanel.vue:61, 285-286` | run 行点击 → `kb-run-detail` | |
| `kb-ui/src/views/kb/KbRunDetailView.vue:58-65, 199, 254` | 部分完成横幅 / 失败红字 / `partialStats` | |
| `kb-ui/src/api/mining.ts:37`、`kb-ui/src/stores/mining.ts:68,96` | 前端调 `/api/runs/{id}/documents` 的既有链路 | |

## 7 勘误记录（2026-11-27 核对）

原报（2026-11-19，v1.1.41）三处失实，本次修订更正：

1. **"没有任何路由暴露 `error_message`"** —— 检索范围只限 `mining/kb/routes/`，漏了 `mining/api/routes/runs.py:404` 的 `GET /api/runs/{run_id}/documents`（仓库 init 0dd66ca 起即有，含 status 过滤与分页）。
2. **"UI 挖掘面板点不进原因"** —— v1.1.41 起 run 行可点击进详情页，失败文档与原因、部分完成横幅均已展示（36号 §九，73b00b2 2026-09-05）。
3. **行号**：db.py 列表查询实为 2055（原报"约 1677"）；run.py 相关段 v1.1.41 在 ~2821-2870，master 1.1.54 漂移至 ~2836-2928。

定级由 P3 降为 P4：失败原因可追溯（详情页与 `/api/runs` 既有端点均可达），实际缺口仅为 KB 列表端点缺少部分失败标记字段（建议 1，一处 SQL + 透传 + 标签）。

## 8 修复记录（2026-11-27，按建议 1）

| 文件 | 改动 |
|---|---|
| `knowledge_mining/mining/kb/db.py` | `list_kb_runs` SELECT 增加 `metadata_json`，出参透传 `has_failures`/`partial_success` 两个布尔并弹出原 metadata（不外泄 readiness 等内部字段；字符串形态防御同 run.py） |
| `knowledge_mining/tests/kb/test_kb_db.py` | 新增 `test_list_kb_runs_surfaces_partial_failure_flags`：部分失败 → 两标记 True 且响应无 `metadata_json` 键；全成功 → 两标记 False |
| `kb-ui/src/types/kb.ts` | `KbRunRecord` 增加可选 `has_failures`/`partial_success` |
| `kb-ui/src/utils/runStatus.ts` | 新增 `isPartialRun`：completed 且（标记为 true 或 `failed_count>0` 兜底——标记字段上线前的旧 run 没有 metadata） |
| `kb-ui/src/components/kb/KbMiningPanel.vue` | 状态列经 `runTagType`/`runTagLabel` 渲染：部分失败 → 黄标"完成（N 篇失败）"，列宽 110→150 |
| `kb-ui/src/locales/{zh,en}/kbManage.ts` | 新增 `kbManage.mining.completedPartial`（"完成（{n} 篇失败）" / "Done ({n} failed)"） |
| `kb-ui/src/utils/__tests__/runStatus.spec.ts` | `isPartialRun` 用例：metadata 标记、旧数据兜底、全成功、failed/running 不误判 |

验证结果：

- 后端：`tests/kb/test_kb_db.py` 真库（远程 kb_db_test，单进程）**14 passed**，含新用例。
- 前端：`runStatus.spec.ts` + `KbMiningPanelQueue.spec.ts` **7 passed**；`npm run build` 通过。
- 端到端（上传 `.exe` → 列表黄标）未实测——需起完整服务，留待部署后按 §5 步骤 1-3 走查。
- 建议 2（部分失败也写 `error_summary`）按"二选一"约定不做；建议 3/4 已在 §4 作废（能力既有）。
