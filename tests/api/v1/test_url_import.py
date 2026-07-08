"""API tests for POST /images/resolve-url (uses the dev fixture resolver — no network)."""

import io

import pytest

from app.core.security import create_access_token
from app.models.user import Users

FIXTURE_SINGLE = "https://urlimport-fixture.local/post/single"
FIXTURE_MULTI = "https://urlimport-fixture.local/post/multi"


@pytest.fixture
async def resolve_user(db_session):
    user = Users(
        username="urlresolver",
        password="hashed_password_here",
        password_type="bcrypt",
        salt="saltsalt12345678",
        email="urlresolver@example.com",
        active=1,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def resolve_client(client, resolve_user):
    access_token = create_access_token(resolve_user.id)
    client.headers.update({"Authorization": f"Bearer {access_token}"})
    return client


class TestResolveUrl:
    async def test_requires_auth(self, client):
        response = await client.post("/api/v1/images/resolve-url", json={"url": FIXTURE_SINGLE})
        assert response.status_code == 401

    async def test_unsupported_site_lists_supported(self, resolve_client):
        response = await resolve_client.post(
            "/api/v1/images/resolve-url", json={"url": "https://example.com/whatever"}
        )
        assert response.status_code == 422
        assert "pixiv" in response.json()["detail"]

    async def test_non_http_url_rejected(self, resolve_client):
        response = await resolve_client.post(
            "/api/v1/images/resolve-url", json={"url": "ftp://example.com/x"}
        )
        assert response.status_code == 422

    async def test_resolves_fixture_single(self, resolve_client):
        response = await resolve_client.post(
            "/api/v1/images/resolve-url", json={"url": FIXTURE_SINGLE}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["site"] == "fixture"
        assert data["canonical_url"] == FIXTURE_SINGLE
        assert data["title"] == "Fixture post"
        assert data["artist_name"] == "Fixture Artist"
        assert len(data["images"]) == 1
        assert data["images"][0]["token"]
        assert data["images"][0]["thumb_token"]

    async def test_resolves_fixture_multi(self, resolve_client):
        response = await resolve_client.post(
            "/api/v1/images/resolve-url", json={"url": FIXTURE_MULTI}
        )
        assert response.status_code == 200
        assert len(response.json()["images"]) == 3

    async def test_tokens_are_verifiable(self, resolve_client):
        from app.services.url_import.tokens import verify_token

        response = await resolve_client.post(
            "/api/v1/images/resolve-url", json={"url": FIXTURE_SINGLE}
        )
        token = response.json()["images"][0]["token"]
        ref = verify_token(token)
        assert "/api/v1/images/url-import-fixture/" in ref.url


class TestFetchExternal:
    def _factory(self, handler):
        import httpx

        def make(timeout):
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

        return make

    async def test_requires_auth(self, client):
        response = await client.get("/api/v1/images/fetch-external", params={"token": "x"})
        assert response.status_code == 401

    async def test_invalid_token_403(self, resolve_client):
        response = await resolve_client.get(
            "/api/v1/images/fetch-external", params={"token": "not-a-token"}
        )
        assert response.status_code == 403

    async def test_streams_image_with_baked_headers(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from app.services.url_import.tokens import mint_token

        seen = {}

        def handler(request):
            seen["referer"] = request.headers.get("referer")
            return httpx.Response(
                200, content=b"\x89PNG-fake-bytes", headers={"content-type": "image/png"}
            )

        token = mint_token(
            "https://i.pximg.net/img-original/img/x_p0.png",
            {"Referer": "https://www.pixiv.net/"},
        )
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == b"\x89PNG-fake-bytes"
        assert seen["referer"] == "https://www.pixiv.net/"

    async def test_non_image_content_type_502(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from app.services.url_import.tokens import mint_token

        def handler(request):
            return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})

        token = mint_token("https://example.test/a.png")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 502

    async def test_oversize_content_length_413(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from app.config import settings
        from app.services.url_import.tokens import mint_token

        def handler(request):
            return httpx.Response(
                200,
                content=b"x",
                headers={
                    "content-type": "image/png",
                    "content-length": str(settings.MAX_IMAGE_SIZE + 1),
                },
            )

        token = mint_token("https://example.test/big.png")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 413

    async def test_malformed_content_length_ignored(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from app.services.url_import.tokens import mint_token

        def handler(request):
            return httpx.Response(
                200,
                content=b"\x89PNG-fake-bytes",
                headers={"content-type": "image/png", "content-length": "banana"},
            )

        token = mint_token("https://example.test/small.png")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 200
        assert response.content == b"\x89PNG-fake-bytes"

    async def test_oversize_streaming_without_content_length_413(
        self, resolve_client, monkeypatch
    ):
        import httpx
        from unittest.mock import patch

        from app.config import settings
        from app.services.url_import.tokens import mint_token

        monkeypatch.setattr(settings, "MAX_IMAGE_SIZE", 1024)

        def handler(request):
            response = httpx.Response(
                200,
                stream=httpx.ByteStream(b"x" * 4096),
                headers={"content-type": "image/png"},
            )
            assert "content-length" not in response.headers
            return response

        token = mint_token("https://example.test/no-content-length.png")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 413

    async def test_webp_is_transcoded_to_png(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from PIL import Image as PILImage

        from app.services.url_import.tokens import mint_token

        buffer = io.BytesIO()
        PILImage.new("RGB", (8, 8), color=(200, 50, 100)).save(buffer, format="WEBP")
        webp_bytes = buffer.getvalue()

        def handler(request):
            return httpx.Response(
                200, content=webp_bytes, headers={"content-type": "image/webp"}
            )

        token = mint_token("https://cdn.bsky.app/img/feed_fullsize/plain/x/abc")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_webp_transcoded_despite_lying_content_type(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from PIL import Image as PILImage

        from app.services.url_import.tokens import mint_token

        buffer = io.BytesIO()
        PILImage.new("RGB", (8, 8), color=(10, 20, 30)).save(buffer, format="WEBP")
        webp_bytes = buffer.getvalue()

        def handler(request):
            # Upstream claims jpeg but actually serves webp bytes -- the
            # proxy must sniff the magic, not trust the header.
            return httpx.Response(
                200, content=webp_bytes, headers={"content-type": "image/jpeg"}
            )

        token = mint_token("https://example.test/lying.jpg")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_non_webp_passes_through_unchanged(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from app.services.url_import.tokens import mint_token

        real_png = b"\x89PNG\r\n\x1a\n" + b"not-really-png-data-but-not-webp-either"

        def handler(request):
            return httpx.Response(
                200, content=real_png, headers={"content-type": "image/png"}
            )

        token = mint_token("https://example.test/real.png")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == real_png

    async def test_animated_webp_rejected(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from PIL import Image as PILImage

        from app.services.url_import.tokens import mint_token

        frame1 = PILImage.new("RGB", (8, 8), color=(255, 0, 0))
        frame2 = PILImage.new("RGB", (8, 8), color=(0, 255, 0))
        buffer = io.BytesIO()
        frame1.save(
            buffer, format="WEBP", save_all=True, append_images=[frame2], duration=100, loop=0
        )
        webp_bytes = buffer.getvalue()

        def handler(request):
            return httpx.Response(
                200, content=webp_bytes, headers={"content-type": "image/webp"}
            )

        token = mint_token("https://example.test/animated.webp")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 422
        assert "Animated" in response.json()["detail"]

    async def test_transcoded_png_too_large_413(self, resolve_client, monkeypatch):
        import os

        import httpx
        from unittest.mock import patch

        from app.config import settings
        from PIL import Image as PILImage

        from app.services.url_import.tokens import mint_token

        # Random noise barely compresses under lossy webp (quality=1) but
        # decodes back to full incompressible noise, so its PNG re-encode is
        # ~15x bigger than the webp bytes -- empirically verified via a
        # throwaway script (webp ~800B, PNG roundtrip ~12KB for 64x64).
        # A cap between those sizes exercises the *post-transcode* size
        # check specifically, distinct from the pre-existing raw-download
        # size cap covered by test_oversize_streaming_without_content_length_413.
        monkeypatch.setattr(settings, "MAX_IMAGE_SIZE", 2000)
        image = PILImage.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3))
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=1)
        webp_bytes = buffer.getvalue()
        assert len(webp_bytes) < 2000  # sanity: raw bytes must clear the cap

        def handler(request):
            return httpx.Response(
                200, content=webp_bytes, headers={"content-type": "image/webp"}
            )

        token = mint_token("https://example.test/noise.webp")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 413

    async def test_webp_decode_failure_502(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from app.services.url_import.tokens import mint_token

        # Passes the RIFF/WEBP magic sniff (and the content-type header)
        # so the proxy routes it into the transcode branch, but the payload
        # isn't a real webp bitstream -- PIL can't decode it.
        garbage = b"RIFFxxxxWEBPnot-a-real-webp"

        def handler(request):
            return httpx.Response(
                200, content=garbage, headers={"content-type": "image/webp"}
            )

        token = mint_token("https://example.test/garbage.webp")
        with patch("app.api.v1.url_import._make_http_client", self._factory(handler)):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 502
        assert "unreadable" in response.json()["detail"]

    async def test_upstream_500_becomes_502(self, resolve_client):
        import httpx
        from unittest.mock import patch

        from app.services.url_import.tokens import mint_token

        token = mint_token("https://example.test/gone.png")
        with patch(
            "app.api.v1.url_import._make_http_client",
            self._factory(lambda r: httpx.Response(500)),
        ):
            response = await resolve_client.get(
                "/api/v1/images/fetch-external", params={"token": token}
            )
        assert response.status_code == 502


class TestFixtureImageEndpoint:
    async def test_serves_unique_pngs(self, client):
        first = await client.get("/api/v1/images/url-import-fixture/single-0.png")
        second = await client.get("/api/v1/images/url-import-fixture/single-0.png")
        assert first.status_code == 200
        assert first.headers["content-type"] == "image/png"
        assert first.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert first.content != second.content  # unique MD5 per request

    async def test_serves_webp_for_dot_webp_name(self, client):
        response = await client.get("/api/v1/images/url-import-fixture/webp-0.webp")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        assert response.content[0:4] == b"RIFF"
        assert response.content[8:12] == b"WEBP"
