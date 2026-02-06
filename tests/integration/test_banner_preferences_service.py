"""Integration tests for banner preference service functions."""

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.misc import Banners, BannerSize, BannerTheme, UserBannerPins, UserBannerPreferences
from app.schemas.banner import BannerPreferencesResponse


@pytest.mark.integration
class TestGetUserPreferences:
    async def test_returns_defaults_when_no_row(self, db_session: AsyncSession):
        from app.services.banner import get_user_preferences

        result = await get_user_preferences(user_id=1, db=db_session)
        assert result.preferred_size == BannerSize.small
        assert result.pins == []

    async def test_returns_stored_preferences(self, db_session: AsyncSession):
        from app.services.banner import get_user_preferences

        prefs = UserBannerPreferences(user_id=1, preferred_size=BannerSize.large)
        db_session.add(prefs)
        await db_session.commit()

        result = await get_user_preferences(user_id=1, db=db_session)
        assert result.preferred_size == BannerSize.large

    async def test_returns_pins_with_banner_data(self, db_session: AsyncSession):
        from app.services.banner import get_user_preferences

        banner = Banners(
            name="pinned",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="pin.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        pin = UserBannerPins(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=banner.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        result = await get_user_preferences(user_id=1, db=db_session)
        assert len(result.pins) == 1
        assert result.pins[0].size == BannerSize.small
        assert result.pins[0].theme == BannerTheme.dark
        assert result.pins[0].banner is not None
        assert result.pins[0].banner.name == "pinned"


@pytest.mark.integration
class TestUpdatePreferredSize:
    async def test_creates_row_if_not_exists(self, db_session: AsyncSession):
        from app.services.banner import update_preferred_size

        await update_preferred_size(user_id=1, size=BannerSize.large, db=db_session)

        result = await db_session.get(UserBannerPreferences, 1)
        assert result is not None
        assert result.preferred_size == BannerSize.large

    async def test_updates_existing_row(self, db_session: AsyncSession):
        from app.services.banner import update_preferred_size

        prefs = UserBannerPreferences(user_id=1, preferred_size=BannerSize.small)
        db_session.add(prefs)
        await db_session.commit()

        await update_preferred_size(user_id=1, size=BannerSize.large, db=db_session)

        await db_session.refresh(prefs)
        assert prefs.preferred_size == BannerSize.large


@pytest.mark.integration
class TestPinBanner:
    async def test_creates_pin(self, db_session: AsyncSession):
        from app.services.banner import pin_banner

        banner = Banners(
            name="pinme",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="pin.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        await pin_banner(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=banner.banner_id,
            db=db_session,
        )

        from sqlalchemy import select

        result = await db_session.execute(
            select(UserBannerPins).where(
                UserBannerPins.user_id == 1,
                UserBannerPins.size == BannerSize.small,
                UserBannerPins.theme == BannerTheme.dark,
            )
        )
        pin = result.scalar_one()
        assert pin.banner_id == banner.banner_id

    async def test_rejects_nonexistent_banner(self, db_session: AsyncSession):
        from app.services.banner import pin_banner
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await pin_banner(
                user_id=1,
                size=BannerSize.small,
                theme=BannerTheme.dark,
                banner_id=99999,
                db=db_session,
            )
        assert exc_info.value.status_code == 404

    async def test_rejects_inactive_banner(self, db_session: AsyncSession):
        from app.services.banner import pin_banner
        from fastapi import HTTPException

        banner = Banners(
            name="inactive",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="x.png",
            active=False,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        with pytest.raises(HTTPException) as exc_info:
            await pin_banner(
                user_id=1,
                size=BannerSize.small,
                theme=BannerTheme.dark,
                banner_id=banner.banner_id,
                db=db_session,
            )
        assert exc_info.value.status_code == 400

    async def test_rejects_size_mismatch(self, db_session: AsyncSession):
        from app.services.banner import pin_banner
        from fastapi import HTTPException

        banner = Banners(
            name="large_banner",
            size=BannerSize.large,
            supports_dark=True,
            supports_light=True,
            full_image="m.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        with pytest.raises(HTTPException) as exc_info:
            await pin_banner(
                user_id=1,
                size=BannerSize.small,
                theme=BannerTheme.dark,
                banner_id=banner.banner_id,
                db=db_session,
            )
        assert exc_info.value.status_code == 400

    async def test_rejects_theme_mismatch(self, db_session: AsyncSession):
        from app.services.banner import pin_banner
        from fastapi import HTTPException

        banner = Banners(
            name="light_only",
            size=BannerSize.small,
            supports_dark=False,
            supports_light=True,
            full_image="l.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        with pytest.raises(HTTPException) as exc_info:
            await pin_banner(
                user_id=1,
                size=BannerSize.small,
                theme=BannerTheme.dark,
                banner_id=banner.banner_id,
                db=db_session,
            )
        assert exc_info.value.status_code == 400

    async def test_upserts_existing_pin(self, db_session: AsyncSession):
        from app.services.banner import pin_banner

        banner1 = Banners(
            name="first",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="1.png",
            active=True,
        )
        banner2 = Banners(
            name="second",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="2.png",
            active=True,
        )
        db_session.add_all([banner1, banner2])
        await db_session.commit()
        await db_session.refresh(banner1)
        await db_session.refresh(banner2)

        await pin_banner(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=banner1.banner_id,
            db=db_session,
        )
        await pin_banner(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=banner2.banner_id,
            db=db_session,
        )

        from sqlalchemy import select

        result = await db_session.execute(
            select(UserBannerPins).where(
                UserBannerPins.user_id == 1,
                UserBannerPins.size == BannerSize.small,
                UserBannerPins.theme == BannerTheme.dark,
            )
        )
        pins = result.scalars().all()
        assert len(pins) == 1
        assert pins[0].banner_id == banner2.banner_id


@pytest.mark.integration
class TestUnpinBanner:
    async def test_removes_pin(self, db_session: AsyncSession):
        from app.services.banner import unpin_banner

        banner = Banners(
            name="unpin_me",
            size=BannerSize.small,
            supports_dark=True,
            supports_light=True,
            full_image="u.png",
            active=True,
        )
        db_session.add(banner)
        await db_session.commit()
        await db_session.refresh(banner)

        pin = UserBannerPins(
            user_id=1,
            size=BannerSize.small,
            theme=BannerTheme.dark,
            banner_id=banner.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        await unpin_banner(
            user_id=1, size=BannerSize.small, theme=BannerTheme.dark, db=db_session
        )

        from sqlalchemy import select

        result = await db_session.execute(
            select(UserBannerPins).where(
                UserBannerPins.user_id == 1,
                UserBannerPins.size == BannerSize.small,
                UserBannerPins.theme == BannerTheme.dark,
            )
        )
        assert result.scalar_one_or_none() is None

    async def test_404_when_no_pin(self, db_session: AsyncSession):
        from app.services.banner import unpin_banner
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await unpin_banner(
                user_id=1, size=BannerSize.small, theme=BannerTheme.dark, db=db_session
            )
        assert exc_info.value.status_code == 404


@pytest.mark.integration
class TestGetCurrentBannerWithPreferences:
    async def test_anonymous_uses_query_param_size(
        self, db_session: AsyncSession, redis_client: redis.Redis,
    ):
        """Anonymous user: size comes from query param, not preferences."""
        from app.services.banner import get_current_banner

        banner = Banners(
            name="small_banner", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="s.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()

        result = await get_current_banner("dark", "small", db_session, redis_client)
        assert result.size == BannerSize.small

    async def test_authenticated_user_preferred_size_overrides_param(
        self, db_session: AsyncSession, redis_client: redis.Redis,
    ):
        """Authenticated user with preferred_size=large gets large banners."""
        from app.services.banner import get_current_banner

        prefs = UserBannerPreferences(user_id=1, preferred_size=BannerSize.large)
        db_session.add(prefs)

        banner = Banners(
            name="large_banner", size=BannerSize.large, supports_dark=True,
            supports_light=True, full_image="lg.png", active=True,
        )
        db_session.add(banner)
        await db_session.commit()

        # Pass user_id=1; the size param "small" should be overridden by preferred_size
        result = await get_current_banner(
            "dark", "small", db_session, redis_client, user_id=1,
        )
        assert result.size == BannerSize.large

    async def test_authenticated_user_with_pin_returns_pinned(
        self, db_session: AsyncSession, redis_client: redis.Redis,
    ):
        """Authenticated user with a pin for the effective size+theme gets pinned banner."""
        from app.services.banner import get_current_banner

        pinned = Banners(
            name="my_fave", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="fave.png", active=True,
        )
        other = Banners(
            name="other", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="other.png", active=True,
        )
        db_session.add_all([pinned, other])
        await db_session.commit()
        await db_session.refresh(pinned)

        pin = UserBannerPins(
            user_id=1, size=BannerSize.small, theme=BannerTheme.dark,
            banner_id=pinned.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        result = await get_current_banner(
            "dark", "small", db_session, redis_client, user_id=1,
        )
        assert result.banner_id == pinned.banner_id
        assert result.name == "my_fave"

    async def test_pinned_inactive_banner_falls_through(
        self, db_session: AsyncSession, redis_client: redis.Redis,
    ):
        """Pin on inactive banner falls through to normal rotation."""
        from app.services.banner import get_current_banner

        inactive = Banners(
            name="inactive_pinned", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="inactive.png", active=False,
        )
        fallback = Banners(
            name="fallback", size=BannerSize.small, supports_dark=True,
            supports_light=True, full_image="fallback.png", active=True,
        )
        db_session.add_all([inactive, fallback])
        await db_session.commit()
        await db_session.refresh(inactive)

        pin = UserBannerPins(
            user_id=1, size=BannerSize.small, theme=BannerTheme.dark,
            banner_id=inactive.banner_id,
        )
        db_session.add(pin)
        await db_session.commit()

        result = await get_current_banner(
            "dark", "small", db_session, redis_client, user_id=1,
        )
        assert result.name == "fallback"
