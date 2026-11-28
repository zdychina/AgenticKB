# BUG-13（P3）MCP 服务先建会话后校验密钥：无效 key 也能 initialize 成功

> 级别：P3（协议体验 / 排障成本）
> 环境：AgenticKB v1.1.41
> 发现：2026-11-19；勘误：2026-11-28（见 §7——现象经本机动态复现确认属实；根因"延迟鉴权"定性正确，但机制归因一处硬伤、文件表两行不准、"注释未见设计理由"需弱化）
> 状态：**现象属实（11-28 动态复现确认），未修复**

## 1 现象

用**非法或已吊销**的 MCP 密钥访问 `POST /mcp`：

- `initialize` → **200**，正常返回 `mcp-session-id` 与 `serverInfo`（server 名、版本）；
- 直到 `tools/call` 才返回 `isError: true`，文案"密钥无效或已被轮换/吊销"。

对 Agent 客户端意味着：连接阶段全绿，第一次业务调用才失败，容易误判成"工具参数错误"而不是"凭据过期"。

（11-28 勘误：三步已本机动态复现全部属实（§7 末尾有记录）。一处细化——"连接阶段全绿"略过了 `tools/list` 的表现：无效 key 的 `tools/list` 返回 200 但**空工具清单**（`server.py:214-217`），细心的客户端在握手后就能看到 0 工具这个可见异常，只是没有任何鉴权错误信号。见 §7 补充 1。）

## 2 复现

```bash
# 用任意无效 key
curl -s -X POST "$MCP/mcp" -H 'Authorization: Bearer kbm_bogus' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}'
# → 200 + mcp-session-id（预期应 401）

curl -s -X POST "$MCP/mcp" -H "Authorization: Bearer kbm_bogus" -H "mcp-session-id: <上面的>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{...}}'
# → isError: 密钥无效或已被轮换/吊销
```

（11-28 勘误：以上在本机复现成功——假 mining 验钥端点一律 401 + `python -m mcp_server`，原始响应记录见 §7 末尾。）

## 3 根因

`mcp_server/server.py` 的会话建立流程：`initialize` 只做协议握手（生成 session），密钥校验放在工具执行前的上下文构造（`current_access_call` / 工具 handler 内）。属"延迟鉴权"实现，代码注释未见刻意设计的理由。

（11-28 勘误："延迟鉴权"定性正确，但后半句机制归因有硬伤——`current_access_call` 是**检索账本**的 ContextVar（`mcp_server/access_records.py:21`），与验钥无关；工具 handler 内的 `require_current_identity()`（`identity.py:119`）也只读中间件注入的身份，不做 key 校验。真正的验钥点是 `on_call_tool` 里的 `require_identity(get_http_headers(...))`（`server.py:241`）→ `mcp_server/identity.py:142` 调 mining 的 `mcp-key-verify`。initialize 不拒钥的直接机制：`on_initialize` 里 `_identity_or_none()` 把 `IdentityError` 吞掉返回 None（`server.py:178-182`、`:194`）。另"代码注释未见刻意设计的理由"不完全成立——`identity.py:1-4` 明确写了逐调用验钥的设计理由，未注释的只是"initialize 为何不 401"。见 §7 更正 1/3。）

## 4 修复建议

1. 在 `initialize` 的请求头解析阶段校验 `Authorization: Bearer <key>`：
   - 无效/吊销 → 返回 HTTP 401 + MCP 错误体（`error.code = -32001, message = "invalid_api_key"`），**不建立 session**；
   - 有效 → 照常返回 session。
2. 保留 `tools/call` 的二次校验（防 session 建立后 key 被轮换）。
3. 密钥轮换后，已有 session 的处理策略需明确：建议轮换即失效（下次调用返回 401 并提示重建会话），并在 `MCP 接入` 页的"轮换"确认弹窗里说明。

（11-28 勘误后补充：建议 1 还顺带堵住一个原报未发现的面——`on_initialize` 目前对**未鉴权**请求也无条件执行 `_recover_pending_uploads_once()`（`server.py:193`），无效 key 也能触发一次 DB 清理任务，见 §7 补充 2。）

## 5 验证

1. 无效 key `initialize` → 401，无 `mcp-session-id` 返回。
2. 有效 key → 200，`tools/list` 与 `tools/call` 正常。
3. 轮换后旧 key 的**已有** session → 下次调用 401。
4. 合法 key 但无开放库 → `tools/call` 返回业务提示（不误判为鉴权失败）。

## 6 相关文件

| 文件 | 位置 |
|---|---|
| `mcp_server/server.py` | `initialize` / session 建立 |
| `mcp_server/client.py` | `current_access_call` 上下文（11-28 勘误：与验钥无关——该 ContextVar 定义在 `access_records.py:21`，client.py 只读取它做账本关联，见 §7 更正 2） |
| `mcp_server/tools.py` | 工具 handler 的 key 校验（11-28 勘误：描述不准——tools.py 是到 mining/serving 的后端 HTTP 客户端，只管内部 X-Internal-Auth，不做 MCP 密钥校验，见 §7 更正 2） |
| `knowledge_mining/mining/kb/routes/auth.py` | `auth/mcp-key-verify`（校验实现） |
| `mcp_server/identity.py` | （11-28 勘误补列：验钥的真正实现——`require_identity()` 调 `mcp-key-verify`、错误文案、`Identity` 解析） |

## 7 勘误记录（2026-11-28 核对）

核对方式：§3/§6 所引代码逐一静态核对；并做本机动态复现——起假 mining 验钥端点（`/api/kb/auth/mcp-key-verify` 一律 401），`python -m mcp_server` 以 streamable-http 起服务（本机 fastmcp 4.0.4），按 §2 三步 curl。

属实部分：

- **initialize 200 + session + serverInfo**：无效 key `Bearer kbm_bogus` → `HTTP/1.1 200`、`mcp-session-id` 头、`serverInfo.name=multi-domain-knowledge`。机制：`on_initialize` 里 `_identity_or_none()` 吞掉 `IdentityError`（`server.py:178-182`、`:194`），握手照常完成；
- **tools/call 才报错且文案一致**：`"isError":true`，全文「MCP 接入密钥无效或已被轮换/吊销：请在平台「个人设置 → MCP 接入」重新生成，并更新 Agent 配置。」（`identity.py:169`）；
- **无任何传输层验钥**：`FastMCP(...)` 构造未传 `auth` 参数（`server.py:302-306`），`__main__.py` 入口无前置鉴权层——"延迟鉴权"定性正确；
- **`auth/mcp-key-verify` 端点属实**：`knowledge_mining/mining/kb/routes/auth.py:254`（`_require_internal` 内部鉴权保护，已登记 auth_guard 豁免集 `auth_guard.py:21`）；
- **P3 定级合理**：错钥拿不到任何数据（tools/list 空清单 + tools/call 全拒），纯协议体验/排障成本问题，非越权。

原报更正（3 项）：

1. **§3 机制归因硬伤**："密钥校验放在……`current_access_call` / 工具 handler 内"——`current_access_call` 是检索账本 ContextVar（`access_records.py:21`），与验钥无关；工具 handler 内的 `require_current_identity()`（`identity.py:119`）只读中间件注入的 ContextVar，不含 key 校验。真正的验钥链：`on_call_tool` 的 `require_identity(get_http_headers(...))`（`server.py:241`）→ `identity.py:142` 的 `require_identity()`；
2. **§6 文件表两行不准**：`client.py`（只读账本 ContextVar 做关联）与 `tools.py`（后端 HTTP 客户端，管内部 X-Internal-Auth）都不是验钥所在；验钥实现所在的 `identity.py` 原表未列，已补；
3. **"代码注释未见刻意设计的理由"不完全成立**：`identity.py:1-4` 明确写了"每次工具调用验一次（不缓存身份）……换来'吊销/轮换立即生效、无 60s 绕过窗口'的语义"，`server.py:5` 也写了"无钥/错钥：tools/list 返回空清单、一切调用拒绝"——逐调用验钥是**有注释说明的设计选择**，未注释的只是"initialize 为何不 401"这一点。

补充（原报未发现）：

1. **`tools/list` 对无效 key 返回 200 空清单**（`server.py:214-217`，复现中 `"tools":[]`）——"连接阶段全绿"略有夸大：0 工具是握手后即可见的异常信号，只是不带任何鉴权错误；
2. **未鉴权 initialize 也触发 DB 清理**：`on_initialize` 无条件执行 `_recover_pending_uploads_once()`（`server.py:193`）——无效 key 也能触发一次 `expire_pending_uploads`（有 asyncio 锁 + best-effort 兜底，风险低）；修复建议 1 在 initialize 阶段拒钥即顺带堵住。

动态复现记录（2026-11-28，本机 fastmcp 4.0.4 + 假 mining 401）：

```
POST /mcp  Authorization: Bearer kbm_bogus  initialize
→ HTTP/1.1 200 OK
→ mcp-session-id: 1f826479abc44ef89bb2773e1f1ff3de
→ serverInfo: {"name":"multi-domain-knowledge",...}

POST /mcp  同 key + 同 session  tools/list
→ {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}

POST /mcp  同 key + 同 session  tools/call search_knowledge
→ {"content":[{"text":"MCP 接入密钥无效或已被轮换/吊销：请在平台「个人设置 → MCP 接入」重新生成，并更新 Agent 配置。","type":"text"}],"isError":true}
```

结论：定级 P3 维持；现象与"延迟鉴权"根因定性属实（机制归因按上更正），修复建议与代码事实自洽、可落地。
