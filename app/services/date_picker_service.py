"""Framework-independent calendar-page generation and callback validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.errors import DatePickerValidationError

DEFAULT_DATE_PICKER_DAYS = 31
_WEEKDAY_LABELS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


@dataclass(frozen=True, slots=True)
class DatePickerDay:
    local_date: date
    label: str
    selectable: bool


@dataclass(frozen=True, slots=True)
class DatePickerPage:
    today: date
    start_date: date
    end_date: date
    days: tuple[DatePickerDay, ...]
    previous_start: date | None
    next_start: date | None


class DatePickerService:
    """Build deterministic pages while treating every callback as untrusted input."""

    def build_page(
        self,
        *,
        today: date,
        requested_start: date | None,
        booking_horizon_days: int,
        allow_saturday: bool,
        allow_sunday: bool,
        page_size: int = DEFAULT_DATE_PICKER_DAYS,
    ) -> DatePickerPage:
        if booking_horizon_days < 0:
            raise ValueError("booking horizon must not be negative")
        if page_size <= 0:
            raise ValueError("page size must be positive")

        latest = today + timedelta(days=booking_horizon_days)
        start = requested_start or today
        if start < today:
            raise DatePickerValidationError(
                "Эта страница календаря устарела. Показаны актуальные даты."
            )
        if start > latest:
            raise DatePickerValidationError("Эти даты находятся за пределами доступного периода.")

        end = min(start + timedelta(days=page_size - 1), latest)
        dates = tuple(self._date_range(start, end))
        days = tuple(
            DatePickerDay(
                local_date=value,
                label=self.label(value),
                selectable=self._is_selectable_weekday(
                    value,
                    allow_saturday=allow_saturday,
                    allow_sunday=allow_sunday,
                ),
            )
            for value in dates
        )
        previous_start = max(today, start - timedelta(days=page_size)) if start > today else None
        next_start = end + timedelta(days=1) if end < latest else None
        return DatePickerPage(
            today=today,
            start_date=start,
            end_date=end,
            days=days,
            previous_start=previous_start,
            next_start=next_start,
        )

    def validate_selection(
        self,
        selected: date,
        *,
        today: date,
        booking_horizon_days: int,
        allow_saturday: bool,
        allow_sunday: bool,
    ) -> date:
        if selected < today:
            raise DatePickerValidationError(
                "Эта дата уже прошла. Выберите дату из актуального календаря."
            )
        if selected > today + timedelta(days=booking_horizon_days):
            raise DatePickerValidationError("Эта дата находится за пределами доступного периода.")
        if not self._is_selectable_weekday(
            selected,
            allow_saturday=allow_saturday,
            allow_sunday=allow_sunday,
        ):
            raise DatePickerValidationError(
                "Этот выходной сейчас недоступен для создания открытого окна."
            )
        return selected

    @staticmethod
    def label(value: date) -> str:
        return f"{value:%d.%m} {_WEEKDAY_LABELS[value.weekday()]}"

    @staticmethod
    def _is_selectable_weekday(
        value: date,
        *,
        allow_saturday: bool,
        allow_sunday: bool,
    ) -> bool:
        if value.weekday() == 5:
            return allow_saturday
        if value.weekday() == 6:
            return allow_sunday
        return True

    @staticmethod
    def _date_range(start: date, end: date) -> list[date]:
        return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
