"""Review, trial and the publish gate（52号 P4）。

审核单位是**一次 submission 批次**，批次内按对象展开（52号 A2）。对象的人审结论
落在 ``kp_objects.review_status``：

    agent_submitted ──人工确认──▶ human_confirmed
            │                            │
            └────────人工驳回───────────▶ rejected
    unresolved（有冲突字段）──必须先消解冲突，不能直接确认

发布门禁照 48号 §七：「发布前至少确认：负责人和适用范围明确、关键内容能回源、
冲突没有被掩盖、试用记录符合本用途的要求、所引用内容有权发布。」门禁返回的是一张
**逐项清单**而不是一个布尔——发布被挡住时，人得知道卡在哪一条。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

AGENT_SUBMITTED = "agent_submitted"
HUMAN_CONFIRMED = "human_confirmed"
UNRESOLVED = "unresolved"
REJECTED = "rejected"

APPROVE = "approve"
REJECT = "reject"

#: 可发布的对象状态。``agent_submitted`` 不在内——没人看过的东西不该对外服务。
PUBLISHABLE_STATUSES = frozenset({HUMAN_CONFIRMED})

# 门禁项编号（前端与验收清单按它对齐，别改字面量）
OWNER_AND_SCOPE = "owner_and_scope"
EVIDENCE_TRACEABLE = "evidence_traceable"
NO_HIDDEN_CONFLICT = "no_hidden_conflict"
ALL_REVIEWED = "all_reviewed"
TRIAL_PASSED = "trial_passed"
REFERENCES_RESOLVABLE = "references_resolvable"


class ReviewRejected(Exception):
    """人审动作本身不合法（比如想确认一个还带冲突的对象）。"""


@dataclass(frozen=True)
class GateCheck:
    code: str
    passed: bool
    detail: str
    offenders: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "passed": self.passed,
            "detail": self.detail,
            "offenders": list(self.offenders),
        }


@dataclass(frozen=True)
class PublishGate:
    checks: tuple[GateCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def blocking(self) -> tuple[GateCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
        }


def next_status(current: str, decision: str) -> str:
    """一次人审决定把对象带到哪个状态。"""
    if decision == APPROVE:
        if current == UNRESOLVED:
            raise ReviewRejected(
                "该对象存在未定论的冲突字段，必须先消解冲突再确认——"
                "直接确认等于把冲突掩盖掉"
            )
        return HUMAN_CONFIRMED
    if decision == REJECT:
        return REJECTED
    raise ReviewRejected(f"未知的人审决定: {decision!r}")


def _required_fields(definition: Mapping[str, Any] | None) -> list[str]:
    fields = (definition or {}).get("fields_json") or {}
    return [name for name, spec in fields.items() if (spec or {}).get("required")]


def evaluate_gate(
    *,
    product: Mapping[str, Any],
    definition: Mapping[str, Any] | None,
    objects: Sequence[Mapping[str, Any]],
    edge_targets: Iterable[str],
    trials: Sequence[Mapping[str, Any]],
) -> PublishGate:
    """按 48号 §七 的五条，逐项算出发布门禁清单。"""
    checks: list[GateCheck] = []

    # ① 负责人和适用范围明确
    missing = [
        label for label, value in (("负责人", product.get("owner")),
                                   ("适用范围", product.get("purpose")))
        if not str(value or "").strip()
    ]
    checks.append(
        GateCheck(
            OWNER_AND_SCOPE,
            not missing,
            "负责人与适用范围已填写" if not missing else f"缺少：{'、'.join(missing)}",
            tuple(missing),
        )
    )

    # ② 关键内容能回源：每个必填字段都得有证据
    required = _required_fields(definition)
    unsourced: list[str] = []
    if required:
        for row in objects:
            fields = (row.get("frontmatter_json") or {}).get("fields") or {}
            if not fields:
                continue  # 没有 fields 块的对象（如身份对象/本体）不在此项范围内
            for name in required:
                field = fields.get(name)
                if not isinstance(field, dict):
                    unsourced.append(f"{row['object_id']}.{name}")
                elif not field.get("evidence") and not field.get("conflict"):
                    unsourced.append(f"{row['object_id']}.{name}")
    checks.append(
        GateCheck(
            EVIDENCE_TRACEABLE,
            not unsourced,
            "全部必填字段都可回源" if not unsourced else f"{len(unsourced)} 处必填字段没有出处",
            tuple(sorted(unsourced)),
        )
    )

    # ③ 冲突没有被掩盖
    conflicted = sorted(
        row["object_id"] for row in objects if row.get("review_status") == UNRESOLVED
    )
    checks.append(
        GateCheck(
            NO_HIDDEN_CONFLICT,
            not conflicted,
            "没有未定论的冲突" if not conflicted else f"{len(conflicted)} 个对象仍有未定论冲突",
            tuple(conflicted),
        )
    )

    # ④ 全部对象都过了人审（没人看过的不对外服务）
    unreviewed = sorted(
        row["object_id"] for row in objects
        if row.get("review_status") not in PUBLISHABLE_STATUSES
        and row.get("review_status") != UNRESOLVED  # 冲突项已由 ③ 报过，不重复计
    )
    checks.append(
        GateCheck(
            ALL_REVIEWED,
            not unreviewed and bool(objects),
            "全部对象已人工确认" if unreviewed == [] and objects
            else ("本修订没有任何对象" if not objects
                  else f"{len(unreviewed)} 个对象尚未人工确认或已被驳回"),
            tuple(unreviewed),
        )
    )

    # ⑤ 试用记录：本修订至少有一条通过
    passed_trials = [t for t in trials if t.get("verdict") == "passed"]
    checks.append(
        GateCheck(
            TRIAL_PASSED,
            bool(passed_trials),
            f"本修订有 {len(passed_trials)} 条通过的试用记录" if passed_trials
            else "本修订还没有通过的试用记录——「调用次数只能说明用过，不能说明有用」",
        )
    )

    # ⑥ 所引用内容拿得到：发布时不允许悬挂边
    known = {row["object_id"] for row in objects}
    dangling = sorted(set(edge_targets) - known)
    checks.append(
        GateCheck(
            REFERENCES_RESOLVABLE,
            not dangling,
            "全部引用都能解析" if not dangling
            else f"{len(dangling)} 个引用指向本修订不存在的对象",
            tuple(dangling),
        )
    )

    return PublishGate(tuple(checks))
