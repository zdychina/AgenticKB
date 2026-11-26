# BUG-04（P2，安全）登录接口无限流、无锁定，且 identify 可枚举账号

> 级别：P2（安全）
> 环境：AgenticKB v1.1.41
> 发现：2026-11-19
> 行号校正：2026-11-26 对齐 master（BUG-03 修复 277fe9b 插入 auth_stats 可观测块后整体下移约 45 行）

## 1 现象

1. **无暴力破解防护**：对同一账号连续 15 次错误密码，全部返回 401，无锁定、无延迟递增、无验证码、无连接级限速。
2. **账号可枚举**：未鉴权的 `POST /auth/identify` 对任意用户名返回三态之一，可直接判定账号是否存在：
   - 不存在的用户 → `{"mode":"not_found"}`
   - 存在且需要密码 → `{"mode":"password"}`
   - 存在且免密（工号类账号）→ `{"mode":"member"}`

实测（同一接口，仅换用户名）：

```
{"username":"no_such_user_zzz"} → {"mode":"not_found"}
{"username":"kbtest_u01"}       → {"mode":"password"}
{"username":"admin"}            → {"mode":"password"}
```

3. 结合 BUG-05（管理员密码/密钥明文可取）与弱默认口令场景，风险放大为：枚举管理员账号 → 无成本暴力破解。

4. **附带暴露面**：登录失败页会展示"联系管理员：<姓名/工号/联系方式>"（`kb-ui/src/views/LoginView.vue:34`，取值来自品牌外观配置 `brand.adminContact`）。配合上面的枚举能力，攻击者可先确认账号存在、再直接拿到管理员的对外联系方式，用于社工。建议：仅在内网可信场景展示，或改为统一的 IT 服务台入口（不指向具体个人）。

## 2 复现

```bash
# 枚举
curl -s -X POST "$BASE/api/control-plane/api/v1/auth/identify" \
     -H 'Content-Type: application/json' --data '{"username":"<任意工号>"}'

# 暴力破解（无任何拦截）
for i in $(seq 1 15); do
  curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE/api/control-plane/api/v1/auth/login" \
       -H 'Content-Type: application/json' \
       --data '{"username":"<目标账号>","password":"wrong-'$i'"}'
done
# → 15 个 401，无 429，无锁定
```

## 3 根因

- `main_control_service/main.py` 的 `login`（197 行起）每次直接转 `verify_user_via_mining`，**没有失败计数、没有时间窗口、没有锁定状态**。
- 全仓库检索 `rate|limit|throttle|attempt|lockout` 在认证路径下无命中（仅 onenet 的 `throttle_seconds` 与 httpx 连接池配置）。
- `identify`（main.py 240 行起）是 `_SKIP_PATHS`（`main_control_service/auth.py` 的 `_SKIP_PATHS`）免鉴权路径，按用户返回 mode，属"为免密工号登录设计"的主动行为，但缺少限流与观测。

## 4 修复建议

按优先级（1 必做，2/3 建议）：

1. **登录失败限流**（进程内即可，无需外部依赖）：

```python
# 伪代码：以 username + 客户端 IP 为键
_FAIL_WINDOW = 300      # 5 分钟
_FAIL_LIMIT  = 5        # 连续失败 5 次
_LOCK_SECONDS = 300     # 锁定 5 分钟

def _guard(username: str, ip: str) -> None:
    key = (username.lower(), ip)
    if _locked_until.get(key, 0) > time.time():
        raise HTTPException(429, "too many failed attempts, try again later")
```

失败计数在 `login` 返回 401 时 +1，成功时清零；被锁定返回 429 + `Retry-After`。
（多实例部署时改用 Redis/共享存储；当前单容器 All-in-One，进程内足够。）

2. **identify 也纳入限流**（防批量枚举）：同一 IP 每分钟 > 30 次即 429。并考虑对"非本次登录流程"的 identify 调用做来源校验（登录页先写一次性 nonce，服务端校验后再 identify）。

3. **失败日志与告警**：记录 `username / ip / 结果`（不记录密码），连续失败触发告警；便于发现撞库。

4. 若产品必须保留三态返回（免密工号体验），至少在帮助中心与运维手册中把它列为已知暴露面，并在网关层（nginx）加 `limit_req` 作为兜底：

```nginx
limit_req_zone $binary_remote_addr zone=auth:10m rate=10r/m;
location = /api/control-plane/api/v1/auth/login { limit_req zone=auth burst=5 nodelay; ... }
```

## 5 验证

1. 连错 5 次后第 6 次返回 429，且 `Retry-After` 存在；等待锁定窗口后恢复正常。
2. 正确密码在未触发锁定前始终 200（不误伤）。
3. 不同 IP 之间互不影响（锁键含 IP）。
4. `identify` 超频返回 429，正常登录流程（1 次 identify + 1 次 login）不受影响。

## 6 相关文件

| 文件 | 位置 |
|---|---|
| `main_control_service/main.py:197-271` | `login` / `identify` handler |
| `main_control_service/auth.py` | `_SKIP_PATHS`（identify/login 免鉴权） |
| `main_control_service/ip_whitelist.py` | 现有 IP 白名单中间件（可作为限流注入点） |
| `knowledge_mining/mining/kb/routes/auth.py` | `auth/verify`、`auth/identify` 实际校验实现 |