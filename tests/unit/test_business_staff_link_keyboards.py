"""Admin presentation exposes only the current, stable controls."""

from app.domain.enums import StaffRole
from app.keyboards.admin.business import business_profile_keyboard
from app.keyboards.admin.features import feature_flags_keyboard
from app.keyboards.admin.main import admin_main_keyboard
from app.schemas.authorization import StaffContext
from app.schemas.features import FeatureSnapshot
from app.schemas.menu import MenuCapabilities


def _texts(markup: object) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]  # type: ignore[attr-defined]


def test_business_settings_hide_manual_business_mode_and_duplicate_master_controls() -> None:
    texts = _texts(business_profile_keyboard())
    assert "📍 Адрес и карта" in texts
    assert "🛟 Источники поддержки" in texts
    assert all("solo" not in text.lower() for text in texts)
    assert all("специалист" not in text.lower() for text in texts)


def test_unfinished_features_are_not_shown_to_owner() -> None:
    snapshot = FeatureSnapshot(
        online_booking=False,
        master_selection=False,
        waitlist=False,
        portfolio=False,
        reviews=False,
        reference_photos=False,
        reminders=False,
        repeat_booking=False,
        broadcasts=False,
        loyalty=True,
        statistics=False,
        prepayment=False,
        manual_payments=False,
        yookassa_payments=False,
        mini_app=True,
        client_support=False,
    )
    texts = _texts(feature_flags_keyboard(snapshot, can_manage=True))
    assert all("Лояльность" not in text for text in texts)
    assert all("Mini App" not in text for text in texts)


def test_admin_main_has_no_legacy_master_profile_button() -> None:
    context = StaffContext(
        business_id=1,
        staff_member_id=1,
        user_id=1,
        telegram_id=1,
        display_name="Owner",
        role=StaffRole.OWNER,
        is_bookable=True,
    )
    markup = admin_main_keyboard(staff_context=context, capabilities=MenuCapabilities())
    texts = [button.text for row in markup.keyboard for button in row]
    assert "ℹ️ Информация о мастере" not in texts
