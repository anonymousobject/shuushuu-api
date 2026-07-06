"""Ordered resolver registry; first match wins."""

from app.services.url_import.base import Resolver
from app.services.url_import.pixiv import PixivResolver

_RESOLVERS: list[Resolver] = [
    PixivResolver(),
]


def get_resolver(url: str) -> Resolver | None:
    for resolver in _RESOLVERS:
        if resolver.match(url):
            return resolver
    return None


def supported_sites() -> list[str]:
    return [resolver.site for resolver in _RESOLVERS]
