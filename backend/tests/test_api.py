from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, settings


class MockCursor:
    def __init__(self, documents: list[dict]):
        self._documents = [dict(doc) for doc in documents]
        self._index = 0

    def sort(self, field: str, direction: int = 1):
        reverse = direction < 0
        self._documents.sort(key=lambda d: d.get(field) or "", reverse=reverse)
        return self

    def limit(self, count: int):
        self._documents = self._documents[:count]
        return self

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._documents):
            raise StopAsyncIteration
        doc = self._documents[self._index]
        self._index += 1
        return dict(doc)


class MockCollection:
    def __init__(self, initial: list[dict] | None = None):
        self.documents: list[dict] = [dict(d) for d in (initial or [])]
        self.lock = Lock()

    async def create_index(self, *args, **kwargs):
        pass

    async def find_one(self, query: dict, projection: dict | None = None, sort=None):
        with self.lock:
            docs = list(self.documents)
            if sort:
                for field, direction in reversed(sort):
                    reverse = direction < 0
                    docs = sorted(docs, key=lambda d: str(d.get(field) or ""), reverse=reverse)
            for doc in docs:
                if matches_query(doc, query):
                    return project_doc(dict(doc), projection)
            return None

    def find(self, query: dict, projection: dict | None = None):
        with self.lock:
            matching = [project_doc(dict(d), projection) for d in self.documents if matches_query(d, query)]
        return MockCursor(matching)

    async def insert_one(self, doc: dict):
        with self.lock:
            self.documents.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query: dict, update: dict):
        with self.lock:
            for doc in self.documents:
                if matches_query(doc, query):
                    apply_update(doc, update)
                    return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def find_one_and_update(self, query: dict, update: dict, return_document=None):
        with self.lock:
            for doc in self.documents:
                if matches_query(doc, query):
                    apply_update(doc, update)
                    return dict(doc)
        return None


def matches_query(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(matches_query(doc, alt) for alt in expected):
                return False
        elif isinstance(expected, dict):
            if "$exists" in expected and (key in doc) != expected["$exists"]:
                return False
            if "$in" in expected and doc.get(key) not in expected["$in"]:
                return False
            if "$ne" in expected and doc.get(key) == expected["$ne"]:
                return False
        elif doc.get(key) != expected:
            return False
    return True


def project_doc(doc: dict, projection: dict | None) -> dict:
    if not projection:
        return doc
    return {k: v for k, v in doc.items() if projection.get(k, 0) == 1}


def apply_update(doc: dict, update: dict):
    for k, v in update.get("$set", {}).items():
        doc[k] = v
    for k in update.get("$unset", {}):
        doc.pop(k, None)


class MockAdmin:
    async def command(self, cmd: str):
        return {"status": "ok"}


class MockMongoClient:
    def __init__(self, db):
        self._db = db
        self.admin = MockAdmin()

    def __getitem__(self, item: str):
        return self._db

    async def close(self):
        pass


@pytest.fixture
def mock_db():
    db = SimpleNamespace(
        users=MockCollection(),
        rides=MockCollection(),
    )
    client = MockMongoClient(db)
    app.state.mongo = client
    app.state.db = db
    settings.jwt_secret = "test-secret-at-least-32-characters-long-12345"
    return db


@pytest.fixture
def mock_route(monkeypatch):
    async def fake_get_route(pickup, dropoff):
        return {"distance_km": 5.2, "duration_min": 14, "fare": 12.04}

    monkeypatch.setattr("app.main.get_route", fake_get_route)


@pytest.mark.anyio
async def test_health_endpoint(mock_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok", "database": "connected"}


@pytest.mark.anyio
async def test_auth_registration_and_login_flow(mock_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register rider
        reg = await client.post(
            "/api/auth/register",
            json={"name": "Alice Rider", "email": "alice@example.com", "password": "password123", "role": "rider"},
        )
        assert reg.status_code == 200
        rider_data = reg.json()
        assert rider_data["user"]["email"] == "alice@example.com"
        assert rider_data["user"]["role"] == "rider"
        assert "access_token" in rider_data

        # Register duplicate email fails with 409
        dup = await client.post(
            "/api/auth/register",
            json={"name": "Alice Two", "email": "alice@example.com", "password": "password123", "role": "rider"},
        )
        assert dup.status_code == 409

        # Register with short name fails with 422
        bad_name = await client.post(
            "/api/auth/register",
            json={"name": "A", "email": "bob@example.com", "password": "password123", "role": "rider"},
        )
        assert bad_name.status_code == 422

        # Login with correct credentials
        login = await client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "password123"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]

        # Login with wrong password fails with 401
        bad_pw = await client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "wrongpassword"},
        )
        assert bad_pw.status_code == 401

        # Check /api/auth/me with valid token
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["id"] == rider_data["user"]["id"]

        # Check /api/auth/me without token fails with 401
        me_unauth = await client.get("/api/auth/me")
        assert me_unauth.status_code == 401


@pytest.mark.anyio
async def test_full_ride_hailing_lifecycle(mock_db, mock_route, monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register rider and driver
        reg_rider = await client.post(
            "/api/auth/register",
            json={"name": "Rider Sarah", "email": "sarah@example.com", "password": "password123", "role": "rider"},
        )
        rider_token = reg_rider.json()["access_token"]

        reg_driver = await client.post(
            "/api/auth/register",
            json={"name": "Driver Dave", "email": "dave@example.com", "password": "password123", "role": "driver"},
        )
        driver_token = reg_driver.json()["access_token"]
        driver_id = reg_driver.json()["user"]["id"]

        # 2. Driver goes online
        driver_avail = await client.patch(
            "/api/drivers/availability",
            json={"available": True},
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        assert driver_avail.status_code == 200
        assert driver_avail.json()["available"] is True

        # 3. Rider checks active ride (none initially)
        active_none = await client.get("/api/rides/active", headers={"Authorization": f"Bearer {rider_token}"})
        assert active_none.status_code == 200
        assert active_none.json() is None

        # 4. Rider requests a ride
        pickup_loc = {"name": "Kings Cross", "address": "Euston Rd, London", "latitude": 51.53, "longitude": -0.12}
        dropoff_loc = {"name": "Piccadilly", "address": "Piccadilly, London", "latitude": 51.50, "longitude": -0.13}
        ride_req = await client.post(
            "/api/rides",
            json={"pickup": pickup_loc, "dropoff": dropoff_loc},
            headers={"Authorization": f"Bearer {rider_token}"},
        )
        assert ride_req.status_code == 200
        ride_data = ride_req.json()["ride"]
        ride_id = ride_data["id"]
        assert ride_data["status"] == "requested"
        assert ride_data["fare"] == 12.04

        # 5. Overlapping ride request fails with 409
        dup_req = await client.post(
            "/api/rides",
            json={"pickup": pickup_loc, "dropoff": dropoff_loc},
            headers={"Authorization": f"Bearer {rider_token}"},
        )
        assert dup_req.status_code == 409

        # 6. Driver sees open ride
        open_rides = await client.get("/api/rides/open", headers={"Authorization": f"Bearer {driver_token}"})
        assert open_rides.status_code == 200
        assert any(r["id"] == ride_id for r in open_rides.json())

        # 7. Driver accepts ride
        accepted = await client.post(
            f"/api/rides/{ride_id}/accept",
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"
        assert accepted.json()["driver_name"] == "Driver Dave"

        # 8. Rider cannot cancel accepted ride (409)
        cancel_attempt = await client.post(
            f"/api/rides/{ride_id}/cancel",
            headers={"Authorization": f"Bearer {rider_token}"},
        )
        assert cancel_attempt.status_code == 409

        # 9. Rider views active ride with driver details
        active_ride = await client.get("/api/rides/active", headers={"Authorization": f"Bearer {rider_token}"})
        assert active_ride.status_code == 200
        assert active_ride.json()["id"] == ride_id
        assert active_ride.json()["driver_name"] == "Driver Dave"

        # 10. Checkout session generation succeeds for accepted ride
        async def fake_create_checkout(ride, email):
            return "https://checkout.stripe.com/pay/test-session-123"

        monkeypatch.setattr("app.main.create_checkout", fake_create_checkout)
        checkout_res = await client.post(
            f"/api/rides/{ride_id}/checkout",
            headers={"Authorization": f"Bearer {rider_token}"},
        )
        assert checkout_res.status_code == 200
        assert checkout_res.json()["url"] == "https://checkout.stripe.com/pay/test-session-123"

        # 11. Driver advances status: accepted -> arriving -> in_progress -> completed
        arriving = await client.patch(
            f"/api/rides/{ride_id}/status",
            json={"status": "arriving"},
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        assert arriving.status_code == 200
        assert arriving.json()["status"] == "arriving"

        in_progress = await client.patch(
            f"/api/rides/{ride_id}/status",
            json={"status": "in_progress"},
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        assert in_progress.status_code == 200
        assert in_progress.json()["status"] == "in_progress"

        completed = await client.patch(
            f"/api/rides/{ride_id}/status",
            json={"status": "completed"},
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"

        # 12. Invalid status transition rejected (409)
        bad_transition = await client.patch(
            f"/api/rides/{ride_id}/status",
            json={"status": "arriving"},
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        assert bad_transition.status_code == 409

        # 13. History shows completed ride for rider and driver
        rider_history = await client.get("/api/rides/history", headers={"Authorization": f"Bearer {rider_token}"})
        assert rider_history.status_code == 200
        assert len(rider_history.json()) == 1

        driver_history = await client.get("/api/rides/history", headers={"Authorization": f"Bearer {driver_token}"})
        assert driver_history.status_code == 200
        assert len(driver_history.json()) == 1


@pytest.mark.anyio
async def test_rider_cancel_unassigned_ride(mock_db, mock_route):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg = await client.post(
            "/api/auth/register",
            json={"name": "Cancelling Rider", "email": "cancel@example.com", "password": "password123", "role": "rider"},
        )
        token = reg.json()["access_token"]

        pickup = {"name": "A", "address": "Address A", "latitude": 51.5, "longitude": -0.1}
        dropoff = {"name": "B", "address": "Address B", "latitude": 51.6, "longitude": -0.1}

        # Request ride
        ride = (await client.post("/api/rides", json={"pickup": pickup, "dropoff": dropoff}, headers={"Authorization": f"Bearer {token}"})).json()["ride"]

        # Cancel ride
        cancelled = await client.post(f"/api/rides/{ride['id']}/cancel", headers={"Authorization": f"Bearer {token}"})
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        # Rider can now request a new ride
        new_ride = await client.post("/api/rides", json={"pickup": pickup, "dropoff": dropoff}, headers={"Authorization": f"Bearer {token}"})
        assert new_ride.status_code == 200
