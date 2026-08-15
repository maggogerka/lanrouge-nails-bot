"""DTOs passed between short notification transactions and Telegram I/O."""

from datetime import datetime

from pydantic import BaseModel

from app.domain.enums import NotificationType


class NotificationDelivery(BaseModel):
    job_id: int
    appointment_id: int
    recipient_user_id: int
    recipient_telegram_id: int
    notification_type: NotificationType
    offset_minutes: int
    attempts: int
    service_name: str
    start_at: datetime
    timezone: str
    address: str
    map_url: str | None = None
    master_telegram_url: str | None = None
    client_name: str
    client_phone: str | None
    payment_id: int | None = None
