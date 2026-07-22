"""Service catalog boundary validation tests."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.service import ServiceCreate, ServicePatch


def test_service_create_normalizes_values() -> None:
    values = ServiceCreate(
        name="  Маникюр с покрытием  ",
        description="  Базовая услуга  ",
        price="2500.50",
        duration_min_minutes=120,
        duration_max_minutes=180,
    )

    assert values.name == "Маникюр с покрытием"
    assert values.description == "Базовая услуга"
    assert values.price == Decimal("2500.50")


def test_service_create_rejects_invalid_range_and_money() -> None:
    with pytest.raises(ValidationError, match="minimum duration"):
        ServiceCreate(
            name="Маникюр",
            price=1000,
            duration_min_minutes=180,
            duration_max_minutes=120,
        )

    with pytest.raises(ValidationError):
        ServiceCreate(
            name="Маникюр",
            price="1000.999",
            duration_min_minutes=120,
            duration_max_minutes=180,
        )


def test_service_patch_distinguishes_clear_description_from_missing() -> None:
    patch = ServicePatch(description="  ")

    assert patch.description is None
    assert patch.model_fields_set == {"description"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", None),
        ("price", None),
        ("duration_min_minutes", None),
        ("duration_max_minutes", None),
    ],
)
def test_service_patch_rejects_null_for_required_editable_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match="must not be null"):
        ServicePatch.model_validate({field: value})


def test_service_patch_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ServicePatch()
