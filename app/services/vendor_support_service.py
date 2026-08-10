"""Safe staff-only projection of CRM vendor support configuration."""

from __future__ import annotations

from html import escape

from app import __version__
from app.config import Settings
from app.domain.errors import AuthorizationError
from app.schemas.authorization import StaffContext, StaffPermission


class VendorSupportService:
    """Render operational support details without exposing application secrets."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def render(self, actor: StaffContext, *, correlation_id: str) -> tuple[str, str | None]:
        if not actor.has_permission(StaffPermission.VIEW_VENDOR_SUPPORT):
            raise AuthorizationError("Техническая поддержка недоступна для этой роли.")

        details = [f"<b>{escape(self._settings.vendor_support_name)}</b>"]
        if self._settings.vendor_support_hours:
            details.append(f"График: {escape(self._settings.vendor_support_hours)}")
        if self._settings.vendor_support_instructions:
            details.append(escape(self._settings.vendor_support_instructions))
        if self._settings.vendor_support_url is None:
            details.append("Контакт технической поддержки ещё не настроен оператором CRM.")
        details.extend(
            (
                f"Экземпляр: <code>{escape(self._settings.instance_id)}</code>",
                f"Версия: <code>{escape(__version__)}</code>",
                f"Correlation ID: <code>{escape(correlation_id)}</code>",
                "Не отправляйте токен бота, пароли БД/Redis, YooKassa secret "
                "или коды подтверждения.",
            )
        )
        url = (
            str(self._settings.vendor_support_url)
            if self._settings.vendor_support_url is not None
            else None
        )
        return "\n\n".join(details), url
