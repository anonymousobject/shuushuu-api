#!/bin/sh
# Nginx entrypoint: substitute env vars into the prod conf template, then exec.

set -e

# Default STORAGE_PATH if not set
export STORAGE_PATH=${STORAGE_PATH:-/shuushuu/images}

echo "Substituting environment variables in nginx config..."
echo "STORAGE_PATH: $STORAGE_PATH"

# Drop the image's stock default.conf so envsubst can write ours in its
# place without leaving a stale default lurking on rebuilds.
rm -f /etc/nginx/conf.d/default.conf

# Allowlist the vars envsubst should expand. Everything else (notably
# nginx's own $variables like $remote_addr, $request_uri, $host) stays
# literal in the output.
envsubst '${STORAGE_PATH},${NGINX_HOST},${NGINX_PORT}' \
    < /etc/nginx/conf.d/frontend.conf.template \
    > /etc/nginx/conf.d/default.conf

echo "Testing nginx config..."
nginx -t

echo "Starting nginx..."
exec nginx -g 'daemon off;'
