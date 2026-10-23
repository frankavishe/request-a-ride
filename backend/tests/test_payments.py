import asyncio
import hashlib
import hmac
import json
from threading import Lock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.main import checkout_payment_matches_ride, settings, stripe_webhook


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.lock = Lock()

    async def find_one(self, query):
        return next((dict(doc) for doc in self.documents if matches(doc, query)), None)

    async def update_one(self, query, update):
        with self.lock:
            for document in self.documents:
                if matches(document, query):
                    for key, value in update.get("$set", {}).items():
                        document[key] = value
                    for key in update.get("$unset", {}):
                        document.pop(key, None)
                    return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def matches(document, query):
    for key, expected in query.items():
        if isinstance(expected, dict):
            if "$ne" in expected and document.get(key) == expected["$ne"]:
                return False
        elif document.get(key) != expected:
            return False
    return True


def webhook_request(body, db):
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/payments/webhook",
            "headers": [],
            "query_string": b"",
            "app": SimpleNamespace(state=SimpleNamespace(db=db)),
        },
        receive,
    )


def stripe_signature(body, secret, timestamp):
    signed_payload = str(timestamp).encode() + b"." + body
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


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


def test_paid_checkout_webhook_marks_ride_paid_and_releases_rider(monkeypatch):
    secret = "webhook-test-secret"
    timestamp = 1_800_000_000
    monkeypatch.setattr(settings, "stripe_webhook_secret", secret)
    monkeypatch.setattr("app.main.time.time", lambda: timestamp)
    ride = {
        "id": "ride-123",
        "rider_id": "rider-1",
        "fare": 12.34,
        "status": "completed",
        "payment_status": "pending",
    }
    db = SimpleNamespace(
        rides=FakeCollection([ride]),
        users=FakeCollection(
            [{"id": "rider-1", "active_ride_id": "ride-123", "active_ride_reserved_at": "reserved"}]
        ),
    )
    body = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "mode": "payment",
                    "payment_status": "paid",
                    "metadata": {"ride_id": "ride-123"},
                    "currency": "gbp",
                    "amount_total": 1234,
                }
            },
        }
    ).encode()

    result = asyncio.run(
        stripe_webhook(
            webhook_request(body, db),
            stripe_signature(body, secret, timestamp),
        )
    )

    assert result == {"received": True}
    assert ride["payment_status"] == "paid"
    assert "active_ride_id" not in db.users.documents[0]
    assert "active_ride_reserved_at" not in db.users.documents[0]


def test_unpaid_checkout_event_does_not_complete_payment(monkeypatch):
    secret = "webhook-test-secret"
    timestamp = 1_800_000_000
    monkeypatch.setattr(settings, "stripe_webhook_secret", secret)
    monkeypatch.setattr("app.main.time.time", lambda: timestamp)
    ride = {"id": "ride-123", "fare": 12.34, "status": "accepted", "payment_status": "pending"}
    db = SimpleNamespace(rides=FakeCollection([ride]), users=FakeCollection([]))
    body = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {"object": {"payment_status": "unpaid"}},
        }
    ).encode()

    result = asyncio.run(
        stripe_webhook(
            webhook_request(body, db),
            stripe_signature(body, secret, timestamp),
        )
    )

    assert result == {"received": True}
    assert ride["payment_status"] == "pending"


def test_checkout_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "webhook-test-secret")
    with pytest.raises(HTTPException) as issue:
        asyncio.run(stripe_webhook(webhook_request(b"{}", SimpleNamespace()), "t=1,v1=bad"))

    assert issue.value.status_code == 400
