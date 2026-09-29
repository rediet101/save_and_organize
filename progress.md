# Project Progress & Status Tracker

**Current Phase:** Phase 1: Core Mechanics (Extraction & Categorization)
**Status:** In Progress
**Last Updated:** 2026-09-21

---

## Phase Breakdown Checklist

### Phase 1: Core Mechanics
- [x] Backend repo skeleton & dependencies (`fastapi`, `google-genai`, `pydantic`)
- [x] Gemini 3.8 Flash structured extraction service
- [x] System prompt engineered for African languages & slang
- [x] Internal testbench web page (Streamlit/HTML)
- [ ] Real-world social media test dataset compiled (TikTok, Telegram, IG)
- [ ] Benchmark report generated (English vs. Non-English accuracy breakdown)

### Phase 2: Platform Backend (No Polished UI)
- [ ] PostgreSQL + PostGIS database migrations
- [ ] Items CRUD API with category-specific filtering
- [ ] Landmark geocoding service (informal address resolution)
- [ ] Date-based reminder scheduler
- [ ] Geofence coordinate provisioning endpoint
- [ ] Basic search and category filter queries

### Phase 3: Mobile Client (Flutter)
- [ ] Flutter app initialization & design system tokens
- [ ] Mobile OS share sheet receiver (`receive_sharing_intent`)
- [ ] Ingestion & editable review screen
- [ ] Library home (4 category counts) & category detail feeds
- [ ] Map view with grouped markers
- [ ] Reminders view (Upcoming date list + Nearby location alerts)
- [ ] Battery-safe OS circular geofencing implementation

### Phase 4: Post-MVP Features
- [ ] Collaborative lists
- [ ] Calendar export
- [ ] Community discovery

---

## Accuracy Benchmark Scorecard (Phase 1 Deliverable)

| Category | Language Group | Samples | Category Accuracy | Field Completeness | Pass/Fail |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Events** | English | 0/10 | - | - | Pending |
| **Events** | Non-English / Slang | 0/10 | - | - | Pending |
| **Restaurants/Food** | English | 0/10 | - | - | Pending |
| **Restaurants/Food** | Non-English / Slang | 0/10 | - | - | Pending |
| **Travel/Activities**| English | 0/10 | - | - | Pending |
| **Travel/Activities**| Non-English / Slang | 0/10 | - | - | Pending |
| **Shops/Must Visit** | English | 0/10 | - | - | Pending |
| **Shops/Must Visit** | Non-English / Slang | 0/10 | - | - | Pending |
| **TOTALS** | - | **0/80** | - | - | **Pending** |

---

## Decision & Change Log

- **Decision 1:** Selected **Gemini 3.8 Flash** (`gemini-3.8-flash`) as primary extraction model due to multimodal speed, low token costs, and high performance on African non-Latin scripts (Amharic) and code-switching.
- **Decision 2:** Replaced continuous GPS background tracking with **OS-level circular geofencing** (radius 400m) to ensure zero battery degradation on mobile clients.
- **Decision 3:** Implemented dual-ingest strategy: direct text/screenshot image OCR to bypass Instagram and TikTok anti-scraping walls.
- **Decision 4 (2026-09-21):** Scaffolded backend as `backend/app/{api,core,models,schemas,services}` with a single `POST /extract` endpoint. `GeminiExtractionService` wraps `google-genai`'s async client, using `response_schema=ExtractedItem` (Pydantic) for structured output and a system prompt explicitly instructing the model to reason across code-switched languages before extracting fields, and to preserve informal landmark location text verbatim rather than normalizing it. No hallucinated `event_datetime` or `normalized_guess` location is permitted by the prompt — both must be left null when not confidently inferable.
- **Decision 5 (2026-09-21):** `models/` (SQLAlchemy) intentionally left empty — DB schema work is Phase 2. `mobile/` and `benchmarks/` are placeholder directories only; no Flutter code or test dataset yet, per phase boundaries.
- **Decision 6 (2026-09-22):** Public Instagram and Facebook links are now readable server-side, so the screenshot path is a fallback rather than the only option for them. The login wall was never about the URL — Meta serves an ordinary browser `User-Agent` a wall with no Open Graph tags, but still serves the real caption and preview image to link-preview crawlers (`facebookexternalhit/1.1`), which is how a pasted link renders in WhatsApp. `LinkFetchService` now dispatches an ordered, merged strategy list per platform: Instagram reads the full untruncated caption from the public `/p/<shortcode>/embed/captioned/` page and the image from the crawler-facing Open Graph tags; Facebook reads Open Graph alone. Verified end to end against a live post, including one carrying a `?stkn=` share token.
  - Rejected: Meta's `instagram_oembed`. It is genuinely tokenless for public posts as of June 2026, but the response carries only an embed `html` blob — no caption and no thumbnail — so it cannot feed extraction, and it would have cost a 2-4 week app review to find that out.
  - Rejected (per product decision): paid third-party resolvers and storing a logged-in session cookie. The first adds per-call cost and a vendor dependency; the second breaches Meta's ToS and risks the account.
  - Limit, stated plainly: this reads only what a link-preview crawler can see. Private accounts, stories, DMs and follower-only posts are unreachable by design and now return **422** with a machine-readable `reason` (`needs_screenshot` / `private_content` / `unsupported_url`) instead of a prose 502, so the Phase 3 Flutter client can branch straight to "share a screenshot instead". Genuine transport faults still return 502 (`fetch_failed`).
  - Unverified: Facebook *individual post permalinks*. Crawler-UA Open Graph is confirmed working on Facebook page and profile URLs; no real public post permalink was available to test. If those turn out to be walled, Facebook lands on the screenshot path and the 422 reason is what makes that recoverable.

---

## Next Immediate Task

Per `implementation_plan.md`, the next unchecked Phase 1 item is: **compile a real-world social media test dataset** (TikTok, Telegram, IG captions/screenshots spanning English and non-English/code-switched content across all four categories), to feed the accuracy benchmark scorecard above.
