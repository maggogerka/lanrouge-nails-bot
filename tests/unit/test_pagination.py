"""Bounded pagination metadata tests."""

import pytest
from pydantic import ValidationError

from app.schemas.pagination import Page, PageRequest


def test_page_request_uses_one_based_offset() -> None:
    request = PageRequest(page=3, page_size=10)

    assert request.offset == 20


@pytest.mark.parametrize("payload", [{"page": 0}, {"page_size": 0}, {"page_size": 51}])
def test_page_request_rejects_unbounded_callbacks(payload: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        PageRequest.model_validate(payload)


def test_page_reports_navigation_without_zero_pages() -> None:
    empty = Page[int](items=[], total=0, page=1, page_size=10)
    middle = Page[int](items=[11], total=25, page=2, page_size=10)

    assert empty.pages == 1
    assert not empty.has_previous
    assert not empty.has_next
    assert middle.pages == 3
    assert middle.has_previous
    assert middle.has_next
