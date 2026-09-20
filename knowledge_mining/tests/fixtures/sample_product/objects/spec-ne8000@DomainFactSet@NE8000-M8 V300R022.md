---
id: "spec-ne8000@DomainFactSet@NE8000-M8 V300R022"
type: DomainFactSet
product: spec-ne8000
name: NE8000-M8 V300R022
review_status: human_confirmed      # 人工样例，已逐字段核对
fields:
  model:
    value: NE8000-M8
    evidence:
      - document_id: doc_ne8000_hwinfo
        snapshot_id: snap_0007
        segment_id: seg_00412
        anchor: 3.1 硬件规格/表3-2
        quoted_value: NE8000-M8
  product_version:
    value: V300R022
    evidence:
      - document_id: doc_ne8000_release_notes
        snapshot_id: snap_0011
        segment_id: seg_01033
        anchor: 2.4 版本说明
        quoted_value: V300R022C00
  key_specification:
    value:
      - 整机功耗 ≤ 2800
      - 业务槽位 8
    evidence:
      - document_id: doc_ne8000_power_guide
        snapshot_id: snap_0003
        segment_id: seg_00087
        anchor: 2.2 功耗规格/表2-1
        quoted_value: 满配功耗 2800W
      - document_id: doc_ne8000_hwinfo
        snapshot_id: snap_0007
        segment_id: seg_00415
        anchor: 3.1 硬件规格/表3-2
        quoted_value: 业务槽位数 8
  unit:
    value: W
---
# NE8000-M8 V300R022

整机功耗上限 2800 W（满配口径，见 [[OntologyModule@device-spec-terms]]），业务槽位 8。

> 数据为虚构，仅用于结构验证。

## 边
- 所属制品: [[DataProduct@spec-ne8000]]
- 依赖本体: [[OntologyModule@device-spec-terms]]
- 同型号其他版本: [[spec-ne8000@DomainFactSet@NE8000-M8 V300R023]]
