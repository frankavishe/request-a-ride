import asyncio
from datetime import timedelta
from threading import Lock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.main import (
    Place,
    RideRequest,
    StatusUpdate,
    request_ride,
    reserve_active_ride,
    update_ride_status,
    utc_now,
)


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = documents or []
        self.lock = Lock()

    async def find_one(self, query, projection=None):
        for document in self.documents:
            if matches(document, query):
                return dict(document)
        return None

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

    async def find_one_and_update(self, query, update, return_document=None):
        with self.lock:
            for document in self.documents:
                if matches(document, query):
                    for key, value in update.get("$set", {}).items():
                        document[key] = value
                    for key in update.get("$unset", {}):
                        document.pop(key, None)
                    return dict(document)
        return None

    async def insert_one(self, document):
        self.documents.append(document)


def matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(document, alternative) for alternative in expected):
                return False
        elif isinstance(expected, dict):
            if "$exists" in expected and (key in document) != expected["$exists"]:
                return False
            if "$in" in expected and document.get(key) not in expected["$in"]:
                return False
            if "$ne" in expected and document.get(key) == expected["$ne"]:
                return False
        elif document.get(key) != expected:
            return False
    return True


def test_active_ride_reservation_prevents_overlapping_requests():
    db = SimpleNamespace(
        users=FakeCollection([{"id": "rider-1"}]),
        rides=FakeCollection(),
    )

    async def scenario():
        return await asyncio.gather(
            reserve_active_ride(db, "rider-1", "ride-1"),
            reserve_active_ride(db, "rider-1", "ride-2"),
        )

    assert sorted(asyncio.run(scenario())) == [False, True]


def test_concurrent_ride_requests_create_at_most_one_active_ride(monkeypatch):
    db = SimpleNamespace(
        users=FakeCollection([{"id": "rider-1"}]),
        rides=FakeCollection(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
    payload = RideRequest(
        pickup=Place(name="Start", address="Start, UK", latitude=51.5, longitude=-0.1),
        dropoff=Place(name="End", address="End, UK", latitude=51.6, longitude=-0.1),
    )

    async def get_route(*_):
        await asyncio.sleep(0)
        return {"distance_km": 10, "duration_min": 20, "fare": 20}

    monkeypatch.setattr("app.main.get_route", get_route)

    async def scenario():
        return await asyncio.gather(
            request_ride(payload, request, {"id": "rider-1"}),
            request_ride(payload, request, {"id": "rider-1"}),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    assert len(db.rides.documents) == 1


@pytest.mark.parametrize(
    "ride",
    [
        {"status": "in_progress"},
        {"status": "completed", "payment_status": "pending"},
    ],
)
def test_reservation_is_not_reclaimed_while_ride_is_active(ride):
    db = SimpleNamespace(
        users=FakeCollection(
            [
                {
                    "id": "rider-1",
                    "active_ride_id": "ride-1",
                    "active_ride_reserved_at": utc_now() - timedelta(minutes=5),
                }
            ]
        ),
        rides=FakeCollection([{"id": "ride-1", **ride}]),
    )

    assert not asyncio.run(reserve_active_ride(db, "rider-1", "ride-2"))


def test_reservation_can_be_reclaimed_after_terminal_ride():
    db = SimpleNamespace(
        users=FakeCollection([{"id": "rider-1"}]),
        rides=FakeCollection(),
    )

    async def scenario():
        assert await reserve_active_ride(db, "rider-1", "ride-1")
        db.rides.documents.append(
            {"id": "ride-1", "status": "completed", "payment_status": "paid"}
        )
        return await reserve_active_ride(db, "rider-1", "ride-2")

    assert asyncio.run(scenario())


def test_reservation_recovers_after_abandoned_creation():
    db = SimpleNamespace(
        users=FakeCollection(
            [
                {
                    "id": "rider-1",
                    "active_ride_id": "abandoned-ride",
                    "active_ride_reserved_at": utc_now() - timedelta(seconds=61),
                }
            ]
        ),
        rides=FakeCollection(),
    )

    assert asyncio.run(reserve_active_ride(db, "rider-1", "ride-2"))


def test_driver_can_advance_ride_through_each_allowed_status():
    db = SimpleNamespace(
        rides=FakeCollection(
            [
                {
                    "id": "ride-1",
                    "rider_id": "rider-1",
                    "driver_id": "driver-1",
                    "pickup": {"name": "Start"},
                    "dropoff": {"name": "End"},
                    "fare": 20,
                    "distance_km": 10,
                    "duration_min": 20,
                    "status": "accepted",
                }
            ]
        ),
        users=FakeCollection(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))
    driver = {"id": "driver-1", "name": "Driver"}

    async def scenario():
        arriving = await update_ride_status(
            "ride-1", StatusUpdate(status="arriving"), request, driver
        )
        in_progress = await update_ride_status(
            "ride-1", StatusUpdate(status="in_progress"), request, driver
        )
        completed = await update_ride_status(
            "ride-1", StatusUpdate(status="completed"), request, driver
        )
        return arriving, in_progress, completed

    assert [ride["status"] for ride in asyncio.run(scenario())] == [
        "arriving",
        "in_progress",
        "completed",
    ]


@pytest.mark.parametrize(
    ("driver_id", "status"),
    [("driver-1", "in_progress"), ("driver-2", "arriving")],
)
def test_driver_cannot_skip_status_or_update_another_drivers_ride(driver_id, status):
    db = SimpleNamespace(
        rides=FakeCollection(
            [{"id": "ride-1", "rider_id": "rider-1", "driver_id": "driver-1", "status": "accepted"}]
        ),
        users=FakeCollection(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))

    with pytest.raises(HTTPException) as issue:
        asyncio.run(
            update_ride_status(
                "ride-1",
                StatusUpdate(status=status),
                request,
                {"id": driver_id, "name": "Driver"},
            )
        )

    assert issue.value.status_code == 409
    assert db.rides.documents[0]["status"] == "accepted"
