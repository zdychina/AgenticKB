# BUG-LLM: Embedding Dimensions 死锁修复指南

> ✅ **已修复（2026-11-24，commit `4527897`，BUG-01）——本文档转为历史诊断快照，勿再按 §3 执行修改**
>
> - **修复 1 按本文档落地**：`service.py` 的 `dig` → `dig_optional`（现位于 503 行；import 已含 `dig_optional`）。
> - **修复 2 未按本文档方案执行**：实际改为 `embedding.send_dimensions` 配置开关（默认 `true`，见 `bigmodel_models.py:115-116`、`main.py:161-165`），而非删除 payload 写入。**不要按 §3 修复 2 直接删那两行**——智谱 embedding-3 默认 2048 维，必须下发 `dimensions: 1024` 压维才能匹配库列 `vector(1024)`；仅 MaaS/bge-m3 等 `extra_forbidden` 网关需在配置设 `send_dimensions: false`。
> - 文中行号均为修复前口径，现整体后移（499→503、106-107→115-116、174→180）。
> - 修复 commit 另含：新增测试 `llm_service/tests/test_bug01_send_dimensions.py`、`provider_resolver.py` 域覆盖透传、`llm_service.yaml` 切换本地 TEI 网关（Qwen3-Embedding-0.6B）。

> **严重级别**：P1 — 阻断全部知识挖掘与检索链路  
> **状态**：~~根因已定位，需修改 2 处源码~~ → **已修复**（`4527897`，2026-11-24）  
> **影响起始**：配置引入 `dimensions` 参数后，所有 embedding 任务持续失败

---

## 1. 现象

知识挖掘流程在 embedding 阶段全部失败，且配置层面陷入死锁——无论 `dimensions` 参数存在与否，系统都无法正常工作：

| `llm_service.yaml` 中 `embedding.dimensions` | 结果 |
|---|---|
| **存在**（如 `1024`） | embedding 任务 HTTP 200 但返回空数据 `data: []` → 校验报 `0 vectors for N inputs` → 3 次重试后进 dead_letter → 挖掘 Run 失败 |
| **不存在**（删除该行） | `POST /api/v1/tasks/embed` → **500 Internal Server Error** → `ValueError: Missing config field: embedding.dimensions` → 服务崩溃 |
| **两种配置都无法工作** | **死锁** |

### 错误日志

**删除 dimensions 时（500 崩溃）**：
```
File "/app/llm_service/runtime/service.py", line 499, in submit_embedding
    actual_dimensions = dimensions if dimensions is not None else dig(domain_cfg, "embedding", "dimensions")
File "/app/llm_service/config.py", line 72, in dig
    raise ValueError(f"Missing config field: {'.'.join(keys)}")
ValueError: Missing config field: embedding.dimensions
```

**存在 dimensions 时（0 vectors）**：
```
embedding result returned 0 vectors for 5 inputs
```
网关实际返回（HTTP 200 但 data 为空）：
```json
{"result": "1 validation error for EmbeddingInput, dimensions, Extra inputs are not permitted [type=extra_forbidden, input_value=1024, input_type=int]"}
```

---

## 2. 根因

两个独立的 P1 Bug 互相锁死，纯配置修改无法绕过。

### BUG-LLM-1：严格 `dig()` 读取可选配置字段导致服务崩溃

**文件**：`llm_service/runtime/service.py` 第 499 行

```python
actual_dimensions = dimensions if dimensions is not None else dig(domain_cfg, "embedding", "dimensions")
```

- `dig()`（`llm_service/config.py:67-74`）在字段缺失时直接 `raise ValueError`
- `embedding.dimensions` **不在** `_REQUIRED_PATHS` 列表（`llm_service/config.py:36-64`），说明设计上它是**可选字段**
- 但提交 embedding 任务时用了严格版 `dig()` 读取它，配置中缺失即崩溃
- 同文件已提供宽松版 `dig_optional()`（`llm_service/config.py:127-134`），缺失时返回 `default=None`

### BUG-LLM-2：`dimensions` 被塞进网关请求体导致 0 vectors

**文件**：`llm_service/providers/bigmodel_models.py` 第 106-107 行

```python
if dimensions is not None:
    payload["dimensions"] = dimensions
```

- 该 Embedding 网关（bge-m3 模型）使用 Pydantic 严格模型（`extra_forbidden`）
- 网关不接受 `dimensions` 字段，收到后报验证错误：`"Extra inputs are not permitted: dimensions"`
- 网关返回 HTTP 200 但 `data: []`（空列表）
- 回到 `llm_service/runtime/worker.py:410` 被 `_validate_embedding_result()` 判为 `"embedding result returned 0 vectors for N inputs"` → 3 次重试后 dead_letter

### `dimensions` 参数的正确用途

`dimensions` **只应用于本地校验**（`llm_service/runtime/worker.py:448`）：

```python
if dimensions is not None and vector_dim is not None and vector_dim != int(dimensions):
    raise ModelProviderError("invalid_response",
        f"embedding dimension {vector_dim} != requested {int(dimensions)}")
```

用于比对网关返回的向量维度是否与数据库列定义（如 `vector(1024)`）匹配。**不应发送给网关**——bge-m3 固定输出 1024 维，网关不接受指定维度参数。

### 死锁关系图

```
配置有 dimensions: 1024
  └→ BUG-LLM-2: dimensions 进 payload → 网关拒收 → 0 vectors → 挖掘失败

配置删 dimensions
  └→ BUG-LLM-1: dig() 读不到字段 → ValueError → 服务 500 崩溃

纯配置无法绕过 ──→ 必须改代码
```

---

## 3. 修复方案

### 修复 1：`service.py:499` — `dig` 改为 `dig_optional`

**文件**：`llm_service/runtime/service.py`

```python
# ── 修改前（第 499 行）──
actual_dimensions = dimensions if dimensions is not None else dig(domain_cfg, "embedding", "dimensions")

# ── 修改后 ──
actual_dimensions = dimensions if dimensions is not None else dig_optional(domain_cfg, "embedding", "dimensions")
```

**原理**：`dig_optional` 在字段缺失时返回 `None` 而非抛异常。`dimensions` 是可选字段（不在 `_REQUIRED_PATHS`），缺失时 `None` 表示"不指定维度"，bge-m3 固定 1024 维无需指定。

> **注意**：确认文件顶部已 import `dig_optional`。若未 import，需补充：
> ```python
> from llm_service.config import dig, dig_optional  # 原来只有 dig
> ```

### 修复 2：`bigmodel_models.py:106-107` — 不将 dimensions 发送给网关

**文件**：`llm_service/providers/bigmodel_models.py`

```python
# ── 修改前（第 102-107 行）──
payload: dict = {
    "model": model or self._embedding_model,
    "input": texts,
}
if dimensions is not None:
    payload["dimensions"] = dimensions   # ← 删除这两行

# ── 修改后 ──
payload: dict = {
    "model": model or self._embedding_model,
    "input": texts,
}
# dimensions 不进 payload：bge-m3 网关用 Pydantic extra_forbidden，
# 不接受 dimensions 字段。dimensions 只用于 worker.py 本地校验返回向量维度。
```

**原理**：`embed()` 方法仍接收 `dimensions` 参数（签名不变），但不放进发送给网关的 payload。`dimensions` 值通过调用链传递到 `worker.py:244` → `_validate_embedding_result(result, texts, dimensions)` 做本地维度校验，这条链路不受影响。

---

## 4. 修复验证步骤

### 4.1 前置条件
- `llm_service.yaml` 中 `embedding` 段配置保持正常（`base_url` / `api_key` / `model` 等必填项齐全）
- `embedding.dimensions` 可以保留（如 `1024`），也可以删除——两种情况修复后都应正常

### 4.2 验证删除 dimensions 不崩溃
1. 从 `llm_service.yaml` 的 `embedding` 段删除 `dimensions` 行
2. 重启 llm_service（或调 reload-config）
3. 触发一次 embedding 任务（上传文档触发挖掘，或直接调 API）
4. **预期**：服务不崩溃，embedding 任务正常执行

### 4.3 验证保留 dimensions 不报 0 vectors
1. 在 `llm_service.yaml` 的 `embedding` 段保留 `dimensions: 1024`
2. 重启 llm_service（或调 reload-config）
3. 触发一次 embedding 任务
4. **预期**：
    - embedding 任务成功，返回 N vectors（与输入条数一致）
    - 每个向量维度为 1024
    - 不再出现 `0 vectors` 或 `Extra inputs are not permitted` 错误

### 4.4 端到端链路验证
1. 上传一个文档到知识库 → 触发挖掘
2. 挖掘各阶段（input_ingest → document_parse → segment_compile → retrieval_unit_project → **embedding** → asset_persist → mining_finalize）全部成功
3. 在该知识库执行检索 → query_embed 算子成功向量化 → 返回检索结果
4. **预期**：全链路无 embedding 相关错误

---

## 5. 影响范围

- 所有 embedding 任务失败（dead_letter 累计大量条目）
- 全部知识挖掘 Run 在 embedding/publishing 阶段失败（`embedding_incomplete`）
- 检索链路 `query_embed` 算子无法向量化查询文本
- 概览页显示大量文档解析失败

---

## 6. 涉及文件清单

| 文件 | 行号 | 说明 |
|---|---|---|
| `llm_service/runtime/service.py` | 499 | BUG-LLM-1 崩溃点，`dig` → `dig_optional` |
| `llm_service/config.py` | 67-74 | `dig()` 严格读取函数（无需修改） |
| `llm_service/config.py` | 127-134 | `dig_optional()` 宽松读取函数（无需修改） |
| `llm_service/config.py` | 36-64 | `_REQUIRED_PATHS` 列表，`dimensions` 不在其中 |
| `llm_service/providers/bigmodel_models.py` | 95-118 | BUG-LLM-2 `embed()` 方法，删除 106-107 行 |
| `llm_service/runtime/worker.py` | 197-249 | `_execute_embedding` 调用链（无需修改） |
| `llm_service/runtime/worker.py` | 396-451 | `_validate_embedding_result` 本地校验（无需修改） |
| `llm_service/main.py` | 174 | `_embedding_dimensions` 配置读取（用了 `.get()`，无问题） |

---

## 7. 修复变更摘要

| # | 文件 | 行号 | 变更 | 风险 |
|---|---|---|---|---|
| 1 | `llm_service/runtime/service.py` | 499 | `dig(` → `dig_optional(` | 极低：同文件已有 `dig_optional` 定义，行为一致只是缺失时不崩 |
| 2 | `llm_service/providers/bigmodel_models.py` | 106-107 | 删除 `if dimensions is not None: payload["dimensions"] = dimensions` | 极低：payload 只少一个字段，网关本就不接受它；dimensions 仍通过参数传递给本地校验 |

两处修改互相独立但需同时部署——只改 1 不改 2：删 dimensions 不崩了但保留 dimensions 仍 0 vectors；只改 2 不改 1：保留 dimensions 不 0 vectors 了但删 dimensions 仍崩溃。