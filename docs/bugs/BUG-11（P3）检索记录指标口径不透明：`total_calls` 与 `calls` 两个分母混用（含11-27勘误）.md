# BUG-11（P3）检索记录指标口径不透明：`total_calls` 与 `calls` 两个分母混用

> 级别：P3（数据可信度）
> 环境：AgenticKB v1.1.41 发现；2026-11-27 在 master（1.1.54）逐条核对
> 发现：2026-11-19；勘误：2026-11-27（见 §7——核心事实属实（P3 维持），一处事实错误更正 + 三处定位勘误 + 一处补充）

## 1 现象

`GET /api/retrieval-records/summary?window=7d` 同时返回两个"调用量"，且比例用另一个分母：

```json
{"total_calls":50,"calls":10,"no_result":1,"failed":6,
 "no_result_rate":0.1,"failure_rate":0.12,
 "trend":[{"date":"2026-11-16","total_calls":10,"calls":0}, ...]}
```

- `total_calls=50` vs `calls=10` → 界面卡片"调用量 50"，但"零结果率 10%"实际是 `1/10`；
- 某一天甚至出现 `total_calls=10` 而 `calls=0`（有调用但无"检索类调用"），用户会认为数据自相矛盾。

（11-27 核对：示例 JSON 与真实代码的分母关系完全自洽——`no_result_rate=1/10` 对应 `calls` 分母、`failure_rate=0.12=6/50` 对应 `total_calls` 分母；这组示例反而佐证了 §7 更正 1：`failure_rate` 的分母是 `total_calls`，并非 §3 所称的 `calls`。"比例用另一个分母"仅对零结果率成立。URL 参数名 `window=7d` 系笔误，实际参数为 `days`，见 §7 更正 2。）

（11-27 补充，原报未发现：混淆比原报描述更直接——"使用分析"页同时有"调用量"与"检索调用"两张卡并排（`RetrievalRecordsAnalysis.vue:28-29`，分别绑定 `total_calls` 与 `calls`，50 vs 10 同屏对比）；趋势图也画 `total_calls`（`:101`）；"记录明细" tab 的卡片同口径绑定（`RetrievalRecordsPanel.vue:4`）。）

## 2 复现

1. `/retrieval-records` → "使用分析"tab。
2. 对照卡片数字与接口 `summary` 字段。
3. 切换"近 7 天 / 近 30 天"，差异同样存在。

（11-27 核对：复现路径属实——"使用分析" tab 与 7/30/90 天档位均存在（`records.ts:13,16-18`）；前端实发参数为 `days`（`api/ops.ts:20-21`），90 天档差异同样存在。）

## 3 根因

口径差异（需产品确认定义）：

- `total_calls`：`knowledge_access_records` 中所有记录（含 MCP 的 `get_knowledge` 读取类操作、上传类操作）；
- `calls`：仅统计"检索类"（`operation='search'` 等）；
- `no_result_rate` / `failure_rate` 的分母取 `calls`，而 UI 卡片标题写"调用量"并用 `total_calls` 展示。（11-27 勘误：此句对 `failure_rate` 不成立——其分母实为 `total_calls`，见 §7 更正 1）

数据来源：`knowledge_mining/mining/infra/retrieval_records_db.py` 的 summary 聚合 SQL（约 640-690 行，`source AS key, COUNT(*)`、按状态计数）；前端 `kb-ui/src/components/retrieval/RetrievalRecordsAnalysis.vue`。（11-27 勘误：定位漂移——summary 聚合实为约 545-616 行；`source AS key, COUNT(*)` 在 645 行、属来源分布 `_breakdown_on_conn`，被原报混入 summary 引用；路由文件实为 `mining/api/routes/retrieval_records.py` 而非 `kb/routes/`，见 §7 更正 3/4）

## 4 修复建议

1. **字段改名显式化**：`total_calls` → `all_operations`，`calls` → `search_calls`（后端一并改，前端同步）；或保留字段但补 `denominator` 说明：

```json
{"search_calls":10,"all_operations":50,
 "no_result_rate":{"value":0.1,"numerator":1,"denominator":10}}
```

2. **UI 标注**：卡片副标题写明"检索类调用（不含读取/上传）"，比例悬浮提示"1 / 10"。
3. **趋势图对齐**：趋势同时给两条线（全部操作 vs 检索），或在图上注明当前口径。
4. **帮助中心**补一段"指标口径说明"（放第 14/16 章）。

（11-27 补充：改名时必须一并定 `failure_rate` 的口径——现状是"全操作失败数 / 全操作调用量"，内部自洽但与零结果率的检索口径不一致；统一到检索口径或保留双口径均可，建议 1 的 `denominator` 显式结构恰好能表达后者。另 `failed` 计数为全操作口径（`retrieval_records_db.py:543,549`，不过滤 `operation='search'`），若统一到检索口径需同步收窄。）

## 5 验证

1. 造 3 次检索（1 次零结果）+ 2 次 `get_knowledge` 读取 → `search_calls=3`、`all_operations=5`、`no_result_rate=1/3`。
2. UI 卡片数字与接口一致，副标题口径正确。
3. 7/30/90 天窗口切换后比例自洽（分子 ≤ 分母）。
4. （11-27 新增）上述场景中令 2 次 `get_knowledge` 读取之一为 `denied` → 若失败率保留全操作口径则为 `1/5=0.2`（而非 `1/3`）——用例须按 §4 补充选定的口径断言，避免修复时又引入一个未声明分母。

## 6 相关文件（行号按 master 1.1.54）

| 文件 | 位置 |
|---|---|
| `knowledge_mining/mining/infra/retrieval_records_db.py:543-622` | summary 聚合：`:543` failed 状态集、`:546-553` 主查询（双口径计数）、`:607-612` 字段与两比率（`:611` 零结果率 /calls、`:612` 失败率 /total_calls） |
| `knowledge_mining/mining/infra/retrieval_records_db.py:639-648` | `_breakdown_on_conn` 来源分布（`source AS key, COUNT(*)`，原报误归入 summary 引用） |
| `knowledge_mining/mining/services/retrieval_records.py:65-104` | summary 组装（仓库结果原样透传；`:225-245` `_empty_summary` 同携双字段） |
| `knowledge_mining/mining/api/routes/retrieval_records.py:257-294` | summary 路由（`:24` Operation 四值枚举、`:261` days 参数；原报误写 `kb/routes/`） |
| `kb-ui/src/components/retrieval/RetrievalRecordsAnalysis.vue:28-31,101` | "使用分析"四卡（调用量→total_calls、检索调用→calls）与趋势 |
| `kb-ui/src/components/retrieval/RetrievalRecordsPanel.vue:4-6` | "记录明细"卡片（同口径绑定，原报未列） |
| `kb-ui/src/locales/zh/records.ts:24-27` | i18n 文案（'调用量'/'检索调用'/'零结果率'/'失败率'） |

## 7 勘误记录（2026-11-27 核对）

核心事实逐条核对**属实**，定级 P3 维持；但 §3 有一处事实错误、定位信息三处勘误，且混淆程度比原报描述更重（多一张"检索调用"卡直接同屏对比）。

属实部分：

- **双口径字段并存**：`retrieval_records_db.py:546-547` 主查询确为 `total_calls = COUNT(*)`（全操作）与 `calls = COUNT(*) FILTER (WHERE operation = 'search')`（仅检索），summary（`:607-608`）与 trend（`:563-566`）均双字段返回；service 层（`retrieval_records.py:90-104`）原样透传；
- **`no_result_rate` 分母 = `calls`**：`retrieval_records_db.py:611` `no_result / calls`，"零结果率 10% 实际是 1/10"的表述与代码一致；
- **UI"调用量"卡绑定 `total_calls`**：`RetrievalRecordsAnalysis.vue:28` 与 `RetrievalRecordsPanel.vue:4`，i18n `records.metric.calls = '调用量'`（`locales/zh/records.ts:24`）；趋势图也画 `total_calls`（Analysis `:101`）；
- **`total_calls` 含读取/上传类**：`Operation = Literal["search","read","upload","replace"]`（`api/routes/retrieval_records.py:24`），MCP `get_knowledge` 系列映射为 read（`kb/services/mcp_key_service.py:83-91`），读取与上传记录全部计入 `COUNT(*)`；
- **"某天 total_calls=10 而 calls=0"结构上成立**：趋势按天补零（`retrieval_records_db.py:576-587`），某日只有 read/upload 记录即此形态；
- **复现路径属实**："使用分析" tab、7/30/90 天切换均存在；
- **示例 JSON 自洽**：`no_result_rate=1/10` 与 `failure_rate=0.12=6/50` 都与真实代码吻合（见更正 1——示例反而证明 failure_rate 分母是 `total_calls`）。

原报更正（4 项）：

1. **§3"failure_rate 的分母取 calls"错误**：实为 `failed / total_calls`（`retrieval_records_db.py:612`）。真实情况比原报更拧巴——两个比率用**两个不同分母**（零结果率→检索口径，失败率→全操作口径），并非同一分母。佐证：原报自己的示例 JSON（failed=6、total_calls=50、failure_rate=0.12）恰为 6/50，与正文自相矛盾。另 `failed` 计数为全操作口径（`retrieval_records_db.py:543,549` `status IN ('denied','invalid','timeout','failed')`，不过滤 `operation='search'`）。已纳入 §4 补充与 §5 第 4 条：修复时须新增口径决策点；
2. **示例 URL 参数名**：`?window=7d` → 实际参数为 `days`（int，1-90，默认 7，`api/routes/retrieval_records.py:261`），路由无 `window` 参数；前端也发 `days`（`api/ops.ts:20-21`）；
3. **路由文件路径**：实为 `mining/api/routes/retrieval_records.py`，非原报的 `mining/kb/routes/`；
4. **行号漂移**：summary 聚合 SQL 实为约 545-616 行（原报"约 640-690"）；`source AS key, COUNT(*)`（645 行）属 `_breakdown_on_conn` 来源分布，原报把它混进了 summary 引用。

补充（原报未发现）：

- **"使用分析"页有第三层混淆**：除"调用量"（total_calls）外还有一张"检索调用"卡直接显示 `calls`（`RetrievalRecordsAnalysis.vue:29`），50 vs 10 同屏并排，比原报描述的"卡片数字与比例分母不一致"更直观可感；"记录明细" tab 卡片（`RetrievalRecordsPanel.vue:4-6`）同口径绑定。

小处措辞（不影响结论）：

- §2 只列"近 7 天 / 近 30 天"，实际还有"近 90 天"档（`records.ts:18`）；
- §3 "`operation='search'` 等"的"等"不准确——`calls` 的口径恰为仅 `operation='search'`，不含其他操作。
