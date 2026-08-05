"""Ordered resolver registry; first match wins."""

from app.config import settings
from app.services.url_import.base import ImportSite, Resolver
from app.services.url_import.bluesky import BlueskyResolver
from app.services.url_import.danbooru import DanbooruResolver
from app.services.url_import.gelbooru import GelbooruResolver
from app.services.url_import.kofi import KofiResolver
from app.services.url_import.moebooru import MoebooruResolver
from app.services.url_import.pixiv import PixivResolver
from app.services.url_import.twitter import TwitterResolver
from app.services.url_import.zerochan import ZerochanResolver

_RESOLVERS: list[Resolver] = [
    PixivResolver(),
    DanbooruResolver(),
]

if settings.GELBOORU_API_KEY and settings.GELBOORU_USER_ID:
    # Gelbooru's dapi returns 401 without credentials; don't advertise a
    # resolver that would always fail (see gelbooru.py module docstring).
    _RESOLVERS.append(GelbooruResolver())

_RESOLVERS += [
    MoebooruResolver(),
    TwitterResolver(),
    BlueskyResolver(),
    ZerochanResolver(),
    KofiResolver(),
]

if settings.ENVIRONMENT == "development":
    from app.services.url_import.fixture import FixtureResolver

    _RESOLVERS.append(FixtureResolver())


def get_resolver(url: str) -> Resolver | None:
    for resolver in _RESOLVERS:
        if resolver.match(url):
            return resolver
    return None


def advertised_sites() -> list[ImportSite]:
    """Sites shown to users. Resolvers with no example_url are registered but
    unadvertised (the dev-only fixture)."""
    return [
        ImportSite(site=resolver.site, example_url=resolver.example_url)
        for resolver in _RESOLVERS
        if resolver.example_url
    ]


def supported_sites() -> list[str]:
    return [entry.site for entry in advertised_sites()]
