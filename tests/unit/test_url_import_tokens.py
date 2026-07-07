"""Tests for HMAC-signed external-fetch tokens."""

import json

import pytest

from app.services.url_import.tokens import (
    InvalidTokenError,
    _b64encode,
    _sign,
    mint_token,
    verify_token,
)


def test_roundtrip_preserves_url_and_headers():
    token = mint_token(
        "https://i.pximg.net/img-original/img/x_p0.png",
        {"Referer": "https://www.pixiv.net/"},
    )
    ref = verify_token(token)
    assert ref.url == "https://i.pximg.net/img-original/img/x_p0.png"
    assert ref.headers == {"Referer": "https://www.pixiv.net/"}


def test_default_headers_empty():
    ref = verify_token(mint_token("https://example.test/a.png"))
    assert ref.headers == {}


def test_tampered_payload_rejected():
    token = mint_token("https://example.test/a.png")
    payload, sig = token.split(".")
    flipped = ("A" if payload[0] != "A" else "B") + payload[1:]
    with pytest.raises(InvalidTokenError):
        verify_token(f"{flipped}.{sig}")


def test_tampered_signature_rejected():
    token = mint_token("https://example.test/a.png")
    payload, sig = token.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(InvalidTokenError):
        verify_token(f"{payload}.{flipped}")


def test_expired_token_rejected():
    token = mint_token("https://example.test/a.png", ttl_seconds=-1)
    with pytest.raises(InvalidTokenError):
        verify_token(token)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "!!!.???"])
def test_malformed_tokens_rejected(garbage):
    with pytest.raises(InvalidTokenError):
        verify_token(garbage)


def test_valid_signature_payload_missing_expiry_field_rejected():
    # Hand-craft a payload with a valid signature but no "e" (expires_at) key,
    # simulating a payload shape that predates or otherwise omits the field.
    payload = json.dumps(
        {"u": "https://example.test/a.png", "h": {}},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    token = f"{_b64encode(payload)}.{_b64encode(_sign(payload))}"
    with pytest.raises(InvalidTokenError):
        verify_token(token)
