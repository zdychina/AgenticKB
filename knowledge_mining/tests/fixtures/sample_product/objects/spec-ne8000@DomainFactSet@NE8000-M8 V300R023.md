---
id: "spec-ne8000@DomainFactSet@NE8000-M8 V300R023"
type: DomainFactSet
product: spec-ne8000
name: NE8000-M8 V300R023
review_status: agent_submitted
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
    value: V300R023
    evidence:
      - document_id: doc_ne8000_release_notes
        snapshot_id: snap_0011
        segment_id: seg_01041
        anchor: 2.4 版本说明
        quoted_value: V300R023C00
  key_specification:
    value:
      - 整机功耗 ≤ 2650
      - 业务槽位 8
    evidence:
      - document_id: doc_ne8000_power_guide
        snapshot_id: snap_0003
        segment_id: seg_00091
        anchor: 2.2 功耗规格/表2-1
        quoted_value: 满配功耗 2650W（R023 优化后）
      - document_id: doc_ne8000_hwinfo
        snapshot_id: snap_0007
        segment_id: seg_00415
        anchor: 3.1 硬件规格/表3-2
        quoted_value: 业务槽位数 8
  unit:
    value: W
---
# NE8000-M8 V300R023

相比 V300R022，满配功耗由 2800 W 降至 2650 W，业务槽位数不变。

> 数据为虚构，仅用于结构验证。

## 边
- 所属制品: [[DataProduct@spec-ne8000]]
- 依赖本体: [[OntologyModule@device-spec-terms]]
- 同型号其他版本: [[spec-ne8000@DomainFactSet@NE8000-M8 V300R022]]
