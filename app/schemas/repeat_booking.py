"""Current repeat-booking offer derived from immutable history and live catalog data."""

from decimal import Decimal

from pydantic import BaseModel


class RepeatBookingOffer(BaseModel):
    previous_appointment_id: int
    service_id: int
    service_name: str
    previous_price: Decimal
    current_price: Decimal | None
    service_active: bool
    master_telegram_url: str

    @property
    def price_changed(self) -> bool:
        return self.current_price is not None and self.current_price != self.previous_price
