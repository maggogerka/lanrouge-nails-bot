"""Vendor support is safe, configured once and role-restricted."""

import pytest

from app.config import Settings
from app.domain.enums import StaffRole
from app.domain.errors import AuthorizationError
from app.schemas.authorization import StaffContext
from app.services.vendor_support_service import VendorSupportService


def context(role: StaffRole) -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=2,
        user_id=3,
        telegram_id=4,
        display_name="Staff",
        role=role,
        is_bookable=role is StaffRole.MASTER,
    )


def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        INSTANCE_ID="studio-01",
        VENDOR_SUPPORT_URL="https://vendor.example.test/help",
        VENDOR_SUPPORT_NAME="Support <CRM>",
        VENDOR_SUPPORT_HOURS="Пн–Пт 10:00–18:00",
        VENDOR_SUPPORT_INSTRUCTIONS="Укажите шаги воспроизведения.",
    )


@pytest.mark.parametrize("role", [StaffRole.OWNER, StaffRole.MANAGER, StaffRole.MASTER])
def test_authorized_roles_receive_safe_operational_context(role: StaffRole) -> None:
    text, url = VendorSupportService(settings()).render(
        context(role),
        correlation_id="corr-123",
    )

    assert "Support &lt;CRM&gt;" in text
    assert "studio-01" in text
    assert "0.4.3" in text
    assert "corr-123" in text
    assert "Не отправляйте токен" in text
    assert url == "https://vendor.example.test/help"


def test_receptionist_cannot_open_vendor_support() -> None:
    with pytest.raises(AuthorizationError):
        VendorSupportService(settings()).render(
            context(StaffRole.RECEPTIONIST),
            correlation_id="corr-123",
        )
