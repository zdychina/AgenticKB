---
id: DataProduct@spec-ne8000
type: DataProduct
name: NE8000 产品规格矩阵
owner: zhangsan
purpose: 比较这些型号的能力，支持专业问答与方案准备
lifecycle_status: draft
revision: 3
---
# NE8000 产品规格矩阵

本表覆盖 5 个型号版本组合的关键规格。每一行对应一张对象卡，逐字段可回原文。

> 数据为虚构，仅用于结构验证。

| 型号 | 产品版本 | 关键规格 | 单位 | 对象卡 |
|---|---|---|---|---|
| NE8000-M8 | V300R022 | 整机功耗 ≤ 2800 | W | [[spec-ne8000@DomainFactSet@NE8000-M8 V300R022]] |
| NE8000-M8 | V300R023 | 整机功耗 ≤ 2650 | W | [[spec-ne8000@DomainFactSet@NE8000-M8 V300R023]] |
| NE8000-M16 | V300R022 | 整机功耗 **口径冲突** | W | [[spec-ne8000@DomainFactSet@NE8000-M16 V300R022]] |
| NE8000-X8 | V300R022 | 整机功耗 ≤ 4200 | W | [[spec-ne8000@DomainFactSet@NE8000-X8 V300R022]] |
| NE8000-X16 | V300R023 | 整机功耗 ≤ 7800 | W | [[spec-ne8000@DomainFactSet@NE8000-X16 V300R023]] |

## 待确认项

- `NE8000-M16 V300R022` 的整机功耗在硬件手册与功耗指南中口径不同（典型值 vs 满配值），未定论。

## 边
- 包含对象卡: [[spec-ne8000@DomainFactSet@NE8000-M8 V300R022]]
- 包含对象卡: [[spec-ne8000@DomainFactSet@NE8000-M8 V300R023]]
- 包含对象卡: [[spec-ne8000@DomainFactSet@NE8000-M16 V300R022]]
- 包含对象卡: [[spec-ne8000@DomainFactSet@NE8000-X8 V300R022]]
- 包含对象卡: [[spec-ne8000@DomainFactSet@NE8000-X16 V300R023]]
- 依赖本体: [[OntologyModule@device-spec-terms]]
- 待接入规则: [[RulePackage@power-budget-check]]
