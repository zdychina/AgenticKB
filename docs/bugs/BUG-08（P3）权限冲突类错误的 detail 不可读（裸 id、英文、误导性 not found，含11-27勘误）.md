# BUG-08（P3）权限/冲突类错误的 detail 不可读（裸 id、英文、误导性 not found）

> 级别：P3（体验/可诊断性；同一根因的三处表现）
> 环境：AgenticKB v1.1.41 发现；2026-11-27 在 master（1.1.54）逐条核对
> 发现：2026-11-19；勘误：2026-11-27（见 §7——三处表现全部属实，仅两处文件定位更正）

## 1 三处表现

| 场景 | 实际响应 | 问题 |
|---|---|---|
| viewer 上传文档到只读库 | `403 {"detail":"06966df99511487e9bf6faa655389c85"}` | detail 是**裸 KB id**，没有原因文案 |
| viewer 删除该库 | `404 {"detail":"KB 06966… not found"}` | 库实际存在（防枚举口径），但文案与"库不存在"完全一致，用户会去核对 id 而不是找 owner 申请权限 |
| 同库重名上传 | `409 {"detail":"Document named 'x.md' already exists in this KB"}` | 英文，未与其它中文化错误对齐 |

对照：同模块其它错误已是中文且可执行（如 `domain_not_bound: 未绑定该知识域，请联系管理员分配`，`kbs.py:76-77`）。

## 2 复现

```bash
# 1) 以 viewer 上传
curl -X POST "$BASE/api/kb/$KB/documents" -H "Authorization: Bearer $VIEWER_TOKEN" -F "file=@a.md"
# → 403 {"detail":"<kb_id>"}

# 2) 以 viewer 删库
curl -X DELETE "$BASE/api/kb/$KB" -H "Authorization: Bearer $VIEWER_TOKEN"
# → 404 {"detail":"KB <kb_id> not found"}

# 3) 重名上传
curl -X POST "$BASE/api/kb/$KB/documents" -H "Authorization: Bearer $OWNER_TOKEN" -F "file=@a.md"
# → 409 {"detail":"Document named 'a.md' already exists in this KB"}
```

## 3 根因

`knowledge_mining/mining/kb/routes/documents.py`：

- 上传端的 `Forbidden` 映射对 `Forbidden` 直接 `HTTPException(403, str(exc))`（`_map_error` 定义在 `kbs.py:69-77`，原报误写 documents.py 69-86 行；documents.py 仅 192-193 行调用），而 `DocumentService.intake_upload`（`document_service.py:358`）两分支（344/738 行）都经 `KbService._assert_write` → `Forbidden(kb_id)`（`kb_service.py:250-255`），携带的是 kb_id 而非可读文案；
- 重名 409 文案硬编码英文（约 200 行：`f"Document named {filename!r} already exists in this KB"`）；
- 删库路径用统一的 404 反探测文案（`KB {kb_id} not found`），无法区分"不存在"与"无权限"。

## 4 修复建议

1. `Forbidden` 携带稳定错误码 + 可执行文案，路由原样透传：

```python
raise Forbidden("kb_write_required: 你不是该知识库的成员或没有编辑权限，请联系库拥有者添加")
```

2. 409 文案中文化并保留文件名：

```python
raise HTTPException(409, f"同名文档已存在：{filename}（请先删除或使用“替换内容”）")
```

3. 删库：保持 404 反探测，但补一个机器码与可执行提示（不泄露存在性）：

```python
HTTPException(404, "kb_not_found_or_no_permission: 知识库不存在或你没有管理权限")
```

前端按 `code` 前缀渲染不同图标/建议（"申请权限" vs "刷新列表"）。

4. 统一约定：所有 4xx 响应体为 `{"code": "...", "detail": "中文可执行文案"}`，前端只读 `detail` 也能给出正确引导。

## 5 验证

1. viewer 上传 → 403，`detail` 含"没有编辑权限/联系拥有者"。
2. viewer 删库 → 404，`detail` 含"不存在或没有管理权限"，且不泄露库是否存在。
3. 重名上传 → 409，中文 + 文件名。
4. 回归：真不存在的库仍 404，文案一致（反探测不被破坏）。

## 6 相关文件（行号按 master 1.1.54）

| 文件 | 说明 |
|---|---|
| `knowledge_mining/mining/kb/routes/kbs.py:69-77` | `_map_error`（Forbidden/NotFound/Duplicate 映射；原报误写 documents.py:69-86） |
| `knowledge_mining/mining/kb/routes/documents.py:192-193, 199-201` | 上传端 `_map_error` 调用点；重名 409 英文文案（恰为第 200 行） |
| `knowledge_mining/mining/kb/routes/kbs.py:260-263` | 删库路由与 404 反探测文案（代码注释明示"不向非属主泄露"） |
| `knowledge_mining/mining/kb/services/kb_service.py:250-255` | `_assert_write` → `Forbidden(kb_id)` 抛出点 |
| `knowledge_mining/mining/kb/services/document_service.py:344, 738` | `intake_upload` 两分支的 `_assert_write` 调用 |
| `kb-ui/src/api/proxyClient.ts:122` | `apiErrorDetail` 前端错误提取（原报误写 src/utils/*；函数与测试均在 src/api/） |

## 7 勘误记录（2026-11-27 核对）

三处表现（§1）逐条核对**全部属实**，定级 P3 维持：

- 403 裸 id：路由 `documents.py:192-193` → `_map_error`（`kbs.py:72-73` 原样透传 `str(exc)`）← `intake_upload` 两分支 `_assert_write`（`document_service.py:344/738`）← `Forbidden(kb_id)`（`kb_service.py:254-255`），链路完整；
- 删库 404：`kbs.py:260-263`，"不存在"与"无权限"同文案——代码注释明示防枚举口径，属可诊断性缺口而非缺陷（权限检查在 confirm_name 校验之前，复现命令成立）；
- 409 英文：恰在 `documents.py:200`，与原报"约 200 行"吻合。

原报两处定位更正：

1. **`_map_error` 所在文件**：定义在 `routes/kbs.py:69-77`，非 documents.py:69-86——documents.py 只有 192-193 行的调用点，行号范围恰好接近导致误记。
2. **`apiErrorDetail` 所在目录**：实际在 `kb-ui/src/api/proxyClient.ts:122`（测试 `kb-ui/src/api/__tests__/download.test.ts`），非 `src/utils/*`。

补充（印证"同一根因"，修复面应覆盖服务层而非仅 `intake_upload`）：`Forbidden(裸id)` 是服务层普遍模式——`kb_service.py:166`（owner-candidates）、`kb_service.py:175`（soft_delete）、`document_service.py:908`（文档级读，裸 doc_id）；另有 `kbs.py:376` 的 403 英文带 id（member-candidates，`write access required for KB {kb_id}`）。只改上传一处会留下同类不一致。