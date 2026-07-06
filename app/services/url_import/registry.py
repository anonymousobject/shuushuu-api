"""Ordered resolver registry; first match wins."""

from app.services.url_import.base import Resolver
from app.services.url_import.danbooru import DanbooruResolver
from app.services.url_import.gelbooru import GelbooruResolver
from app.services.url_import.moebooru import MoebooruResolver
from app.services.url_import.pixiv import PixivResolver
from app.services.url_import.twitter import TwitterResolver

_RESOLVERS: list[Resolver] = [
    PixivResolver(),
    DanbooruResolver(),
    GelbooruResolver(),
    MoebooruResolver(),
    TwitterResolver(),
]


def get_resolver(url: str) -> Resolver | None:
    for resolver in _RESOLVERS:
        if resolver.match(url):
            return resolver
    return None


def supported_sites() -> list[str]:
    return [resolver.site for resolver in _RESOLVERS]
