import base64
import html as html_lib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.schemas.extraction import LinkFailureReason, SourcePlatform

logger = get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Meta answers ordinary browser user-agents with a login wall carrying no Open
# Graph tags at all, but still serves the real caption and preview image to
# link-preview crawlers — that is how a pasted link renders in WhatsApp.
CRAWLER_USER_AGENT = "facebookexternalhit/1.1"

MAX_IMAGE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT = 15.0
TIKTOK_OEMBED_URL = "https://www.tiktok.com/oembed"
INSTAGRAM_EMBED_URL = "https://www.instagram.com/p/{shortcode}/embed/captioned/"

INSTAGRAM_SHORTCODE_RE = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")

# Instagram wraps og:description in engagement counts, e.g.
#   18K likes, 171 comments - someuser on September 18, 2026: "<caption>"
# Meta localizes that preamble, and this product is explicitly multilingual, so
# match on its structure rather than on English words, and fall back to the raw
# string whenever the shape does not hold.
OG_CAPTION_RE = re.compile(r'^.*? on .*?: "(?P<caption>.*)"\s*$', re.DOTALL)

# Suffix match, so www./m./mbasic./vm. subdomains resolve to the same platform.
PLATFORM_BY_HOST: dict[str, SourcePlatform] = {
    "instagram.com": SourcePlatform.INSTAGRAM,
    "instagr.am": SourcePlatform.INSTAGRAM,
    "facebook.com": SourcePlatform.FACEBOOK,
    "fb.com": SourcePlatform.FACEBOOK,
    "fb.watch": SourcePlatform.FACEBOOK,
    "tiktok.com": SourcePlatform.TIKTOK,
    "t.me": SourcePlatform.TELEGRAM,
    "telegram.me": SourcePlatform.TELEGRAM,
}

# Platforms carrying no host information of their own, so the URL decides.
AMBIGUOUS_PLATFORMS = frozenset(
    {SourcePlatform.TEXT, SourcePlatform.SCREENSHOT, SourcePlatform.OTHER}
)
META_PLATFORMS = frozenset({SourcePlatform.INSTAGRAM, SourcePlatform.FACEBOOK})

# Link shapes pointing at no readable post, so no strategy can ever help.
UNSUPPORTED_PATH_PREFIXES = ("/stories/", "/direct/", "/accounts/", "/messages/")

NEEDS_SCREENSHOT_MESSAGE = (
    "This post is private, login-walled or has no public preview. "
    "Share a screenshot of it instead and we will read it from the image."
)


class LinkFetchError(RuntimeError):
    """A shared link could not be read, tagged with a reason the client can act on."""

    def __init__(
        self,
        message: str,
        reason: LinkFailureReason = LinkFailureReason.FETCH_FAILED,
    ) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class FetchedContent:
    text: str | None
    image_base64: str | None = None
    image_mime_type: str | None = None


Strategy = Callable[
    [httpx.AsyncClient, str, SourcePlatform], Awaitable[FetchedContent | None]
]


class LinkFetchService:
    """Fetches the public caption + preview image for a shared social link.

    Each platform gets an ordered list of strategies whose results are merged:
    the first non-null caption wins, the first non-null image wins, and the run
    stops as soon as both are filled. Instagram needs that merge because its two
    public sources are complementary — the `/embed/captioned/` page carries the
    full untruncated caption but no image, while the crawler-facing Open Graph
    tags carry the image.

    Everything here reads only what a link-preview crawler can see: no platform
    API, no credentials, no stored session. Private accounts, stories and
    follower-only posts are therefore out of reach by design and raise
    `LinkFailureReason.NEEDS_SCREENSHOT` for the caller to fall back on.
    """

    async def fetch(self, url: str, platform: SourcePlatform) -> FetchedContent:
        platform = self._resolve_platform(url, platform)
        self._reject_unsupported(url, platform)

        text: str | None = None
        image_base64: str | None = None
        image_mime_type: str | None = None
        hard_error: LinkFetchError | None = None
        clean_runs = 0

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
        ) as client:
            for strategy in self._strategies_for(platform):
                try:
                    result = await strategy(client, url, platform)
                except LinkFetchError as exc:
                    # A definitive answer from upstream ends the run; anything
                    # else is worth another strategy's attempt.
                    if exc.reason in (
                        LinkFailureReason.PRIVATE_CONTENT,
                        LinkFailureReason.UNSUPPORTED_URL,
                    ):
                        raise
                    hard_error = exc
                    logger.warning(
                        "link_strategy_failed",
                        strategy=strategy.__name__,
                        platform=platform.value,
                        error=str(exc),
                        url=url,
                    )
                    continue

                clean_runs += 1
                if result is None:
                    continue

                text = text or result.text
                if image_base64 is None and result.image_base64:
                    image_base64 = result.image_base64
                    image_mime_type = result.image_mime_type
                if text and image_base64:
                    break

        if not text and not image_base64:
            # Every strategy erroring out is a server-side fault, not a walled
            # post, so surface the transport error rather than blaming the user.
            if hard_error is not None and clean_runs == 0:
                raise hard_error
            raise LinkFetchError(
                NEEDS_SCREENSHOT_MESSAGE,
                reason=LinkFailureReason.NEEDS_SCREENSHOT,
            )

        return FetchedContent(
            text=text, image_base64=image_base64, image_mime_type=image_mime_type
        )

    def _strategies_for(self, platform: SourcePlatform) -> tuple[Strategy, ...]:
        if platform == SourcePlatform.INSTAGRAM:
            return (self._instagram_embed_captioned, self._crawler_open_graph)
        if platform == SourcePlatform.TIKTOK:
            return (self._tiktok_oembed, self._crawler_open_graph)
        return (self._crawler_open_graph,)

    # --------------------------------------------------------------- strategies

    async def _crawler_open_graph(
        self, client: httpx.AsyncClient, url: str, platform: SourcePlatform
    ) -> FetchedContent | None:
        """Read the Open Graph / Twitter Card tags a link preview would show."""
        response = await self._get(
            client, self._normalize_url(url, platform), self._user_agent_for(platform)
        )
        soup = BeautifulSoup(response.text, "html.parser")

        text = self._extract_text(soup, platform)
        image_url = self._extract_image_url(soup)
        image_base64, image_mime_type = await self._try_download_image(client, image_url)

        if not text and not image_base64:
            return None
        return FetchedContent(
            text=text, image_base64=image_base64, image_mime_type=image_mime_type
        )

    async def _instagram_embed_captioned(
        self, client: httpx.AsyncClient, url: str, platform: SourcePlatform
    ) -> FetchedContent | None:
        """Instagram's public embed page renders the full, untruncated caption."""
        shortcode = self._instagram_shortcode(url)
        if shortcode is None:
            return None

        response = await self._get(
            client,
            INSTAGRAM_EMBED_URL.format(shortcode=shortcode),
            CRAWLER_USER_AGENT,
        )
        soup = BeautifulSoup(response.text, "html.parser")

        node = soup.select_one("div.Caption")
        if node is None:
            return None

        # The poster's handle is an anchor inside the caption node, so a plain
        # get_text() would glue it onto the first word of the caption.
        for extra in node.select("a.CaptionUsername, .CaptionComments"):
            extra.decompose()

        text = self._collapse_blank_lines(node.get_text("\n"))
        return FetchedContent(text=text) if text else None

    async def _tiktok_oembed(
        self, client: httpx.AsyncClient, url: str, platform: SourcePlatform
    ) -> FetchedContent | None:
        """TikTok's oEmbed endpoint is public and needs no auth; caption is `title`."""
        try:
            response = await client.get(
                TIKTOK_OEMBED_URL,
                params={"url": url},
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("tiktok_oembed_failed", error=str(exc), url=url)
            return None

        text = (data.get("title") or "").strip() or None
        image_base64, image_mime_type = await self._try_download_image(
            client, data.get("thumbnail_url")
        )

        if not text and not image_base64:
            return None
        return FetchedContent(
            text=text, image_base64=image_base64, image_mime_type=image_mime_type
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _user_agent_for(platform: SourcePlatform) -> str:
        return CRAWLER_USER_AGENT if platform in META_PLATFORMS else USER_AGENT

    @staticmethod
    def _resolve_platform(url: str, platform: SourcePlatform) -> SourcePlatform:
        """Trust an explicit platform; otherwise infer one from the link's host."""
        if platform not in AMBIGUOUS_PLATFORMS:
            return platform

        host = urlparse(url).netloc.lower().split(":")[0]
        for known_host, known_platform in PLATFORM_BY_HOST.items():
            if host == known_host or host.endswith(f".{known_host}"):
                return known_platform
        return platform

    @staticmethod
    def _reject_unsupported(url: str, platform: SourcePlatform) -> None:
        if platform not in META_PLATFORMS:
            return
        path = urlparse(url).path.rstrip("/") + "/"
        if path.startswith(UNSUPPORTED_PATH_PREFIXES):
            raise LinkFetchError(
                "Stories, direct messages and profile links carry no readable post. "
                "Share a screenshot instead.",
                reason=LinkFailureReason.UNSUPPORTED_URL,
            )

    @staticmethod
    def _instagram_shortcode(url: str) -> str | None:
        match = INSTAGRAM_SHORTCODE_RE.search(urlparse(url).path)
        return match.group(1) if match else None

    @staticmethod
    def _normalize_url(url: str, platform: SourcePlatform) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":")[0]

        if platform == SourcePlatform.TELEGRAM or host.endswith("t.me"):
            path = parsed.path.strip("/")
            if path and not path.startswith("s/"):
                return f"https://t.me/s/{path}"

        # mbasic/m. serve a login wall even to crawlers; the www. host does not.
        if platform == SourcePlatform.FACEBOOK and host.startswith(("m.", "mbasic.")):
            return urlunparse(parsed._replace(netloc="www.facebook.com"))

        return url

    @classmethod
    def _extract_text(cls, soup: BeautifulSoup, platform: SourcePlatform) -> str | None:
        if platform == SourcePlatform.TELEGRAM:
            nodes = soup.select("div.tgme_widget_message_text")
            if nodes:
                return nodes[-1].get_text("\n").strip()

        og_description = soup.find("meta", property="og:description")
        if og_description and og_description.get("content"):
            return cls._clean_caption(og_description["content"], platform)

        twitter_description = soup.find("meta", attrs={"name": "twitter:description"})
        if twitter_description and twitter_description.get("content"):
            return cls._clean_caption(twitter_description["content"], platform)

        return None

    @classmethod
    def _clean_caption(cls, raw: str, platform: SourcePlatform) -> str | None:
        text = html_lib.unescape(raw).strip()
        if platform in META_PLATFORMS:
            match = OG_CAPTION_RE.match(text)
            if match:
                text = match.group("caption").strip()
        return cls._collapse_blank_lines(text)

    @staticmethod
    def _collapse_blank_lines(text: str) -> str | None:
        lines = [line.strip() for line in text.splitlines()]
        collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        return collapsed or None

    @staticmethod
    def _extract_image_url(soup: BeautifulSoup) -> str | None:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            return og_image["content"]
        return None

    async def _try_download_image(
        self, client: httpx.AsyncClient, image_url: str | None
    ) -> tuple[str | None, str | None]:
        """A missing preview image never fails the fetch; the caption may carry it."""
        if not image_url:
            return None, None
        try:
            return await self._download_image(client, image_url)
        except LinkFetchError as exc:
            logger.warning("link_image_fetch_failed", error=str(exc), url=image_url)
            return None, None

    @staticmethod
    async def _get(
        client: httpx.AsyncClient, url: str, user_agent: str
    ) -> httpx.Response:
        try:
            response = await client.get(url, headers={"User-Agent": user_agent})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                raise LinkFetchError(
                    f"The post at {url} is unavailable — it may be deleted or private.",
                    reason=LinkFailureReason.PRIVATE_CONTENT,
                ) from exc
            raise LinkFetchError(f"Failed to fetch {url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LinkFetchError(f"Failed to fetch {url}: {exc}") from exc
        return response

    @staticmethod
    async def _download_image(client: httpx.AsyncClient, image_url: str) -> tuple[str, str]:
        try:
            response = await client.get(image_url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LinkFetchError(f"Failed to fetch preview image: {exc}") from exc

        content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
        if not content_type.startswith("image/"):
            raise LinkFetchError(f"Preview URL did not return an image (got {content_type})")
        if len(response.content) > MAX_IMAGE_BYTES:
            raise LinkFetchError("Preview image exceeds maximum allowed size")

        return base64.b64encode(response.content).decode("ascii"), content_type
