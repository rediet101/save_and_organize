# Implementation Plan

Phased roadmap. Each phase gates the next — do not start a later phase until the current one's
checklist in `progress.md` is complete. See `architecture.md` for system design detail.

## Phase 1: Core Mechanics (Extraction & Categorization)

Goal: prove that Gemini 3.8 Flash can reliably extract structured, categorized items from
real African multilingual/code-switched social content, including informal landmark locations.
This is the project's core technical risk — nothing else matters if this doesn't work.

1. Backend repo skeleton & dependencies — `fastapi`, `google-genai`, `pydantic`. **(done)**
2. Gemini 3.8 Flash structured extraction service (`services/gemini_extraction.py`), using
   `response_schema=ExtractedItem`. **(done)**
3. System prompt engineered for African languages & slang (Amharic, Swahili, Sheng, Pidgin,
   French, Arabic) and informal landmark addresses. **(done)**
4. Internal testbench web page for manual QA (`backend/workbench/index.html`). **(done)**
5. Compile a real-world social media test dataset — 80 samples (20 per category, split
   English vs. non-English/code-switched) sourced from actual TikTok/Telegram/IG captions and
   screenshots. Store in `benchmarks/`.
6. Run the dataset through `/extract`, fill in the Accuracy Benchmark Scorecard in `progress.md`,
   and produce a benchmark report (category accuracy + field completeness, English vs. non-English).
7. **Exit criteria:** non-English/code-switched accuracy must be within an acceptable margin of
   English accuracy (target: no more than ~10 percentage points lower) before Phase 2 begins. If it
   isn't, iterate on the system prompt / few-shot examples, not on new features.

## Phase 2: Platform Backend (No Polished UI)

Only after Phase 1 exit criteria are met.

1. PostgreSQL + PostGIS database migrations (`items`, `reminders` tables per `architecture.md`).
2. Items CRUD API with category-specific filtering.
3. Landmark geocoding service — resolve `InformalLocation` into coordinates where possible
   (Mapbox/Google Places), storing both the raw and normalized forms.
4. Date-based reminder scheduler.
5. Geofence coordinate provisioning endpoint (for OS-level circular geofences).
6. Basic search and category filter queries.

## Phase 3: Mobile Client (Flutter)

Only after Phase 2 backend is functional end-to-end (no UI polish required yet).

1. Flutter app initialization & design system tokens.
2. Mobile OS share sheet receiver (`receive_sharing_intent`).
3. Ingestion & editable review screen (user corrects AI extraction before saving).
4. Library home (4 category counts) & category detail feeds.
5. Map view with grouped markers.
6. Reminders view (upcoming date list + nearby location alerts).
7. Battery-safe OS circular geofencing implementation (`CLCircularRegion` / `GeofencingClient`).

## Phase 4: Post-MVP Features

1. Collaborative lists.
2. Calendar export.
3. Community discovery.

## Working Agreement for AI Sessions

See `kickoff-prompt.md`. In short: read `architecture.md` → `implementation_plan.md` →
`progress.md` before writing code; work only within the current phase; update `progress.md`
(checklist, Decision Log, next task) after finishing each task.
