# BUG-05 分析：系统配置接口明文泄露密钥

## 核心风险评级确认

**同意 P2 定级**，但要注意一个容易被低估的攻击链：

```
Site Admin 会话（XSS/CSRF/共享终端/浏览器插件）
        ↓
GET /api/v1/system/auth  ← 会话内的一次普通 GET（无需口令二次确认；member 403）
        ↓
jwt_secret (64 hex) + internal_verify_secret 离线获取
        ↓
本地用 HS256 + 任意 claims 签发 JWT（无需知道任何用户口令）
        ↓
冒充任一现存活跃 site_admin → 全平台失守
```

关键点在于 **`jwt_secret` 泄露是"离线、不可撤销、无需交互"的**：轮换密钥才能止损，而轮换会踢掉所有在线会话。这与"泄露某个用户口令"不是同一量级——所以 P2 合理，若该实例面向公网或多租户，可考虑 P1。

### 机制细节修正（按代码核查）

1. **alg 并非"任意"**：`jwt_util.decode` 钉死 HS256，header.alg != "HS256" 一律拒绝（jwt_util.py:49）。伪造只能走 HS256，"任意 claims" 成立，"任意 alg" 不成立。
2. **admin token 有实时复验**：中间件对 role=admin 的 token 每请求回查用户表（auth.py:284-307），不能凭空捏造用户，只能冒充**现存活跃** site_admin（默认用户名 `admin` 公开已知；member token 无复验，可冒充任意成员）。结论"全平台失守"不变，路径是冒充而非无中生有。

### 核查中发现的遗漏（加重而非减轻）

- 同一响应还泄露 **`internal_verify_secret`**，且比 jwt_secret 更直接：`X-Internal-Auth` 分支在 bearer 校验之前放行（auth.py:259-262），持它可绕过 JWT 以内部身份读全部 `/api/v1/system/*`，网络可达时还可直连 mining 内部端点。
- 仓库本身已提交真实明文凭据：`llm_service.yaml` 两枚 API key（:20/:24）、`database.yaml` 的 PG 密码（:7）、`storage.yaml` 的 MinIO access_key/secret_key（:25-26，注释自认"明文凭据随仓库提交"惯例）。泄露面不止"接口读"，还包括任何有仓库访问权的人——修复时这些凭据应视为已泄露并轮换。

---

## 对修复建议的补充与修正

### 4.1 掩码函数有几个必须补的坑

原 `mask_secrets` 的键名匹配会**漏掉**本 bug 明确列出的字段：

| 配置项 | 实际键名 | 原白名单是否命中 |
|---|---|---|
| `X-HW-ID` / `X-HW-APPKEY` | 含连字符、大写；当前提交的 yaml 未出现，仅 `.env.example` 记载（`LLM_SERVICE_MODEL_EXTRA_HEADERS`），内网部署填入 `provider.headers` 后即成真实键名 | ❌ 未命中（`appkey` ≠ `X-HW-APPKEY`） |
| `bootstrap.admin_password` | `admin_password` | ✅ |
| `static_token` | `static_token` | ✅ |
| 连接串 | 当前各 yaml 均为离散键（`password`/`access_key`/`secret_key`），无 DSN 内嵌形态；DSN 仅见于 `.env.example` 遗留的 `DATABASE_URL` | ❌ 未命中（值内嵌，键名无害）——通用防御，非本系统现状 |

建议改为**归一化 + 子串匹配**，并单独处理连接串：

```python
import re

_SENSITIVE_SUBSTR = (
    "secret", "password", "passwd", "token", "api_key", "apikey",
    "appkey", "credential", "private_key", "access_key", "x_hw_",  # 归一化后匹配，须写下划线形态
)

def _is_sensitive_key(k: str) -> bool:
    kl = k.lower().replace("-", "_")
    return any(s in kl for s in _SENSITIVE_SUBSTR)

_DSN_CRED = re.compile(r"(?P<scheme>\w+://)(?P<user>[^:/@]+):(?P<pw>[^@]+)@")

def _mask_dsn(v: str) -> str:
    return _DSN_CRED.sub(lambda m: f"{m['scheme']}{m['user']}:••••••@{v.split('@',1)[1]}", v)
```

另外两个语义细节：

1. **掩码要保留末 4 位还是固定长度？** 建议固定 `••••••` + 末 4 位，但**空串、纯数字短值（如端口）不要掩码**，否则 `token_ttl_seconds` 之类误伤。判断条件应是"键名敏感 **且** 值为长度 ≥ 8 的字符串"。
2. **容器元素**：本仓库 `llm_service.yaml` 的模型配置是按模型名索引的**嵌套 dict**（`provider.models.<模型名>.api_key`，非 list），掩码递归必须覆盖 dict 成员；模型内嵌的 `headers: {"X-HW-ID": ...}` 也要被覆盖——归一化后 `x_hw_id` 命中 `x_hw_` 子串，成立（子串表若写成连字符形态 `x-hw-`，则永远匹配不上归一化后的键）。

### 4.1 "掩码回传 = 保留原值"的语义必须收窄

掩码回传的识别若靠 `••••••` 前缀做**隐式**识别（而非 `<KEEP_EXISTING>` 这类用户不可能手输的**显式**占位符——注意：仓库中并无此既有机制，此处仅是设计风格对比），有两个风险：

- 用户真的想把某个字段改成字面量 `••••••`（几乎不可能，但服务端不应接受掩码字面量落盘）。
- **掩码回填是"写路径"的信任边界**：服务端必须按"键名敏感 + 值以 `••••••` 开头"才替换为磁盘原值，**否则报 400**，绝不能把 `••••••` 写进配置文件。

建议在 `update_system_config_yaml` 里做一次"合并"而非"覆盖"：

```python
def _merge_preserving_masked(new, old):
    # 对每个敏感键：若 new 值以 MASK_PREFIX 开头 → 取 old 原值；否则用 new
    # 对非敏感键：直接取 new
    # 结构不一致（dict/list 类型变化）→ 抛 409 让前端重载
```

这样"改 `token_ttl_seconds` 保存后密钥不变"（验证项 3）是**结构性保证**，而不是依赖前端不回传。

### 4.2 `bootstrap.admin_password` 的处理顺序

建议比原文更激进：**该字段不应以任何可读形态存在**。现状是：deploy 首次部署写入后仅把 `initialize_on_deploy` 翻为 `false`，字段本身留在 auth.yaml（deploy-server.sh:225-229），播种日志也只是提示人工移除（mining/kb/bootstrap.py:47-49）。目标态：

1. 写入用户表（哈希）——现状已如此（`hash_password` 入库）；
2. 从 `auth.yaml` 中**删除** `bootstrap.admin_password`，仅保留 `bootstrap.done: true`；
3. 登录校验如今就只查用户表（main_control 转发 mining verify → `kb_users.password_hash`，main.py:249），删字段后须一并移除的残留读取路径只有 mining 启动播种（mining/api/app.py:112-116）。

若担心"首次部署失败需重试"，用一次性 token 文件（0600 权限）替代，而非长期驻留配置。

### 4.3 环境变量注入 ≠ 自动安全

`${DSH_JWT_SECRET}` 引用是正确方向，但要注意：

- **加载器必须支持 `${VAR}` 展开且"未设置即启动失败"**，否则 `jwt_secret="${DSH_JWT_SECRET}"` 被当成字面量当密钥用，比明文更糟（可预测）。
- **`/raw` 接口要返回展开前的引用形态**，不能把展开后的值回吐给前端——这是最容易漏的一处：掩码层加在"已解析配置"上，而 `get_system_config_yaml` 读的是原始文件，天然安全；但 `get_system_config`（结构化）若基于解析后对象，需确认展开发生在哪一层。
- 启动校验**已存在**：auth 中间件拒绝空值与 `change-me*` 占位符并 503（auth.py:138-139、243-244）——但它挡不住字面量 `${DSH_JWT_SECRET}`（非空、不以 change-me 开头，能通过校验被当密钥用），这正是上一条"未设置即启动失败"必须补的洞。

### 4.4 审计要记录"读"而不只是"改"

原文 4.4 强调 PUT，但**本 bug 的利用路径是 GET**。审计必须覆盖：

- `GET /system/auth`、`/system/*/raw` 等**含敏感键的读**——记录 user、IP、路径、响应中被掩码的键名列表（不记值）；
- `POST /reveal`（原报告提议的口令确认接口，当前代码中不存在）必记，且**失败尝试也要记**（口令错误 = 潜在攻击信号）；
- 审计日志本身不可通过 `/system` 接口读取或删除。

---

## 验证清单的补充

原文 5 项之外，建议加：

6. **负向测试**：直接把 `{"jwt_secret": "••••••abcd"}` PUT 上去 → 返回 400，磁盘不变。
7. **结构变更测试**：把 `llm_service.providers` 从 list 改成 dict 保存 → 合并逻辑不 panic，返回明确错误。
8. **`/raw` 与结构化接口一致性**：两者对同一字段的掩码形态一致，且都不含完整值。
9. **回归**：`llm_service` 保存后 reload 生效，但**provider 的 api_key 实际用的是磁盘原值**（可通过一次真实调用验证），而非掩码串。
10. **越权回归**：非 site_admin 调三个接口仍 403（本 bug 不涉及越权，但改动权限层时别顺手放宽）。

---

## 一句话总结

修复方向正确，但**成败在写路径的合并语义**：读接口掩码只是"遮挡"，真正的安全保证是"服务端在保存时把掩码识别为保留原值、并拒绝掩码字面量落盘"。同时 `bootstrap.admin_password` 应删除而非掩码，`jwt_secret` 应 env 注入且**加载器对未设置变量 fail-fast**。审计要把 GET 纳入，因为这才是本次利用路径。最后别忘了：伪造只能走 HS256、admin 身份须冒充现存活跃用户，但同接口泄露的 `internal_verify_secret` 可绕过 JWT 直读，仓库里三份已提交的明文凭据也应视为已泄露——修复清单别漏了它们。