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

exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
