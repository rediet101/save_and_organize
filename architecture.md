# Architecture

## System Overview

```
Mobile (Flutter)  ──share/paste──▶  FastAPI Backend  ──structured extract──▶  Gemini 3.8 Flash
       ▲                                  │
       │                                  ▼
       └──────── items, reminders ◀── PostgreSQL + PostGIS
```

The client never talks to Gemini directly — all extraction is proxied through the backend so the
API key stays server-side and every extraction can be logged for the accuracy benchmark.

## Backend Layout (`backend/app/`)

- `api/` — route handlers. One module per resource (`extract.py`, later `items.py`, `geo.py`,
  `reminders.py`). Routers are included in `main.py`.
- `core/` — cross-cutting concerns: `config.py` (Pydantic Settings, env-driven), `logging.py`
  (structlog JSON logging), later `database.py` (async SQLAlchemy session factory) and `security.py`.
- `models/` — SQLAlchemy ORM models mapping to PostgreSQL/PostGIS tables. Empty until Phase 2.
- `schemas/` — Pydantic v2 models. `extraction.py` doubles as both the API request/response contracts
  *and* the Gemini `response_schema` — the same `ExtractedItem` model is passed straight to the SDK so
  the AI output and the API contract can never drift apart.
- `services/` — business logic and third-party integrations. `gemini_extraction.py` wraps the
  `google-genai` SDK. Future: `geocoding.py` (landmark → coordinates), `reminders.py` (scheduler).

## Data Contracts

### `ExtractedItem` (backend/app/schemas/extraction.py)

The single structured object Gemini must return for any saved item:

| Field | Type | Notes |
| --- | --- | --- |
| `title` | str | Short human-readable title |
| `category` | enum | `events` \| `restaurants_food` \| `travel_activities` \| `shops_must_visit` \| `unknown` |
| `description` | str? | Kept in the original language |
| `languages_detected` | list[DetectedLanguage] | Supports code-switched captions; one entry per language present |
| `location` | InformalLocation? | `raw_text` is always verbatim; `normalized_guess` only filled when confident |
| `event_datetime` | datetime? | Null unless explicitly/confidently stated — never fabricated |
| `price_text` / `contact_text` | str? | Captured verbatim, no reformatting |
| `tags` | list[str] | Free-form |
| `confidence` | float [0,1] | Model's self-reported confidence |
| `extraction_notes` | str? | Ambiguity/slang notes for human review |

This schema is intentionally permissive (most fields optional) because informal social content
frequently omits structured details — the extraction service must degrade gracefully rather than
error out.

### API: `POST /extract`

Request (`ExtractionRequest`): `raw_text` and/or `image_base64`+`image_mime_type`, plus
`source_platform` and optional `source_url`.

Response (`ExtractionResponse`): `{ items: [ExtractedItem], model_used, processing_ms }`.

#### Link resolution

When only `source_url` is given, `LinkFetchService` resolves the post before the model sees it.
Each platform has an ordered list of strategies whose results are merged — first caption wins,
first image wins, stop once both are filled:

| Platform | Strategies |
|---|---|
| Instagram | `/p/<shortcode>/embed/captioned/` (full caption) → crawler Open Graph (image) |
| Facebook | crawler Open Graph |
| TikTok | public oEmbed → crawler Open Graph |
| Telegram | public `t.me/s/...` preview page |

Instagram and Facebook fetches identify as `facebookexternalhit/1.1`. This is load-bearing: Meta
answers an ordinary browser user-agent with a login wall carrying no Open Graph tags, but serves
link-preview crawlers the real caption and image. No platform API, credentials or stored session
is used, so only what a link preview would show is visible.

#### Link failures

Content no crawler can reach is a fact about the link, not a server fault, so it answers **422**
with a machine-readable reason the client can branch on, rather than a prose 502:

| `reason` | HTTP | Meaning |
|---|---|---|
| `needs_screenshot` | 422 | Private, login-walled or no public preview — ask the user for a screenshot |
| `private_content` | 422 | Upstream 404/410; deleted or private |
| `unsupported_url` | 422 | Story, DM or profile link with no post to read |
| `fetch_failed` | 502 | Genuine network/timeout/5xx — a real server fault |

Body: `{ reason, platform, message }`. This is what lets the Phase 3 share-sheet flow fall back to
the screenshot path automatically instead of parsing English error strings.

## Database Schema (Phase 2 — not yet implemented)

Planned tables:
- `items` — one row per saved item; JSONB column for the raw `ExtractedItem` payload plus
  normalized columns (`category`, `event_datetime`, `geom` PostGIS point) for querying.
- `reminders` — date-based and geofence-based reminders linked to `items`.
- `users` — auth (deferred; single-user/local mode acceptable for Phase 1–2 workbench testing).

## AI Extraction Engine

- SDK: official `google-genai`, async client (`client.aio.models.generate_content`).
- Model: `gemini-3.8-flash` (configurable via `GEMINI_MODEL` env var).
- Structured output via `response_schema=ExtractedItem` + `response_mime_type=application/json` —
  no manual JSON parsing/repair logic needed.
- System prompt (in `services/gemini_extraction.py`) is the primary lever for accuracy on
  code-switched African-language content and informal landmark addresses; see the Decision Log in
  `progress.md` for the reasoning behind its constraints.
- `temperature=0.1` to keep extraction deterministic/repeatable across benchmark runs.

## Geofencing

Location alerts use native OS circular geofences (`CLCircularRegion` / `GeofencingClient`,
400m radius) registered by the mobile client directly with the OS — the backend only provisions
coordinates via a `geo` endpoint (Phase 2) and never runs continuous background tracking, to avoid
battery drain (see Decision 2 in `progress.md`).

## Non-Goals for Phase 1

No database, no auth, no mobile client, no geocoding — Phase 1 is scoped to proving extraction
accuracy via the backend `/extract` endpoint and the workbench harness.
