# BUG-14 修复记录：一张网查询偶发上游错误原样透传

对应问题单：[BUG-14（P3）一张网查询偶发上游错误原样透传（前端看到 S3 XML）（含11-28勘误）.md](./BUG-14%EF%BC%88P3%EF%BC%89%E4%B8%80%E5%BC%A0%E7%BD%91%E6%9F%A5%E8%AF%A2%E5%81%B6%E5%8F%91%E4%B8%8A%E6%B8%B8%E9%94%99%E8%AF%AF%E5%8E%9F%E6%A0%B7%E9%80%8F%E4%BC%A0%EF%BC%88%E5%89%8D%E7%AB%AF%E7%9C%8B%E5%88%B0%20S3%20XML%EF%BC%89%EF%BC%88%E5%90%AB11-28%E5%8B%98%E8%AF%AF%EF%BC%89.md)（已核对：代码缺口属实；400+S3 XML 的透传路径未定因）。**按勘误后口径实施**：修的是「`OnenetQueryError` 未接 → 裸 500」与「上游报文可进异常文本」两个确证缺口；未定因的透传路径无法从仓库侧修复，本次修复保证走 mining 链的响应只会是 502 稳定码（若生产再出现 400+XML 原文，按勘误补充 3 查网关反代/mining 两级访问日志）。

## 修复内容

### 1. `knowledge_mining/mining/onenet/client.py` — 非 2xx 稳定类别化

- **`_post` 状态码/形状判定移出重试 try**（原 `raise_for_status` 抛的 `HTTPStatusError` 会被 `except` 捕获而退避重试）：
  - **4xx 快速失败，不重试**（不会自愈；原行为会白等 1.5s+3s 重试 3 次）：异常文本只含稳定类别+状态码——`onenet_upstream_error: upstream_status=400`；401/403 归类 `onenet_auth_error`（建议 4 的独立分类，运维可区分凭据问题与网络抖动）；
  - **5xx 保留退避重试**（1.5s/3s，最后一次不白等），耗尽后 `请求失败(3次): upstream_status=50x`；
  - **401 首次强制重取 token 后重试一次**（原行为保留），重取后仍 401 → `onenet_auth_error` 快速失败；
  - 200 非 JSON / 形状异常（缺 `searchResults`）：按瞬态退避重试（原行为），最终 `onenet_bad_shape`。
- **原始报文只进服务端日志**：`_log_excerpt()`（脱敏 `<Authorization>…</Authorization>` 与 `Bearer …` 回显 + 300 字符截断）——堵住原报列出的与未列出的全部三处泄漏点：`_post` 非 2xx、`get_token` 的 `res.text[:300]`、`_post` 形状异常分支的 `json.dumps(j)[:300]`。
- **`get_token` 请求前失败强化**：token 端点 4xx → `onenet_auth_error: token_status=40x`（不重试）；result 为空/非 JSON → 同类失败；绝不带着空/坏凭据发 search 请求。

### 2. `knowledge_mining/mining/onenet/routes.py` — 上游失败统一 502

新增 `_onenet_upstream_502(e)`：`logger.warning` 记异常文本（已无报文）后，按类别返回 **502 + BUG-08 约定的稳定码+中文文案**（`onenet_auth_error: 一张网凭据失效或未授权…` / `onenet_upstream_error: 一张网查询服务暂时不可用…`）。接入端点按勘误修正的清单：**search、probe、toc（缓存探测与扫描两处）、resync**——imports 不接（上游调用在后台任务，HTTP 层不抛）。

### 3. `kb-ui/src/views/onenet/OnenetAdminView.vue` — 错误呈现取响应体 detail

`handleError` 改走 BUG-08 后统一的 `apiErrorDetail`：502 的稳定码+中文文案直达用户（原实现只显 axios message，用户看不到类别区分）。

### 附带效果

resync/后台导入失败回写记录的 `error` 文本同样不再可能携带上游报文（旧版 `动态 token 获取失败: <上游body>` / `异常响应: <上游json>` 路径已堵）。

## 与问题单 §4 建议的对账（两处有意偏差）

| 建议 | 实施 | 偏差说明 |
|---|---|---|
| 1 统一 catch → 502 | ✅ | 响应形态用 BUG-08 字符串 `code: 中文`（前端 `apiErrorDetail` 按此取文案），非建议中的 dict detail；端点清单按勘误修正（含 resync、不含 imports） |
| 2 非 2xx 不透出报文 | ✅ | 且覆盖勘误补充 1 指出的两处消息泄漏点；脱敏对齐观测现场（S3 XML 的 Authorization 回显） |
| 3 token 空请求前抛错 | ✅（现状补强） | 勘误已澄清原代码即满足；本次把 token 端点 4xx / result 空也并入请求前 auth 失败 |
| 4 鉴权类独立分类 | ✅ | `onenet_auth_error`；重试语义按勘误更正实施——4xx 不重试（原代码会重试），5xx/网络/形状异常保留重试 |

## 验证

问题单 §5 的场景在内网凭据下无法本地原样复现，以离线等价单测钉住（MockTransport 注入 S3 XML 原文）：

| 验证项 | 结果 | 证据（`tests/onenet/`） |
|---|---|---|
| 凭据坏 → 稳定错误、无 XML/凭据信息、不发 search | ✅ | `test_client.py::test_token_endpoint_4xx_auth_error_no_search_sent`、`test_token_empty_result_auth_error_no_search_sent` |
| 400 + S3 XML → 异常文本仅 `onenet_upstream_error: upstream_status=400`、不重试、日志有脱敏报文 | ✅ | `test_client.py::test_4xx_fails_fast_without_body_leak`（caplog 断言 `InvalidArgument` 在日志、`<Authorization>***` 已脱敏、`Bearer null` 不出现） |
| 持续 401 → auth 类别快速失败（重取一次后再试一次即停） | ✅ | `test_client.py::test_persistent_401_classified_auth_error` |
| 5xx 恢复 → 正常返回（重试不误伤） | ✅ | `test_client.py::test_5xx_retried_then_recovers`、`test_5xx_exhausts_retries_status_only` |
| search/probe/toc/resync 上游失败 → 502 稳定码+中文文案 | ✅ | `test_routes.py::test_search_upstream_failure_502_stable_detail` 等 5 例（含 auth 类别分码） |
| 既有行为不回归（401 重取、翻页、缓存、导入、删除、域路由） | ✅ | `tests/onenet`（离线）199 passed / 4 skipped；`kb-ui` `npm run build` 通过 |

生产部署与部署后复测待做。
