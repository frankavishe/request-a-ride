import pytest

from app.main import checkout_payment_matches_ride


@pytest.fixture
def ride():
    return {"id": "ride-123", "fare": 12.34}


@pytest.fixture
def paid_session():
    return {
        "mode": "payment",
        "payment_status": "paid",
        "metadata": {"ride_id": "ride-123"},
        "currency": "gbp",
        "amount_total": 1234,
    }


def test_checkout_payment_matches_expected_ride_and_amount(ride, paid_session):
    assert checkout_payment_matches_ride(paid_session, ride)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "subscription"),
        ("payment_status", "unpaid"),
        ("metadata", {"ride_id": "another-ride"}),
        ("metadata", None),
        ("currency", "usd"),
        ("amount_total", 1235),
    ],
)
def test_checkout_payment_rejects_mismatched_session(ride, paid_session, field, value):
    paid_session[field] = value

    assert not checkout_payment_matches_ride(paid_session, ride)
