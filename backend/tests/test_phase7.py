from __future__ import annotations

from types import SimpleNamespace
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, settings, TIER_CONFIG, calculate_tier_fare
from tests.test_api import MockCollection, MockMongoClient


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
        distance_km = 10.0
        duration_min = 20
        return {
            "distance_km": distance_km,
            "duration_min": duration_min,
            "fare": 19.6,
            "tiers": {key: calculate_tier_fare(key, distance_km, duration_min) for key in TIER_CONFIG},
        }

    monkeypatch.setattr("app.main.get_route", fake_get_route)


@pytest.mark.anyio
async def test_multi_tier_route_and_booking(mock_db, mock_route):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register rider
        reg = await client.post(
            "/api/auth/register",
            json={"name": "Tier Rider", "email": "tier_rider@example.com", "password": "password123", "role": "rider"},
        )
        token = reg.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        pickup = {"name": "Piccadilly", "address": "Piccadilly Circus, London", "latitude": 51.51, "longitude": -0.13}
        dropoff = {"name": "King's Cross", "address": "Euston Rd, London", "latitude": 51.53, "longitude": -0.12}

        # Check route tiers
        route_res = await client.post("/api/maps/route", json={"pickup": pickup, "dropoff": dropoff}, headers=headers)
        assert route_res.status_code == 200
        data = route_res.json()
        assert "tiers" in data
        assert "standard" in data["tiers"]
        assert "comfort" in data["tiers"]
        assert "xl" in data["tiers"]
        assert "green" in data["tiers"]

        # Request Comfort ride
        comfort_ride_res = await client.post(
            "/api/rides",
            json={"pickup": pickup, "dropoff": dropoff, "tier": "comfort"},
            headers=headers,
        )
        assert comfort_ride_res.status_code == 200
        ride = comfort_ride_res.json()["ride"]
        assert ride["tier"] == "comfort"
        assert ride["tier_name"] == "Waygo Comfort"
        # comfort base £3.50 + 10km * £1.80 + 20min * £0.25 = 3.50 + 18.00 + 5.00 = 26.50
        assert ride["fare"] == 26.50
        assert "itemized_fare" in ride
        assert ride["itemized_fare"]["base_fare"] == 3.50


@pytest.mark.anyio
async def test_driver_vehicle_and_profile_updates(mock_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register driver with vehicle
        vehicle_data = {
            "make": "Toyota",
            "model": "Prius",
            "color": "Silver",
            "license_plate": "AB21 XYZ",
        }
        reg = await client.post(
            "/api/auth/register",
            json={
                "name": "Jane Driver",
                "email": "jane@example.com",
                "password": "password123",
                "role": "driver",
                "vehicle": vehicle_data,
            },
        )
        assert reg.status_code == 200
        driver_user = reg.json()["user"]
        assert driver_user["vehicle"] == vehicle_data
        assert driver_user["rating_avg"] == 5.0
        assert driver_user["rating_count"] == 0
        token = reg.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Update vehicle
        new_vehicle = {
            "make": "Tesla",
            "model": "Model 3",
            "color": "Midnight Blue",
            "license_plate": "EV24 GOO",
        }
        upd = await client.patch(
            "/api/drivers/vehicle",
            json={"vehicle": new_vehicle},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["vehicle"] == new_vehicle


@pytest.mark.anyio
async def test_driver_accept_and_decline_flow(mock_db, mock_route):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register rider & driver
        rider_res = await client.post(
            "/api/auth/register",
            json={"name": "Alice Rider", "email": "alice@example.com", "password": "password123", "role": "rider"},
        )
        rider_token = rider_res.json()["access_token"]

        driver_res = await client.post(
            "/api/auth/register",
            json={
                "name": "Bob Driver",
                "email": "bob@example.com",
                "password": "password123",
                "role": "driver",
                "vehicle": {"make": "Skoda", "model": "Octavia", "color": "Black", "license_plate": "SK68 DEF"},
            },
        )
        driver_token = driver_res.json()["access_token"]

        # Driver goes online
        await client.patch(
            "/api/drivers/availability",
            json={"available": True},
            headers={"Authorization": f"Bearer {driver_token}"},
        )

        # Rider books XL ride
        pickup = {"name": "A", "address": "London A", "latitude": 51.5, "longitude": -0.1}
        dropoff = {"name": "B", "address": "London B", "latitude": 51.6, "longitude": -0.1}
        ride = (await client.post(
            "/api/rides",
            json={"pickup": pickup, "dropoff": dropoff, "tier": "xl"},
            headers={"Authorization": f"Bearer {rider_token}"},
        )).json()["ride"]
        assert ride["status"] == "requested"

        # Driver accepts
        accepted = await client.post(
            f"/api/rides/{ride['id']}/accept",
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        assert accepted.status_code == 200
        ride_data = accepted.json()
        assert ride_data["status"] == "accepted"
        assert ride_data["driver_name"] == "Bob Driver"
        assert ride_data["vehicle"]["make"] == "Skoda"

        # Driver declines/redispatches
        decline = await client.post(
            f"/api/rides/{ride['id']}/decline",
            json={"reason": "Flat tire on way"},
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        assert decline.status_code == 200
        assert decline.json()["status"] == "requested"

        # Ride is back in open pool
        open_rides = await client.get("/api/rides/open", headers={"Authorization": f"Bearer {driver_token}"})
        assert any(r["id"] == ride["id"] for r in open_rides.json())


@pytest.mark.anyio
async def test_trip_rating_and_driver_score_update(mock_db, mock_route):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register rider and driver
        rider_res = await client.post(
            "/api/auth/register",
            json={"name": "Rating Rider", "email": "rr@example.com", "password": "password123", "role": "rider"},
        )
        rider_token = rider_res.json()["access_token"]

        driver_res = await client.post(
            "/api/auth/register",
            json={"name": "Dan Driver", "email": "dan@example.com", "password": "password123", "role": "driver"},
        )
        driver_token = driver_res.json()["access_token"]
        driver_id = driver_res.json()["user"]["id"]

        # Driver goes online
        await client.patch(
            "/api/drivers/availability",
            json={"available": True},
            headers={"Authorization": f"Bearer {driver_token}"},
        )

        pickup = {"name": "A", "address": "Address A", "latitude": 51.5, "longitude": -0.1}
        dropoff = {"name": "B", "address": "Address B", "latitude": 51.6, "longitude": -0.1}
        ride = (await client.post(
            "/api/rides",
            json={"pickup": pickup, "dropoff": dropoff},
            headers={"Authorization": f"Bearer {rider_token}"},
        )).json()["ride"]

        await client.post(f"/api/rides/{ride['id']}/accept", headers={"Authorization": f"Bearer {driver_token}"})
        await client.patch(f"/api/rides/{ride['id']}/status", json={"status": "arriving"}, headers={"Authorization": f"Bearer {driver_token}"})
        await client.patch(f"/api/rides/{ride['id']}/status", json={"status": "in_progress"}, headers={"Authorization": f"Bearer {driver_token}"})
        await client.patch(f"/api/rides/{ride['id']}/status", json={"status": "completed"}, headers={"Authorization": f"Bearer {driver_token}"})

        # Cannot rate unpaid ride
        unpaid_rate = await client.post(
            f"/api/rides/{ride['id']}/rate",
            json={"rating": 5, "feedback": "Great ride!"},
            headers={"Authorization": f"Bearer {rider_token}"},
        )
        assert unpaid_rate.status_code == 409

        # Simulate payment complete
        await app.state.db.rides.update_one({"id": ride["id"]}, {"$set": {"payment_status": "paid"}})

        # Rate ride 5 stars
        rate_res = await client.post(
            f"/api/rides/{ride['id']}/rate",
            json={"rating": 5, "feedback": "Smooth driving and clean car"},
            headers={"Authorization": f"Bearer {rider_token}"},
        )
        assert rate_res.status_code == 200
        rated_ride = rate_res.json()
        assert rated_ride["rating"] == 5
        assert rated_ride["feedback"] == "Smooth driving and clean car"

        # Cannot rate twice
        dup_rate = await client.post(
            f"/api/rides/{ride['id']}/rate",
            json={"rating": 4},
            headers={"Authorization": f"Bearer {rider_token}"},
        )
        assert dup_rate.status_code == 409

        # Driver profile shows updated rating
        driver_doc = await app.state.db.users.find_one({"id": driver_id})
        assert driver_doc["rating_avg"] == 5.0
        assert driver_doc["rating_count"] == 1
