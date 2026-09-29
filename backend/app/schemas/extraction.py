from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ItemCategory(str, Enum):
    EVENTS = "events"
    RESTAURANTS_FOOD = "restaurants_food"
    TRAVEL_ACTIVITIES = "travel_activities"
    SHOPS_MUST_VISIT = "shops_must_visit"
    UNKNOWN = "unknown"


class SourcePlatform(str, Enum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TELEGRAM = "telegram"
    TEXT = "text"
    SCREENSHOT = "screenshot"
    OTHER = "other"


class LinkFailureReason(str, Enum):
    """Why a shared link could not be read, in a form the client can branch on."""

    NEEDS_SCREENSHOT = "needs_screenshot"
    PRIVATE_CONTENT = "private_content"
    UNSUPPORTED_URL = "unsupported_url"
    FETCH_FAILED = "fetch_failed"


class LinkFailureDetail(BaseModel):
    """Body of a 4xx returned when a link is readable by no available strategy."""

    reason: LinkFailureReason
    platform: SourcePlatform
    message: str


class DetectedLanguage(BaseModel):
    """One language present in the source content, for code-switched inputs."""

    code: str = Field(description="ISO 639-1/639-3 code, e.g. 'am', 'sw', 'pcm', 'fr', 'en'")
    name: str = Field(description="Human readable language name, e.g. 'Amharic', 'Sheng'")
    is_primary: bool = Field(description="True for the dominant language of the content")


class InformalLocation(BaseModel):
    """A landmark-based or informal address as commonly written in African social content."""

    raw_text: str = Field(description="Location exactly as written in the source, unmodified")
    landmark_reference: str | None = Field(
        default=None,
        description="Named landmark used as reference point, e.g. 'behind Bole Medhanialem church'",
    )
    city: str | None = None
    neighborhood: str | None = None
    country: str | None = None
    normalized_guess: str | None = Field(
        default=None,
        description="Best-effort normalized/geocodable address string, if inferable",
    )


class ExtractedItem(BaseModel):
    """Structured output of a single saved item, produced by the Gemini extraction service."""

    title: str = Field(description="Short, human-readable title for the saved item")
    category: ItemCategory
    description: str | None = Field(default=None, description="Concise summary in the original language")
    languages_detected: list[DetectedLanguage] = Field(default_factory=list)
    location: InformalLocation | None = None
    event_datetime: datetime | None = Field(
        default=None, description="Parsed date/time for events; null if not applicable or not stated"
    )
    price_text: str | None = Field(default=None, description="Price or price range as stated in source, verbatim")
    contact_text: str | None = Field(default=None, description="Phone/handle/contact info as stated, verbatim")
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, description="Model's confidence in category + field extraction")
    extraction_notes: str | None = Field(
        default=None, description="Ambiguities, slang interpreted, or fields the model could not resolve"
    )


class ExtractionRequest(BaseModel):
    raw_text: str | None = Field(default=None, description="Pasted caption/message text")
    image_base64: str | None = Field(default=None, description="Base64-encoded screenshot for OCR + extraction")
    image_mime_type: str | None = Field(default=None, description="e.g. 'image/jpeg', 'image/png'")
    source_platform: SourcePlatform = SourcePlatform.TEXT
    source_url: str | None = None


class ExtractionResponse(BaseModel):
    items: list[ExtractedItem]
    model_used: str
    processing_ms: int
