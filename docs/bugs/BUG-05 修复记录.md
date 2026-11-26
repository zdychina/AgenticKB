# BUG-05 修复记录：系统配置接口明文泄露密钥

对应分析：[BUG-05 分析：系统配置接口明文泄露密钥.md](./BUG-05%20分析：系统配置接口明文泄露密钥.md)（已按代码核查勘误）。

## 修复内容

### 1. 读路径掩码（`main_control_service/config_masking.py` 新建）

- **开关规则**：所有内部消费方（mining / llm_service / serving Java）都带
  `X-Internal-Auth`，中间件已设 `request.state.internal_call` →
  **非内部调用（浏览器/admin JWT）拿掩码值，内部调用拿原值**。不破坏任何生产链路。
- **覆盖端点**：`GET /api/v1/system/{name}`（结构化）与 `/raw`、llm/onenet 域覆盖 `/raw`、
  两个 `effective/{id}`、`/api/v1/serving-config`、`GET /api/v1/domains/{id}[/raw]`
  （域注册表内联 `database.password`——分析文档遗漏、本次核查补上的泄露面）。
  域列表摘要本就不含密钥，未改。
- **键规则**：归一化（lower+连字符转下划线）子串匹配
  `secret/password/passwd/token/api_key/apikey/appkey/credential/private_key/access_key/authorization/x_hw_`
  + 全等豁免 `token_url`（公开端点 URL，含 "token" 子串但非凭据——Plan 压力测试抓到的真碰撞）。
- **值规则**：仅掩码"长度 ≥ 8 的字符串"——`token_ttl_seconds: 43200`（int）、短串、空串不掩。
- **掩码形态**：`••••••` + 末 4 位；`${VAR}` 引用形态纯前缀（不泄末 4 位）。
- **实现**：PyYAML `compose` 节点 start/end_mark 列区间替换（非行级正则）——
  精确保注释/引号/缩进/行内尾注释；raw 与结构化接口对同一文件产出同形掩码（验证项 8）。
- **fail-closed**：重复键、YAML 锚点/别名、多行/块标量 → 拒绝处理
  （掩码侧跳过该键、合并侧 400），宁可漏掩不可破坏文件；文件本身不可解析 → 503 拒读。

### 2. 写路径合并（结构性保证，验证项 3/6/7）

PUT 保存前 `merge_masked_yaml(incoming, disk)`：incoming 中"敏感键 + 值以 `••••••` 开头"
→ 按路径配对取磁盘原值写回（同一文件多个 `api_key` 靠路径而非行序配对，增删重排不错位）；
- 掩码回传=保留原值；输入新真实值=轮换；**新增/改名键填掩码 → 400 列出键名，磁盘不变**；
- onenet 的 `validate_onenet_global_yaml` 在合并后执行（校验器看到的是恢复原值后的最终落盘文本）；
- PUT 响应增加 `preserved`（被保留原值的路径列表，不含值）。

### 3. bootstrap.admin_password 播种后自动清理（分析 4.2）

- 新端点 `POST /api/v1/system/auth/consume-bootstrap`：**仅内部调用**（admin JWT 也 403），
  行级删除 auth.yaml 的 `admin_password` 行（保留注释）。
- mining 启动链（`api/app.py`）：密码为真实非占位值且未因占位符跳过播种 → best-effort
  调 consume。**覆盖 `--force-config` 重置后已有 admin 的滞留场景**（此时播种跳过但密码照样消费）。
- `deploy-server.sh`：`admin_password` 仅在 `initialize_on_deploy=true`（首次初始化）时
  要求存在/生成；后续部署不因其缺失而 die、不重新生成。
- **reset_db.py 未改**，运维流程：要重置库时人工在 auth.yaml 临时回填
  `bootstrap.admin_password` → `reset_db.py --execute` → 重启 mining（播种）→ 字段自动清理。

### 4. jwt_secret 环境变量注入（分析 4.3）

- `auth.yaml` 的 `jwt_secret` 支持**整体** `${VAR_NAME}` 形态，启动时从环境变量展开；
- 变量未设置/空/引用形态残缺（如 `${VAR:-fallback}`）→ 一律按无效 secret 处理 →
  沿用既有 503 fail-closed（分析文档"字面量当密钥"的洞由此闭合）。
- **仅 jwt_secret 做文件内引用**：internal_verify_secret 不做——serving Java
  （`ControlPlaneInternalAuth.java`）直读文件字面量，且 env 通道
  `CONTROL_PLANE_INTERNAL_AUTH_SECRET` 对所有消费方已存在。
- deploy 脚本仍写字面量（向后兼容）；需要 env 注入的部署可自行改 auth.yaml 为引用形态。

### 5. 审计（分析 4.4：利用路径是 GET）

- 新 `main_control_service/audit.py`：`log_dir()/audit.log`，JSON 行
  （ts/event/user/ip/method/path/keys），**只记被掩码或被保留的键名，绝不记值**；
  浏览器读（config_read）与全部 PUT（config_write）都记，内部读不记（服务间噪音）。
- `logs.py` 把 `audit.log` 从 `/api/v1/logs` 列表与读取中排除——审计与被审计对象不同窗。
- 写审计失败静默，不阻断业务请求。

## 分析文档验证项对账

| 验证项 | 结果 | 测试 |
|---|---|---|
| 1/2 掩码覆盖 jwt_secret/internal_verify_secret/api_key/static_token 等 | ✅ | test_bug05_config_masking_endpoints.py::test_browser_get_masks_auth_secrets、test_config_masking.py 全套 |
| 3 改 token_ttl_seconds 保存后密钥不变 | ✅ | test_put_roundtrip_preserves_secrets |
| 4 bootstrap.admin_password 删除 | ✅ | test_consume_bootstrap_internal_only_and_idempotent |
| 5 env fail-fast | ✅ | test_auth_middleware.py::test_jwt_secret_env_ref_missing_fails_closed / malformed |
| 6 掩码字面量 PUT 新键 → 400 磁盘不变 | ✅ | test_put_mask_literal_on_new_key_400 |
| 7 结构变化 → 明确错误不 panic | ✅ | test_put_structure_change_with_mask_400、test_config_masking.py::test_merge_rejects_structure_change |
| 8 /raw 与结构化掩码一致 | ✅ | test_config_masking.py::test_mask_yaml_text_structured_consistency |
| 9 llm_service 保存后 reload 生效且用磁盘原值 | ✅ | 既有 reload_notifier 测试 + test_put_roundtrip_preserves_secrets（磁盘原值） |
| 10 非 site_admin 调三接口仍 403 | ✅ | 既有 test_auth_middleware 权限档测试未动（本轮未改 `_is_admin_only`） |

## 接受的风险与遗留事项

1. **仓库中已提交的真实凭据应视为已泄露并轮换**（掩码只关接口，不治愈 git 历史）：
   - `llm_service.yaml`：DeepSeek key（:20）、智谱 key（:24）
   - `database.yaml`：PG 密码（:7）
   - `storage.yaml`：MinIO access_key/secret_key（:25-26）
   - `domain_registry.yaml`：各域内联 database.password
2. `ip_whitelist.yaml` 的 IP 不按敏感处理（运维数据非凭据，端点 admin-only）。
3. DSN 内嵌凭据掩码未实现（当前所有配置均为离散键，留作通用防御）。
4. 块标量（`|`/`>`）形式的敏感值不掩码（跳过并记录，fail-open 但保守——本仓库无此形态）。
5. `/reveal` 口令确认接口未实现（原报告提议；掩码已消除泄露面，如需"查看明文"再立项）。

## 修改文件清单

- 新建：`main_control_service/config_masking.py`、`main_control_service/audit.py`、
  `main_control_service/tests/test_config_masking.py`、
  `main_control_service/tests/test_bug05_config_masking_endpoints.py`、
  `knowledge_mining/tests/infra/test_control_plane_consume_bootstrap.py`
- 修改：`main_control_service/main.py`（handler 接线 + consume-bootstrap 端点 + 审计调用）、
  `main_control_service/auth.py`（disabled 分支内部头识别、internal scope 扩 POST、
  jwt_secret `${VAR}` 展开）、`main_control_service/service.py`
  （consume_bootstrap_admin_password）、`main_control_service/logs.py`（排除 audit.log）、
  `knowledge_mining/mining/kb/bootstrap.py`（返回播种状态）、
  `knowledge_mining/mining/infra/control_plane.py`（consume_bootstrap_password）、
  `knowledge_mining/mining/api/app.py`（播种后消费）、`deploy-server.sh`
  （admin_password 仅首次初始化要求/生成）、
  kb-ui：`SystemConfigTab.vue`（掩码横幅 + 400 透传）、`OnenetDomainOverridesTab.vue` /
  `LlmDomainOverridesTab.vue`（密码框三态文案）、两个 spec 补掩码契约用例、
  两份 40-部署与发版.md（密码自动移除说明）

## 测试结论

- `python -m pytest main_control_service/tests/ -q`：158 过 + 4 个**既有**环境红
  （`TestClient(client=)` 的 httpx/starlette 版本坑，BUG-03 在案，与本次无关）。
- `cd knowledge_mining && PG_HOST=… KB_ALLOW_TEST_TRUNCATE=1 python -m pytest
  tests/infra tests/test_deployment_auth_surface.py tests/kb/test_bootstrap.py -q`：
  全过（`test_sensitive_service_ports_bind_to_loopback_only` 与 tests/kb 的 PG 环境错误
  均为基线既有，见基线留档）。
- `cd kb-ui && npm test`：958/958 过；`npm run build`（vue-tsc + vite）：通过。
