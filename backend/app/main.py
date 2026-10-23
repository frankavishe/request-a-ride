from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo import AsyncMongoClient, ReturnDocument


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "bolt"
    jwt_secret: str = ""
    mapbox_access_token: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    app_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"


settings = Settings()
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "available": user.get("available", False),
    }


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"{base64.urlsafe_b64encode(salt).decode()}.{base64.urlsafe_b64encode(digest).decode()}"


def password_matches(password: str, stored: str) -> bool:
    salt_text, digest_text = stored.split(".", maxsplit=1)
    salt = base64.urlsafe_b64decode(salt_text)
    expected = base64.urlsafe_b64decode(digest_text)
    actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(actual, expected)


def issue_token(user: dict) -> str:
    now = utc_now()
    return jwt.encode(
        {"sub": user["id"], "iat": now, "exp": now + timedelta(days=7)},
        settings.jwt_secret,
        algorithm="HS256",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("Set JWT_SECRET to a randomly generated value of at least 32 characters.")
    client = AsyncMongoClient(settings.mongodb_url, serverSelectionTimeoutMS=3000, tz_aware=True)
    app.state.mongo = client
    app.state.db = client[settings.mongodb_database]
    try:
        await client.admin.command("ping")
        await app.state.db.users.create_index("email", unique=True)
        await app.state.db.rides.create_index([("status", 1), ("created_at", -1)])
    except Exception:
        await client.close()
        raise
    yield
    await client.close()


app = FastAPI(title="Waygo Ride API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=2, max_length=80)
    role: Literal["rider", "driver"] = "rider"


class Place(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=1, max_length=300)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class RideRequest(BaseModel):
    pickup: Place
    dropoff: Place


class AvailabilityUpdate(BaseModel):
    available: bool


class StatusUpdate(BaseModel):
    status: Literal["arriving", "in_progress", "completed"]


class RouteEstimate(BaseModel):
    pickup: Place
    dropoff: Place


async def current_user(
    request: Request, authorization: str | None = Header(default=None)
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Your session has expired. Sign in again.") from exc
    user = await request.app.state.db.users.find_one({"id": payload.get("sub")})
    if not user:
        raise HTTPException(status_code=401, detail="Account not found.")
    return user


def require_role(*roles: str):
    async def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Your account cannot perform this action.")
        return user

    return dependency


async def get_route(pickup: Place, dropoff: Place) -> dict:
    if not settings.mapbox_access_token:
        raise HTTPException(status_code=503, detail="Location and routing are unavailable. Configure MAPBOX_ACCESS_TOKEN.")
    for place in (pickup, dropoff):
        if not 49 <= place.latitude <= 61.5 or not -9 <= place.longitude <= 2.5:
            raise HTTPException(status_code=422, detail="This MVP currently supports rides within the United Kingdom.")
    url = (
        "https://api.mapbox.com/directions/v5/mapbox/driving/"
        f"{pickup.longitude},{pickup.latitude};{dropoff.longitude},{dropoff.latitude}"
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(
                url,
                params={"access_token": settings.mapbox_access_token, "overview": "false"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="The map provider could not be reached.") from exc
    if response.is_error:
        raise HTTPException(status_code=502, detail="The map provider could not calculate that route.")
    routes = response.json().get("routes", [])
    if not routes:
        raise HTTPException(status_code=422, detail="No driving route was found between those locations.")
    route = routes[0]
    distance_km = round(route["distance"] / 1000, 1)
    duration_min = max(1, round(route["duration"] / 60))
    fare = round(2.5 + distance_km * 1.35 + duration_min * 0.18, 2)
    return {"distance_km": distance_km, "duration_min": duration_min, "fare": fare}


def ride_response(ride: dict, driver: dict | None = None) -> dict:
    result = {
        "id": ride["id"],
        "pickup": ride["pickup"],
        "dropoff": ride["dropoff"],
        "fare": ride["fare"],
        "distance_km": ride["distance_km"],
        "duration_min": ride["duration_min"],
        "status": ride["status"],
        "payment_status": ride.get("payment_status", "pending"),
        "created_at": ride["created_at"].isoformat() if ride.get("created_at") else None,
    }
    if driver:
        result["driver_name"] = driver["name"]
    return result


async def create_checkout(ride: dict, email: str) -> str:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Payments are unavailable. Configure STRIPE_SECRET_KEY.")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.stripe.com/v1/checkout/sessions",
                auth=(settings.stripe_secret_key, ""),
                headers={"Idempotency-Key": f"ride-checkout-{ride['id']}"},
                data={
                    "mode": "payment",
                    "customer_email": email,
                    "success_url": f"{settings.app_url}/?payment=success&ride_id={ride['id']}",
                    "cancel_url": f"{settings.app_url}/?payment=cancelled&ride_id={ride['id']}",
                    "line_items[0][price_data][currency]": "gbp",
                    "line_items[0][price_data][unit_amount]": str(round(ride["fare"] * 100)),
                    "line_items[0][price_data][product_data][name]": "Waygo ride",
                    "line_items[0][quantity]": "1",
                    "metadata[ride_id]": ride["id"],
                    "payment_intent_data[metadata][ride_id]": ride["id"],
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="The payment provider could not be reached.") from exc
    if response.is_error:
        raise HTTPException(status_code=502, detail="The payment provider could not start checkout.")
    return response.json()["url"]


@app.get("/api/health")
async def health(request: Request):
    await request.app.state.mongo.admin.command("ping")
    return {"status": "ok", "database": "connected"}


@app.post("/api/auth/register")
async def register(payload: Registration, request: Request):
    db = request.app.state.db
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Enter a name with at least two characters.")
    if await db.users.find_one({"email": payload.email.lower()}):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = {
        "id": str(uuid4()),
        "name": name,
        "email": payload.email.lower(),
        "role": payload.role,
        "password_hash": await run_in_threadpool(password_hash, payload.password),
        "available": False,
        "created_at": utc_now(),
    }
    try:
        await db.users.insert_one(user)
    except Exception as exc:
        if getattr(exc, "code", None) == 11000:
            raise HTTPException(status_code=409, detail="An account with this email already exists.") from exc
        raise
    return {"access_token": issue_token(user), "token_type": "bearer", "user": public_user(user)}


@app.post("/api/auth/login")
async def login(payload: Credentials, request: Request):
    user = await request.app.state.db.users.find_one({"email": payload.email.lower()})
    if not user or not await run_in_threadpool(password_matches, payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    return {"access_token": issue_token(user), "token_type": "bearer", "user": public_user(user)}


@app.get("/api/auth/me")
async def me(user: dict = Depends(current_user)):
    return public_user(user)


@app.get("/api/maps/geocode")
async def geocode(q: str, _: dict = Depends(current_user)):
    if not settings.mapbox_access_token:
        raise HTTPException(status_code=503, detail="Location search is unavailable. Configure MAPBOX_ACCESS_TOKEN.")
    if len(q.strip()) > 120:
        raise HTTPException(status_code=422, detail="Search for a shorter location name.")
    if len(q.strip()) < 3:
        return {"places": []}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(
                f"https://api.mapbox.com/geocoding/v5/mapbox.places/{quote(q, safe='')}.json",
                params={"access_token": settings.mapbox_access_token, "limit": 5, "country": "gb"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="The map provider could not be reached.") from exc
    if response.is_error:
        raise HTTPException(status_code=502, detail="The map provider could not search for that location.")
    features = response.json().get("features", [])
    return {
        "places": [
            {
                "name": feature.get("text", feature["place_name"]),
                "address": feature["place_name"],
                "longitude": feature["center"][0],
                "latitude": feature["center"][1],
            }
            for feature in features
        ]
    }


@app.get("/api/maps/reverse")
async def reverse_geocode(longitude: float, latitude: float, _: dict = Depends(current_user)):
    if not settings.mapbox_access_token:
        raise HTTPException(status_code=503, detail="Location search is unavailable. Configure MAPBOX_ACCESS_TOKEN.")
    if not 49 <= latitude <= 61.5 or not -9 <= longitude <= 2.5:
        raise HTTPException(status_code=422, detail="This MVP currently supports locations within the United Kingdom.")
    async with httpx.AsyncClient(timeout=12) as client:
        try:
            response = await client.get(
                f"https://api.mapbox.com/geocoding/v5/mapbox.places/{longitude},{latitude}.json",
                params={"access_token": settings.mapbox_access_token, "limit": 1, "country": "gb"},
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="The map provider could not be reached.") from exc
    if response.is_error:
        raise HTTPException(status_code=502, detail="The map provider could not look up your location.")
    features = response.json().get("features", [])
    if not features:
        raise HTTPException(status_code=404, detail="No address was found near your location.")
    feature = features[0]
    return {
        "name": feature.get("text", feature["place_name"]),
        "address": feature["place_name"],
        "longitude": feature["center"][0],
        "latitude": feature["center"][1],
    }


@app.post("/api/maps/route")
async def route(payload: RouteEstimate, _: dict = Depends(current_user)):
    return await get_route(payload.pickup, payload.dropoff)


@app.post("/api/rides")
async def request_ride(
    payload: RideRequest,
    request: Request,
    user: dict = Depends(require_role("rider")),
):
    active = await request.app.state.db.rides.find_one({
        "rider_id": user["id"],
        "$or": [
            {"status": {"$in": ["requested", "accepted", "arriving", "in_progress"]}},
            {"status": "completed", "payment_status": {"$ne": "paid"}},
        ],
    })
    if active:
        raise HTTPException(status_code=409, detail="Finish your current ride before requesting another.")
    estimate = await get_route(payload.pickup, payload.dropoff)
    ride = {
        "id": str(uuid4()),
        "rider_id": user["id"],
        "driver_id": None,
        "pickup": payload.pickup.model_dump(),
        "dropoff": payload.dropoff.model_dump(),
        **estimate,
        "status": "requested",
        "payment_status": "pending",
        "checkout_url": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    await request.app.state.db.rides.insert_one(ride)
    return {"ride": ride_response(ride), "checkout_url": None}


@app.get("/api/rides/open")
async def list_driver_rides(request: Request, user: dict = Depends(require_role("driver"))):
    filters = [{"driver_id": user["id"], "status": {"$in": ["accepted", "arriving", "in_progress"]}}]
    if user.get("available"):
        filters.append({"status": "requested"})
    cursor = request.app.state.db.rides.find({"$or": filters}).sort("created_at", -1).limit(30)
    rides = []
    async for ride in cursor:
        rides.append(ride_response(ride))
    return rides


@app.patch("/api/drivers/availability")
async def update_availability(
    payload: AvailabilityUpdate,
    request: Request,
    user: dict = Depends(require_role("driver")),
):
    await request.app.state.db.users.update_one({"id": user["id"]}, {"$set": {"available": payload.available}})
    user["available"] = payload.available
    return public_user(user)


@app.post("/api/rides/{ride_id}/accept")
async def accept_ride(ride_id: str, request: Request, user: dict = Depends(require_role("driver"))):
    if not user.get("available"):
        raise HTTPException(status_code=409, detail="Go online before accepting a trip.")
    db = request.app.state.db
    ride = await db.rides.find_one_and_update(
        {"id": ride_id, "status": "requested", "driver_id": None},
        {"$set": {"status": "accepted", "driver_id": user["id"], "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not ride:
        raise HTTPException(status_code=409, detail="This trip is no longer available.")
    return ride_response(ride, user)


@app.post("/api/rides/{ride_id}/cancel")
async def cancel_ride(ride_id: str, request: Request, user: dict = Depends(require_role("rider"))):
    ride = await request.app.state.db.rides.find_one_and_update(
        {"id": ride_id, "rider_id": user["id"], "status": "requested"},
        {"$set": {"status": "cancelled", "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not ride:
        raise HTTPException(status_code=409, detail="Only unassigned ride requests can be cancelled.")
    return ride_response(ride)


@app.patch("/api/rides/{ride_id}/status")
async def update_ride_status(
    ride_id: str,
    payload: StatusUpdate,
    request: Request,
    user: dict = Depends(require_role("driver")),
):
    db = request.app.state.db
    transitions = {
        "accepted": "arriving",
        "arriving": "in_progress",
        "in_progress": "completed",
    }
    ride = await db.rides.find_one({"id": ride_id, "driver_id": user["id"]})
    if not ride or transitions.get(ride["status"]) != payload.status:
        raise HTTPException(status_code=409, detail="That ride status transition is not allowed.")
    updated = await db.rides.find_one_and_update(
        {"id": ride_id, "driver_id": user["id"], "status": ride["status"]},
        {"$set": {"status": payload.status, "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="The ride status has already changed.")
    return ride_response(updated, user)


@app.get("/api/rides/active")
async def active_ride(request: Request, user: dict = Depends(require_role("rider"))):
    ride = await request.app.state.db.rides.find_one(
        {
            "rider_id": user["id"],
            "$or": [
                {"status": {"$in": ["requested", "accepted", "arriving", "in_progress"]}},
                {"status": "completed", "payment_status": {"$ne": "paid"}},
            ],
        },
        sort=[("created_at", -1)],
    )
    if not ride:
        return None
    driver = await request.app.state.db.users.find_one({"id": ride["driver_id"]}) if ride.get("driver_id") else None
    return ride_response(ride, driver)


@app.get("/api/rides/history")
async def ride_history(request: Request, user: dict = Depends(current_user)):
    ownership = "rider_id" if user["role"] == "rider" else "driver_id"
    cursor = request.app.state.db.rides.find({ownership: user["id"]}).sort("created_at", -1).limit(50)
    rides = [ride async for ride in cursor]
    driver_ids = list({ride["driver_id"] for ride in rides if ride.get("driver_id")})
    drivers = {}
    if driver_ids:
        cursor = request.app.state.db.users.find({"id": {"$in": driver_ids}}, {"id": 1, "name": 1})
        drivers = {driver["id"]: driver async for driver in cursor}
    return [ride_response(ride, drivers.get(ride.get("driver_id"))) for ride in rides]


@app.get("/api/rides/{ride_id}")
async def get_ride(ride_id: str, request: Request, user: dict = Depends(current_user)):
    ride = await request.app.state.db.rides.find_one({
        "id": ride_id,
        "$or": [{"rider_id": user["id"]}, {"driver_id": user["id"]}],
    })
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found.")
    driver = await request.app.state.db.users.find_one({"id": ride["driver_id"]}) if ride.get("driver_id") else None
    return ride_response(ride, driver)


@app.post("/api/rides/{ride_id}/checkout")
async def checkout(ride_id: str, request: Request, user: dict = Depends(require_role("rider"))):
    db = request.app.state.db
    ride = await db.rides.find_one({"id": ride_id, "rider_id": user["id"]})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found.")
    if ride["status"] not in {"accepted", "arriving", "in_progress", "completed"}:
        raise HTTPException(status_code=409, detail="Payment is available after a driver accepts the trip.")
    if ride["payment_status"] == "paid":
        raise HTTPException(status_code=409, detail="This ride has already been paid.")
    if ride.get("checkout_url"):
        return {"url": ride["checkout_url"]}
    url = await create_checkout(ride, user["email"])
    await db.rides.update_one({"id": ride_id}, {"$set": {"checkout_url": url}})
    return {"url": url}


@app.post("/api/payments/webhook")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None)):
    if not settings.stripe_webhook_secret or not stripe_signature:
        raise HTTPException(status_code=400, detail="Payment webhook signature is missing.")
    raw_body = await request.body()
    signature_parts = [item.split("=", 1) for item in stripe_signature.split(",") if "=" in item]
    timestamp = next((value for key, value in signature_parts if key == "t"), None)
    signatures = [value for key, value in signature_parts if key == "v1"]
    try:
        issued_at = int(timestamp) if timestamp else 0
    except ValueError:
        issued_at = 0
    if not issued_at or abs(time.time() - issued_at) > 300:
        raise HTTPException(status_code=400, detail="Payment webhook signature is invalid or expired.")
    signed_payload = str(issued_at).encode() + b"." + raw_body
    expected = hmac.new(
        settings.stripe_webhook_secret.encode(), signed_payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(signature, expected) for signature in signatures):
        raise HTTPException(status_code=400, detail="Payment webhook signature is invalid.")
    try:
        event = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Payment webhook payload is invalid.") from exc
    if event.get("type") in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        session = event.get("data", {}).get("object", {})
        ride_id = session.get("metadata", {}).get("ride_id")
        if ride_id and session.get("payment_status") == "paid":
            await request.app.state.db.rides.update_one(
                {"id": ride_id},
                {"$set": {"payment_status": "paid", "updated_at": utc_now()}},
            )
    return {"received": True}
