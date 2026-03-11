#!/bin/sh
set -e

VERSION=$(cat /app/VERSION 2>/dev/null || echo "unknown")

cat <<'BANNER'

 ___ __  __ _  _______   __       __  __  ___ ___   ___ _           _ _
/ __|  \/  | |/ / _ \ \ / /  ●   |  \/  |/ __| _ \ / __| |_ _  _ __| (_)___
\__ \ |\/| | ' <|   /\ V /   ●   | |\/| | (__|  _/ \__ \  _| || / _` | / _ \
|___/_|  |_|_|\_\_|_\ \_/        |_|  |_|\___|_|   |___/\__|\_,_\__,_|_\___/
BANNER

echo "  v${VERSION}"
echo ""
echo "  Web:  https://smcps.net/"
echo "  Docs: https://docs.smcps.net/"
echo "  Hub:  https://hub.docker.com/r/smkrv/smkrv-mcp-studio"
echo ""
echo "  License: /app/LICENSE"
echo "  ─────────────────────────────────────────────────────────"
echo ""

# Initialize nginx config if not generated yet (preserve SSL config across restarts)
if [ ! -f /etc/nginx/conf.d/default.conf ]; then
    cp /etc/nginx/default.conf.default /etc/nginx/conf.d/default.conf
fi

# Ensure bind-mount directories exist and are writable by their respective users
mkdir -p /app/data /data/redis /var/www/certbot /etc/letsencrypt /etc/nginx/conf.d
chown -R 1000:1000 /app/data /etc/nginx/conf.d /var/www/certbot /etc/letsencrypt
chown -R redis:redis /data/redis

exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
