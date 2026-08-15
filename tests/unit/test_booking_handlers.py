"""Booking presentation and callback safety tests."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.enums import BusinessType
from app.handlers.client.booking_browse import should_show_master_selection
from app.handlers.client.booking_common import (
    format_duration_range,
    render_booking_confirmation,
)
from app.keyboards.client.booking import (
    BookingCallback,
    addons_keyboard,
    confirmation_keyboard,
    masters_keyboard,
)
from app.schemas.booking import (
    BookableMasterView,
    BookingMasterOptions,
    BookingWindowView,
    BusinessInfo,
)
from app.schemas.service import ServiceAddonView, ServiceView


def test_confirmation_uses_local_time_and_escapes_client_values() -> None:
    service = ServiceView(
        id=3,
        name="Консультация <premium>",
        description=None,
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        is_active=True,
    )
    window = BookingWindowView(
        id=7,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        timezone="Europe/Moscow",
    )
    info = BusinessInfo(
        business_name="Example Studio",
        address="Дом <20>",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/example_studio",
    )

    rendered = render_booking_confirmation(
        service,
        window,
        info,
        client_name="Анна & Ко",
    )

    assert "Консультация &lt;premium&gt;" in rendered
    assert "23.07.2026" in rendered
    assert "10:00" in rendered
    assert "Анна &amp; Ко" in rendered
    assert "Дом &lt;20&gt;" in rendered
    assert format_duration_range(120, 180) == "примерно 2 ч.–3 ч."


def test_zero_price_is_rendered_as_negotiated_in_booking_confirmation() -> None:
    service = ServiceView(
        id=3,
        name="Сложный дизайн",
        description=None,
        price=Decimal("0.00"),
        duration_min_minutes=60,
        duration_max_minutes=90,
        is_active=True,
    )
    window = BookingWindowView(
        id=7,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 9, tzinfo=UTC),
        timezone="Europe/Moscow",
    )
    info = BusinessInfo(
        business_name="Example Studio",
        address="Адрес",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/example_studio",
    )

    rendered = render_booking_confirmation(service, window, info, client_name="Анна")

    assert "Основная услуга: договорная" in rendered
    assert "Итоговая стоимость: договорная" in rendered


def test_booking_callbacks_fit_telegram_limit() -> None:
    callback = BookingCallback(
        action="window",
        object_id=9_223_372_036_854_775_807,
    ).pack()

    assert len(callback.encode()) <= 64
    assert confirmation_keyboard().inline_keyboard


def test_master_selection_is_only_shown_for_multi_master_salon() -> None:
    two_masters = BookingMasterOptions(
        selection_enabled=True,
        masters=[
            BookableMasterView(id=1, display_name="Анна"),
            BookableMasterView(id=2, display_name="Мария"),
        ],
    )

    assert should_show_master_selection(BusinessType.SALON, two_masters)
    assert should_show_master_selection(BusinessType.SOLO, two_masters)
    assert not should_show_master_selection(
        BusinessType.SALON,
        two_masters.model_copy(update={"selection_enabled": False}),
    )
    assert not should_show_master_selection(
        BusinessType.SALON,
        two_masters.model_copy(update={"masters": two_masters.masters[:1]}),
    )


def test_master_keyboard_offers_any_master_without_exposing_ids_in_text() -> None:
    keyboard = masters_keyboard(
        [
            BookableMasterView(id=1, display_name="Анна"),
            BookableMasterView(id=2, display_name="Мария"),
        ]
    )
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert labels[:3] == ["✨ Любой мастер", "Анна", "Мария"]
    assert all("1" not in label and "2" not in label for label in labels[:3])


def test_addon_keyboard_marks_multiple_choices_and_confirmation_sums_them() -> None:
    service = ServiceView(
        id=3,
        name="Основная услуга",
        description=None,
        price=Decimal("2000.00"),
        duration_min_minutes=60,
        duration_max_minutes=90,
        is_active=True,
    )
    addons = [
        ServiceAddonView(
            id=10,
            service_id=3,
            name="Дополнение A",
            description=None,
            price=Decimal("300.00"),
            duration_min_minutes=15,
            duration_max_minutes=15,
            is_active=True,
        ),
        ServiceAddonView(
            id=11,
            service_id=3,
            name="Дополнение B",
            description=None,
            price=Decimal("500.00"),
            duration_min_minutes=20,
            duration_max_minutes=30,
            is_active=True,
        ),
    ]
    keyboard = addons_keyboard(addons, {10, 11})
    window = BookingWindowView(
        id=7,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, tzinfo=UTC),
        timezone="Europe/Moscow",
        base_price=Decimal("2000.00"),
        addons_price=Decimal("800.00"),
        price=Decimal("2800.00"),
        duration_min_minutes=95,
        duration_max_minutes=135,
        prepayment_amount=Decimal("500.00"),
    )
    info = BusinessInfo(
        business_name="Бизнес",
        address="Адрес",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/example",
    )

    rendered = render_booking_confirmation(service, window, info, addons=addons, client_name="Анна")

    assert keyboard.inline_keyboard[0][0].text.startswith("✅")
    assert keyboard.inline_keyboard[1][0].text.startswith("✅")
    assert "Основная услуга: 2000.00 ₽" in rendered
    assert "Сумма дополнений: 800.00 ₽" in rendered
    assert "Итоговая стоимость: 2800.00 ₽" in rendered
    assert format_duration_range(95, 135) in rendered
