"""Unit tests for app.services.feeds pure helpers."""

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.config import TagType
from app.models.image import Images
from app.models.tag import Tags
from app.models.user import Users
from app.schemas.image import ImageDetailedResponse
from app.services.feeds import (
    FeedMeta,
    build_atom_feed,
    compose_entry_title,
    compute_feed_etag,
    mime_type_for_ext,
    newest_timestamp,
)


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


ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _feed_meta() -> FeedMeta:
    return FeedMeta(
        feed_id="tag:e-shuushuu.net,2005:feed:images",
        title="Shuushuu — latest images",
        self_url="https://e-shuushuu.net/api/v1/images.atom",
        alternate_url="https://e-shuushuu.net/",
    )


class TestBuildAtomFeedEmpty:
    def test_empty_feed_is_valid_atom(self):
        xml = build_atom_feed(_feed_meta(), entries=[])
        root = ET.fromstring(xml)
        assert root.tag == f"{ATOM_NS}feed"

    def test_empty_feed_has_id_title_self_link_updated(self):
        xml = build_atom_feed(_feed_meta(), entries=[])
        root = ET.fromstring(xml)
        assert root.find(f"{ATOM_NS}id").text == (
            "tag:e-shuushuu.net,2005:feed:images"
        )
        assert root.find(f"{ATOM_NS}title").text == "Shuushuu — latest images"
        self_link = root.find(f"{ATOM_NS}link[@rel='self']")
        assert self_link is not None
        assert self_link.get("href") == (
            "https://e-shuushuu.net/api/v1/images.atom"
        )
        assert root.find(f"{ATOM_NS}updated") is not None

    def test_empty_feed_has_no_entries(self):
        xml = build_atom_feed(_feed_meta(), entries=[])
        root = ET.fromstring(xml)
        assert root.findall(f"{ATOM_NS}entry") == []


def _orm_image(
    image_id: int = 42,
    filename: str = "abc42",
    ext: str = "png",
    caption: str | None = None,
    filesize: int = 1024,
    date_added: datetime | None = None,
    username: str | None = "alice",
    tags: list[Tags] | None = None,
) -> Images:
    user = None
    if username is not None:
        user = Users(
            user_id=1,
            username=username,
            password="x",
            password_type="bcrypt",
            salt="",
            email="a@b.c",
            active=1,
        )

    img = Images(
        image_id=image_id,
        filename=filename,
        ext=ext,
        caption=caption or "",
        filesize=filesize,
        user_id=1,
        status=1,
        date_added=date_added or datetime(2026, 4, 24, 10, 0, 0, tzinfo=UTC),
    )
    # Bypass SQLAlchemy instrumentation — we're building a plain object graph
    # for from_db_model to read, not persisting through a session.
    img.__dict__["user"] = user
    img.__dict__["tag_links"] = [SimpleNamespace(tag=t) for t in (tags or [])]
    return img


def _entry(**overrides) -> ImageDetailedResponse:
    return ImageDetailedResponse.from_db_model(_orm_image(**overrides))


class TestBuildAtomFeedWithEntries:
    def test_entry_has_tag_uri_id(self):
        xml = build_atom_feed(_feed_meta(), entries=[_entry(image_id=42)])
        root = ET.fromstring(xml)
        entry_node = root.find(f"{ATOM_NS}entry")
        assert entry_node.find(f"{ATOM_NS}id").text == (
            "tag:e-shuushuu.net,2005:image:42"
        )

    def test_entry_alternate_link_points_to_detail_page(self):
        xml = build_atom_feed(_feed_meta(), entries=[_entry(image_id=42)])
        root = ET.fromstring(xml)
        alt = root.find(f"{ATOM_NS}entry/{ATOM_NS}link[@rel='alternate']")
        assert alt is not None
        assert alt.get("href", "").endswith("/images/42")

    def test_entry_enclosure_has_mime_and_length(self):
        xml = build_atom_feed(
            _feed_meta(), entries=[_entry(image_id=42, ext="png", filesize=1024)]
        )
        root = ET.fromstring(xml)
        enc = root.find(f"{ATOM_NS}entry/{ATOM_NS}link[@rel='enclosure']")
        assert enc is not None
        assert enc.get("type") == "image/png"
        assert enc.get("length") == "1024"

    def test_entry_author_is_uploader(self):
        xml = build_atom_feed(_feed_meta(), entries=[_entry(username="alice")])
        root = ET.fromstring(xml)
        assert (
            root.find(f"{ATOM_NS}entry/{ATOM_NS}author/{ATOM_NS}name").text
            == "alice"
        )

    def test_entry_author_falls_back_for_deleted_uploader(self):
        xml = build_atom_feed(_feed_meta(), entries=[_entry(username=None)])
        root = ET.fromstring(xml)
        assert (
            root.find(f"{ATOM_NS}entry/{ATOM_NS}author/{ATOM_NS}name").text
            == "[deleted user]"
        )

    def test_entry_content_is_html_escaped_caption(self):
        xml = build_atom_feed(
            _feed_meta(),
            entries=[_entry(caption="a <b> caption & stuff")],
        )
        root = ET.fromstring(xml)
        content = root.find(f"{ATOM_NS}entry/{ATOM_NS}content")
        assert content is not None
        assert content.get("type") == "html"
        assert content.text == "a &lt;b&gt; caption &amp; stuff"

    def test_entry_with_no_caption_omits_content(self):
        xml = build_atom_feed(_feed_meta(), entries=[_entry(caption=None)])
        root = ET.fromstring(xml)
        assert root.find(f"{ATOM_NS}entry/{ATOM_NS}content") is None

    def test_entry_categories_carry_scheme(self):
        tags = [
            Tags(tag_id=1, title="hatsune miku", type=TagType.CHARACTER, usage_count=500),
            Tags(tag_id=2, title="vocaloid", type=TagType.SOURCE, usage_count=1000),
        ]
        xml = build_atom_feed(_feed_meta(), entries=[_entry(tags=tags)])
        root = ET.fromstring(xml)
        cats = root.findall(f"{ATOM_NS}entry/{ATOM_NS}category")
        assert len(cats) == 2
        by_term = {c.get("term"): c.get("scheme") for c in cats}
        assert by_term["hatsune miku"].endswith("/tag-type/Character")
        assert by_term["vocaloid"].endswith("/tag-type/Source")
