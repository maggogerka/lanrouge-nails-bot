"""Consistent human-facing price labels."""

from decimal import Decimal


def format_rub_price(price: Decimal) -> str:
    """Zero is the explicit catalog marker for a negotiated price."""

    return "договорная" if price == 0 else f"{price:.2f} ₽"
