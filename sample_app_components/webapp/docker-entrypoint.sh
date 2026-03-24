#!/bin/sh
set -e

# Extract DNS resolver from the container's resolv.conf so nginx can do
# runtime DNS lookups (prevents 502 after backend pod IP changes).
NAMESERVER=$(grep -m1 '^nameserver' /etc/resolv.conf | awk '{print $2}')
export NAMESERVER=${NAMESERVER:-168.63.129.16}

# Replace environment variables in nginx config template
envsubst '${FASTAPI_BACKEND_URL} ${FASTAPI_BACKEND_HOST} ${NAMESERVER}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf

echo "nginx resolver: $NAMESERVER"
echo "backend URL:    $FASTAPI_BACKEND_URL"
echo "backend Host:   $FASTAPI_BACKEND_HOST"

# Execute the main command
exec "$@"
