"""Versioned in-product legal copy whose exact digest is persisted with consent."""

from __future__ import annotations

import hashlib

from app.domain.privacy import PolicyDocument

MARKETING_CONSENT_VERSION = "marketing-consent-2026-08"
MARKETING_CONSENT_TEXT = (
    "Хотите отдельно получать рекламные сообщения о новых услугах, работах и свободных "
    "окнах? Отказ не отключает подтверждения записи и другие сервисные уведомления."
)
MARKETING_CONSENT_SHA256 = hashlib.sha256(MARKETING_CONSENT_TEXT.encode("utf-8")).hexdigest()


def marketing_consent_policy() -> PolicyDocument:
    """Return the stable identity of the mailing-consent copy shown by the bot."""

    return PolicyDocument(
        version=MARKETING_CONSENT_VERSION,
        url=None,
        sha256=MARKETING_CONSENT_SHA256,
    )
