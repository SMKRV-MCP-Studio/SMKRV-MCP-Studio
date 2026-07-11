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

# REDIS_PASSWORD: auto-generate and persist on first boot so an existing user
# upgrading to an image that newly requires it is not crash-looped (Redis is
# internal-only; the persisted secret stays stable across restarts/upgrades).
if [ -z "$REDIS_PASSWORD" ]; then
    mkdir -p /app/data
    REDIS_PW_FILE=/app/data/.redis_password
    if [ -f "$REDIS_PW_FILE" ]; then
        REDIS_PASSWORD=$(cat "$REDIS_PW_FILE")
        echo "REDIS_PASSWORD not set - using the persisted auto-generated value."
    else
        REDIS_PASSWORD=$(openssl rand -hex 24)
        printf '%s' "$REDIS_PASSWORD" > "$REDIS_PW_FILE"
        chmod 600 "$REDIS_PW_FILE"
        echo "REDIS_PASSWORD not set - generated one and persisted it to $REDIS_PW_FILE."
    fi
    export REDIS_PASSWORD
fi

# The remaining secrets cannot be auto-generated (they protect existing encrypted
# data and sessions), so they stay hard-required.
missing_vars=""
for var in STUDIO_ENCRYPTION_KEY STUDIO_JWT_SECRET STUDIO_AGENT_SERVICE_TOKEN; do
    if [ -z "$(eval echo \$$var)" ]; then
        missing_vars="$missing_vars $var"
    fi
done

if [ -n "$missing_vars" ]; then
    echo "============================================="
    echo "ERROR: Required environment variables not set:"
    echo "$missing_vars"
    echo ""
    echo "Generate them with:"
    echo "  openssl rand -base64 32"
    echo ""
    echo "Then set in your .env file or docker run --env"
    echo "============================================="
    exit 1
fi

# Initialize nginx config if not generated yet (preserve SSL config across restarts)
if [ ! -f /etc/nginx/conf.d/default.conf ]; then
    cp /etc/nginx/default.conf.default /etc/nginx/conf.d/default.conf
fi

# Ensure bind-mount directories exist and are writable by their respective users
mkdir -p /app/data /data/redis /var/www/certbot /etc/letsencrypt /etc/nginx/conf.d
chown -R 1000:1000 /app/data /etc/nginx/conf.d /var/www/certbot /etc/letsencrypt
chown -R redis:redis /data/redis

# Check Redis data compatibility — Redis 7.0 can't read RDB format v12 (from 7.2+)
# If incompatible data exists, move it aside so Redis can start fresh.
# Tokens and metrics will be re-synced from DB on backend startup.
# Guard: only fires if image somehow has Redis 7.0 (e.g. Watchtower rollback).
if [ -f /data/redis/dump.rdb ]; then
    # RDB header: "REDIS" (5 bytes) + 4-digit ASCII version at bytes 5-8
    RDB_VER=$(dd if=/data/redis/dump.rdb bs=1 skip=5 count=4 2>/dev/null | tr -d '\0')
    RDB_VER=${RDB_VER:-0}
    # If RDB_VER contains non-digits (corrupted header), reset to 0
    case "$RDB_VER" in *[!0-9]*) RDB_VER=0 ;; esac
    REDIS_VER=$(redis-server --version 2>/dev/null | sed -n 's/.*v=\([0-9]*\.[0-9]*\).*/\1/p')
    REDIS_VER=${REDIS_VER:-0.0}
    if [ "$RDB_VER" -gt 10 ] && echo "$REDIS_VER" | grep -q '^7\.0'; then
        echo "WARNING: Redis $REDIS_VER cannot read RDB format v$RDB_VER — moving old data aside"
        BACKUP="/data/redis/incompatible.$(date +%Y%m%d%H%M%S)"
        mkdir -p "$BACKUP"
        mv /data/redis/dump.rdb "$BACKUP/" 2>/dev/null || true
        mv /data/redis/appendonlydir "$BACKUP/" 2>/dev/null || true
        mkdir -p /data/redis/appendonlydir
        chown -R redis:redis /data/redis
    fi
fi

# Pre-upgrade SQLite backup (safety net for Watchtower auto-updates). Only taken
# when the image version changed, so a plain restart of the same version does not
# overwrite a genuine pre-upgrade snapshot with the already-migrated database.
if [ -f /app/data/studio.db ]; then
    VERSION_MARKER=/app/data/.deployed_version
    LAST_VERSION=$(cat "$VERSION_MARKER" 2>/dev/null || echo "")
    if [ "$LAST_VERSION" != "$VERSION" ]; then
        cp /app/data/studio.db /app/data/studio.db.pre-upgrade.bak 2>/dev/null || true
        echo "SQLite pre-upgrade backup created (${LAST_VERSION:-none} -> ${VERSION})"
        printf '%s' "$VERSION" > "$VERSION_MARKER" 2>/dev/null || true
    fi
fi

exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
