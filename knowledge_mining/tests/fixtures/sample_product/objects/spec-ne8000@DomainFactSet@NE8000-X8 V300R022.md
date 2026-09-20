---
id: "spec-ne8000@DomainFactSet@NE8000-X8 V300R022"
type: DomainFactSet
product: spec-ne8000
name: NE8000-X8 V300R022
review_status: agent_submitted
fields:
  model:
    value: NE8000-X8
    evidence:
      - document_id: doc_ne8000_hwinfo
        snapshot_id: snap_0007
        segment_id: seg_00431
        anchor: 3.2 硬件规格（X 系列）/表3-5
        quoted_value: NE8000-X8
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
      - 整机功耗 ≤ 4200
      - 业务槽位 8
    evidence:
      - document_id: doc_ne8000_power_guide
        snapshot_id: snap_0003
        segment_id: seg_00102
        anchor: 2.2 功耗规格/表2-1
        quoted_value: 满配功耗 4200W
      - document_id: doc_ne8000_hwinfo
        snapshot_id: snap_0007
        segment_id: seg_00434
        anchor: 3.2 硬件规格（X 系列）/表3-5
        quoted_value: 业务槽位数 8
  unit:
    value: W
---
# NE8000-X8 V300R022

X 系列 8 槽位机型，满配功耗 4200 W。

> 数据为虚构，仅用于结构验证。

## 边
- 所属制品: [[DataProduct@spec-ne8000]]
- 依赖本体: [[OntologyModule@device-spec-terms]]
- 同系列其他型号: [[spec-ne8000@DomainFactSet@NE8000-X16 V300R023]]
