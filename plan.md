# Bolt-Style Ride-Hailing MVP Plan

## Problem and proposed approach

The workspace is empty. Build a Bolt-inspired transportation-request application with a React + TypeScript frontend, a FastAPI backend, and MongoDB persistence. Deliver rider and driver workflows, email/password authentication, real map/geocoding and route services using a configurable keyed provider, and payment integration using a configurable provider. Keep provider selection and credentials configurable rather than committing secrets.

## Todos

1. **Phase 1 — Foundation:** React + TypeScript/Vite, FastAPI, MongoDB, and local configuration are scaffolded and verified.
2. **Phase 2 — Accounts:** Implement registration, login, and rider/driver roles.
3. **Phase 3 — Rider requests:** Build map-based pickup/dropoff selection, route/fare estimates, and ride requests.
4. **Phase 4 — Driver operations:** Implement driver availability, request acceptance, and ride status updates.
5. **Phase 5 — Payments:** Integrate payment authorization/collection and ride payment state.
6. **Phase 6 — Verification and handoff:** Test configuration established (`pytest.ini`), comprehensive API integration tests added (`backend/tests/test_api.py`), frontend build and lint verified clean, and all 30 backend test cases passing.
7. **Phase 7 — Ride experience:** Ride tiers with itemized fares, driver vehicle profiles, driver decline, rider ratings with driver scores, and trip receipts.
8. **Phase 8 — Real-time updates:** A WebSocket channel pushes ride changes to riders and online drivers and streams the assigned driver's GPS position to the rider, with polling kept as a slower fallback.

Implement and verify each phase before starting the next, preserving a runnable application at every milestone. Reassess provider/configuration assumptions as each integration phase begins.

## Progress

- The plan was saved here before implementation began.
- Phase 1: scaffolded the React + TypeScript/Vite client, FastAPI service, MongoDB connection, Docker Compose database, example environments, and project setup documentation.
- Phase 2: implemented role-based rider/driver registration and login, salted scrypt password hashes, expiring JWT sessions, and API role checks.
- Phase 3: implemented Mapbox-backed UK location search, current-location lookup, driving/fare estimates, ride creation, active-ride refresh, cancellation before driver assignment, and history.
- Phase 4: implemented driver availability, atomic ride acceptance, assigned-trip refresh, and guarded trip status transitions.
- Phase 5: implemented hosted Stripe Checkout after driver acceptance and signature-verified payment webhooks.
- Phase 6: added focused backend tests, local run instructions, provider/security configuration guidance, configured test execution environments with `pytest.ini` in root and backend, added comprehensive API integration tests (`backend/tests/test_api.py`), verified client build (`tsc -b && vite build`) and linting (`oxlint`) with 0 errors, and confirmed all 30 backend tests passing across auth, rides, drivers, security, and payment webhooks.
- Phase 7: added Standard/Comfort/XL/Green tiers with itemized fares, driver vehicle details, decline before pickup, post-trip ratings that update driver averages, and receipts (`backend/tests/test_phase7.py`).
- Phase 8: added an authenticated `/api/ws` WebSocket (token sent in the first message), an in-process connection hub, ride-change pushes from every ride mutation and the payment webhook, throttled driver location sharing saved on the ride, a reconnecting client with live/reconnecting indicator, driver `watchPosition` streaming, and a driver pin on the rider map. Polling slows to 30s/60s while the socket is live. Covered by `backend/tests/test_realtime.py` (38 backend tests passing).
- The dependency installation blocker was resolved. All Phase gates (Foundation, Accounts, Rider, Driver, Payments, Handoff) are verified.

## Implementation outline

- Establish a maintainable frontend/backend layout, environment-based configuration, MongoDB connection, and local run instructions.
- Provide account flows with securely hashed passwords and authenticated, role-checked API operations.
- Build the rider flow around map-based location selection, route/fare preview, ride-class selection, booking, and trip status.
- Build the driver flow to go online, view/accept available requests, and advance ride statuses through pickup and completion.
- Persist users, rides, and payment state in MongoDB; expose typed API schemas and explicit ride lifecycle transitions.
- Connect a configurable map provider for map tiles, geocoding, and routes, and a configurable payment provider for secure payment processing. Keep secret keys in environment variables and document required setup.
- Verify backend and frontend behavior with focused tests, including authorization, ride transitions, and payment outcomes.

## Phase gates

- **Foundation gate:** Frontend and API start locally, MongoDB configuration is documented, and a health check works.
- **Accounts gate:** Rider and driver can register/sign in; role-protected routes reject unauthorized access.
- **Rider gate:** A rider can select locations, review a route/fare, create a persisted request, and see its status.
- **Driver gate:** A driver can accept an available request and update its lifecycle; rider status reflects updates.
- **Payments gate:** Payment flow is connected without exposing secrets; ride/payment state handles success and failure.
- **Handoff gate:** Focused tests pass and setup, service credentials, and end-to-end flow are documented.
- **Ride experience gate:** Riders choose a tier and see an itemized fare; drivers manage vehicle details and can decline; paid trips can be rated once.
- **Real-time gate:** Requests, acceptance, status and payment changes reach the other party without waiting for a poll; the rider sees the assigned driver's live location; invalid sockets are rejected and a socket failure never fails an HTTP request.

## Assumptions and decisions

- This is a web MVP for both riders and drivers, not separate native mobile apps.
- Email/password accounts are required; users have rider or driver roles.
- Real maps and payments are in scope. Mapbox and Stripe are used as configurable implementation defaults; credentials remain environment-only.
- Geographic coverage and fare/payment rules were unspecified, so this MVP is configured for the UK, uses GBP, and applies a simple route-based fare estimate documented in the README.
