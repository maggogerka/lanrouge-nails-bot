from decimal import Decimal

import pytest

from app.utils.pagination import paginate_sequence
from app.utils.pricing import format_rub_price


def test_sequence_pagination_is_bounded_and_clamps_stale_pages() -> None:
    first = paginate_sequence(list(range(19)), page=1, page_size=8)
    last = paginate_sequence(list(range(19)), page=99, page_size=8)

    assert first.items == tuple(range(8))
    assert (first.page, first.pages, first.total) == (1, 3, 19)
    assert last.items == (16, 17, 18)
    assert last.page == 3


def test_sequence_pagination_rejects_invalid_page_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        paginate_sequence([], page=1, page_size=0)


def test_zero_price_has_one_consistent_negotiated_label() -> None:
    assert format_rub_price(Decimal("0")) == "договорная"
    assert format_rub_price(Decimal("2500")) == "2500.00 ₽"
