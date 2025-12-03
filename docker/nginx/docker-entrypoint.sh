#!/bin/sh
# Nginx entrypoint script to substitute environment variables in config template

set -e

# Default STORAGE_PATH if not set
export STORAGE_PATH=${STORAGE_PATH:-/shuushuu/images}

echo "Substituting environment variables in nginx config..."
echo "STORAGE_PATH: $STORAGE_PATH"

# Use envsubst to replace ${STORAGE_PATH} in template
envsubst '${STORAGE_PATH}' < /etc/nginx/conf.d/frontend.conf.template > /etc/nginx/conf.d/default.conf

echo "Generated nginx config:"
cat /etc/nginx/conf.d/default.conf

# Start nginx
exec nginx -g 'daemon off;'
