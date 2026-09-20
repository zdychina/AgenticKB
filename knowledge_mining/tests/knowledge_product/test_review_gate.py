"""发布门禁与人审状态流转单测（52号 P4，48号 §六/§七）。"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.knowledge_product.review import (
    AGENT_SUBMITTED,
    ALL_REVIEWED,
    APPROVE,
    EVIDENCE_TRACEABLE,
    HUMAN_CONFIRMED,
    NO_HIDDEN_CONFLICT,
    OWNER_AND_SCOPE,
    REFERENCES_RESOLVABLE,
    REJECT,
    REJECTED,
    TRIAL_PASSED,
    UNRESOLVED,
    ReviewRejected,
    evaluate_gate,
    next_status,
)

PRODUCT = {"owner": "zhangsan", "purpose": "比较这些型号的能力"}
DEFINITION = {"fields_json": {"model": {"required": True}, "unit": {"required": False}}}


def _object(object_id: str, *, status: str = HUMAN_CONFIRMED, sourced: bool = True) -> dict:
    field: dict = {"value": "M8"}
    if sourced:
        field["evidence"] = [{"document_id": "d", "snapshot_id": "s", "segment_id": "g"}]
    return {
        "object_id": object_id,
        "review_status": status,
        "frontmatter_json": {"fields": {"model": field}},
    }


def _passing_gate(**overrides):
    kwargs = dict(
        product=PRODUCT,
        definition=DEFINITION,
        objects=[_object("p@DomainFactSet@a")],
        edge_targets=[],
        trials=[{"verdict": "passed"}],
    )
    kwargs.update(overrides)
    return evaluate_gate(**kwargs)


# ----------------------------------------------------------------- 状态流转


def test_approve_confirms_an_agent_submission() -> None:
    assert next_status(AGENT_SUBMITTED, APPROVE) == HUMAN_CONFIRMED


def test_reject_marks_the_object_rejected() -> None:
    assert next_status(AGENT_SUBMITTED, REJECT) == REJECTED


def test_confirming_a_conflicted_object_is_refused() -> None:
    """直接确认一个带冲突的对象，等于把冲突掩盖掉。"""
    with pytest.raises(ReviewRejected, match="冲突"):
        next_status(UNRESOLVED, APPROVE)


def test_conflicted_object_may_still_be_rejected() -> None:
    assert next_status(UNRESOLVED, REJECT) == REJECTED


def test_unknown_decision_is_refused() -> None:
    with pytest.raises(ReviewRejected, match="未知"):
        next_status(AGENT_SUBMITTED, "maybe")


# ----------------------------------------------------------------- 门禁


def test_a_fully_prepared_revision_passes() -> None:
    gate = _passing_gate()
    assert gate.passed
    assert gate.blocking == ()
    assert {c.code for c in gate.checks} == {
        OWNER_AND_SCOPE, EVIDENCE_TRACEABLE, NO_HIDDEN_CONFLICT,
        ALL_REVIEWED, TRIAL_PASSED, REFERENCES_RESOLVABLE,
    }


def test_missing_owner_or_purpose_blocks() -> None:
    gate = _passing_gate(product={"owner": "", "purpose": ""})
    check = next(c for c in gate.checks if c.code == OWNER_AND_SCOPE)
    assert not check.passed
    assert set(check.offenders) == {"负责人", "适用范围"}


def test_required_field_without_evidence_blocks() -> None:
    gate = _passing_gate(objects=[_object("p@DomainFactSet@a", sourced=False)])
    check = next(c for c in gate.checks if c.code == EVIDENCE_TRACEABLE)
    assert not check.passed
    assert check.offenders == ("p@DomainFactSet@a.model",)


def test_conflict_field_counts_as_sourced_but_blocks_elsewhere() -> None:
    """冲突字段本身带候选出处，不算「无来源」——它被「冲突没被掩盖」那条挡住。"""
    conflicted = {
        "object_id": "p@DomainFactSet@a",
        "review_status": UNRESOLVED,
        "frontmatter_json": {
            "fields": {"model": {"value": None, "conflict": {"reason": "口径不同"}}}
        },
    }
    gate = _passing_gate(objects=[conflicted])
    assert next(c for c in gate.checks if c.code == EVIDENCE_TRACEABLE).passed
    assert not next(c for c in gate.checks if c.code == NO_HIDDEN_CONFLICT).passed


def test_unresolved_object_blocks_publication() -> None:
    gate = _passing_gate(objects=[_object("p@DomainFactSet@a", status=UNRESOLVED)])
    check = next(c for c in gate.checks if c.code == NO_HIDDEN_CONFLICT)
    assert not check.passed
    assert check.offenders == ("p@DomainFactSet@a",)


def test_unreviewed_object_blocks_publication() -> None:
    """没人看过的东西不对外服务。"""
    gate = _passing_gate(objects=[_object("p@DomainFactSet@a", status=AGENT_SUBMITTED)])
    check = next(c for c in gate.checks if c.code == ALL_REVIEWED)
    assert not check.passed
    assert check.offenders == ("p@DomainFactSet@a",)


def test_rejected_object_also_blocks() -> None:
    gate = _passing_gate(objects=[_object("p@DomainFactSet@a", status=REJECTED)])
    assert not next(c for c in gate.checks if c.code == ALL_REVIEWED).passed


def test_conflicted_object_is_not_double_counted() -> None:
    """冲突项由「冲突」那条报，不在「未人审」里重复计一遍。"""
    gate = _passing_gate(objects=[_object("p@DomainFactSet@a", status=UNRESOLVED)])
    assert next(c for c in gate.checks if c.code == ALL_REVIEWED).offenders == ()


def test_empty_revision_cannot_be_published() -> None:
    gate = _passing_gate(objects=[])
    check = next(c for c in gate.checks if c.code == ALL_REVIEWED)
    assert not check.passed
    assert "没有任何对象" in check.detail


def test_no_passing_trial_blocks() -> None:
    gate = _passing_gate(trials=[{"verdict": "failed"}])
    check = next(c for c in gate.checks if c.code == TRIAL_PASSED)
    assert not check.passed
    assert "试用" in check.detail


def test_dangling_reference_blocks_publication() -> None:
    """制作期间悬挂边是正常的，但**发布时不行**——引用得拿得到。"""
    gate = _passing_gate(edge_targets=["RulePackage@not-built-yet"])
    check = next(c for c in gate.checks if c.code == REFERENCES_RESOLVABLE)
    assert not check.passed
    assert check.offenders == ("RulePackage@not-built-yet",)


def test_resolvable_reference_passes() -> None:
    gate = _passing_gate(edge_targets=["p@DomainFactSet@a"])
    assert next(c for c in gate.checks if c.code == REFERENCES_RESOLVABLE).passed


def test_gate_reports_every_blocker_not_just_the_first() -> None:
    gate = evaluate_gate(
        product={"owner": "", "purpose": ""},
        definition=DEFINITION,
        objects=[_object("p@DomainFactSet@a", status=AGENT_SUBMITTED, sourced=False)],
        edge_targets=["X@y"],
        trials=[],
    )
    assert not gate.passed
    assert len(gate.blocking) == 5
