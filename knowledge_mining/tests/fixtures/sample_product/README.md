# sample_product — 知识制品样品 fixture

> 对应 [52号移植计划](../../../../docs/下一阶段/52-newsfc制品能力移植-知识制品制作闭环-分析与实施计划-2026-09-19.md) 的「步骤 2：手工造一个样品制品」。
> 场景取 [50号DSH试点](../../../../docs/下一阶段/50-首个知识制品试点-DSH与知识平台解耦联调设计-2026-09-16.md) 写死的**产品规格矩阵**。
>
> ⚠️ **本目录所有型号、规格值、文档 ID 均为虚构**，只用于验证载体结构，不是任何真实产品数据。

## 这份 fixture 是什么

在写 `018_knowledge_product.sql` 和移植 newsfc 代码**之前**，先按目标形态手写一个制品，用来证伪四条设计假设。它同时就是 P1 的测试 fixture（对应 newsfc 的 `tests/fixtures/sample_bundle/`）。

```text
sample_product/
  product.yaml                        制品定义（kp_products + kp_product_definitions + kp_scope_items 的文件形态）
  objects/
    DataProduct@spec-ne8000.md        制品对外身份对象：整张规格矩阵
    OntologyModule@device-spec-terms.md   跨制品共享的术语本体（cross scope）
    spec-ne8000@DomainFactSet@*.md    5 张对象卡，一张 = 一行
```

对象类型的骨架（D5 第一级）在 `knowledge_mining/mining/knowledge_product/default_registry.yaml`——fixture 直接用它，不另存一份副本，避免两处定义漂移。

## 验证结论

### 已证实

| 假设 | 结论 |
|---|---|
| D3 md + `[[ID]]` 边 | ✅ 成立。对象卡之间、卡与总览、卡与本体模块都能用裸 wikilink 连，newsfc 的 `## 边` 骨架原样可用 |
| D5 两级骨架 | ✅ 成立。registry 定死身份字段与必备章节，制品定义补业务字段（见 `registry_excerpt.yaml` 与 `product.yaml` 的分工） |
| A5 悬挂边 | ✅ 应报告不阻断。总览引用了 `RulePackage@power-budget-check`（本制品内不存在），这是制作过程中的正常中间态 |

### 需要修正计划的六处

**F1 — 一个制品需要两类对象，不是一类。**
`DataProduct@spec-ne8000`（2 段 cross scope）是制品对外的身份与入口，正文是整张表；`spec-ne8000@DomainFactSet@{型号} {版本}`（3 段 product scope）是一行一张的对象卡。别的制品引用的是前者，逐字段回源查的是后者。52 号 §5.4 把 `DataProduct` 和 `DomainFactSet` 列成并列类型，实际是**一个制品同时产出这两类**。

**F2 — 字段和证据必须结构化进 frontmatter，正文只是给人读的呈现。**
逐字段回源要求每个值单独挂证据，md 正文的表格做不到机器校验。所以对象卡的 `fields` 是 frontmatter 里的结构化块（值 + 单位 + 证据数组），正文的表格是它的渲染。这等于把 D3 往「52号当时列的第三个选项」推了一步——**md 是统一载体，结构化块嵌在 frontmatter 里**。`submit_creation_result` 校验的是 `fields`，不是正文。

**F3 — 证据必须带 `segment_id`，50 号的 JSON 示例缺这个字段。**
50 号 §2.2 的提交结构是 `{document_id, snapshot_id, anchor, quoted_value}`。但 `kp_evidence` 要 FK 到 `asset_raw_segments` 才能做「证据真在允许范围内」的硬校验——`anchor` 是自由文本，校验不了。**Agent 必须返回 `segment_id`**，`anchor` 只用于页面展示。这条要回写进 50 号的提交契约。

**F4 — 复合行标识撑不进逻辑 ID 的 local 段，需要 registry 支持拼接规则。**
行标识是「型号 + 产品版本」两个字段，local 段只能是一个字符串。本 fixture 用空格拼（`NE8000-M8 V300R022`，沿用 newsfc「保留空格」的约定）。建议 registry 增加 `local_from: [model, product_version]`，由平台按序拼接并校验唯一，而不是让 Agent 自己拼——Agent 拼会出现同一行两种写法。

**F5 — 跨制品共享对象不能做反向回填，这条 newsfc 的规则在这里不成立。**
newsfc 要求「被引用对象在 `## 边` 反向回填 `被引用于`」。本 fixture 里 `OntologyModule@device-spec-terms` 被 5 张对象卡引用，若逐条回填，会出现两个问题：它的 `## 边` 段随引用方数量无限增长；而引用方属于别的制品、别的修订，对它**并无写权限**。newsfc 能这么做是因为它是单一资产库、全量重建索引，这里制品有独立修订和权限边界。

**结论**：正向边由源对象的 md 承载，**反向边由平台 `kp_edges` 索引查询提供，不写回 md**。fixture 里本体模块只回指制品身份对象 `DataProduct@spec-ne8000`（一个制品一条，有界），不回指 5 张卡。

**F6 — 边必须按 (from, to, relation) 去重。**
统一成「全文扫 `[[ID]]`」之后，同一个目标常常在正文内联和 `## 边` 里各出现一次（如对象卡正文写「见 [[OntologyModule@device-spec-terms]]」，`## 边` 又列了一条）。不去重的话本 fixture 会从 22 条边虚增到 29 条。newsfc 的 `PK(from_id, from_version, relation, "to")` 天然去重，移植时别把这个主键改窄。

## 校验

fixture 由 `knowledge_mining/mining/knowledge_product/` 的真实模块解析、建边、校验：

```bash
python -m pytest knowledge_mining/tests/knowledge_product/ -q
# 41 passed
```

当前规模：**7 个对象、22 条去重后的边、1 个有意保留的悬挂边目标**。

## 未验证

- 冲突项（`NE8000-M16 V300R022` 的功耗两份资料口径不同）只表达了静态形态，没有验证 `unresolved_items` 的提交与人审流转。
- 证据的 `segment_id` 是虚构值，没有真的 FK 到 `asset_raw_segments`。P1 接上真实快照后要重新造一遍。
- 没有验证同一制品的多个修订并存（`kp_objects` 的 `PK(product_id, object_id, revision_no)`）。
