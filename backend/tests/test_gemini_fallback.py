"""Covers the Gemini model fallback chain used when a model is overloaded.

The SDK client is replaced with a fake that fails or answers per model, so the
suite never touches the network.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app.api.extract import get_extraction_service
from app.core.config import Settings
from app.main import app
from app.schemas.extraction import ExtractedItem, ExtractionRequest, ItemCategory
from app.services.gemini_extraction import (
    GeminiExtractionError,
    GeminiExtractionService,
    GeminiUnavailableError,
)

ITEM = ExtractedItem(title="Buna spot", category=ItemCategory.RESTAURANTS_FOOD, confidence=0.9)


def api_error(code: int) -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": "nope", "status": "X"}})


class FakeModels:
    """Answers generate_content per model: an int raises that status, anything else is returned."""

    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        outcome = self.outcomes[model]
        if isinstance(outcome, int):
            raise api_error(outcome)
        return outcome


def make_service(outcomes: dict[str, object]) -> tuple[GeminiExtractionService, FakeModels]:
    settings = Settings(
        gemini_api_key="test-key",
        gemini_model="primary",
        gemini_fallback_models=["second", "third"],
    )
    service = GeminiExtractionService(settings)
    fake = FakeModels(outcomes)
    service._client = SimpleNamespace(aio=SimpleNamespace(models=fake))
    return service, fake


def ok_response() -> SimpleNamespace:
    return SimpleNamespace(parsed=[ITEM], text="[]")


REQUEST = ExtractionRequest(raw_text="Best buna in Bole")


@pytest.mark.asyncio
async def test_primary_model_used_when_available():
    service, fake = make_service({"primary": ok_response()})

    result = await service.extract(REQUEST)

    assert result.model_used == "primary"
    assert fake.calls == ["primary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [429, 500, 503, 504])
async def test_overloaded_model_falls_back_to_next(code):
    service, fake = make_service({"primary": code, "second": ok_response()})

    result = await service.extract(REQUEST)

    assert result.model_used == "second"
    assert fake.calls == ["primary", "second"]


async def test_all_models_overloaded_raises_unavailable():
    service, fake = make_service({"primary": 503, "second": 429, "third": 503})

    with pytest.raises(GeminiUnavailableError):
        await service.extract(REQUEST)
    assert fake.calls == ["primary", "second", "third"]


@pytest.mark.asyncio
async def test_client_error_does_not_fall_back():
    """A bad request or bad key fails the same way on every model, so don't burn the chain."""
    service, fake = make_service({"primary": 400, "second": ok_response()})

    with pytest.raises(GeminiExtractionError) as exc_info:
        await service.extract(REQUEST)
    assert not isinstance(exc_info.value, GeminiUnavailableError)
    assert fake.calls == ["primary"]


def test_duplicate_fallbacks_are_skipped():
    settings = Settings(gemini_api_key="k", gemini_model="a", gemini_fallback_models=["a", "b", "b"])

    assert GeminiExtractionService(settings).models == ["a", "b"]


def test_endpoint_returns_503_with_retry_after_when_all_busy():
    service, _ = make_service({"primary": 503, "second": 503, "third": 503})
    app.dependency_overrides[get_extraction_service] = lambda: service
    try:
        response = TestClient(app).post("/extract", json={"raw_text": "Best buna in Bole"})
    finally:
        app.dependency_overrides.pop(get_extraction_service, None)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert "busy" in response.json()["detail"]
