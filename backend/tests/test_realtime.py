from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import TIER_CONFIG, ConnectionHub, app, calculate_tier_fare, settings
from tests.test_api import MockCollection, MockMongoClient

PICKUP = {"name": "Piccadilly", "address": "Piccadilly Circus, London", "latitude": 51.51, "longitude": -0.13}
DROPOFF = {"name": "King's Cross", "address": "Euston Rd, London", "latitude": 51.53, "longitude": -0.12}


@pytest.fixture
def client(monkeypatch):
    db = SimpleNamespace(users=MockCollection(), rides=MockCollection())
    app.state.mongo = MockMongoClient(db)
    app.state.db = db
    app.state.hub = ConnectionHub()
    settings.jwt_secret = "test-secret-at-least-32-characters-long-12345"

    async def fake_get_route(pickup, dropoff):
        return {
            "distance_km": 10.0,
            "duration_min": 20,
            "fare": 19.6,
            "tiers": {key: calculate_tier_fare(key, 10.0, 20) for key in TIER_CONFIG},
        }

    monkeypatch.setattr("app.main.get_route", fake_get_route)
    # No context manager: the lifespan (real MongoDB) must not run.
    return TestClient(app)


def register(client: TestClient, email: str, role: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"name": email.split("@")[0], "email": email, "password": "password123", "role": role},
    )
    assert response.status_code == 200
    body = response.json()
    return {"token": body["access_token"], "id": body["user"]["id"], "headers": {"Authorization": f"Bearer {body['access_token']}"}}


def connect(client: TestClient, token: str):
    socket = client.websocket_connect("/api/ws")
    ws = socket.__enter__()
    ws.send_json({"type": "auth", "token": token})
    assert ws.receive_json()["type"] == "ready"
    return socket, ws


def assert_no_pending(ws) -> None:
    """A ping answered by an immediate pong proves no other event was queued first."""
    ws.send_json({"type": "ping"})
    assert ws.receive_json() == {"type": "pong"}


def test_socket_rejects_missing_or_invalid_token(client):
    for message in ({"type": "auth", "token": "not-a-jwt"}, {"type": "auth"}, {"type": "hello"}):
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json(message)
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401


def test_ride_lifecycle_is_pushed_to_rider_and_online_drivers(client):
    rider = register(client, "rider@example.com", "rider")
    online = register(client, "online@example.com", "driver")
    offline = register(client, "offline@example.com", "driver")
    assert client.patch("/api/drivers/availability", json={"available": True}, headers=online["headers"]).status_code == 200

    rider_sock, rider_ws = connect(client, rider["token"])
    online_sock, online_ws = connect(client, online["token"])
    offline_sock, offline_ws = connect(client, offline["token"])
    try:
        created = client.post("/api/rides", json={"pickup": PICKUP, "dropoff": DROPOFF}, headers=rider["headers"])
        assert created.status_code == 200
        ride_id = created.json()["ride"]["id"]

        event = online_ws.receive_json()
        assert event["type"] == "ride.updated"
        assert event["ride"]["id"] == ride_id
        assert event["ride"]["status"] == "requested"
        assert rider_ws.receive_json()["ride"]["status"] == "requested"
        assert_no_pending(offline_ws)

        accepted = client.post(f"/api/rides/{ride_id}/accept", headers=online["headers"])
        assert accepted.status_code == 200
        rider_event = rider_ws.receive_json()
        assert rider_event["ride"]["status"] == "accepted"
        assert rider_event["ride"]["driver_name"] == "online"
        assert online_ws.receive_json()["ride"]["status"] == "accepted"

        assert client.patch(
            f"/api/rides/{ride_id}/status", json={"status": "arriving"}, headers=online["headers"]
        ).status_code == 200
        assert rider_ws.receive_json()["ride"]["status"] == "arriving"
        assert online_ws.receive_json()["ride"]["status"] == "arriving"
        assert_no_pending(offline_ws)
    finally:
        for socket in (rider_sock, online_sock, offline_sock):
            socket.__exit__(None, None, None)


def test_driver_location_is_forwarded_to_rider_and_saved(client):
    rider = register(client, "loc_rider@example.com", "rider")
    driver = register(client, "loc_driver@example.com", "driver")
    other = register(client, "loc_other@example.com", "driver")
    client.patch("/api/drivers/availability", json={"available": True}, headers=driver["headers"])
    ride_id = client.post(
        "/api/rides", json={"pickup": PICKUP, "dropoff": DROPOFF}, headers=rider["headers"]
    ).json()["ride"]["id"]
    assert client.post(f"/api/rides/{ride_id}/accept", headers=driver["headers"]).status_code == 200

    rider_sock, rider_ws = connect(client, rider["token"])
    driver_sock, driver_ws = connect(client, driver["token"])
    other_sock, other_ws = connect(client, other["token"])
    try:
        driver_ws.send_json({"type": "location", "ride_id": ride_id, "latitude": 51.515, "longitude": -0.125})
        event = rider_ws.receive_json()
        assert event["type"] == "driver.location"
        assert event["ride_id"] == ride_id
        assert (event["latitude"], event["longitude"]) == (51.515, -0.125)

        # Updates closer than the throttle interval are dropped silently.
        driver_ws.send_json({"type": "location", "ride_id": ride_id, "latitude": 51.6, "longitude": -0.1})
        assert_no_pending(driver_ws)
        assert_no_pending(rider_ws)

        other_ws.send_json({"type": "location", "ride_id": ride_id, "latitude": 51.0, "longitude": -0.1})
        assert other_ws.receive_json()["type"] == "error"

        rider_ws.send_json({"type": "location", "ride_id": ride_id, "latitude": 51.0, "longitude": -0.1})
        assert rider_ws.receive_json() == {"type": "error", "detail": "Only drivers can share location."}

        other_ws.send_json({"type": "location", "ride_id": ride_id, "latitude": "north", "longitude": 0})
        assert other_ws.receive_json()["type"] == "error"
        assert_no_pending(rider_ws)
    finally:
        for socket in (rider_sock, driver_sock, other_sock):
            socket.__exit__(None, None, None)

    active = client.get("/api/rides/active", headers=rider["headers"]).json()
    assert active["driver_location"]["latitude"] == 51.515
    assert active["driver_location"]["longitude"] == -0.125


def test_broken_socket_does_not_fail_http_request(client):
    rider = register(client, "broken@example.com", "rider")

    class BrokenSocket:
        async def send_json(self, data):
            raise RuntimeError("socket gone")

    broken = BrokenSocket()
    hub: ConnectionHub = app.state.hub
    hub.connect({"id": rider["id"], "role": "rider"}, broken)

    response = client.post("/api/rides", json={"pickup": PICKUP, "dropoff": DROPOFF}, headers=rider["headers"])
    assert response.status_code == 200
    assert rider["id"] not in hub.sockets
