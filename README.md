# trip_pilot
# TripPilot AI — Full Build Prompt (Django MVT)

Use this as the instruction prompt for a coding assistant (Claude Code, etc.) to turn the current static HTML/JS prototype into a working, data-driven Django MVT application.

---
## 1. Data Sources & External APIs (per feature)

| Feature | Recommended API/Source | Notes |
|---|---|---|
| **Flights** | Amadeus Self-Service "Flight Offers Search" API (free tier) or Skyscanner RapidAPI / Duffel API | Amadeus is the most complete free option; returns carrier, duration, stops, price. Duffel is better if you eventually want real booking, not just search. |
| **Hotels** | Amadeus "Hotel Search" + "Hotel Offers" APIs, or Booking.com via RapidAPI | Amadeus keeps you on one vendor/auth flow with flights. |
| **Attractions & Activities** | GetYourGuide Partner API, Viator Partner API, or OpenTripMap (free, good for basic POI + descriptions) | OpenTripMap is easiest to prototype with no partner approval needed. |
| **Visa Requirements** | Sherpa° API (Timatic-based), VisaHQ API, or Travel-Advisory.info (free, coarse) | Sherpa° is the industry-standard source (used by airlines); paid. Travel-Advisory.info is a free fallback for MVP/demo. |
| **Currency Conversion** | exchangerate.host (free, no key) or Open Exchange Rates | exchangerate.host is simplest for a prototype. |
| **Maps / Local Transport estimates** | Google Maps Distance Matrix API or Mapbox Directions API | Needed for airport-transfer and local-transport cost estimation. |
| **Travel Insurance** | SafetyWing API or affiliate link (most insurers don't expose public quote APIs) | If no API access, keep this as a static/affiliate estimate with a disclaimer, same as now. |
| **SIM / eSIM** | Airalo Partner API (eSIM) | Affiliate/partner integration; fallback to flat estimate if not approved yet. |
| **PDF Export** | WeasyPrint (HTML→PDF, matches existing CSS) or xhtml2pdf | WeasyPrint gives the best fidelity to your current card-based layout. |
| **Background jobs / API calls** | Celery + Redis | Needed so live API calls (flights/hotels) don't block the request-response cycle. |
| **Caching** | Django cache framework + Redis | Cache flight/hotel/visa responses per (route, date, party-size) key for a few hours — these APIs are rate-limited and slow. |

All of the above except Google Maps and Amadeus have workable free/sandbox tiers, so the assistant can wire up sandbox credentials first and swap to production keys later without changing code — just `.env` values.

---

## 2. Functional Requirements (carried over, tightened for Django)

1. **Trip Configuration Form** — Django `ModelForm` backing a `TripRequest` model: departure city, destination, nationality, dates, adults/children, travel style, accommodation type, currency, budget.
2. **Budget Engine & Cost Breakdown** — a `pricing/` service module (pure Python, unit-testable) that takes normalized API responses + `TripRequest` and returns a `BudgetBreakdown` (flights, hotel, transfer, transport, food, attractions, visa, insurance, SIM, contingency). Keep this logic separate from views so it can be tested without hitting live APIs.
3. **Live Travel Aggregation** — one Django app (`integrations/`) with a thin client class per provider (`AmadeusClient`, `OpenTripMapClient`, `SherpaVisaClient`, `ExchangeRateClient`), each returning normalized dataclasses so the rest of the app never touches raw API JSON.
4. **Visa Guidance** — nationality + destination → visa type, cost, processing time, validity; cached per (nationality, destination) pair.
5. **Smart Optimizer** — a function that, given an over-budget `BudgetBreakdown`, re-runs allocation with a cheaper travel style / hotel tier / flight option and returns a new breakdown — same logic you already sketched in `optimizeBtn`, moved server-side.
6. **Automated Itinerary Builder** — generates a day-by-day `Itinerary` (arrival → N exploration days → departure) from the attractions returned by the API, persisted so it can be re-rendered or exported later.
7. **User Accounts & Saved Itineraries** — Django's built-in auth (or `django-allauth` if you want social login), a `SavedTrip` model tied to `User`, a dashboard view listing saved trips, checklist progress stored per trip, and a PDF export view per trip.
8. **Admin Panel** — see Section 4.

---

## 3. Suggested Django Project Layout

```
trippilot/
├── config/                  # settings, urls, celery.py
├── accounts/                 # auth, profile, dashboard
├── trips/                    # TripRequest, SavedTrip, Itinerary, BudgetBreakdown models + views
├── integrations/             # API clients: amadeus.py, visa.py, exchange.py, activities.py, maps.py
├── pricing/                  # budget engine + optimizer (framework-agnostic, unit-tested)
├── pdfexport/                 # WeasyPrint templates + view
├── static/                    # existing style.css, adapted app.js (progressively replaced by server-rendered data)
├── templates/
│   ├── base.html
│   ├── trips/planner.html     # port of index.html form
│   ├── trips/results.html     # port of #results section
│   └── pdfexport/itinerary_pdf.html
```

**Migration approach for the frontend:** keep `index.html`'s markup/CSS as the template skeleton, but move `render()` in `app.js` server-side — the planner form does a normal POST to a Django view, which calls `pricing.build_plan()`, and `results.html` renders the same panels using Django template tags instead of the JS template-literal building. This keeps your current visual design intact while making the data real and shareable via URL (`/trips/<id>/`).

---

## 4. Admin Panel Requirements

- Use **django-unfold** or **django-jazzmin** as the base theme (both are actively maintained, modern, and easy to re-skin) — do **not** ship the default Django admin look.
- Re-theme it with the existing palette: `--ink:#14213d`, `--blue:#276ef1`, `--soft:#f4f7fc`, `--green:#138a57`, `--orange:#e8861a` — same accent colors as the public site so admin feels like part of the product.
- Register and customize:
  - `TripRequest` / `SavedTrip` — list view with destination, user, budget, status, created date; filter by travel style, currency, over/under budget.
  - `Itinerary` — inline editable day/activity rows.
  - Integration health — a read-only admin dashboard view (or `django-admin` custom page) showing last successful call time and error count per API client (Amadeus, Sherpa, OpenTripMap, exchange rate) — critical for a system depending on five external APIs.
  - Users — standard `auth.User` admin, extended with a `Profile` (nationality default, preferred currency) inline.
- Add a small analytics widget on the admin index: trips created this week, average budget, most-requested destinations — a couple of Django ORM aggregates rendered as cards, styled to match.

---

## 5. Non-functional requirements to include in the prompt

- **Environment config**: all API keys in `.env` via `django-environ`, never hardcoded; ship a `.env.example` listing every key from Section 1.
- **Error handling**: every integration client must degrade gracefully — if an API is down or rate-limited, fall back to the existing illustrative sample data (keep `demo` data in `app.js`/a fixtures file as the fallback, labeled "Estimated — live data unavailable").
- **Rate limiting / caching**: Redis-backed cache on all external calls, sensible TTL (e.g., 6h for flights/hotels, 7d for visa rules, 1h for exchange rates).
- **Testing**: unit tests for `pricing/` (budget math, optimizer) with no network calls; integration tests for API clients using recorded fixtures (e.g., `vcrpy` or `responses`).
- **PDF export**: one route per saved trip, styled from the same base CSS, download button on the trip dashboard.
- **Security**: CSRF on all forms (default Django), never expose provider API keys client-side, rate-limit the planner endpoint per user/IP to avoid quota exhaustion on paid APIs.

---

## 6. Planned Feature Requirements (Phase 2)

Not yet implemented — captured here as the spec to build against next.

### 6.1 Multi-City & Multi-Destination Trip Planning

- **Route structure**: support trips involving multiple destinations (e.g. Cape Town → Istanbul), not just a single origin/destination pair.
- **Dynamic accommodation/room allocation**: let users specify a different room configuration per destination (e.g. a couple's room in one city, a family room with children in the next).

### 6.2 Authentication & User Flow Overhaul

- **Optional pre-planning**: let users build and submit a trip plan *before* being forced to create an account or log in.
- **Post-submission account creation**: after a plan is submitted, prompt the user to register (email + password) to save and revisit it later, rather than requiring signup up front.
- **Email as primary username**: replace username-based login with email — easier for users to remember.
- **Password recovery**: add a "Forgot password" / email-based recovery flow.

---