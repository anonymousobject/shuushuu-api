"""Label building against the real test database (app/services/ml_probe/labels.py)."""

import numpy as np

from app.config import ImageStatus, TagType
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users
from app.services.ml_probe.labels import (
    APPROVED,
    LINKED,
    NONE,
    PENDING,
    build_label_set,
    load_theme_tag_graph,
)


async def _make_user(db_session, suffix):
    user = Users(
        username=f"probe_{suffix}",
        email=f"probe_{suffix}@example.com",
        password="hashed",
        password_type="bcrypt",
        salt="testsalt12345678",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_tag(db_session, title, *, type_=TagType.THEME, alias_of=None, parent=None):
    tag = Tags(title=title, type=type_, alias_of=alias_of, inheritedfrom_id=parent)
    db_session.add(tag)
    await db_session.flush()
    return tag


async def _make_image(db_session, user, suffix, *, status=ImageStatus.ACTIVE):
    image = Images(
        filename=f"2024-01-01-probe{suffix}",
        ext="jpg",
        user_id=user.user_id,
        md5_hash=f"probe_hash_{suffix}",
        filesize=1,
        width=1,
        height=1,
        status=status,
    )
    db_session.add(image)
    await db_session.flush()
    return image


async def _link(db_session, image, tag):
    db_session.add(TagLinks(image_id=image.image_id, tag_id=tag.tag_id))
    await db_session.flush()


async def _suggest(db_session, image, tag, status):
    db_session.add(
        MlTagSuggestions(
            image_id=image.image_id,
            tag_id=tag.tag_id,
            confidence=0.9,
            model_version="probe-test",
            status=status,
        )
    )
    await db_session.flush()


async def _graph_fixture(db_session):
    parent = await _make_tag(db_session, "probe parent")
    child = await _make_tag(db_session, "probe child", parent=parent.tag_id)
    alias = await _make_tag(db_session, "probe child alias", alias_of=child.tag_id)
    character = await _make_tag(db_session, "probe character", type_=TagType.CHARACTER)
    return parent, child, alias, character


async def test_load_theme_tag_graph_collapses_aliases_and_parents(db_session):
    parent, child, alias, character = await _graph_fixture(db_session)

    graph = await load_theme_tag_graph(db_session)

    tag_ids = graph.tag_ids.tolist()
    assert tag_ids == sorted(tag_ids), "columns are in ascending tag id order"
    assert parent.tag_id in tag_ids and child.tag_id in tag_ids
    assert alias.tag_id not in tag_ids, "aliases are not columns"
    assert character.tag_id not in tag_ids, "non-theme tags are not columns"
    assert graph.column_of[alias.tag_id] == graph.column_of[child.tag_id]
    assert graph.parent_index[graph.column_of[child.tag_id]] == graph.column_of[parent.tag_id]
    assert graph.parent_index[graph.column_of[parent.tag_id]] == -1
    assert graph.titles[graph.column_of[parent.tag_id]] == "probe parent"
    assert len(graph.titles) == len(tag_ids) == len(graph.parent_index)


async def test_build_label_set_provenance_rules(db_session):
    parent, child, alias, _character = await _graph_fixture(db_session)
    user = await _make_user(db_session, "labels")
    images = {i: await _make_image(db_session, user, i) for i in range(1, 7)}
    images[5] = await _make_image(db_session, user, 50, status=ImageStatus.DEACTIVATED)

    await _link(db_session, images[1], child)
    await _suggest(db_session, images[1], child, "approved")  # link via approved suggestion
    await _link(db_session, images[2], parent)
    await _suggest(db_session, images[2], parent, "pending")  # the link wins
    await _suggest(db_session, images[3], parent, "pending")  # no links: dropped
    await _suggest(db_session, images[3], child, "rejected")
    await _link(db_session, images[4], alias)  # alias collapses onto the child column
    await _link(db_session, images[5], child)  # inactive image: absent
    await _link(db_session, images[6], child)
    await _suggest(db_session, images[6], parent, "pending")  # pending on a parent made positive

    graph = await load_theme_tag_graph(db_session)
    labels = await build_label_set(db_session, graph)

    ids = labels.image_ids.tolist()
    assert ids == sorted(ids)
    present = {n: images[n].image_id in ids for n in images}
    assert present == {1: True, 2: True, 3: False, 4: True, 5: False, 6: True}

    row = {n: ids.index(images[n].image_id) for n in (1, 2, 4, 6)}
    col_parent, col_child = graph.column_of[parent.tag_id], graph.column_of[child.tag_id]
    assert labels.provenance[row[1], col_child] == APPROVED
    assert labels.provenance[row[1], col_parent] == NONE
    assert labels.provenance[row[2], col_parent] == LINKED
    assert labels.provenance[row[4], col_child] == LINKED
    assert labels.provenance[row[6], col_child] == LINKED
    assert labels.provenance[row[6], col_parent] == PENDING

    positives = labels.positives()
    assert positives[row[1], col_parent], "closure: approved child implies parent"
    assert labels.approved_mask()[row[1], col_parent]
    assert not labels.pending_mask()[row[6], col_parent], "pending under a linked child is positive"
    assert labels.tag_ids.tolist() == graph.tag_ids.tolist()
    assert labels.provenance.dtype == np.uint8
