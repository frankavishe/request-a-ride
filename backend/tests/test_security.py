import jwt
import pytest
from pydantic import ValidationError

from app.main import Place, issue_token, password_hash, password_matches, settings


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
