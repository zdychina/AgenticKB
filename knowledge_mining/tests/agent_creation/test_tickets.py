"""任务票据单测（52号 P2，50号 §3.1）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from knowledge_mining.mining.agent_creation import tickets
from knowledge_mining.mining.agent_creation.models import (
    INSTANCE_NOT_ACCEPTING,
    TICKET_EXPIRED,
    TICKET_INVALID,
    TICKET_PRODUCT_MISMATCH,
    TICKET_REVOKED,
    TicketRejected,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _ticket(**overrides) -> dict:
    row = {
        "id": "kpt_1",
        "instance_id": "kpi_1",
        "product_id": "spec-ne8000",
        "token_hash": "h",
        "status": "active",
        "issued_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "revoked_at": None,
        "revoked_reason": None,
    }
    row.update(overrides)
    return row


def _instance(**overrides) -> dict:
    row = {
        "id": "kpi_1",
        "product_id": "spec-ne8000",
        "definition_revision": 1,
        "base_draft_revision": 1,
        "status": "running",
    }
    row.update(overrides)
    return row


def test_minted_token_is_opaque_and_hashes_stably() -> None:
    token, digest = tickets.mint_token()
    assert token.startswith("kpt_")
    assert len(token) > 40
    assert digest == tickets.hash_token(token)
    assert token not in digest  # 哈希里不该残留明文

    other, _ = tickets.mint_token()
    assert other != token


def test_redaction_never_shows_the_middle() -> None:
    token, _ = tickets.mint_token()
    masked = tickets.redact(token)
    assert token not in masked
    assert masked.startswith(token[:8]) and masked.endswith(token[-4:])


def test_verify_accepts_a_live_ticket() -> None:
    claims = tickets.verify(_ticket(), _instance(), now=NOW)
    assert claims.instance_id == "kpi_1"
    assert claims.product_id == "spec-ne8000"
    assert claims.definition_revision == 1


def test_unknown_ticket_is_rejected() -> None:
    with pytest.raises(TicketRejected) as exc:
        tickets.verify(None, _instance(), now=NOW)
    assert exc.value.code == TICKET_INVALID


def test_revoked_ticket_is_rejected_with_its_reason() -> None:
    row = _ticket(status="revoked", revoked_reason="资料范围已收缩")
    with pytest.raises(TicketRejected) as exc:
        tickets.verify(row, _instance(), now=NOW)
    assert exc.value.code == TICKET_REVOKED
    assert "资料范围已收缩" in exc.value.message


def test_expiry_is_a_time_judgement_not_a_status() -> None:
    """过期不靠后台任务改状态——少一条能失效的路径就少一处漏判。"""
    row = _ticket(expires_at=(NOW - timedelta(seconds=1)).isoformat())
    assert row["status"] == "active"
    with pytest.raises(TicketRejected) as exc:
        tickets.verify(row, _instance(), now=NOW)
    assert exc.value.code == TICKET_EXPIRED


def test_naive_expiry_timestamps_are_treated_as_utc() -> None:
    row = _ticket(expires_at=(NOW + timedelta(hours=1)).replace(tzinfo=None).isoformat())
    assert tickets.verify(row, _instance(), now=NOW).ticket_id == "kpt_1"


@pytest.mark.parametrize("status", ["paused", "cancelled", "completed", "failed"])
def test_late_submission_after_instance_stops_is_rejected(status: str) -> None:
    """调 DSH cancel 之后队列里可能还有迟到调用——实例状态这一关要挡住。"""
    with pytest.raises(TicketRejected) as exc:
        tickets.verify(_ticket(), _instance(status=status), now=NOW)
    assert exc.value.code == INSTANCE_NOT_ACCEPTING


def test_ticket_for_another_product_is_rejected() -> None:
    with pytest.raises(TicketRejected) as exc:
        tickets.verify(_ticket(), _instance(), expected_product_id="other", now=NOW)
    assert exc.value.code == TICKET_PRODUCT_MISMATCH


def test_missing_instance_is_rejected() -> None:
    with pytest.raises(TicketRejected) as exc:
        tickets.verify(_ticket(), None, now=NOW)
    assert exc.value.code == TICKET_INVALID


def test_default_expiry_is_short() -> None:
    expires = datetime.fromisoformat(tickets.expiry_from(now=NOW))
    assert expires - NOW == tickets.DEFAULT_TTL
    assert tickets.DEFAULT_TTL <= timedelta(hours=8)
