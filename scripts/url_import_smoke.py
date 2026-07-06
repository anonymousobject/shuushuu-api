"""Manual smoke test for URL-import resolvers against LIVE sites.

Usage:
    uv run python scripts/url_import_smoke.py           # all sites
    uv run python scripts/url_import_smoke.py pixiv     # one site
    uv run python scripts/url_import_smoke.py twitter=https://x.com/foo/status/123

Not run in CI (external dependencies). Run before deploys or when a
resolver is suspected of having rotted.
"""

import asyncio
import sys

import httpx

from app.services.url_import.base import BROWSER_USER_AGENT
from app.services.url_import.registry import get_resolver

# Known-good public posts. If one gets deleted, replace it with any public
# post from the same site (pass site=URL on the CLI to test a candidate).
SAMPLES = {
    "pixiv": "https://www.pixiv.net/en/artworks/138823691",
    "danbooru": "https://danbooru.donmai.us/posts/1",
    "gelbooru": "https://gelbooru.com/index.php?page=post&s=view&id=1",
    "yande.re": "https://yande.re/post/show/1",
    "twitter": "",  # fill at implementation time with a live SFW art tweet
    "bluesky": "",  # fill at implementation time with a live image post
    "zerochan": "",  # fill at implementation time with a live entry
    "ko-fi": "",  # fill at implementation time with a live gallery post
}


async def check(site: str, url: str) -> bool:
    if not url:
        print(f"[SKIP] {site}: no sample URL configured")
        return True
    resolver = get_resolver(url)
    if resolver is None:
        print(f"[FAIL] {site}: no resolver matched {url}")
        return False
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            post = await resolver.resolve(url, client)
            image = post.images[0]
            response = await client.get(
                image.full_url,
                headers={"User-Agent": BROWSER_USER_AGENT, **image.headers},
            )
    except Exception as exc:  # noqa: BLE001 — smoke tool, report everything
        print(f"[FAIL] {site}: {type(exc).__name__}: {exc}")
        return False
    ok = response.status_code == 200 and response.headers.get(
        "content-type", ""
    ).startswith("image/")
    marker = "PASS" if ok else "FAIL"
    print(
        f"[{marker}] {site}: {len(post.images)} image(s), "
        f"HTTP {response.status_code}, {len(response.content)} bytes, "
        f"canonical={post.canonical_url}"
    )
    return ok


async def main() -> int:
    targets = dict(SAMPLES)
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if "=" in arg:
            site, url = arg.split("=", 1)
            targets = {site: url}
        else:
            if arg not in SAMPLES:
                print(f"[FAIL] {arg}: unknown site (known: {', '.join(SAMPLES)})")
                return 1
            targets = {arg: SAMPLES[arg]}
    results = [await check(site, url) for site, url in targets.items()]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
