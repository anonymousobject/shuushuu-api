"""Row builders shared by the ml_probe DB tests."""

from app.config import ImageStatus, TagType
from app.models.image import Images
from app.models.ml_tag_suggestion import MlTagSuggestions
from app.models.tag import Tags
from app.models.tag_link import TagLinks
from app.models.user import Users


async def make_user(db_session, suffix):
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


async def make_tag(db_session, title, *, type_=TagType.THEME, alias_of=None, parent=None):
    tag = Tags(title=title, type=type_, alias_of=alias_of, inheritedfrom_id=parent)
    db_session.add(tag)
    await db_session.flush()
    return tag


async def make_image(db_session, user, suffix, *, status=ImageStatus.ACTIVE):
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


async def link(db_session, image, tag):
    db_session.add(TagLinks(image_id=image.image_id, tag_id=tag.tag_id))
    await db_session.flush()


async def suggest(db_session, image, tag, status):
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


async def graph_fixture(db_session):
    """parent <- child <- alias, plus a character tag that must never become a column."""
    parent = await make_tag(db_session, "probe parent")
    child = await make_tag(db_session, "probe child", parent=parent.tag_id)
    alias = await make_tag(db_session, "probe child alias", alias_of=child.tag_id)
    character = await make_tag(db_session, "probe character", type_=TagType.CHARACTER)
    return parent, child, alias, character
