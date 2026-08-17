"""Unit tests for the pure logic in scripts/bench_images_list.py.

The HTTP measurement loop is exercised against the live local stack (see the
script's docstring); these tests cover the statistics, scenario construction,
and golden-comparison logic that the autoresearch loop's keep/revert decision
depends on.
"""

from pytest import approx

from scripts.bench_images_list import (
    build_scenarios,
    golden_diff,
    percentile,
    summarize,
)


class TestPercentile:
    def test_median_of_odd_count(self) -> None:
        assert percentile([3.0, 1.0, 2.0, 5.0, 4.0], 50) == 3.0

    def test_median_of_even_count_interpolates(self) -> None:
        assert percentile([10.0, 20.0, 30.0, 40.0], 50) == 25.0

    def test_p95_interpolates_between_ranks(self) -> None:
        assert percentile([1.0, 2.0, 3.0, 4.0], 95) == approx(3.85)

    def test_single_value(self) -> None:
        assert percentile([42.0], 95) == 42.0


class TestSummarize:
    def test_reports_min_p50_p95(self) -> None:
        stats = summarize([10.0] * 14 + [100.0])
        assert stats.min_ms == 10.0
        assert stats.p50_ms == 10.0
        assert stats.p95_ms == approx(37.0)
        assert stats.noisy is True

    def test_stable_timings_are_not_noisy(self) -> None:
        stats = summarize([10.0, 11.0, 12.0])
        assert stats.noisy is False


class TestBuildScenarios:
    def test_anonymous_mix_covers_pagination_and_tags(self) -> None:
        scenarios = {s.name: s for s in build_scenarios(authed=False)}
        assert set(scenarios) == {
            "page1",
            "page100",
            "page5000",
            "tag_filter",
            "tag_intersect",
        }

    def test_all_scenarios_use_the_fe_home_page_shape(self) -> None:
        for scenario in build_scenarios(authed=True):
            assert scenario.params["per_page"] == "20"
            assert scenario.params["sort_by"] == "image_id"
            assert scenario.params["sort_order"] == "DESC"

    def test_tag_intersect_requires_all_tags(self) -> None:
        scenarios = {s.name: s for s in build_scenarios(authed=False)}
        assert scenarios["tag_intersect"].params["tags_mode"] == "all"
        assert "," in scenarios["tag_intersect"].params["tags"]

    def test_authed_adds_auth_variants(self) -> None:
        names = {s.name for s in build_scenarios(authed=True)}
        assert {"page1_auth", "page5000_auth"} <= names
        by_name = {s.name: s for s in build_scenarios(authed=True)}
        assert by_name["page1_auth"].needs_auth is True
        assert by_name["page1"].needs_auth is False


class TestGoldenDiff:
    def test_identical_bodies_match(self) -> None:
        body = {"total": 5, "images": [{"image_id": 1}]}
        assert golden_diff(body, {"images": [{"image_id": 1}], "total": 5}) is None

    def test_changed_nested_value_reports_path(self) -> None:
        expected = {"images": [{"image_id": 1, "width": 100}]}
        actual = {"images": [{"image_id": 1, "width": 200}]}
        diff = golden_diff(expected, actual)
        assert diff is not None
        assert "images[0].width" in diff

    def test_missing_items_report_length_mismatch(self) -> None:
        diff = golden_diff({"images": [1, 2, 3]}, {"images": [1, 2]})
        assert diff is not None
        assert "images" in diff
