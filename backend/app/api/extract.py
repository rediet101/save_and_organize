from fastapi import APIRouter, Depends, HTTPException, status

from app.core.logging import get_logger
from app.schemas.extraction import (
    ExtractionRequest,
    ExtractionResponse,
    LinkFailureDetail,
    LinkFailureReason,
)
from app.services.gemini_extraction import (
    GeminiExtractionError,
    GeminiExtractionService,
    GeminiUnavailableError,
)
from app.services.link_extraction import LinkFetchError, LinkFetchService

logger = get_logger(__name__)
router = APIRouter(prefix="/extract", tags=["extraction"])

# A walled or unreadable post is a fact about the link, not a server fault, so
# the client is told to fall back to a screenshot rather than shown a 502.
LINK_FAILURE_STATUS = {
    LinkFailureReason.NEEDS_SCREENSHOT: status.HTTP_422_UNPROCESSABLE_ENTITY,
    LinkFailureReason.PRIVATE_CONTENT: status.HTTP_422_UNPROCESSABLE_ENTITY,
    LinkFailureReason.UNSUPPORTED_URL: status.HTTP_422_UNPROCESSABLE_ENTITY,
    LinkFailureReason.FETCH_FAILED: status.HTTP_502_BAD_GATEWAY,
}


def get_extraction_service() -> GeminiExtractionService:
    return GeminiExtractionService()


def get_link_service() -> LinkFetchService:
    return LinkFetchService()


@router.post("", response_model=ExtractionResponse)
async def extract_item(
    request: ExtractionRequest,
    service: GeminiExtractionService = Depends(get_extraction_service),
    link_service: LinkFetchService = Depends(get_link_service),
) -> ExtractionResponse:
    if not request.raw_text and not request.image_base64 and not request.source_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide raw_text, image_base64, and/or source_url",
        )

    if request.source_url and not request.raw_text and not request.image_base64:
        try:
            fetched = await link_service.fetch(request.source_url, request.source_platform)
        except LinkFetchError as exc:
            logger.error(
                "link_fetch_failed",
                error=str(exc),
                reason=exc.reason.value,
                url=request.source_url,
            )
            detail = LinkFailureDetail(
                reason=exc.reason,
                platform=request.source_platform,
                message=str(exc),
            )
            raise HTTPException(
                status_code=LINK_FAILURE_STATUS[exc.reason],
                detail=detail.model_dump(mode="json"),
            ) from exc

        request = request.model_copy(
            update={
                "raw_text": fetched.text,
                "image_base64": fetched.image_base64,
                "image_mime_type": fetched.image_mime_type,
            }
        )

    try:
        return await service.extract(request)
    except GeminiUnavailableError as exc:
        # Upstream capacity, not a bug on our side: tell the client to retry rather than show a 502.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"Retry-After": "30"},
        ) from exc
    except GeminiExtractionError as exc:
        logger.error("extract_endpoint_failed", error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
