"""Unit tests for RedactedStr — a string subclass whose repr() hides its value.

Used to wrap secrets passed as arq job arguments. arq logs job args via repr()
at INFO level when picking up a job; without this wrapper the raw token would
land in stdout and be ingested into Loki for the configured retention.
"""

import pickle

import pytest

from app.core.security import RedactedStr


@pytest.mark.unit
class TestRedactedStr:
    def test_string_value_is_preserved(self) -> None:
        token = "abc123-secret-token"
        wrapped = RedactedStr(token)
        assert str(wrapped) == token
        assert wrapped == token
        assert len(wrapped) == len(token)

    def test_repr_does_not_leak_value(self) -> None:
        token = "abc123-secret-token"
        wrapped = RedactedStr(token)
        rendered = repr(wrapped)
        assert token not in rendered
        assert "REDACTED" in rendered

    def test_format_string_does_not_leak_value(self) -> None:
        token = "abc123-secret-token"
        wrapped = RedactedStr(token)
        # arq's job-start log uses %r-style formatting on each kwarg value.
        # This is the actual leak path we're closing.
        assert token not in f"token={wrapped!r}"
        assert token not in "token=%r" % wrapped

    def test_str_format_still_exposes_value(self) -> None:
        # str() must round-trip the actual value — the email-sending code
        # needs the real token. Only repr() is redacted.
        token = "abc123-secret-token"
        wrapped = RedactedStr(token)
        assert f"{wrapped}" == token
        assert "%s" % wrapped == token

    def test_survives_pickle_round_trip(self) -> None:
        # arq serialises job args via pickle. The wrapper must round-trip
        # so the worker receives a usable token, and repr() must still
        # redact after unpickling.
        token = "abc123-secret-token"
        restored = pickle.loads(pickle.dumps(RedactedStr(token)))
        assert isinstance(restored, RedactedStr)
        assert str(restored) == token
        assert token not in repr(restored)

    def test_subclass_of_str(self) -> None:
        # Code that type-checks `isinstance(x, str)` (e.g. validators) must
        # still accept a RedactedStr.
        assert isinstance(RedactedStr("x"), str)
