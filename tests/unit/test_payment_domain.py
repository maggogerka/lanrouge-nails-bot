"""Payment value validation, lifecycle and persistence-shape tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.database.models.payment import PaymentWebhookEvent
from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.domain.payments import (
    PaymentStateError,
    require_payment_transition,
    require_refund_transition,
    validate_money,
    validate_safe_metadata,
)
from app.schemas.payment import PaymentCreate, RefundCreate


def test_money_is_exact_and_currency_is_normalized() -> None:
    assert validate_money(Decimal("250"), "rub") == (Decimal("250.00"), "RUB")

    with pytest.raises(ValueError, match="two decimal"):
        validate_money(Decimal("1.001"), "RUB")
    with pytest.raises(ValueError, match="positive"):
        validate_money(Decimal("0"), "RUB")
    with pytest.raises(ValueError, match="three-letter"):
        validate_money(Decimal("1"), "RUBLE")


def test_safe_metadata_is_flat_bounded_and_rejects_secret_or_pii_keys() -> None:
    assert validate_safe_metadata({"business_id": "7", "campaign": "summer"}) == {
        "business_id": "7",
        "campaign": "summer",
    }

    for unsafe in ("api_token", "client_phone", "card_number", "webhook_payload"):
        with pytest.raises(ValueError, match="personal or secret"):
            validate_safe_metadata({unsafe: "value"})
    with pytest.raises(ValueError, match="at most 16"):
        validate_safe_metadata({f"key_{index}": "value" for index in range(17)})


def test_payment_transition_table_rejects_regression_and_terminal_change() -> None:
    require_payment_transition(PaymentStatus.CREATED, PaymentStatus.PENDING)
    require_payment_transition(PaymentStatus.PENDING, PaymentStatus.SUCCEEDED)
    require_payment_transition(PaymentStatus.SUCCEEDED, PaymentStatus.REFUND_PENDING)
    require_payment_transition(PaymentStatus.REFUND_PENDING, PaymentStatus.PARTIALLY_REFUNDED)

    with pytest.raises(PaymentStateError):
        require_payment_transition(PaymentStatus.SUCCEEDED, PaymentStatus.PENDING)
    with pytest.raises(PaymentStateError):
        require_payment_transition(PaymentStatus.REFUNDED, PaymentStatus.SUCCEEDED)


def test_refund_transition_is_one_way_and_idempotent() -> None:
    require_refund_transition(RefundStatus.PENDING, RefundStatus.SUCCEEDED)
    require_refund_transition(RefundStatus.SUCCEEDED, RefundStatus.SUCCEEDED)

    with pytest.raises(PaymentStateError):
        require_refund_transition(RefundStatus.SUCCEEDED, RefundStatus.PENDING)


def test_payment_create_rejects_disabled_insecure_url_and_weak_key() -> None:
    common = {
        "business_id": 1,
        "appointment_id": 2,
        "payment_type": "deposit",
        "amount": "500.00",
        "currency": "RUB",
        "idempotency_key": "12345678-1234-1234-1234-123456789012",
    }
    with pytest.raises(ValidationError, match="disabled"):
        PaymentCreate(provider=PaymentMode.DISABLED, **common)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="HTTPS"):
        PaymentCreate(  # type: ignore[arg-type]
            provider=PaymentMode.YOOKASSA,
            return_url="http://example.test/return",
            **common,
        )
    with pytest.raises(ValidationError, match="at least 16"):
        PaymentCreate(  # type: ignore[arg-type]
            provider=PaymentMode.MANUAL,
            idempotency_key="short",
            **{key: value for key, value in common.items() if key != "idempotency_key"},
        )


def test_refund_reason_is_machine_code_not_free_text() -> None:
    with pytest.raises(ValidationError, match="machine code"):
        RefundCreate(
            business_id=1,
            payment_id=2,
            amount=Decimal("100"),
            idempotency_key="12345678-1234-1234-1234-123456789012",
            reason_code="Клиент написал номер карты",
        )


def test_webhook_table_has_only_bounded_projection_and_expiry() -> None:
    columns = set(PaymentWebhookEvent.__table__.columns.keys())

    assert {"event_key", "payload_sha256", "expires_at", "last_error_code"} <= columns
    assert not {"payload", "headers", "authorization", "raw_body"} & columns
