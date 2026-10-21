from decimal import Decimal

import pytest

from app.contract import redact
from app.services.promos import discount


@pytest.mark.parametrize(
    "subtotal,kind,value,expected",
    [
        ("100", "PERCENTAGE", "90", "70.00"),
        ("0.01", "PERCENTAGE", "50", "0.01"),
        ("19.99", "PERCENTAGE", "10", "2.00"),
        ("100", "FIXED_AMOUNT", "200", "100.00"),
    ],
)
def test_discount_rounding(subtotal, kind, value, expected):
    assert discount(Decimal(subtotal), kind, Decimal(value)) == Decimal(expected)


def test_nested_redaction():
    assert redact({"Password": "secret", "nested": [{"refresh_token": "secret"}], "quantity": 2}) == {
        "Password": "[redacted]",
        "nested": [{"refresh_token": "[redacted]"}],
        "quantity": 2,
    }
