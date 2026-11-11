#!/bin/sh
# Reload nginx when certbot has renewed a certificate.
#
# nginx reads its certificates once, at start or reload. The certbot service
# renews them on the volume both containers mount, but it has no way to signal
# nginx, so a renewed certificate used to sit unused until nginx next happened
# to restart. Started in the background by the image's entrypoint
# (Dockerfile.nginx).
#
# It reloads only when a certificate has changed, never on a timer: nginx.conf
# and the conf.d files are bind-mounted from the repo checkout, and a reload
# applies whatever is on disk at that moment.
#
# Where no certificates are mounted (dev, test) the fingerprint never changes
# and this does nothing.

CERT_RELOAD_GLOB=${CERT_RELOAD_GLOB:-/etc/letsencrypt/live/*/fullchain.pem}
CERT_RELOAD_INTERVAL=${CERT_RELOAD_INTERVAL:-3600}

fingerprint() {
    # Unquoted on purpose: the glob has to expand.
    # shellcheck disable=SC2086
    cat $CERT_RELOAD_GLOB 2>/dev/null | sha256sum
}

loaded=$(fingerprint)

while :; do
    sleep "$CERT_RELOAD_INTERVAL"

    current=$(fingerprint)
    [ "$current" = "$loaded" ] && continue

    echo "cert-reload: certificates changed, reloading nginx"
    # Only a reload that worked moves the baseline. A poll can land while
    # certbot is partway through writing a renewal, and the config can be
    # broken for unrelated reasons; either way the next poll tries again.
    if nginx -t && nginx -s reload; then
        loaded=$current
    else
        echo "cert-reload: reload failed, will retry in ${CERT_RELOAD_INTERVAL}s" >&2
    fi
done
