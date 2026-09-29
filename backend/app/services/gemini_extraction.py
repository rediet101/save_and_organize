import base64
import time

from google import genai
from google.genai import errors, types

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.extraction import ExtractedItem, ExtractionRequest, ExtractionResponse

logger = get_logger(__name__)

SYSTEM_PROMPT = """\
You are an extraction engine for a multilingual save-and-organize app used across African markets.

Your job: read a social media caption/message (and optionally a screenshot) and extract structured
items describing what the user wants to save.

CRITICAL RULES:
0. A single source may describe MULTIPLE distinct items (e.g. a listicle of several restaurants, a
   roundup of several events, a "top 5 spots" post). Extract EVERY distinct item you can identify as
   its own entry — never collapse a list down to only the first/primary one. If the source genuinely
   describes only one item, return a single-item list.
1. Content is frequently code-switched (e.g. English mixed with Amharic, Swahili, Sheng, Nigerian
   Pidgin, French, or Arabic within the same sentence/caption). Read the FULL meaning across all
   languages present before extracting fields — do not extract only the English portions.
2. Detect and list every language present in `languages_detected`, marking the dominant one as
   primary. Use informal/slang language names when appropriate (e.g. "Sheng", "Pidgin").
3. Classify into exactly one category:
   - events: parties, concerts, weddings, religious events, meetups, dated happenings
   - restaurants_food: restaurants, cafes, street food, delivery, recipes tied to a place
   - travel_activities: trips, hikes, tours, experiences, activities without a fixed retail purpose
   - shops_must_visit: stores, boutiques, markets, must-visit spots that are not primarily food
   - unknown: only if truly none of the above fit
4. Locations in this market are frequently informal landmark references rather than street
   addresses (e.g. "mesalemiya megna hotel jib bal", "near Yaya Center, opposite the mosque",
   "kwa stage ya Rongai"). Capture the location `raw_text` VERBATIM. Extract `landmark_reference`
   separately if a named landmark is used as the anchor. Only fill `normalized_guess` when you can
   confidently infer a real, geocodable place — never invent one.
5. Do not translate the `title` or `description` out of the original language unless the source is
   already in English — preserve the user's language and tone; keep descriptions concise.
6. Extract price and contact info verbatim (do not convert currencies or reformat phone numbers).
7. Set `confidence` honestly (0.0-1.0). If key fields are ambiguous or missing, note it in
   `extraction_notes` rather than guessing silently.
8. Never fabricate a date/time for `event_datetime` — leave it null if not explicitly stated or
   clearly inferable (e.g. "this Friday" relative to a known post date is acceptable; a vague
   "soon" is not).
"""


# Overload/rate-limit responses: worth trying the next model rather than failing the request.
FALLBACK_STATUS_CODES = frozenset({429, 500, 503, 504})


class GeminiExtractionError(RuntimeError):
    pass


class GeminiUnavailableError(GeminiExtractionError):
    """Every configured model was overloaded or rate-limited; the caller should retry later."""


class GeminiExtractionService:
    """Wraps the google-genai SDK for structured multilingual item extraction."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.gemini_api_key:
            raise GeminiExtractionError("GEMINI_API_KEY is not configured")
        # One quick retry per model; the SDK default (5 attempts, exponential backoff) would stall
        # for ~15s on an overloaded model before the fallback chain got a chance to move on.
        self._client = genai.Client(
            api_key=self._settings.gemini_api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0)),
        )

    @property
    def models(self) -> list[str]:
        """Primary model first, then fallbacks, without duplicates."""
        return list(dict.fromkeys([self._settings.gemini_model, *self._settings.gemini_fallback_models]))

    def _build_contents(self, request: ExtractionRequest) -> list[types.Part]:
        parts: list[types.Part] = []
        if request.raw_text:
            parts.append(types.Part.from_text(text=request.raw_text))
        if request.image_base64:
            if not request.image_mime_type:
                raise GeminiExtractionError("image_mime_type is required when image_base64 is provided")
            try:
                image_bytes = base64.b64decode(request.image_base64, validate=True)
            except (base64.binascii.Error, ValueError) as exc:
                raise GeminiExtractionError("image_base64 is not valid base64") from exc
            parts.append(
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=request.image_mime_type,
                )
            )
        if not parts:
            raise GeminiExtractionError("Extraction request must include raw_text and/or image_base64")
        return parts

    async def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        started = time.monotonic()
        contents = self._build_contents(request)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=list[ExtractedItem],
            temperature=0.1,
        )

        response = None
        model = self._settings.gemini_model
        for model in self.models:
            try:
                response = await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
                break
            except errors.APIError as exc:
                if exc.code not in FALLBACK_STATUS_CODES:
                    logger.error("gemini_extraction_failed", error=str(exc), model=model)
                    raise GeminiExtractionError(f"Gemini extraction failed: {exc}") from exc
                logger.warning("gemini_model_unavailable", model=model, status_code=exc.code)
            except Exception as exc:  # noqa: BLE001 - surfaced as a domain error for API layer
                logger.error("gemini_extraction_failed", error=str(exc), source_platform=request.source_platform)
                raise GeminiExtractionError(f"Gemini extraction failed: {exc}") from exc

        if response is None:
            logger.error("gemini_all_models_unavailable", models=self.models)
            raise GeminiUnavailableError(
                "The AI service is busy right now. Please try again in a minute."
            )

        parsed = response.parsed
        if parsed is None or not isinstance(parsed, list) or not all(isinstance(item, ExtractedItem) for item in parsed):
            logger.error("gemini_extraction_unparsed", raw_response=getattr(response, "text", None))
            raise GeminiExtractionError("Gemini returned a response that did not match the expected schema")
        if not parsed:
            raise GeminiExtractionError("Gemini returned no extractable items")

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "gemini_extraction_succeeded",
            item_count=len(parsed),
            categories=[item.category for item in parsed],
            processing_ms=elapsed_ms,
            model=model,
        )

        return ExtractionResponse(
            items=parsed,
            model_used=model,
            processing_ms=elapsed_ms,
        )
