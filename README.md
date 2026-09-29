# Multilingual Save-and-Organize App

An AI-powered mobile app and backend that allows users to share or paste links, screenshots, and text from TikTok, Telegram, or Instagram. The system automatically detects the language, extracts structured details, and organizes items into four categories: **Events**, **Restaurants/Food**, **Travel/Activities**, or **Shops/Must Visit**.

Designed specifically for African and multilingual markets, handling regional languages (e.g., Amharic, Swahili, Yoruba, Pidgin, French, Arabic) and informal landmark-based locations.

---

## Tech Stack

- **Mobile Client:** Flutter (Dart) — iOS & Android cross-platform support with native share-sheet extensions.
- **Backend API:** FastAPI (Python 3.11+) — Async REST endpoints, Pydantic v2 structured schemas.
- **AI Extraction Engine:** Google Gemini 3.8 Flash (`gemini-3.8-flash`) via `google-genai` SDK for multimodal extraction and multilingual reasoning.
- **Database & Spatial Engine:** PostgreSQL 16 + PostGIS for spatial coordinates and proximity queries.
- **Maps & Geocoding:** Mapbox / Google Places API with landmark normalization.
- **Geofencing:** Native OS Circular Geofences (`CLCircularRegion` on iOS, `GeofencingClient` on Android) for battery-efficient location alerts.

---

## Project Structure

```text
.
├── backend/
│   ├── app/
│   │   ├── api/          # Route handlers (/extract, /items, /geo)
│   │   ├── core/         # Config, security, database sessions
│   │   ├── models/       # SQLAlchemy / SQLModel database models
│   │   ├── schemas/      # Pydantic validation & Gemini output schemas
│   │   └── services/     # Gemini 3.8 Flash engine, geocoding, scrapers
│   ├── tests/            # Unit & integration tests
│   ├── workbench/        # Phase 1 test harness (Streamlit / HTML UI)
│   ├── Dockerfile
│   └── requirements.txt
├── mobile/
│   ├── lib/
│   │   ├── core/         # Theme, constants, networking (Dio)
│   │   ├── features/     # Feature-first modules (extract, library, map, reminders)
│   │   └── main.dart
│   └── pubspec.yaml
├── benchmarks/           # Phase 1 test dataset (English vs. Non-English)
├── architecture.md       # Full technical system architecture
├── implementation_plan.md# Phase-by-phase execution roadmap
├── progress.md           # Active project tracker & changelog
├── kickoff-prompt.md     # AI agent session bootstrapper
└── README.md
```

## Getting Started (Backend)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements-dev.txt
copy .env.example .env        # then fill in GEMINI_API_KEY
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the interactive API docs, or `backend/workbench/index.html` for the manual extraction testbench.
