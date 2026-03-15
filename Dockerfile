# SMKRV MCP Studio — All-in-One Docker Image
# Contains: nginx + uvicorn (backend) + FastMCP (mcp) + FastMCP (agent-mcp) + redis
# Core IP files are pre-compiled .so (amd64 + arm64) — no build stage needed

FROM python:3.12-slim-bookworm

# --- System packages: nginx, supervisor, build tools, Redis 7.2+ ---
# Redis 7.0 (Debian Bookworm apt) cannot read RDB format v12 written by 7.2+
# Install from official packages.redis.io to get 7.2+
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx supervisor libpq5 gcc libpq-dev curl gpg \
    && curl -fsSL https://packages.redis.io/gpg | gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb bookworm main" \
       > /etc/apt/sources.list.d/redis.list \
    && apt-get update && apt-get install -y --no-install-recommends "redis-server=6:7.4*" "redis-tools=6:7.4*" \
    && rm -rf /var/lib/apt/lists/*

# --- Python venv with all dependencies ---
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    pip install --no-cache-dir \
        "fastmcp>=3.0.0,<4" \
        "httpx>=0.28.0,<1" \
        "redis[hiredis]>=5.0.0,<6" \
        "bcrypt>=4.0.0,<5" \
        "Jinja2>=3.1.0,<4" \
        "certbot>=3.0.0,<4" \
        "certbot-dns-cloudflare>=3.0.0,<4" \
        "certbot-dns-route53>=3.0.0,<4" \
    && pip install --no-cache-dir "pip>=26.0" \
    && rm -f /tmp/requirements.txt

# Remove build tools after building native extensions
RUN apt-get purge -y gcc gcc-12 cpp cpp-12 libpq-dev curl gpg \
    && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# --- App user ---
RUN useradd -m -u 1000 appuser

# --- Backend application ---
WORKDIR /app
COPY VERSION ./VERSION
COPY LICENSE ./LICENSE
# Backend app with pre-compiled .so for core IP (both amd64 + arm64 in same dir;
# Python automatically imports the correct platform-specific .so by filename suffix)
COPY backend/app/ ./app/
COPY backend/agent_mcp/ ./agent_mcp/
COPY backend/alembic/ ./alembic/
COPY backend/alembic.ini ./alembic.ini
COPY backend/mcp_entrypoint.sh ./mcp_entrypoint.sh
RUN chmod +x ./mcp_entrypoint.sh

# --- Frontend (built JS bundle) ---
COPY frontend/dist/ /usr/share/nginx/html/

# --- Nginx config ---
COPY nginx.conf /etc/nginx/default.conf.default
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN rm -f /etc/nginx/sites-enabled/default

# --- Supervisord config ---
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# --- Data directories ---
RUN mkdir -p \
        /app/data \
        /shared/generated \
        /shared/nginx_config \
        /data/redis \
        /var/log \
        /var/www/certbot \
        /etc/letsencrypt \
    && chown -R appuser:appuser /app/data /shared/generated /shared/nginx_config \
        /var/www/certbot /etc/letsencrypt /etc/nginx/conf.d \
    && chown -R redis:redis /data/redis

ENV PYTHONPATH="/app" \
    PYTHONDONTWRITEBYTECODE=1 \
    STUDIO_DATABASE_URL="sqlite+aiosqlite:///data/studio.db" \
    STUDIO_SSL_STAGING="false" \
    STUDIO_EXTERNAL_HTTPS_PORT="443" \
    STUDIO_NGINX_CONFIG_DIR="/etc/nginx/conf.d" \
    STUDIO_NGINX_BACKEND_HOST="127.0.0.1" \
    STUDIO_NGINX_MCP_HOST="127.0.0.1" \
    STUDIO_NGINX_AGENT_MCP_HOST="127.0.0.1" \
    STUDIO_NGINX_HTTP2_MODERN="false" \
    STUDIO_TRUSTED_PROXIES="127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,141.101.64.0/18,108.162.192.0/18,190.93.240.0/20,188.114.96.0/20,197.234.240.0/22,198.41.128.0/17,162.158.0.0/15,104.16.0.0/13,104.24.0.0/14,172.64.0.0/13,131.0.72.0/22"
# Security-sensitive vars MUST be provided at runtime:
# REDIS_PASSWORD, STUDIO_AGENT_SERVICE_TOKEN, STUDIO_ENCRYPTION_KEY, STUDIO_JWT_SECRET

LABEL org.opencontainers.image.title="SMKRV MCP Studio" \
      org.opencontainers.image.description="Turn your database into an AI data source via MCP" \
      org.opencontainers.image.url="https://smcps.net/" \
      org.opencontainers.image.documentation="https://docs.smcps.net/" \
      org.opencontainers.image.source="https://github.com/SMKRV-MCP-Studio/SMKRV-MCP-Studio" \
      org.opencontainers.image.vendor="Sergey Makarov" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary" \
      com.smcps.eula="By pulling or running this image you accept the license at /app/LICENSE and https://github.com/SMKRV-MCP-Studio/SMKRV-MCP-Studio/blob/main/LICENSE"

EXPOSE 3000 80 443

HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
