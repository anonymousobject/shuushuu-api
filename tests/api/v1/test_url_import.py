"""API tests for POST /images/resolve-url (uses the dev fixture resolver — no network)."""

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
