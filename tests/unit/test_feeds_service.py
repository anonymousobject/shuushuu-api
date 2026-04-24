"""Unit tests for app.services.feeds pure helpers."""

from datetime import UTC, datetime

import pytest

from app.config import TagType
from app.services.feeds import compose_entry_title, compute_feed_etag, mime_type_for_ext, newest_timestamp


class TestMimeTypeForExt:
    @pytest.mark.parametrize(
        "ext,expected",
        [
            ("jpg", "image/jpeg"),
            ("jpeg", "image/jpeg"),
            ("JPG", "image/jpeg"),
            ("png", "image/png"),
            ("gif", "image/gif"),
            ("webp", "image/webp"),
        ],
    )
    def test_known_extensions(self, ext: str, expected: str):
        assert mime_type_for_ext(ext) == expected

    def test_unknown_extension_falls_back_to_octet_stream(self):
        assert mime_type_for_ext("xyz") == "application/octet-stream"

    def test_empty_string_falls_back(self):
        assert mime_type_for_ext("") == "application/octet-stream"


def _tag(tag_id: int, title: str, type_: int, usage_count: int):
    """Lightweight stand-in for TagSummary — matches its attribute names."""
    from types import SimpleNamespace

    return SimpleNamespace(
        tag_id=tag_id,
        tag=title,      # TagSummary's alias for Tags.title
        type_id=type_,  # TagSummary's alias for Tags.type
        usage_count=usage_count,
    )


class TestComposeEntryTitle:
    def test_all_three_sections(self):
        tags = [
            _tag(1, "hatsune miku", TagType.CHARACTER, 500),
            _tag(2, "kagamine rin", TagType.CHARACTER, 100),
            _tag(3, "vocaloid", TagType.SOURCE, 1000),
            _tag(4, "cutesexyrobutts", TagType.ARTIST, 50),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "hatsune miku (vocaloid) by cutesexyrobutts"
        )

    def test_no_character_tags(self):
        tags = [
            _tag(3, "vocaloid", TagType.SOURCE, 1000),
            _tag(4, "cutesexyrobutts", TagType.ARTIST, 50),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "(vocaloid) by cutesexyrobutts"
        )

    def test_no_source_tags(self):
        tags = [
            _tag(1, "hatsune miku", TagType.CHARACTER, 500),
            _tag(4, "cutesexyrobutts", TagType.ARTIST, 50),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "hatsune miku by cutesexyrobutts"
        )

    def test_no_artist_tags(self):
        tags = [
            _tag(1, "hatsune miku", TagType.CHARACTER, 500),
            _tag(3, "vocaloid", TagType.SOURCE, 1000),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "hatsune miku (vocaloid)"
        )

    def test_no_relevant_tags_falls_back(self):
        tags = [_tag(5, "solo", TagType.THEME, 999)]
        assert compose_entry_title(image_id=42, tags=tags) == "Image #42"

    def test_no_tags_at_all_falls_back(self):
        assert compose_entry_title(image_id=42, tags=[]) == "Image #42"

    def test_picks_highest_usage_count_per_category(self):
        tags = [
            _tag(1, "low usage char", TagType.CHARACTER, 1),
            _tag(2, "high usage char", TagType.CHARACTER, 9999),
            _tag(3, "low usage artist", TagType.ARTIST, 1),
            _tag(4, "high usage artist", TagType.ARTIST, 9999),
        ]
        assert (
            compose_entry_title(image_id=42, tags=tags)
            == "high usage char by high usage artist"
        )


class TestComputeFeedEtag:
    def _sentinel(self):
        return [
            (100, datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)),
            (99, datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)),
        ]

    def test_returns_weak_etag(self):
        etag = compute_feed_etag(self._sentinel())
        assert etag.startswith('W/"')
        assert etag.endswith('"')

    def test_deterministic_for_same_input(self):
        s = self._sentinel()
        assert compute_feed_etag(s) == compute_feed_etag(s)

    def test_changes_when_image_id_changes(self):
        a = self._sentinel()
        b = self._sentinel()
        b[0] = (101, b[0][1])
        assert compute_feed_etag(a) != compute_feed_etag(b)

    def test_changes_when_timestamp_changes(self):
        a = self._sentinel()
        b = self._sentinel()
        b[0] = (b[0][0], datetime(2026, 4, 24, 13, 0, 0, tzinfo=UTC))
        assert compute_feed_etag(a) != compute_feed_etag(b)

    def test_empty_sentinel_still_returns_valid_etag(self):
        etag = compute_feed_etag([])
        assert etag.startswith('W/"') and etag.endswith('"')

    def test_ignores_rows_with_null_date(self):
        a = [(100, datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC))]
        b = [
            (100, datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)),
            (99, None),
        ]
        assert compute_feed_etag(a) == compute_feed_etag(b)


class TestNewestTimestamp:
    def test_picks_newest_from_sentinel(self):
        sentinel = [
            (100, datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)),
            (99, datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)),
        ]
        assert newest_timestamp(sentinel) == datetime(
            2026, 4, 24, 12, 0, 0, tzinfo=UTC
        )

    def test_empty_returns_none(self):
        assert newest_timestamp([]) is None

    def test_skips_none_timestamps(self):
        sentinel = [
            (100, None),
            (99, datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)),
        ]
        assert newest_timestamp(sentinel) == datetime(
            2026, 4, 23, 12, 0, 0, tzinfo=UTC
        )

    def test_all_none_returns_none(self):
        assert newest_timestamp([(100, None), (99, None)]) is None

    def test_result_is_floored_to_whole_seconds(self):
        sentinel = [
            (100, datetime(2026, 4, 24, 12, 0, 0, 999_999, tzinfo=UTC)),
        ]
        result = newest_timestamp(sentinel)
        assert result is not None
        assert result.microsecond == 0
