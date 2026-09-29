"""Covers the shared-link fetcher, especially the Meta login-wall workaround.

Every HTTP call is served by an `httpx.MockTransport`, so the suite never
touches the network. The fixture HTML mirrors what Instagram and Facebook
actually returned when this behaviour was verified: a browser user-agent gets a
login wall with no Open Graph tags, while a crawler user-agent gets the real
caption wrapped in engagement counts.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.extract import get_extraction_service, get_link_service
from app.main import app
from app.schemas.extraction import LinkFailureReason, SourcePlatform
from app.services import link_extraction
from app.services.link_extraction import (
    CRAWLER_USER_AGENT,
    USER_AGENT,
    FetchedContent,
    LinkFetchError,
    LinkFetchService,
)

# What Instagram serves a desktop browser user-agent: no og: tags at all.
IG_LOGIN_WALL = "<html><head><title>Instagram</title></head><body></body></html>"

# What Instagram serves a crawler: the caption behind an engagement preamble,
# HTML-escaped, and truncated with an ellipsis.
IG_CRAWLER_OG = """<html><head>
<meta property="og:description"
      content="18K likes, 171 comments - chefsara on September 18, 2026: &quot;Beans &amp; injera at Bole... &quot;" />
<meta property="og:image" content="https://scontent.cdninstagram.com/preview.jpg" />
</head></html>"""

# The embed page carries the full caption, with the poster's handle as an
# anchor immediately before the text and a comment count after it.
IG_EMBED = """<html><body>
<div class="Caption">
  <a class="CaptionUsername" href="/chefsara/">chefsara</a>
  Beans &amp; injera at Bole<br>
  Behind Bole Medhanialem church<br>
  <div class="CaptionComments">View all 171 comments</div>
</div>
</body></html>"""

FB_PAGE_OG = """<html><head>
<meta property="og:title" content="Kuriftu Resort" />
<meta property="og:description" content="Kuriftu Resort. 12,000 followers. Bishoftu, Ethiopia." />
</head></html>"""

TELEGRAM_PREVIEW = """<html><body>
<div class="tgme_widget_message_text">Enkutatash party, 2000 birr entry</div>
</body></html>"""

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


class FakeNetwork:
    """Routes mock responses by URL substring and records what was requested."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, object]] = []
        self.requests: list[httpx.Request] = []

    def add(self, url_fragment: str, response) -> None:
        self.routes.append((url_fragment, response))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for fragment, response in self.routes:
            if fragment in str(request.url):
                if isinstance(response, Exception):
                    raise response
                return response
        return httpx.Response(404)

    def requested_urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def user_agent_for(self, url_fragment: str) -> str | None:
        for request in self.requests:
            if url_fragment in str(request.url):
                return request.headers.get("user-agent")
        return None


@pytest.fixture
def network(monkeypatch) -> FakeNetwork:
    fake = FakeNetwork()
    real_client = httpx.AsyncClient

    def patched(**kwargs):
        kwargs["transport"] = httpx.MockTransport(fake.handle)
        return real_client(**kwargs)

    monkeypatch.setattr(link_extraction.httpx, "AsyncClient", patched)
    return fake


def image_response() -> httpx.Response:
    return httpx.Response(200, content=PNG_1PX, headers={"content-type": "image/png"})


# --------------------------------------------------------------- instagram


@pytest.mark.asyncio
async def test_instagram_merges_embed_caption_with_open_graph_image(network) -> None:
    """The embed page owns the caption, the Open Graph tags own the image."""
    network.add("/embed/captioned/", httpx.Response(200, text=IG_EMBED))
    network.add("instagram.com/p/", httpx.Response(200, text=IG_CRAWLER_OG))
    network.add("preview.jpg", image_response())

    result = await LinkFetchService().fetch(
        "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.INSTAGRAM
    )

    assert "Beans & injera at Bole" in result.text
    assert "Behind Bole Medhanialem church" in result.text
    assert result.image_mime_type == "image/png"
    assert result.image_base64


@pytest.mark.asyncio
async def test_instagram_uses_crawler_user_agent_not_browser(network) -> None:
    """The browser UA is exactly what gets the login wall, so it must not be sent."""
    network.add("/embed/captioned/", httpx.Response(200, text=IG_LOGIN_WALL))
    network.add("instagram.com/p/", httpx.Response(200, text=IG_CRAWLER_OG))
    network.add("preview.jpg", image_response())

    await LinkFetchService().fetch(
        "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.INSTAGRAM
    )

    assert network.user_agent_for("/embed/captioned/") == CRAWLER_USER_AGENT
    assert network.user_agent_for("instagram.com/p/DdbSi57F5a-/") == CRAWLER_USER_AGENT


@pytest.mark.asyncio
async def test_open_graph_caption_is_unwrapped_and_unescaped(network) -> None:
    """The engagement preamble would otherwise be fed to the model as content."""
    network.add("/embed/captioned/", httpx.Response(200, text=IG_LOGIN_WALL))
    network.add("instagram.com/p/", httpx.Response(200, text=IG_CRAWLER_OG))
    network.add("preview.jpg", image_response())

    result = await LinkFetchService().fetch(
        "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.INSTAGRAM
    )

    assert "likes" not in result.text
    assert "comments" not in result.text
    assert "chefsara on September" not in result.text
    assert "&quot;" not in result.text
    assert result.text.startswith("Beans & injera at Bole")


@pytest.mark.asyncio
async def test_embed_caption_does_not_glue_on_the_username(network) -> None:
    """A naive get_text() yields 'chefsaraBeans & injera' — the handle must go."""
    network.add("/embed/captioned/", httpx.Response(200, text=IG_EMBED))
    network.add("instagram.com/p/", httpx.Response(200, text=IG_LOGIN_WALL))

    result = await LinkFetchService().fetch(
        "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.INSTAGRAM
    )

    assert "chefsara" not in result.text
    assert "View all 171 comments" not in result.text
    assert result.text.startswith("Beans & injera at Bole")


@pytest.mark.asyncio
async def test_share_token_is_stripped_when_building_the_embed_url(network) -> None:
    """The ?stkn= share token is harmless on the post URL but breaks the embed path."""
    network.add("/embed/captioned/", httpx.Response(200, text=IG_EMBED))
    network.add("instagram.com/p/", httpx.Response(200, text=IG_LOGIN_WALL))

    await LinkFetchService().fetch(
        "https://www.instagram.com/p/DdbSi57F5a-/?stkn=MThqMDl4djYzd3o1Mg==",
        SourcePlatform.INSTAGRAM,
    )

    embed_url = next(u for u in network.requested_urls() if "/embed/" in u)
    assert embed_url == "https://www.instagram.com/p/DdbSi57F5a-/embed/captioned/"
    assert "stkn" not in embed_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/DdbSi57F5a-/",
        "https://www.instagram.com/reels/DdbSi57F5a-/",
        "https://www.instagram.com/tv/DdbSi57F5a-/",
    ],
)
async def test_reel_and_tv_urls_resolve_to_the_same_shortcode(network, url) -> None:
    network.add("/embed/captioned/", httpx.Response(200, text=IG_EMBED))
    network.add("instagram.com/", httpx.Response(200, text=IG_LOGIN_WALL))

    result = await LinkFetchService().fetch(url, SourcePlatform.INSTAGRAM)

    assert result.text.startswith("Beans & injera at Bole")


@pytest.mark.asyncio
async def test_platform_is_inferred_from_the_host_when_not_declared(network) -> None:
    """A share sheet often gives a bare URL with no platform attached."""
    network.add("/embed/captioned/", httpx.Response(200, text=IG_EMBED))
    network.add("instagram.com/p/", httpx.Response(200, text=IG_LOGIN_WALL))

    result = await LinkFetchService().fetch(
        "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.OTHER
    )

    assert result.text.startswith("Beans & injera at Bole")
    assert network.user_agent_for("/embed/captioned/") == CRAWLER_USER_AGENT


# ---------------------------------------------------------------- facebook


@pytest.mark.asyncio
async def test_facebook_reads_open_graph_with_crawler_user_agent(network) -> None:
    network.add("facebook.com", httpx.Response(200, text=FB_PAGE_OG))

    result = await LinkFetchService().fetch(
        "https://www.facebook.com/kuriftu/", SourcePlatform.FACEBOOK
    )

    assert "Kuriftu Resort" in result.text
    assert network.user_agent_for("facebook.com") == CRAWLER_USER_AGENT


@pytest.mark.asyncio
async def test_facebook_mbasic_host_is_normalized_to_www(network) -> None:
    """mbasic serves a login wall even to crawlers; www does not."""
    network.add("www.facebook.com", httpx.Response(200, text=FB_PAGE_OG))

    await LinkFetchService().fetch(
        "https://mbasic.facebook.com/kuriftu/", SourcePlatform.FACEBOOK
    )

    assert network.requested_urls()[0] == "https://www.facebook.com/kuriftu/"


# ------------------------------------------------------------ failure modes


@pytest.mark.asyncio
async def test_login_walled_post_asks_for_a_screenshot(network) -> None:
    """The exact case the fetcher cannot solve — it must say so in a code."""
    network.add("/embed/captioned/", httpx.Response(200, text=IG_LOGIN_WALL))
    network.add("instagram.com/p/", httpx.Response(200, text=IG_LOGIN_WALL))

    with pytest.raises(LinkFetchError) as exc_info:
        await LinkFetchService().fetch(
            "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.INSTAGRAM
        )

    assert exc_info.value.reason == LinkFailureReason.NEEDS_SCREENSHOT


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 410])
async def test_deleted_post_is_reported_as_private_content(network, status_code) -> None:
    network.add("instagram.com", httpx.Response(status_code))

    with pytest.raises(LinkFetchError) as exc_info:
        await LinkFetchService().fetch(
            "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.INSTAGRAM
        )

    assert exc_info.value.reason == LinkFailureReason.PRIVATE_CONTENT


@pytest.mark.asyncio
async def test_story_links_are_rejected_without_a_request(network) -> None:
    with pytest.raises(LinkFetchError) as exc_info:
        await LinkFetchService().fetch(
            "https://www.instagram.com/stories/chefsara/123/", SourcePlatform.INSTAGRAM
        )

    assert exc_info.value.reason == LinkFailureReason.UNSUPPORTED_URL
    assert network.requests == []


@pytest.mark.asyncio
async def test_transport_failure_is_a_server_fault_not_a_walled_post(network) -> None:
    """Every strategy erroring means our side broke, which is a 502, not a 422."""
    network.add("instagram.com", httpx.ConnectTimeout("timed out"))

    with pytest.raises(LinkFetchError) as exc_info:
        await LinkFetchService().fetch(
            "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.INSTAGRAM
        )

    assert exc_info.value.reason == LinkFailureReason.FETCH_FAILED


@pytest.mark.asyncio
async def test_a_broken_preview_image_still_yields_the_caption(network) -> None:
    network.add("/embed/captioned/", httpx.Response(200, text=IG_EMBED))
    network.add("instagram.com/p/", httpx.Response(200, text=IG_CRAWLER_OG))
    network.add("preview.jpg", httpx.Response(500))

    result = await LinkFetchService().fetch(
        "https://www.instagram.com/p/DdbSi57F5a-/", SourcePlatform.INSTAGRAM
    )

    assert result.text.startswith("Beans & injera at Bole")
    assert result.image_base64 is None


# ------------------------------------------------------------- regressions


@pytest.mark.asyncio
async def test_telegram_still_uses_the_public_preview_mirror(network) -> None:
    network.add("t.me/s/", httpx.Response(200, text=TELEGRAM_PREVIEW))

    result = await LinkFetchService().fetch(
        "https://t.me/addisevents/123", SourcePlatform.TELEGRAM
    )

    assert result.text == "Enkutatash party, 2000 birr entry"
    assert network.requested_urls()[0] == "https://t.me/s/addisevents/123"
    assert network.user_agent_for("t.me") == USER_AGENT


@pytest.mark.asyncio
async def test_tiktok_still_prefers_its_oembed_endpoint(network) -> None:
    network.add(
        "tiktok.com/oembed",
        httpx.Response(
            200,
            json={
                "title": "Best shiro in Addis",
                "thumbnail_url": "https://p16.tiktokcdn.com/thumb.png",
            },
        ),
    )
    network.add("thumb.png", image_response())

    result = await LinkFetchService().fetch(
        "https://www.tiktok.com/@user/video/123", SourcePlatform.TIKTOK
    )

    assert result.text == "Best shiro in Addis"
    assert result.image_mime_type == "image/png"


# ------------------------------------------------------------ route mapping


class StubLinkService:
    def __init__(self, error: LinkFetchError) -> None:
        self._error = error

    async def fetch(self, url: str, platform: SourcePlatform) -> FetchedContent:
        raise self._error


@pytest.mark.parametrize(
    ("reason", "expected_status"),
    [
        (LinkFailureReason.NEEDS_SCREENSHOT, 422),
        (LinkFailureReason.PRIVATE_CONTENT, 422),
        (LinkFailureReason.UNSUPPORTED_URL, 422),
        (LinkFailureReason.FETCH_FAILED, 502),
    ],
)
def test_route_maps_each_reason_to_its_status(reason, expected_status) -> None:
    error = LinkFetchError("nope", reason=reason)
    app.dependency_overrides[get_link_service] = lambda: StubLinkService(error)
    # Never reached, but the real one needs a Gemini key just to construct.
    app.dependency_overrides[get_extraction_service] = lambda: None
    try:
        response = TestClient(app).post(
            "/extract",
            json={
                "source_url": "https://www.instagram.com/p/DdbSi57F5a-/",
                "source_platform": "instagram",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == expected_status
    detail = response.json()["detail"]
    assert detail["reason"] == reason.value
    assert detail["platform"] == "instagram"
    assert detail["message"]
