import random

import pytest

from app.schemas.image import FavoriteAttribution, TagSummary
from app.services.recommendations import FavoritePool, _weighted_shuffle, compose_day_list

pytestmark = [pytest.mark.unit]


def _attr(tag_id: int) -> FavoriteAttribution:
    return FavoriteAttribution(tag=TagSummary(tag_id=tag_id, title=f"t{tag_id}", type=2))


def _compose(affinity, pools, seed="1:2026-08-18", **kw):
    defaults = {"feed_size": 500, "fav_share": 0.33, "affinity_decay": 0.997}
    defaults.update(kw)
    return compose_day_list(affinity, pools, random.Random(seed), **defaults)


def test_weighted_shuffle_degenerate_decays():
    ids = list(range(100))
    # decay -> 0: each rank's weight dwarfs every later rank's; the order
    # collapses to the input order (log-space keys make this exact — no underflow)
    assert _weighted_shuffle(ids, random.Random(1), 1e-9) == ids
    # decay = 1.0: uniform shuffle — same membership, and (for this seed) a new order
    shuffled = _weighted_shuffle(ids, random.Random(1), 1.0)
    assert sorted(shuffled) == ids and shuffled != ids


def test_deterministic_per_seed_and_rotates_across_seeds():
    affinity = list(range(1000, 1600))
    pools = [FavoritePool(attribution=_attr(1), image_ids=list(range(2000, 2050)))]
    day1, attr1 = _compose(affinity, pools, seed="7:2026-08-18")
    day1_again, _ = _compose(affinity, pools, seed="7:2026-08-18")
    day2, _ = _compose(affinity, pools, seed="7:2026-08-19")
    assert day1 == day1_again
    assert day1 != day2  # rotation: next day reshuffles
    assert len(day1) == 500 and len(set(day1)) == 500
    assert set(attr1) == set(day1) & set(range(2000, 2050))


def test_fav_share_bounds_each_page():
    affinity = list(range(1000, 2000))
    pools = [FavoritePool(attribution=_attr(1), image_ids=list(range(3000, 3500)))]
    day, attr = _compose(affinity, pools, seed="9:2026-08-18")
    first_page = day[:20]
    fav_on_page = sum(1 for iid in first_page if iid in attr)
    # E[fav] = 6.6 per 20; a fixed seed makes this exact, the range guards refactors
    assert 2 <= fav_on_page <= 13


def test_fav_share_degenerates():
    affinity = [1, 2, 3]
    pools = [FavoritePool(attribution=_attr(1), image_ids=[10, 11])]
    # share 0: favorites only appear after affinity is exhausted
    day, _ = _compose(affinity, pools, fav_share=0.0)
    assert set(day[:3]) == {1, 2, 3}
    assert set(day[3:]) == {10, 11}
    # share 1: favorites drain first
    day, _ = _compose(affinity, pools, fav_share=1.0)
    assert set(day[:2]) == {10, 11}


def test_round_robin_across_favorites():
    pools = [
        FavoritePool(attribution=_attr(1), image_ids=list(range(100, 150))),
        FavoritePool(attribution=_attr(2), image_ids=list(range(200, 250))),
    ]
    day, attr = _compose([], pools, feed_size=10, fav_share=1.0)
    kinds = [attr[iid].tag.tag_id for iid in day]
    assert kinds == [1, 2, 1, 2, 1, 2, 1, 2, 1, 2]


def test_duplicate_across_pools_goes_to_lowest_position():
    pools = [
        FavoritePool(attribution=_attr(1), image_ids=[10, 11]),
        FavoritePool(attribution=_attr(2), image_ids=[10, 12]),
    ]
    day, attr = _compose([], pools, fav_share=1.0)
    assert sorted(day) == [10, 11, 12]
    assert attr[10].tag.tag_id == 1


def test_cross_pool_overlap_served_once_with_favorite_attribution():
    pools = [FavoritePool(attribution=_attr(1), image_ids=[10])]
    day, attr = _compose([10, 20], pools)
    assert sorted(day) == [10, 20]
    assert day.count(10) == 1
    assert attr[10].tag.tag_id == 1 and 20 not in attr


def test_feed_size_caps_and_short_pools_drain():
    day, _ = _compose([1, 2], [FavoritePool(attribution=_attr(1), image_ids=[10])], feed_size=500)
    assert sorted(day) == [1, 2, 10]
    day, _ = _compose(list(range(100)), [], feed_size=10)
    assert len(day) == 10
