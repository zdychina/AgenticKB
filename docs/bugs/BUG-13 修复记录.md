# BUG-13 修复记录：MCP 服务先建会话后校验密钥

对应问题单：[BUG-13（P3）MCP 服务先建会话后校验密钥：无效 key 也能 initialize 成功（含11-28勘误）.md](./BUG-13%EF%BC%88P3%EF%BC%89MCP%20%E6%9C%8D%E5%8A%A1%E5%85%88%E5%BB%BA%E4%BC%9A%E8%AF%9D%E5%90%8E%E6%A0%A1%E9%AA%8C%E5%AF%86%E9%92%A5%EF%BC%9A%E6%97%A0%E6%95%88%20key%20%E4%B9%9F%E8%83%BD%20initialize%20%E6%88%90%E5%8A%9F%EF%BC%88%E5%90%AB11-28%E5%8B%98%E8%AF%AF%EF%BC%89.md)（已核对+动态复现）。**按 §4 建议全量实施**：建议 1（连接期拒钥）、建议 2（保留逐调用二次校验——现状即满足，未动）、建议 3（轮换即失效 + kb-ui 弹窗说明；服务端语义本就满足，见"对账"）。

## 方案：fastmcp auth 框架层验钥（而非 on_initialize 抛错）

选型依据（fastmcp 4.0.4 源码级核对，3.4.7 同构）：

- **on_initialize 抛异常做不到"401 + 不建会话"**：MCPError → JSON-RPC error + HTTP 200；其他异常 → `-32603`；且 streamable-http 传输对首个请求**先建 transport 再处理消息**（`status < 400` 即算 established），响应头必带 `mcp-session-id`。MiddlewareContext 也没有任何 HTTP 句柄。
- **fastmcp auth 框架是唯一正确层级**：`RequireAuthMiddleware` 只包 `/mcp` 路由，401 短路在 `StreamableHTTPSessionManager.handle_request` 之前——不建 transport、无 `mcp-session-id` 头；`/health` 与 `/upload/{ticket}` 自定义路由在鉴权路由之后追加、**天然豁免**（预签名票据模型不受影响，既有三个直传测试保绿即证）。

### 与问题单 §4 建议 1 的一处偏差（有意为之）

建议 1 设想的响应形态是"MCP 错误体（`error.code = -32001`）"；实际采用 **HTTP 401 + RFC 6750 形态**：

- 缺 Authorization 头 → 401、空 body、`WWW-Authenticate: Bearer`；
- 无效/吊销/轮换后旧钥 → 401、`{"error": "invalid_token", "error_description": …}`、`WWW-Authenticate: Bearer error="invalid_token", …`。

理由：① JSON-RPC 错误体只能搭 HTTP 200（见上，session 已建），与"不建 session"互斥；② 401 + `WWW-Authenticate` 是 MCP 规范对 streamable-http 鉴权失败的标准表达，合规客户端据此走重认证流程；③ 由框架层统一发出，不引入自造错误码。

## 修复内容

### 1. `mcp_server/identity.py` — 抽出验钥主体（行为不变的小重构）

`require_identity` 拆为 `verify_key(token) -> Identity`（验钥主体：调 mining `mcp-key-verify`、解析 payload，`IdentityError` 语义不变）+ 薄壳 `require_identity(headers)`（只补"缺钥"文案与 Bearer 提取）。HTTP 层与 middleware 层共用同一验钥语义。

### 2. 新增 `mcp_server/http_auth.py` — `McpKeyTokenVerifier(TokenVerifier)`

- `async verify_token(token) -> AccessToken | None`：`asyncio.to_thread(verify_key)`（同步 httpx 不阻塞事件循环）；`IdentityError` → **None**（框架语义：None → 401；抛非受控异常会变 500，必须折叠）。
- 有效 → `AccessToken(token, client_id=username, scopes=[], claims={key_id, key_domain})`。
- **空开放库 ≠ 无效钥**：verify 端点对零开放库的合法 key 回 200，本层照常放行（§5.4：开放库问题留给 tools/call 的业务提示，不误判为鉴权失败——有单测钉住）。

### 3. `mcp_server/server.py` — 一行接线

`FastMCP(..., auth=McpKeyTokenVerifier())`；模块 docstring 鉴权段改为两层分工说明。**PersonalizationMiddleware 一行未动**——`on_initialize` 的 `_identity_or_none` 吞错保留（能走到那里的必是通过 HTTP 层验钥的请求），`on_call_tool` 逐调用验钥保留。

### 4. 双验设计（每请求两次 mining 验钥，接受）

HTTP 层（每请求）+ `on_call_tool`（每工具调用）各自实时验钥：连接期 401 短路 + 会话建立后吊销/轮换即拒，两层都不缓存身份、无绕过窗口。mining 为 localhost 调用且验钥仅一次 hash 查库（`identity.py` 模块头对逐调用验钥的开销与语义有既有说明），翻倍可忽略。

### 5. kb-ui 轮换弹窗文案（建议 3 后半）

`mcp.confirm.rotateMessage`（zh/en 两个字典，`McpAccessView` 快捷轮换与 `McpKeyConfigPanel` 抽屉轮换共用）：在"旧密钥立即失效 + Agent 断连"之上补齐 **"下次调用时被拒（401）"与"更新 Agent 配置中的密钥并重建会话"** 两点。后端轮换语义无需改动——`rotate_key` 原地覆盖 `key_hash`（`db.py` 的 UPDATE），旧钥即时失效、无宽限期。

## 问题单 §5 验证项对账

| 验证项 | 结果 | 证据 |
|---|---|---|
| 1 无效 key initialize → 401、无 `mcp-session-id` | ✅ 手工 curl + ASGI 测试双重确认（无钥→空 body+`WWW-Authenticate: Bearer`；错钥→`invalid_token`） | `test_initialize_without_auth_header_is_401_no_session`、`test_initialize_with_invalid_key_is_401_no_session`；手工记录见下 |
| 2 有效 key → 200，`tools/list`/`tools/call` 正常 | ✅ 握手 200 + session + serverInfo，`tools/list` 三件套齐全 | `test_valid_key_initializes_then_lists_all_three_tools` |
| 3 轮换后旧 key 已有 session → 下次调用 401 | ✅ 会话建立后翻转验钥结果，同 session 下次请求即 401（HTTP 层逐请求验钥，天然无绕过窗口） | `test_rotated_key_rejects_established_session_on_next_call` |
| 4 合法 key 无开放库 → 业务提示不误判鉴权 | ✅ 验钥层对空 `open_kbs` 照常放行；业务提示是既有行为（`resolve_kb_ids`/`_resolve_open_kb`） | `test_verify_token_empty_open_kbs_still_valid` |

另加 `/health` 无认证 200 的豁免锚点测试（auth 只包 /mcp 的回归防线；/upload 豁免由既有三个直传测试继续覆盖）。

## 手工端到端复测（2026-11-28，本机 fastmcp 4.0.4 + 假 mining：好钥 200/坏钥 401/可翻转）

```
initialize 无 Authorization      → 401，content-length: 0，www-authenticate: Bearer，无 mcp-session-id
initialize Bearer kbm_bogus      → 401，{"error":"invalid_token",...}，无 mcp-session-id
initialize Bearer kbm_good       → 200 + mcp-session-id + serverInfo；tools/list → 三件套全齐
翻转（模拟轮换）后同 session 请求 → 401
GET /health 无认证               → 200
```

## 兼容性

- 生产 fastmcp 版本未锁（`pyproject: fastmcp>=2.0`，镜像构建时取最新；仓库注释按 3.4.7 写）。已对 **3.4.7 源码级核对**（wheel 下载检查）：`TokenVerifier`/`AccessToken` 同路径导出、`verify_token` 签名相同（async → AccessToken | None）、`RequireAuthMiddleware` 同样只包 MCP 端点而自定义路由后追加豁免、`AccessToken.claims` 字段存在——**本修复在 3.4.7 与 4.0.4 上同构**。
- 生产实际版本未能核对（生产 SSH 未获本次授权）；部署后按问题单 §5 用 curl 三步法复测即可闭环。
- stdio 传输不受影响（auth 只作用于 http_app；stdio 本就无 HTTP 头，维持既有行为）。

## 边缘行为变化（记录取舍）

- `domain_not_bound`（403）与验钥服务不可达：此前 tools/call 返回针对性中文文案；现在**连接期即 401**（fastmcp 标准 invalid_token 英文 body），细因文案不再透出。前者是管理员级配置异常、后者是服务故障，泛化 401 可接受；需要细因时看 mcp_server 日志（`identity.py` 的 warning 均保留）。
- 未鉴权 initialize 不再触发 `_recover_pending_uploads_once()`（401 短路在前）——问题单 §7 补充 2 顺带解决。

## 测试与构建

- `python -m pytest mcp_server/tests/ -q` → **179 passed**（新增 `test_http_auth.py` 9 例：4 单元折叠 + 5 ASGI 级；既有 170 例零回归）。
- kb-ui：`npx vitest run` → **966 passed**（含逐字断言 `rotateMessage` 的两个 en spec 已同步）；`npm run build` → 通过（vue-tsc + vite）。

## 修改文件清单

- 新建：`mcp_server/http_auth.py`、`mcp_server/tests/test_http_auth.py`（9 例）、本记录
- 修改：`mcp_server/identity.py`（`verify_key` 抽取，行为不变）、`mcp_server/server.py`（`auth=` 接线 + docstring）、`kb-ui/src/locales/zh/mcp.ts` 与 `en/mcp.ts`（`rotateMessage`）、`kb-ui/src/views/__tests__/McpAccessView.en.spec.ts` 与 `kb-ui/src/components/mcp/__tests__/McpKeyConfigPanel.en.spec.ts`（fixture 同步）、问题单状态行
