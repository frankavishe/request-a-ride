import asyncio

import jwt
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    Place,
    checkout_payment_matches_ride,
    issue_token,
    parse_bearer_token,
    password_hash,
    password_matches,
    require_role,
    settings,
)


def test_password_hash_verifies_without_storing_plaintext():
    encoded = password_hash("correct horse battery staple")

    assert encoded != "correct horse battery staple"
    assert password_matches("correct horse battery staple", encoded)
    assert not password_matches("incorrect password", encoded)


def test_password_hash_uses_a_fresh_salt():
    assert password_hash("same password") != password_hash("same password")


def test_access_token_contains_user_identity_and_expiry(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-with-at-least-32-characters")

    token = issue_token({"id": "rider-123"})
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])

    assert payload["sub"] == "rider-123"
    assert payload["exp"] > payload["iat"]


def test_places_reject_out_of_range_coordinates():
    with pytest.raises(ValidationError):
        Place(name="London", address="London, UK", latitude=93, longitude=0)


def test_bearer_tokens_are_parsed_case_insensitively_and_trimmed():
    assert parse_bearer_token("Bearer token-123") == "token-123"
    assert parse_bearer_token("bearer   token-456") == "token-456"
    assert parse_bearer_token("Token token-789") is None
    assert parse_bearer_token("Bearer   ") is None


def test_role_guard_rejects_users_with_the_wrong_role():
    with pytest.raises(HTTPException) as issue:
        asyncio.run(require_role("driver")({"role": "rider"}))

    assert issue.value.status_code == 403


def test_checkout_payment_matches_precision_and_rounding():
    ride = {"id": "ride-456", "fare": 12.345}
    session = {
        "mode": "payment",
        "payment_status": "paid",
        "metadata": {"ride_id": "ride-456"},
        "currency": "gbp",
        "amount_total": 1235,
    }

    assert checkout_payment_matches_ride(session, ride)
