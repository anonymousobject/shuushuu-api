#!/bin/sh
# Add this frontend image's hashed chunks to the shared frontend_assets volume
# (docker-compose.prod.yml), where nginx looks when a frontend replica 404s one.
# `make prod-deploy` runs it in a one-off container of the NEW image before the
# rollout starts, so every chunk the new pages reference is on disk before any
# is served.
#
# Add-only, and each file lands via rename: browsers and Cloudflare cache these
# URLs for a year as immutable, so no reader may ever see a half-written file.
# Overwriting is never needed anyway — the filename is a hash of the content.
set -eu

src=/app/build/client/_app/immutable
dst=/frontend-assets/_app/immutable

cd "$src"
added=0
for f in $(find . -type f); do
    [ -e "$dst/$f" ] && continue
    mkdir -p "$dst/$(dirname "$f")"
    cp "$f" "$dst/$f.tmp"
    mv "$dst/$f.tmp" "$dst/$f"
    added=$((added + 1))
done
echo "frontend assets: added $added file(s) to $dst"
