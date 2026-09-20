---
id: "spec-ne8000@DomainFactSet@NE8000-M16 V300R022"
type: DomainFactSet
product: spec-ne8000
name: NE8000-M16 V300R022
review_status: unresolved           # 有冲突项，不得发布
fields:
  model:
    value: NE8000-M16
    evidence:
      - document_id: doc_ne8000_hwinfo
        snapshot_id: snap_0007
        segment_id: seg_00418
        anchor: 3.1 硬件规格/表3-2
        quoted_value: NE8000-M16
  product_version:
    value: V300R022
    evidence:
      - document_id: doc_ne8000_release_notes
        snapshot_id: snap_0011
        segment_id: seg_01033
        anchor: 2.4 版本说明
        quoted_value: V300R022C00
  key_specification:
    # 冲突字段：按 object_rules.conflict_rule，不得任选其一
    value: null
    conflict:
      reason: 两份资料的功耗口径不同（典型值 vs 满配值），本体模块规定二者不可混用
      candidates:
        - value: 整机功耗 ≤ 3400
          evidence:
            document_id: doc_ne8000_hwinfo
            snapshot_id: snap_0007
            segment_id: seg_00419
            anchor: 3.1 硬件规格/表3-2
            quoted_value: 典型功耗 3400W
        - value: 整机功耗 ≤ 5200
          evidence:
            document_id: doc_ne8000_power_guide
            snapshot_id: snap_0003
            segment_id: seg_00095
            anchor: 2.2 功耗规格/表2-1
            quoted_value: 满配功耗 5200W
  unit:
    value: W
---
# NE8000-M16 V300R022

**整机功耗未定论。** 硬件手册给的是典型功耗 3400 W，功耗指南给的是满配功耗 5200 W，两者口径不同，按 [[OntologyModule@device-spec-terms]] 不可混用，需人工裁定本表采用哪个口径。

> 数据为虚构，仅用于结构验证。

## 边
- 所属制品: [[DataProduct@spec-ne8000]]
- 依赖本体: [[OntologyModule@device-spec-terms]]
