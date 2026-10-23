# Waygo Ride App

A Bolt-inspired ride-hailing MVP with original Waygo branding, rider and driver accounts, live route and fare estimates, driver trip operations, and Stripe Checkout. The frontend uses React, TypeScript, and Vite; the API uses FastAPI and MongoDB.

## What works

- Register and sign in as a rider or driver.
- Search for UK pickup/drop-off locations, view Mapbox driving estimates, and create a persisted ride request.
- Drivers can go online, accept requests, and advance trips from accepted to arriving, in progress, and completed.
- Riders see trip status updates and can pay via hosted Stripe Checkout once a driver accepts.
- Passwords are stored as salted scrypt hashes; API sessions use expiring signed JWTs. Payment keys stay on the API server.

## Requirements

- Node.js 20.19+ and npm
- Python 3.11+
- Docker Desktop (or a MongoDB instance)
- Mapbox public and server-side access tokens
- Stripe test-mode secret and webhook secret for payment testing

## Configure

Start MongoDB from the project root:

```powershell
docker compose up -d mongodb
```

Copy the backend environment example to a local `.env` and set secrets. In PowerShell:

```powershell
Copy-Item backend\.env.example backend\.env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `JWT_SECRET` to the generated value in `backend\.env`, and provide:

| Variable | Purpose |
| --- | --- |
| `MONGODB_URL` | MongoDB connection string (default `mongodb://localhost:27017`) |
| `MONGODB_DATABASE` | Database name (default `bolt`) |
| `JWT_SECRET` | Random secret of at least 32 characters |
| `MAPBOX_ACCESS_TOKEN` | Server-side Mapbox token for geocoding and driving directions |
| `STRIPE_SECRET_KEY` | Stripe test/live secret key used to create Checkout sessions |
| `STRIPE_WEBHOOK_SECRET` | Signing secret for Stripe's `checkout.session.completed` webhook |
| `APP_URL` | Frontend URL used in Checkout success/cancel links |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins |

Copy the root Vite environment template:

```powershell
Copy-Item .env.example .env.local
```

Then edit `.env.local`:

```dotenv
VITE_API_URL=http://localhost:8000
VITE_MAPBOX_TOKEN=your_mapbox_public_token
```

`VITE_MAPBOX_TOKEN` is embedded in the browser for Mapbox static maps. Restrict it to your app's domain and required scopes; never put Stripe secrets or server-side Mapbox credentials in a `VITE_` variable.

The initial fare calculation and Stripe Checkout currency target Great Britain (GBP): £2.50 base + £1.35 per kilometre + £0.18 per minute. Adjust the fare formula and Stripe currency together before launching in another region.

## Run locally

In one PowerShell terminal, install/start the API:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, install/start the web client:

```powershell
npm install
npm run dev
```

The web app runs at `http://localhost:5173`; the API health check is `http://localhost:8000/api/health` and interactive API docs are at `http://localhost:8000/docs`.

For Stripe test webhooks, point your Stripe CLI at `http://localhost:8000/api/payments/webhook` and set its signing secret as `STRIPE_WEBHOOK_SECRET`. A ride is marked paid only after Stripe's signed completion event is received.

## Verify

```powershell
# Project root
npm run build
npm run lint

# backend directory with its virtual environment activated
python -m pytest
```

## Scope notes

Map search is restricted to the UK for this MVP. Payments are hosted by Stripe Checkout; rider payment details never pass through this API. Dispatch is request/accept based, without continuous driver GPS tracking, push notifications, or mobile apps. Before production, add privacy retention controls, rate limiting, monitoring, payment/refund reconciliation, and deployed-domain token restrictions.
