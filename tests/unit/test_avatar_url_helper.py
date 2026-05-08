"""Tests for avatar URL helper and content-type derivation."""

import pytest

from app.config import settings
from app.services.avatar import _avatar_content_type, avatar_url


def test_avatar_url_returns_none_for_empty():
    assert avatar_url("", in_r2=True) is None
    assert avatar_url(None, in_r2=True) is None


def test_avatar_url_local_when_r2_disabled(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", False)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    assert avatar_url("abc.png", in_r2=True) == "http://local.test/images/avatars/abc.png"


def test_avatar_url_local_when_bit_false(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    assert avatar_url("abc.png", in_r2=False) == "http://local.test/images/avatars/abc.png"


def test_avatar_url_cdn_when_both_true(monkeypatch):
    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    assert avatar_url("abc.png", in_r2=True) == "https://cdn.test/avatars/abc.png"


@pytest.mark.parametrize(
    "ext,expected",
    [
        ("png", "image/png"),
        ("jpg", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        ("gif", "image/gif"),
    ],
)
def test_avatar_content_type(ext, expected):
    assert _avatar_content_type(ext) == expected


def test_user_response_avatar_url_uses_helper(monkeypatch):
    from app.schemas.user import UserResponse

    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    # Use model_construct to bypass deep validation for fields we don't care about
    data = {
        "user_id": 1,
        "username": "alice",
        "avatar": "abc.png",
        "avatar_in_r2": True,
    }
    resp = UserResponse.model_construct(**data)
    assert resp.avatar_url == "https://cdn.test/avatars/abc.png"


def test_user_summary_avatar_url_uses_helper(monkeypatch):
    from app.schemas.common import UserSummary

    monkeypatch.setattr(settings, "R2_ENABLED", True)
    monkeypatch.setattr(settings, "R2_PUBLIC_CDN_URL", "https://cdn.test")
    monkeypatch.setattr(settings, "IMAGE_BASE_URL", "http://local.test")
    summary = UserSummary(user_id=1, username="alice", avatar="abc.png", avatar_in_r2=True)
    assert summary.avatar_url == "https://cdn.test/avatars/abc.png"
